# Ongoing Links

A Telegram community bot that gates access behind channel membership, welcomes new members, answers messages with AI, auto-approves join requests, and grows through a referral system. Runs on Render as a webhook service.

- Bot: https://t.me/ANIME_WORLD_8BOT
- Repository: https://github.com/DeepakDevss/Telegram_Invited_AIBot

---

## Overview

Ongoing Links runs as a webhook-based Python service. It handles community management (force-join gate, auto-approval of join requests, welcome messages, referral tracking) alongside an AI chat layer that replies to every private message and to group messages that mention the bot or reply to it.

The force-join check runs in both private and group chats, so adding the bot to a group doesn't get anyone past the gate. All settings live in one file (`config.py`), and all runtime data is kept in a local JSON database.

---

## Features

- **Force-join gate** — Users must join the configured channels before the bot will respond. Applies in private chats and in groups.
- **Auto-approval of join requests** — Incoming join requests to the target chat are approved automatically.
- **Welcome message** — New members get a photo caption welcome.
- **AI chat** — Every private message gets an AI reply. In groups, the bot replies only when mentioned or replied to.
- **Referral system** — Each user gets a personal invite link. Invite counts are tracked and shown through `/ref`.
- **Admin controls** — Broadcast, ban, unban, member lookup, ID management, and diagnostics for configured admins.
- **Webhook deployment** — Runs behind FastAPI on Render. No polling loop.

---

## Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Bot framework | python-telegram-bot v22 |
| Web framework | FastAPI (webhook server) |
| Deployment | Render |
| Storage | Local JSON database (`data/db.json`) |

---

## Requirements

- Python 3.12 or newer
- A Telegram bot token from BotFather
- An API key for the AI provider used by the bot
- The numeric Telegram user ID of the bot administrator
- One or more channel or group IDs to enforce the force-join gate against

---

## Project Structure

```
Telegram_Invited_AIBot/
├── bot.py               Bot logic, database, AI, handlers, webhook server
├── config.py            All settings — the only file that must be edited
├── fonts.py             Unicode text styling helpers
├── requirements.txt     Python dependencies
├── render.yaml          Render blueprint (optional)
├── env.example.txt      Environment variable template (no secrets)
├── gitignore.txt        Rename to .gitignore before pushing
├── welcome.png          Welcome image
└── README.md
```

Runtime files, created automatically and not committed:

```
.env                Real credentials (never commit)
data/db.json        Persistent user, chat, and referral data
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/DeepakDevss/Telegram_Invited_AIBot.git
cd Telegram_Invited_AIBot
```

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

Copy the environment template and fill in the values:

```bash
cp env.example.txt .env
```

Then open `config.py` and set every value listed under Configuration below. For a standard deployment, `config.py` is the only file you need to edit.

### 4. Prepare the repository for GitHub

Rename `gitignore.txt` to `.gitignore` before pushing. This keeps `.env` and `data/db.json` out of your commits.

```bash
git mv gitignore.txt .gitignore
```

### 5. Run locally (optional)

```bash
python bot.py
```

---

## Configuration

All settings live in `config.py`. The important ones are grouped below.

### Credentials

| Setting | Purpose |
|---|---|
| Bot token | Telegram bot token issued by BotFather |
| AI API key | Credentials for the AI provider |
| Admin user ID | Numeric Telegram user ID of the primary administrator |

### Force-join gate

| Setting | Purpose |
|---|---|
| Channel or group IDs | The chats a user must join before using the bot |
| MIN_JOINS_REQUIRED | How many of those chats must be joined |
| LINK_BLOCKS | Groupings used to organise the join buttons |
| JOIN_FAIL_MODE | Behaviour when the join check cannot be completed |

### Welcome and referrals

| Setting | Purpose |
|---|---|
| Welcome image and caption | Message sent to new members |
| Referral reward rules | Conditions under which invite counts increase |

Refer to `config.py` for the exact variable names, accepted values, and inline comments.

---

## Deploy on Render

1. Push the repository to GitHub.
2. Create a new Web Service on Render and connect the repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `python bot.py`.
5. Add the environment variables from your local `.env` in the Render dashboard.
6. Set the public URL of the service as the webhook URL used by the bot.
7. Attach a persistent disk and point the database path at it. Without a persistent disk, `data/db.json` is lost on every deploy.

`render.yaml` is included as an optional blueprint for the same setup.

---

## Bot Usage

### Private chat

| Command | Description |
|---|---|
| `/start` | Entry point; runs the force-join check and opens the main menu |
| `/ref` | Returns the user's personal invite link and invite count |

### Admin

| Command | Description |
|---|---|
| `/stats` | Usage and growth statistics |
| `/broadcast` | Send a message to all known users |
| `/ban` | Ban a user |
| `/unban` | Remove a ban |
| `/getid` | Look up a user or chat ID |
| `/setid` | Set a configured chat ID |
| `/diag` | Diagnostics for the force-join gate and webhook status |

Admin commands are restricted to the configured administrator IDs.

---

## Force-Join Gate

The gate is checked on every user interaction, in both private chats and groups.

- `LINK_BLOCKS` groups the required chats so they can be shown as buttons in the join prompt.
- `MIN_JOINS_REQUIRED` sets how many of the listed chats must be joined before access is granted.
- `JOIN_FAIL_MODE` defines what happens when the membership check can't be completed — for example, when the bot lacks permission to query membership or the Telegram API is unreachable. Two common modes are to fail open (allow access) or fail closed (deny access).
- `/diag` reports the current state of the gate so misconfiguration can be spotted quickly.
- `/getid` and `/setid` are used to discover and correct chat IDs used by the gate.

Adding the bot to a group does not bypass the gate. Group members are subject to the same check as private users.

---

## Notes and Limits

- **No auto-add.** The bot does not add users to any chat. Users join through the links configured in `LINK_BLOCKS`.
- **Broadcast is opt-in for recipients.** Only users known to the bot receive broadcasts. Unknown users are never messaged.
- **Data is local.** All state lives in `data/db.json`. The file is created on first run and is not part of the repository.
- **Persistent storage is required in production.** On ephemeral filesystems, attach a persistent disk and point the database path at it.
- **The webhook URL must be public.** The bot will not function correctly behind a URL that Telegram cannot reach.

---

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| Bot does not respond at all | Webhook URL not set or unreachable | Set the Render service URL as the webhook and verify it is public |
| Users pass the gate without joining | Bot lacks permission to check membership | Grant the bot the required permissions in each configured chat, then run `/diag` |
| Users are always blocked | `MIN_JOINS_REQUIRED` higher than the number of configured chats | Lower the value or add the missing chat IDs |
| Join buttons show wrong chats | `LINK_BLOCKS` or chat IDs are incorrect | Re-check IDs with `/getid` and update `config.py` |
| Data resets after deploy | Ephemeral filesystem | Attach a persistent disk and set the database path to it |
| Broadcast does not reach users | No known users yet, or the bot was blocked by recipients | Confirm recipient count with `/stats` |

---

## Related projects

- **Linnet | AI Companion** — A Telegram AI bot with persistent memory, media understanding, and voice replies.
  Repository: https://github.com/DeepakDevss/Linnet_AI_Telegram
  Bot: https://t.me/Linnet_Agents_Bot

---

## Author

Deepak Devs

- GitHub: https://github.com/DeepakDevss
- X: https://x.com/Deepak_Devs
- Reddit: https://www.reddit.com/u/Deepak_Devs

---

## License

There's no license file yet. Add one before you distribute or publicly modify this project.
