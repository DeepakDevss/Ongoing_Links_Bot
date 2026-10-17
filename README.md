# Ongoing Links

A Telegram community bot that gates access behind channel membership, welcomes new members, answers messages with AI, sends join requests to the owner for approval, and grows through a referral system. Runs on Render as a webhook service.

- Bot: https://t.me/ANIME_WORLD_8BOT
- Repository: https://github.com/DeepakDevss/Ongoing_Links_Bot

---

## Overview

Ongoing Links runs as a webhook-based Python service. It handles community management (force-join gate, owner approval of join requests, welcome messages, referral tracking) alongside an AI chat layer that replies to every private message and to group messages that @mention the bot.

The force-join check runs in both private and group chats, so adding the bot to a group doesn't get anyone past the gate. All settings live in one file (`config.py`), and all runtime data is kept in a local JSON database.

---

## Features

- **Force-join gate** — Users must join the configured channels before the bot will respond. Applies in private chats and in groups.
- **Owner approval of join requests** — Each incoming join request is sent to the owner with the requester's details and an Approve button. Nothing is auto-approved.
- **Welcome message** — New members get a photo caption welcome.
- **AI chat** — Every private message gets an AI reply. In groups, the bot replies only when @mentioned.
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
Ongoing_Links_Bot/
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
git clone https://github.com/DeepakDevss/Ongoing_Links_Bot.git
cd Ongoing_Links_Bot
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

Other settings you may want (all optional, defaults are set in `config.py`):

| Setting | Purpose |
|---|---|
| `OWNER_ID` / `OWNER_NAME` | Bot owner; skips the gate, full power |
| `ADMIN_IDS` / `ADMIN_NAMES` | Admin IDs and their display names, in the same order |
| `BOT_BRAND` | Display name used in messages (default `Ongoing`) |
| `RULES_TEXT` | Text shown by `/rules` |
| `NOTIFY_OWNER` | `True` = send DM notifications to the owner (default `False`) |
| `GATE_MESSAGE` / `GATE_BUTTON_TEXT` / `JOIN_PROMPT` | Gate text, button label, and the "join first" prompt |
| `AI_MODEL` / `AI_MODEL_LITE` / `AI_THINKING_LEVEL` | AI model IDs and thinking level |
| `AI_BUSY_MESSAGE` | Message shown when the AI is overloaded |
| `MEMBERSHIP_CACHE_TTL` | Seconds a membership result is cached |
| `WEBHOOK_SECRET` | Optional; otherwise a random secret is generated per boot |

Refer to `config.py` for the exact variable names, accepted values, and inline comments.

---

## Deploy on Render

1. Push the repository to GitHub.
2. Create a new Web Service on Render and connect the repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `python bot.py`.
5. Add the environment variables from your local `.env` in the Render dashboard.
6. The bot registers its own webhook from Render's service URL (`RENDER_EXTERNAL_URL`). Do **not** set `WEBHOOK_URL` yourself.
7. `data/db.json` lives on the local filesystem. Persistent disks need a paid Render plan; on the free tier the file is lost on every deploy.

`render.yaml` is included as an optional blueprint for the same setup.

---

## Bot Usage

## Roles

| Role | Who | Commands | Power |
|---|---|---|---|
| Owner | `OWNER_ID` | 27 | ALL permissions — every admin command plus the owner-only set. Skips the force-join gate. |
| Admin | `ADMIN_IDS` (labelled by `ADMIN_NAMES`) | 18 | User commands plus group moderation and posting. No diagnostics, no ID/config commands, no bot-wide actions. |
| Member | everyone else | 7 | User commands only. |

`/help` shows each role a flat list of only their own commands — member and admin lists
look complete, so neither can tell that a higher tier exists. The owner's list shows every
command, with `★` marking the owner-only ones.

`ADMIN_IDS[0]` is "Admin 1" — join requests and admin-promotion approvals go to Admin 1, never to the owner.

**Approvals.** Two kinds, both handled by Admin 1 (the owner can always override):

- **Join request** — someone asks to join a channel/group; Admin 1 gets the details and an Approve button.
- **Promotion** — an admin runs `/promote` in a group; Admin 1 gets an Approve button before the promotion happens. The owner's own `/promote` runs directly.

`/post` needs no approval.

---

## Commands

### Everyone

| Command | Description |
|---|---|
| `/start` | Entry point; runs the force-join check, then you can just type |
| `/help` | The commands available to your role |
| `/id` | Your Telegram user ID |
| `/ref` | Your referral link and invite count |
| `/rules` | The rules text (`RULES_TEXT`) |
| `/ai <question>` | Ask the AI directly |
| `/report` | Reply to a message to report it to the admins |

### Admin and Owner

| Command | Description |
|---|---|
| `/admins` / `/list_admins` | Team list — admins see only admins; the owner also sees the owner entry |
| `/post` | Build a channel post with a tracked deep link |
| `/posts` | List built posts, their code and link |

### Owner only

| Command | Description |
|---|---|
| `/stats` | Usage and growth statistics |
| `/getid` | Look up a chat ID from a forwarded message |
| `/setid` | Set a configured chat ID |
| `/diag` | Gate diagnostics, plus every chat the bot is in |
| `/chats` | Every chat the bot is in — ID, type, bot status, bound block |
| `/perms` | The bot's permissions in this chat, what each one means, and its command |
| `/broadcast` | Send a message to all known users |
| `/ban` / `/unban` | Bot-wide ban / unban by user ID |

An admin who sends an owner-only command gets *"Sirf owner ye kar sakta hai."*

### Group moderation — the bot must be an admin in that group

| Command | Needs permission | What it does |
|---|---|---|
| `/settitle <title>` | Change Group Info | Renames the group |
| `/invitelink` | Invite Users via Link | Creates an invite link |
| `/kick` / `/unkick` | Ban Users | Bans / unbans a member |
| `/del` | Delete Messages | Deletes the replied-to message |
| `/pin` | Pin Messages | Pins the replied-to message |
| `/promote` | Add New Admins | Promotes a member to admin (admin's request goes to Admin 1 for approval) |

Reply to a member's message, or pass a user ID as the argument.

---

## Post builder — `/post`

Owner and admins only. Builds a channel post whose button is a tracked deep link.

1. `/post` → pick a type (`POST_TYPES`: Anime / Movie / Series).
2. The bot asks for each field in `POST_FIELDS` (Name, Season, Quality, Language). Reply, or send `skip` to leave one out.
3. Send the channel — a public link (`https://t.me/ChannelName`) or the number of a chat the bot is an admin in.
4. Choose **Abhi post karo** (now) or **Schedule** (time as `YYYY-MM-DD HH:MM`, or just `HH:MM` — IST).

The bot posts to that channel with a **📥 Download** button. The button is a deep link —
`https://t.me/<bot>?start=p<code>` — so when a reader taps it, their chat with the bot opens
and the bot already knows which post and which channel they came from. It replies with a short
line plus a **🔔 Request to Join** button that takes them to that channel.

`/posts` lists every post with its code and link.

**Two limits worth knowing.** The bot can only post to a chat where it is an admin. And on a
free Render instance the service sleeps when idle, so a scheduled post may fire late — keep the
service awake (a cron ping to `/health`) or post immediately.

`POST_COMMAND` renames the command; `POST_BUTTON_TEXT` and `JOIN_BUTTON_TEXT` change the button
labels.

---

## Force-Join Gate

The gate is checked on every user interaction, in both private chats and groups.

- `LINK_BLOCKS` groups the required chats so they can be shown as buttons in the join prompt.
- `MIN_JOINS_REQUIRED` sets how many of the listed chats must be joined before access is granted.
- `JOIN_FAIL_MODE` defines what happens when the membership check can't be completed — for example, when the bot lacks permission to query membership or the Telegram API is unreachable. Two common modes are to fail open (allow access) or fail closed (deny access).
- `/diag` reports the current state of the gate so misconfiguration can be spotted quickly.
- `/getid` and `/setid` are used to discover and correct chat IDs used by the gate.

### How the bot learns a chat's ID

Telegram has no API to list the chats a bot is in, so the bot learns each chat's ID from events:

- **Added or re-promoted as admin** — the bot learns the ID instantly (`my_chat_member`). This is silent; nobody sees a message.
- **Any channel post** — the bot receives it and learns the chat.
- **Any group message** (text, photo, sticker, command) — the bot learns the chat.
- **Someone joins the group** — a service message the bot always receives.
- **A join request** — the bot learns the chat ID.

The bot does **not** receive its own outgoing messages, so it cannot post its way into a chat it has no ID for. To make the bot learn a chat it has been in since before this feature existed: send any message there, or re-promote the bot. `/chats` lists every chat the bot knows, with its live status.

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
