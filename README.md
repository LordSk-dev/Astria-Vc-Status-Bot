<div align="center">

  <img src="app.png" alt="Astria VC Status Bot Banner" width="100%" style="border-radius: 10px;" />

  # 🎙️ Astria Voice Status Bot

  **A high-performance, feature-rich Discord bot for dynamic Voice Channel status updates & real-time server statistics.**

  [![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
  [![Discord.py](https://img.shields.io/badge/discord.py-v2.x-5865F2.svg?style=for-the-badge&logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
  [![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](LICENSE)
  [![Maintenance](https://img.shields.io/badge/maintained%3F-yes-brightgreen.svg?style=for-the-badge)](https://github.com/LordSk-dev/Astria-Vc-Status-Bot)

</div>

---

## 🌟 Key Features

- ⚡ **Dynamic VC Status Tracking**: Live server stats directly displayed inside Discord Voice Channel Statuses.
- 🔄 **Real-Time Event Triggers**: Smart debounced updates triggered by voice state changes, member joins, and member leaves.
- 🛡️ **Rate-Limit Safe Architecture**: Built-in 120-second per-channel cooldowns and automatic `429` backoff retries compliant with Discord API guidelines.
- 👑 **Extra Owner Permission System**: Delegate administrative control to specific server members beyond server owner roles.
- 💎 **Premium & Free Tier Management**: Limit free servers to 5 tracked VCs while unlocking unlimited slots for premium guilds.
- 📦 **Automated Database Backups**: Hourly scheduled backups of database states sent directly to your private Discord backup channel.
- 🔔 **Webhook Command Audit Logging**: Real-time webhook notifications for executed commands.
- 🔀 **Hybrid Command Support**: Both traditional prefix commands (`!vc`, `!extraowner`) and modern Discord Slash Commands (`/vc`, `/extraowner`).

---

## 📸 Banner Preview

![Astria Preview](app.png)

---

## 📊 Available Placeholders

Use these dynamic tags when configuring your voice channel status templates:

| Placeholder | Description | Example Output |
| :--- | :--- | :--- |
| `{totalusers}` or `{total}` | Total members in the server | `1,250` |
| `{onlineusers}` | Total online (non-offline) members | `480` |
| `{activevc}` or `{active}` | Active voice/stage channels with participants | `5` |
| `{vcusers}` | Total members currently in voice or stage channels | `32` |
| `{vanityuses}` or `{invites}` | Total uses on the server vanity invite link | `1,042` |

---

## 📜 Command Reference

### 🎙️ Voice Status Commands (`!vc` / `/vc`)

| Command | Usage | Description | Access Level |
| :--- | :--- | :--- | :--- |
| `add` | `!vc add #channel <status>` | Sets status template for a voice channel | Server Owner / Extra Owner |
| `remove` | `!vc remove #channel` | Removes status tracking for a voice channel | Server Owner / Extra Owner |

### 👑 Extra Owner Commands (`!extraowner` / `/extraowner`)

| Command | Usage | Description | Access Level |
| :--- | :--- | :--- | :--- |
| `add` | `!extraowner add @user` | Adds an extra owner to manage VC bot settings | Server Owner / Bot Owner |
| `remove` | `!extraowner remove @user` | Removes an extra owner | Server Owner / Bot Owner |
| `list` | `!extraowner list` | Lists all active extra owners in the server | Server Owner / Bot Owner |

### 💎 Premium Commands (`!premium` / `/premium`)

| Command | Usage | Description | Access Level |
| :--- | :--- | :--- | :--- |
| `add` | `!premium add <guild_id> [days]` | Grants premium access for specified days or lifetime | Bot Owner Only |
| `remove` | `!premium remove <guild_id>` | Revokes premium status from a server | Bot Owner Only |

---

## 📂 Repository Structure

```tree
Astria-Vc-Status-Bot/
├── data/
│   ├── anti.db           # SQLite database for Extra Owners permissions
│   ├── premium.db        # SQLite database for Premium Guilds
│   ├── config.json       # Core bot configurations
│   └── vcstatus.json     # Saved channel status templates
├── app.png               # Banner & README graphic asset
├── bot.py                # Main bot application entry point
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variables template
├── .gitignore            # Git exclusion definitions
└── README.md             # Project documentation
```

---

## 🚀 Quick Setup & Installation

### 1. Prerequisites
- Python `3.8` or higher installed.
- Git installed.

### 2. Clone Repository
```bash
git clone https://github.com/LordSk-dev/Astria-Vc-Status-Bot.git
cd Astria-Vc-Status-Bot
```

### 3. Set Up Virtual Environment & Dependencies
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your Discord Bot Token:
```bash
cp .env.example .env
```
Inside `.env`:
```env
TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
```

### 5. Configure `data/config.json`
Open `data/config.json` to configure owner and logging settings:
```json
{
  "cmdLogWebhook": "YOUR_DISCORD_WEBHOOK_URL_HERE",
  "premiumApplyChannelId": "YOUR_PREMIUM_CHANNEL_ID",
  "backupChannelId": "YOUR_BACKUP_CHANNEL_ID",
  "ownerId": "YOUR_DISCORD_USER_ID",
  "prefix": "!"
}
```

### 6. Run the Bot
```bash
python bot.py
```

---

## 🛠️ Configuration Details

- **`ownerId`**: Discord User ID of the primary bot owner (has master override permissions).
- **`backupChannelId`**: Discord Text Channel ID where hourly `.db` and `.json` backups will be posted.
- **`cmdLogWebhook`**: Webhook URL for auditing command usage.
- **`prefix`**: Default prefix for non-slash commands (e.g., `!`).

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!  
Feel free to check [issues page](https://github.com/LordSk-dev/Astria-Vc-Status-Bot/issues).

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

<div align="center">
  <sub>Made with ❤️ by <a href="https://github.com/LordSk-dev">Lord Sk</a></sub>
</div>
