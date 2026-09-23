import os
import json
import asyncio
import aiohttp
import logging
from logging.handlers import RotatingFileHandler
import time

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# Setup Config
current_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(current_dir, 'data')
os.makedirs(data_dir, exist_ok=True)

config_path = os.path.join(data_dir, 'config.json')
vc_config_path = os.path.join(data_dir, 'vcstatus.json')
premium_db_path = os.path.join(data_dir, 'premium.db')
anti_db_path = os.path.join(data_dir, 'anti.db')

default_config = {
    "cmdLogWebhook": "",
    "premiumApplyChannelId": "",
    "backupChannelId": "",
    "ownerId": "",
    "prefix": "!"
}

if not os.path.exists(config_path):
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(default_config, f, indent=2)

with open(config_path, 'r', encoding='utf-8') as f:
    try:
        config = json.load(f)
    except Exception:
        config = default_config


# Setup Logging
logger = logging.getLogger("vcbot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(name)s: %(message)s')

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = RotatingFileHandler(
    os.path.join(current_dir, 'bot.log'),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Rate limiting & caching state
_debounce_tasks = {}
_channel_last_update = {}
_vanity_cache = {}
COOLDOWN_SECONDS = 120  # Safe interval between PUT status requests per channel

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def cv2(text: str) -> discord.ui.LayoutView:
    layout = discord.ui.LayoutView()
    container = discord.ui.Container(accent_color=discord.Colour(0xFFFFFF))
    container.add_item(discord.ui.TextDisplay(text))
    layout.add_item(container)
    return layout

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.message_content = True
        intents.members = True
        intents.presences = True
        bot_prefix = config.get('prefix', '!')
        super().__init__(command_prefix=commands.when_mentioned_or(bot_prefix), intents=intents, help_command=None)
        self.session: aiohttp.ClientSession = None
        self.premium_db: aiosqlite.Connection = None
        self.anti_db: aiosqlite.Connection = None

    async def setup_hook(self):
        # Shared HTTP Session
        self.session = aiohttp.ClientSession()

        # Async SQLite DB Connections
        self.premium_db = await aiosqlite.connect(premium_db_path)
        await self.premium_db.execute('''CREATE TABLE IF NOT EXISTS premium_guilds (guild_id TEXT PRIMARY KEY, expires_at INTEGER)''')
        await self.premium_db.commit()

        self.anti_db = await aiosqlite.connect(anti_db_path)
        await self.anti_db.execute('''CREATE TABLE IF NOT EXISTS extra_owners (guild_id TEXT, user_id TEXT)''')
        await self.anti_db.commit()

        # Sync App Commands
        await self.tree.sync()

        # Start Background Loops
        self.hourly_backup.start()
        check_premium_expirations.start()
        vc_status_updater.start()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        if self.premium_db:
            await self.premium_db.close()
        if self.anti_db:
            await self.anti_db.close()
        await super().close()

    @tasks.loop(hours=1)
    async def hourly_backup(self):
        try:
            channel_id = int(config.get('backupChannelId', 0))
            if not channel_id:
                return
            channel = self.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.fetch_channel(channel_id)
                except Exception as e:
                    logger.error(f"Failed to fetch backup channel {channel_id}: {e}")
                    return

            if not hasattr(channel, 'send') or not isinstance(channel, (discord.TextChannel, discord.Thread)):
                logger.warning(f"Backup channel {channel_id} is not a text-capable channel.")
                return

            files = []
            total_size = 0
            for file_name in os.listdir(data_dir):
                if file_name.endswith(('.db', '.sqlite', '.json')) and file_name != 'config.json':
                    file_path = os.path.join(data_dir, file_name)
                    try:
                        size = os.path.getsize(file_path)
                        if total_size + size > 24 * 1024 * 1024:  # 24MB Discord attachment limit
                            logger.warning(f"Backup file {file_name} skipped: exceeds size limit.")
                            continue
                        total_size += size
                        files.append(discord.File(file_path))
                    except Exception as f_err:
                        logger.error(f"Failed to prepare backup file {file_name}: {f_err}")

            if not files:
                logger.info("No files available for hourly backup.")
                return

            await channel.send("🕒 Hourly Auto DB Backup", files=files)
            logger.info("Hourly backup completed successfully.")
        except Exception as e:
            logger.error(f"Hourly backup failed: {e}", exc_info=True)

bot = MyBot()
webhook_url = config.get('cmdLogWebhook')

# Permission Helpers
async def is_mod(target) -> bool:
    guild = getattr(target, 'guild', None)
    author = getattr(target, 'author', None) or getattr(target, 'user', None)
    if not guild or not author:
        return False
    if str(author.id) == str(guild.owner_id):
        return True
    if str(author.id) == str(config.get('ownerId')):
        return True
    if bot.anti_db:
        async with bot.anti_db.execute('SELECT 1 FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(guild.id), str(author.id))) as cursor:
            if await cursor.fetchone():
                return True
    return False

async def is_guild_or_bot_owner(target) -> bool:
    guild = getattr(target, 'guild', None)
    author = getattr(target, 'author', None) or getattr(target, 'user', None)
    if not guild or not author:
        return False
    if str(author.id) == str(guild.owner_id) or str(author.id) == str(config.get('ownerId')):
        return True
    return False

# Global Error Handlers
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return await ctx.send(view=cv2("❌ Access Denied: You do not have permission to use this command."))
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.ChannelNotFound)):
        return await ctx.send(view=cv2(f"⚠️ Invalid arguments or usage: {error}"))
    
    logger.error(f"Unhandled error in command '{ctx.command}':", exc_info=error)
    await ctx.send(view=cv2("❌ An unexpected error occurred while executing the command."))

async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message(view=cv2("❌ Access Denied: You do not have permission to use this command."), ephemeral=True)
        return
    logger.error(f"Unhandled slash command error in '{interaction.command.name if interaction.command else 'unknown'}':", exc_info=error)
    if not interaction.response.is_done():
        await interaction.response.send_message(view=cv2("❌ An unexpected error occurred while processing the command."), ephemeral=True)

bot.tree.on_error = on_tree_error

# Premium Expiration Task
@tasks.loop(minutes=5)
async def check_premium_expirations():
    await bot.wait_until_ready()
    now = int(time.time())
    if not bot.premium_db:
        return
    async with bot.premium_db.execute('SELECT guild_id FROM premium_guilds WHERE expires_at IS NOT NULL AND expires_at < ?', (now,)) as cursor:
        expired_guilds = await cursor.fetchall()
    
    for row in expired_guilds:
        guild_id = row[0]
        await bot.premium_db.execute('DELETE FROM premium_guilds WHERE guild_id = ?', (guild_id,))
        await bot.premium_db.commit()
                
        configs = load_json(vc_config_path)
        guild_configs = [c for c in configs if isinstance(c, dict) and c.get('guildId') == guild_id]
        if len(guild_configs) > 5:
            to_delete = guild_configs[5:]
            for d in to_delete:
                if d in configs:
                    configs.remove(d)
                try:
                    headers = {"Authorization": f"Bot {bot.http.token}", "Content-Type": "application/json"}
                    url = f"https://discord.com/api/v10/channels/{d['channelId']}/voice-status"
                    if bot.session and not bot.session.closed:
                        async with bot.session.put(url, headers=headers, json={"status": None}) as resp:
                            pass
                except Exception as e:
                    logger.error(f"Failed to reset VC status for channel {d.get('channelId')}: {e}")
            save_json(vc_config_path, configs)

async def log_command(user, command_name, guild_id):
    if not webhook_url or not bot.session or bot.session.closed:
        return
    try:
        webhook = discord.Webhook.from_url(webhook_url, session=bot.session)
        layout = discord.ui.LayoutView()
        container = discord.ui.Container(accent_color=discord.Colour(0xFFFFFF))
        container.add_item(discord.ui.TextDisplay(f"### Command Executed\n**User:** {user.mention} ({user})\n**Command:** `{command_name}`\n**Server ID:** `{guild_id or 'DM'}`"))
        layout.add_item(container)
        await webhook.send(view=layout)
    except Exception as e:
        logger.error(f"Failed to log command to webhook: {e}")

async def get_vanity_uses(guild: discord.Guild) -> str:
    if not guild or not guild.me.guild_permissions.manage_guild:
        return _vanity_cache.get(str(guild.id), "0")
    try:
        invite = await guild.vanity_invite()
        if invite and invite.uses is not None:
            uses_str = str(invite.uses)
            _vanity_cache[str(guild.id)] = uses_str
            return uses_str
    except (discord.Forbidden, discord.HTTPException, discord.NotFound, AttributeError):
        pass
    return _vanity_cache.get(str(guild.id), "0")

async def update_guild_channels(guild_id: str):
    configs = load_json(vc_config_path)
    guild_configs = [c for c in configs if isinstance(c, dict) and c.get('guildId') == guild_id]
    if not guild_configs:
        return

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return

    totalusers = guild.member_count or 0
    onlineusers = len([m for m in guild.members if m.status != discord.Status.offline])
    activevc = 0
    vcusers = 0
    vc_dict = {}
    for vc in guild.voice_channels:
        vc_dict[str(vc.id)] = vc
        cnt = len(vc.members)
        if cnt > 0:
            activevc += 1
            vcusers += cnt
    for sc in guild.stage_channels:
        vc_dict[str(sc.id)] = sc
        cnt = len(sc.members)
        if cnt > 0:
            activevc += 1
            vcusers += cnt

    vanity_uses = await get_vanity_uses(guild)

    headers = {
        "Authorization": f"Bot {bot.http.token}",
        "Content-Type": "application/json"
    }

    if not bot.session or bot.session.closed:
        return

    now = time.time()

    for cfg in guild_configs:
        channel_id = cfg['channelId']
        vc = vc_dict.get(channel_id)
        if not vc:
            continue

        # Respect per-channel cooldown
        last_sent = _channel_last_update.get(channel_id, 0)
        if now - last_sent < COOLDOWN_SECONDS:
            continue

        status = cfg.get('template', '') \
                    .replace('{totalusers}', str(totalusers)) \
                    .replace('{onlineusers}', str(onlineusers)) \
                    .replace('{activevc}', str(activevc)) \
                    .replace('{vcusers}', str(vcusers)) \
                    .replace('{total}', str(totalusers)) \
                    .replace('{active}', str(activevc)) \
                    .replace('{vanityuses}', str(vanity_uses)) \
                    .replace('{invites}', str(vanity_uses))
        
        status = status.strip()[:500]
        body = {"status": status}
        url = f"https://discord.com/api/v10/channels/{channel_id}/voice-status"

        try:
            async with bot.session.put(url, headers=headers, json=body) as resp:
                if resp.status == 429:
                    retry_after = 5.0
                    try:
                        resp_data = await resp.json()
                        retry_after = float(resp_data.get('retry_after', 5.0))
                    except Exception:
                        retry_after = float(resp.headers.get('Retry-After', 5.0))
                    logger.warning(f"Rate limited (429) on channel {channel_id}. Backing off for {retry_after}s.")
                    await asyncio.sleep(retry_after)
                    # Retry once after backoff
                    async with bot.session.put(url, headers=headers, json=body) as retry_resp:
                        if retry_resp.status in (200, 204):
                            _channel_last_update[channel_id] = time.time()
                elif resp.status in (200, 204):
                    _channel_last_update[channel_id] = time.time()
                else:
                    logger.error(f"Failed to update VC status for {channel_id}: {resp.status} - {await resp.text()}")
        except Exception as e:
            logger.error(f"Exception updating VC status for {channel_id}: {e}")

async def trigger_guild_update(guild_id: str, delay: float = 4.0):
    if guild_id in _debounce_tasks:
        task = _debounce_tasks[guild_id]
        if not task.done():
            task.cancel()
    
    async def _debounced_call():
        try:
            await asyncio.sleep(delay)
            await update_guild_channels(guild_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in debounced update for guild {guild_id}: {e}")

    _debounce_tasks[guild_id] = bot.loop.create_task(_debounced_call())

@bot.event
async def on_ready():
    try:
        logger.info(f'Ready! Logged in as {bot.user}')
    except Exception:
        logger.info('Ready! Logged in.')
    
    try:
        await bot.change_presence(status=discord.Status.online, activity=discord.CustomActivity(name="VC Monitoring"))
    except Exception:
        pass

    configs = load_json(vc_config_path)
    guild_ids = set(c.get('guildId') for c in configs if isinstance(c, dict) and c.get('guildId'))
    for gid in guild_ids:
        await trigger_guild_update(gid, delay=1.0)

@bot.event
async def on_voice_state_update(member, before, after):
    configs = load_json(vc_config_path)
    if after.channel and any(isinstance(c, dict) and c.get('guildId') == str(after.channel.guild.id) for c in configs):
        await trigger_guild_update(str(after.channel.guild.id))
    if before.channel and before.channel.guild.id != (after.channel.guild.id if after.channel else None):
        if any(isinstance(c, dict) and c.get('guildId') == str(before.channel.guild.id) for c in configs):
            await trigger_guild_update(str(before.channel.guild.id))

@bot.event
async def on_member_join(member):
    configs = load_json(vc_config_path)
    if any(isinstance(c, dict) and c.get('guildId') == str(member.guild.id) for c in configs):
        await trigger_guild_update(str(member.guild.id))

@bot.event
async def on_member_remove(member):
    configs = load_json(vc_config_path)
    if any(isinstance(c, dict) and c.get('guildId') == str(member.guild.id) for c in configs):
        await trigger_guild_update(str(member.guild.id))

@tasks.loop(minutes=5)
async def vc_status_updater():
    await bot.wait_until_ready()
    try:
        configs = load_json(vc_config_path)
        guild_ids = set(c.get('guildId') for c in configs if isinstance(c, dict) and c.get('guildId'))
        for gid in guild_ids:
            await update_guild_channels(gid)
            await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"VC Status updater failed: {e}")

# PREFIX COMMANDS & HELPER FUNCTIONS
@bot.command()
async def premium(ctx, action: str = None, guild_id: str = None, days: str = None):
    if str(ctx.author.id) != str(config.get('ownerId')): return
    if not action or not guild_id:
        return await ctx.send(view=cv2("Usage: `!premium add <guild_id> [days]` or `!premium remove <guild_id>`"))
    
    if action.lower() == 'add':
        expires_at = None
        days_float = None
        if days:
            try:
                days_float = float(days)
                if days_float <= 0:
                    return await ctx.send(view=cv2("❌ Error: Days must be a positive number greater than 0."))
                expires_at = int(time.time() + (days_float * 86400))
            except ValueError:
                return await ctx.send(view=cv2("❌ Error: Days must be a valid number."))
        
        async with bot.premium_db.execute('SELECT 1 FROM premium_guilds WHERE guild_id = ?', (guild_id,)) as cursor:
            exists = await cursor.fetchone()
        
        if exists:
            await bot.premium_db.execute('UPDATE premium_guilds SET expires_at = ? WHERE guild_id = ?', (expires_at, guild_id))
        else:
            await bot.premium_db.execute('INSERT INTO premium_guilds (guild_id, expires_at) VALUES (?, ?)', (guild_id, expires_at))
            
        await bot.premium_db.commit()
        time_str = f"for {days_float} days" if days_float is not None else "for Lifetime"
        await ctx.send(view=cv2(f"✅ Added premium to guild {guild_id} {time_str}"))
    elif action.lower() == 'remove':
        await bot.premium_db.execute('DELETE FROM premium_guilds WHERE guild_id = ?', (guild_id,))
        await bot.premium_db.commit()
        await ctx.send(view=cv2(f"🗑️ Removed premium from guild {guild_id}"))

@bot.group(invoke_without_command=True)
async def extraowner(ctx):
    if not await is_guild_or_bot_owner(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."))
    await ctx.send(view=cv2("Usage: `!extraowner add @user`, `!extraowner remove @user`, `!extraowner list`"))

@extraowner.command(name="add")
async def extraowner_add(ctx, user: discord.User):
    if not await is_guild_or_bot_owner(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."))
    
    async with bot.anti_db.execute('SELECT 1 FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(ctx.guild.id), str(user.id))) as cursor:
        if await cursor.fetchone():
            return await ctx.send(view=cv2(f"⚠️ {user.mention} is already an extra owner."))
        
    await bot.anti_db.execute('INSERT INTO extra_owners (guild_id, user_id) VALUES (?, ?)', (str(ctx.guild.id), str(user.id)))
    await bot.anti_db.commit()
    await ctx.send(view=cv2(f"✅ Added {user.mention} as an extra owner for this server."))

@extraowner.command(name="remove")
async def extraowner_remove(ctx, user: discord.User):
    if not await is_guild_or_bot_owner(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."))
        
    async with bot.anti_db.execute('SELECT 1 FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(ctx.guild.id), str(user.id))) as cursor:
        if not await cursor.fetchone():
            return await ctx.send(view=cv2(f"⚠️ {user.mention} is not an extra owner."))
        
    await bot.anti_db.execute('DELETE FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(ctx.guild.id), str(user.id)))
    await bot.anti_db.commit()
    await ctx.send(view=cv2(f"✅ Removed {user.mention} as an extra owner for this server."))

@extraowner.command(name="list")
async def extraowner_list(ctx):
    if not await is_guild_or_bot_owner(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."))
        
    async with bot.anti_db.execute('SELECT user_id FROM extra_owners WHERE guild_id = ?', (str(ctx.guild.id),)) as cursor:
        owners = await cursor.fetchall()
    
    if not owners:
        return await ctx.send(view=cv2("ℹ️ There are no extra owners for this server."))
        
    owner_mentions = [f"<@{row[0]}>" for row in owners]
    owners_list = "\n".join(owner_mentions)
    await ctx.send(view=cv2(f"👑 **Extra Owners for this server:**\n{owners_list}"))

@bot.group(invoke_without_command=True)
async def vc(ctx):
    bot_prefix = config.get('prefix', '!')
    await ctx.send(view=cv2(f"Usage: `{bot_prefix}vc add #channel <status>` or `{bot_prefix}vc remove #channel`\nPlaceholders: `{{totalusers}}`, `{{onlineusers}}`, `{{activevc}}`, `{{vcusers}}`, `{{vanityuses}}`"))

@vc.command()
async def add(ctx, channel: discord.VoiceChannel = None, *, template: str = None):
    if not await is_mod(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner and Extra Owners can use this."))
    if ctx.guild:
        await log_command(ctx.author, 'vc add', ctx.guild.id)
    bot_prefix = config.get('prefix', '!')
    if not channel or not template:
        return await ctx.send(view=cv2(f"Usage: `{bot_prefix}vc add #channel <status template>`\nPlaceholders: `{{totalusers}}`, `{{onlineusers}}`, `{{activevc}}`, `{{vcusers}}`, `{{vanityuses}}`"))

    configs = load_json(vc_config_path)
    async with bot.premium_db.execute('SELECT 1 FROM premium_guilds WHERE guild_id = ?', (str(ctx.guild.id),)) as cursor:
        is_premium = bool(await cursor.fetchone())
    is_owner = str(ctx.author.id) == str(config.get('ownerId'))
    
    guild_configs = [c for c in configs if isinstance(c, dict) and c.get('guildId') == str(ctx.guild.id)]
    existing_idx = next((i for i, c in enumerate(configs) if isinstance(c, dict) and c.get('channelId') == str(channel.id)), -1)

    if not is_premium and not is_owner and existing_idx == -1 and len(guild_configs) >= 5:
        layout = discord.ui.LayoutView()
        container = discord.ui.Container(accent_color=discord.Colour(0xFFFFFF))
        container.add_item(discord.ui.TextDisplay("### 🔒 Premium Required\nYou have reached the limit of **5 tracked VCs** for free servers.\nPlease apply for premium to track unlimited channels.\n\n-# Made by Lord Sk"))
        container.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Get Premium", url="https://discord.gg/astria", style=discord.ButtonStyle.link)
        ))
        layout.add_item(container)
        return await ctx.send(view=layout)

    if existing_idx != -1:
        configs[existing_idx]['template'] = template
    else:
        configs.append({"guildId": str(ctx.guild.id), "channelId": str(channel.id), "template": template})

    save_json(vc_config_path, configs)
    await ctx.send(view=cv2(f"🎀 VC status configured for <#{channel.id}> and saved!"))
    await trigger_guild_update(str(ctx.guild.id), delay=1.0)

@add.error
async def add_error(ctx, error):
    bot_prefix = config.get('prefix', '!')
    if isinstance(error, commands.ChannelNotFound):
        await ctx.send(view=cv2(f"❌ Channel not found. Please mention a valid voice channel (e.g. `{bot_prefix}vc add #general <status>`)."))
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send(view=cv2(f"Usage: `{bot_prefix}vc add #channel <status template>`\nPlaceholders: `{{totalusers}}`, `{{onlineusers}}`, `{{activevc}}`, `{{vcusers}}`, `{{vanityuses}}`"))

@vc.command()
async def remove(ctx, channel: discord.VoiceChannel = None):
    if not await is_mod(ctx):
        return await ctx.send(view=cv2("❌ Access Denied: Only the Server Owner and Extra Owners can use this."))
    if ctx.guild:
        await log_command(ctx.author, 'vc remove', ctx.guild.id)
    bot_prefix = config.get('prefix', '!')
    if not channel:
        return await ctx.send(view=cv2(f"Usage: `{bot_prefix}vc remove #channel`"))

    configs = load_json(vc_config_path)
    new_configs = [c for c in configs if isinstance(c, dict) and c.get('channelId') != str(channel.id)]

    if len(configs) == len(new_configs):
        return await ctx.send(view=cv2("That channel is not currently being tracked."))

    save_json(vc_config_path, new_configs)

    try:
        headers = {"Authorization": f"Bot {bot.http.token}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/channels/{channel.id}/voice-status"
        if bot.session and not bot.session.closed:
            async with bot.session.put(url, headers=headers, json={"status": None}) as resp:
                pass
    except Exception as e:
        logger.error(f"Error clearing VC status on remove: {e}")

    await ctx.send(view=cv2(f"🗑️ Successfully removed tracking for <#{channel.id}>."))

@remove.error
async def remove_error(ctx, error):
    bot_prefix = config.get('prefix', '!')
    if isinstance(error, commands.ChannelNotFound):
        await ctx.send(view=cv2(f"❌ Channel not found. Please mention a valid voice channel (e.g. `{bot_prefix}vc remove #general`)."))

# SLASH COMMAND GROUPS
vc_group = app_commands.Group(name="vc", description="Manage voice channel status tracking")
extraowner_group = app_commands.Group(name="extraowner", description="Manage server extra owners")
premium_group = app_commands.Group(name="premium", description="Manage bot premium status for servers")

@vc_group.command(name="add", description="Configure voice channel status tracking template")
async def slash_vc_add(interaction: discord.Interaction, channel: discord.VoiceChannel, template: str):
    if not await is_mod(interaction):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Server Owner and Extra Owners can use this."), ephemeral=True)
    
    if interaction.guild:
        await log_command(interaction.user, 'slash vc add', interaction.guild.id)
    
    configs = load_json(vc_config_path)
    async with bot.premium_db.execute('SELECT 1 FROM premium_guilds WHERE guild_id = ?', (str(interaction.guild.id),)) as cursor:
        is_premium = bool(await cursor.fetchone())
    is_owner = str(interaction.user.id) == str(config.get('ownerId'))

    guild_configs = [c for c in configs if isinstance(c, dict) and c.get('guildId') == str(interaction.guild.id)]
    existing_idx = next((i for i, c in enumerate(configs) if isinstance(c, dict) and c.get('channelId') == str(channel.id)), -1)

    if not is_premium and not is_owner and existing_idx == -1 and len(guild_configs) >= 5:
        layout = discord.ui.LayoutView()
        container = discord.ui.Container(accent_color=discord.Colour(0xFFFFFF))
        container.add_item(discord.ui.TextDisplay("### 🔒 Premium Required\nYou have reached the limit of **5 tracked VCs** for free servers.\nPlease apply for premium to track unlimited channels.\n\n-# Made by Lord Sk"))
        container.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Get Premium", url="https://discord.gg/astria", style=discord.ButtonStyle.link)
        ))
        layout.add_item(container)
        return await interaction.response.send_message(view=layout, ephemeral=True)

    if existing_idx != -1:
        configs[existing_idx]['template'] = template
    else:
        configs.append({"guildId": str(interaction.guild.id), "channelId": str(channel.id), "template": template})

    save_json(vc_config_path, configs)
    await interaction.response.send_message(view=cv2(f"🎀 VC status configured for <#{channel.id}> and saved!"))
    await trigger_guild_update(str(interaction.guild.id), delay=1.0)

@vc_group.command(name="remove", description="Remove voice channel status tracking")
async def slash_vc_remove(interaction: discord.Interaction, channel: discord.VoiceChannel):
    if not await is_mod(interaction):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Server Owner and Extra Owners can use this."), ephemeral=True)
    if interaction.guild:
        await log_command(interaction.user, 'slash vc remove', interaction.guild.id)

    configs = load_json(vc_config_path)
    new_configs = [c for c in configs if isinstance(c, dict) and c.get('channelId') != str(channel.id)]

    if len(configs) == len(new_configs):
        return await interaction.response.send_message(view=cv2("That channel is not currently being tracked."), ephemeral=True)

    save_json(vc_config_path, new_configs)

    try:
        headers = {"Authorization": f"Bot {bot.http.token}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/channels/{channel.id}/voice-status"
        if bot.session and not bot.session.closed:
            async with bot.session.put(url, headers=headers, json={"status": None}) as resp:
                pass
    except Exception as e:
        logger.error(f"Error clearing VC status on slash remove: {e}")

    await interaction.response.send_message(view=cv2(f"🗑️ Successfully removed tracking for <#{channel.id}>."))

@extraowner_group.command(name="add", description="Add an extra owner for this server")
async def slash_extraowner_add(interaction: discord.Interaction, user: discord.User):
    if not await is_guild_or_bot_owner(interaction):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."), ephemeral=True)
    
    async with bot.anti_db.execute('SELECT 1 FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(interaction.guild.id), str(user.id))) as cursor:
        if await cursor.fetchone():
            return await interaction.response.send_message(view=cv2(f"⚠️ {user.mention} is already an extra owner."), ephemeral=True)
        
    await bot.anti_db.execute('INSERT INTO extra_owners (guild_id, user_id) VALUES (?, ?)', (str(interaction.guild.id), str(user.id)))
    await bot.anti_db.commit()
    await interaction.response.send_message(view=cv2(f"✅ Added {user.mention} as an extra owner for this server."))

@extraowner_group.command(name="remove", description="Remove an extra owner for this server")
async def slash_extraowner_remove(interaction: discord.Interaction, user: discord.User):
    if not await is_guild_or_bot_owner(interaction):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."), ephemeral=True)
        
    async with bot.anti_db.execute('SELECT 1 FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(interaction.guild.id), str(user.id))) as cursor:
        if not await cursor.fetchone():
            return await interaction.response.send_message(view=cv2(f"⚠️ {user.mention} is not an extra owner."), ephemeral=True)
        
    await bot.anti_db.execute('DELETE FROM extra_owners WHERE guild_id = ? AND user_id = ?', (str(interaction.guild.id), str(user.id)))
    await bot.anti_db.commit()
    await interaction.response.send_message(view=cv2(f"✅ Removed {user.mention} as an extra owner for this server."))

@extraowner_group.command(name="list", description="List extra owners for this server")
async def slash_extraowner_list(interaction: discord.Interaction):
    if not await is_guild_or_bot_owner(interaction):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Server Owner or Bot Owner can use this."), ephemeral=True)
        
    async with bot.anti_db.execute('SELECT user_id FROM extra_owners WHERE guild_id = ?', (str(interaction.guild.id),)) as cursor:
        owners = await cursor.fetchall()
    
    if not owners:
        return await interaction.response.send_message(view=cv2("ℹ️ There are no extra owners for this server."))
        
    owner_mentions = [f"<@{row[0]}>" for row in owners]
    owners_list = "\n".join(owner_mentions)
    await interaction.response.send_message(view=cv2(f"👑 **Extra Owners for this server:**\n{owners_list}"))

@premium_group.command(name="add", description="Add premium to a server (Bot Owner Only)")
async def slash_premium_add(interaction: discord.Interaction, guild_id: str, days: str = None):
    if str(interaction.user.id) != str(config.get('ownerId')):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Bot Owner can use this command."), ephemeral=True)

    expires_at = None
    days_float = None
    if days:
        try:
            days_float = float(days)
            if days_float <= 0:
                return await interaction.response.send_message(view=cv2("❌ Error: Days must be a positive number greater than 0."), ephemeral=True)
            expires_at = int(time.time() + (days_float * 86400))
        except ValueError:
            return await interaction.response.send_message(view=cv2("❌ Error: Days must be a valid number."), ephemeral=True)
    
    async with bot.premium_db.execute('SELECT 1 FROM premium_guilds WHERE guild_id = ?', (guild_id,)) as cursor:
        exists = await cursor.fetchone()
    
    if exists:
        await bot.premium_db.execute('UPDATE premium_guilds SET expires_at = ? WHERE guild_id = ?', (expires_at, guild_id))
    else:
        await bot.premium_db.execute('INSERT INTO premium_guilds (guild_id, expires_at) VALUES (?, ?)', (guild_id, expires_at))
        
    await bot.premium_db.commit()
    time_str = f"for {days_float} days" if days_float is not None else "for Lifetime"
    await interaction.response.send_message(view=cv2(f"✅ Added premium to guild {guild_id} {time_str}"))

@premium_group.command(name="remove", description="Remove premium from a server (Bot Owner Only)")
async def slash_premium_remove(interaction: discord.Interaction, guild_id: str):
    if str(interaction.user.id) != str(config.get('ownerId')):
        return await interaction.response.send_message(view=cv2("❌ Access Denied: Only the Bot Owner can use this command."), ephemeral=True)

    await bot.premium_db.execute('DELETE FROM premium_guilds WHERE guild_id = ?', (guild_id,))
    await bot.premium_db.commit()
    await interaction.response.send_message(view=cv2(f"🗑️ Removed premium from guild {guild_id}"))

# Register Slash Command Groups
bot.tree.add_command(vc_group)
bot.tree.add_command(extraowner_group)
bot.tree.add_command(premium_group)

if __name__ == '__main__':
    bot.run(os.environ['TOKEN'])
