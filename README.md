# Linnet Bot

A Telegram community bot: force-join gate, welcome messages, AI chat, and a
referral system. Deploy-ready on Render via webhook.

This repository is source code only. It contains no secrets, no keys, and no
runtime data.

---

## What it does

1. **Force-join gate** — before a user can use the bot, they must join the
   channels/groups listed in `config.py` (`LINK_BLOCKS`). Nothing is enforced
   until you fill in the chat IDs (see below).
2. **Auto-approve join requests** — join requests are approved automatically.
3. **Welcome message** — a photo plus a caption is posted when a new member joins.
4. **AI chat** — in private chat on any message; in groups only when the bot is
   `@mentioned` or replied to. Membership is checked in groups too, so the gate
   cannot be bypassed from a group.
5. **Referral / invite growth** — every user gets their own link and invite count.

**Stack:** Python 3.12, python-telegram-bot v22, FastAPI (webhook), Render.

---

## Files

| File | Purpose | Goes to GitHub? |
|---|---|---|
| `config.py` | All settings — the only file you edit | Yes |
| `bot.py` | The bot (database, AI, handlers, server) | Yes |
| `fonts.py` | Unicode text styling (small-caps / bold) | Yes |
| `requirements.txt` | Python packages | Yes |
| `render.yaml` | Render blueprint (optional) | Yes |
| `env.example.txt` | Environment variable template (no secrets) | Yes |
| `gitignore.txt` | Rename to `.gitignore` before pushing | Yes |
| `README.md` | This file | Yes |
| `welcome.png` | Welcome image | Yes |
| `.env` | Your real secrets | **No** (ignored) |
| `data/db.json` | Runtime data | **No** (ignored) |

> Rename `env.example.txt` to `.env.example` and `gitignore.txt` to `.gitignore`
> before pushing (dotfiles are hidden in Windows Explorer / macOS Finder —
> enable "Show hidden items" / press `Cmd + Shift + .`).

---

## Step 1 — Edit `config.py`

| Setting | What to put | Where |
|---|---|---|
| `BOT_TOKEN` | Token from BotFather (`/newbot`) | `#1` |
| `BOT_USERNAME` | e.g. `@LinnetBot` | `#1b` |
| `BOT_BRAND` | Public name shown to users (default `Linnet`) | `#1c` |
| `AI_API_KEY` | Your AI provider API key | `#2` |
| `AI_MODEL` / `AI_MODEL_LITE` | Model IDs (defaults are set) | `#2b` |
| `ADMIN_IDS` | Your Telegram user ID (send `/id` to the bot) | `#3` |
| `LINK_BLOCKS` | Buttons + which chats are force-join (same `row` = side by side) | `#4` |
| `JOIN_FAIL_MODE` | `closed` (recommended) or `open` | `#5` |
| `WELCOME_TEMPLATE` / `WELCOME_IMAGE` | Welcome text (blank = built-in styled text) and image | `#6` |

### Force-join needs numeric chat IDs

A Telegram **invite link** (`t.me/+xxxx`) cannot be used to verify membership.
To enforce a join you need the chat's **numeric ID**:

1. Make the bot an **admin** in that channel/group.
2. Forward any message from that chat to the bot and send `/getid`.
3. Put the returned ID in that block's `"id"` field in `LINK_BLOCKS`.

Public chats also accept `"@username"`. A block marked `"require_join": True`
with an empty `"id"` will only show as a button — it will **not** be enforced,
and the bot logs a warning about it on startup.

> **`JOIN_FAIL_MODE`:** with `closed`, if the bot cannot verify membership
> (not admin, or wrong ID), the user is **not** let through — the gate holds.
> With `open`, the user is let through — the gate is effectively bypassable.
> Use `closed` in production.

---

## Step 2 — Push to GitHub

Push with the command line, not the website "Upload files" button — the website
upload ignores `.gitignore` and can publish your `.env`.

```bash
git init
git add -A
git commit -m "Linnet bot"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

Verify with `git status` — `.gitignore` and `render.yaml` should appear.

If the push is rejected because the remote already has commits:

```bash
git pull origin main --allow-unrelated-histories
git push origin main
```

---

## Step 3 — Local test (optional)

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your values
python bot.py             # polling mode when WEBHOOK_URL is empty
```

---

## Step 4 — Deploy on Render

1. Render → **New +** → **Web Service** → connect the GitHub repo.
2. Settings:

| Field | Value |
|---|---|
| Runtime | `Python 3` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python bot.py` |
| Instance Type | `Free` |
| Health Check Path | `/health` |

3. **Environment Variables** (Dashboard → Environment):

| Key | Value | Required |
|---|---|---|
| `BOT_TOKEN` | BotFather token | Yes |
| `BOT_USERNAME` | `@YourBot` | Yes |
| `AI_API_KEY` | Your AI provider key | Yes |
| `ADMIN_IDS` | Your user ID | Yes |
| `AI_MODEL` | model ID | Optional |
| `AI_MODEL_LITE` | model ID | Optional |
| `AUTO_APPROVE_JOIN_REQUESTS` | `True` | Optional |
| `PYTHON_VERSION` | `3.12.13` | Optional |

Do **not** set `WEBHOOK_URL` or `PORT` — Render provides them.

4. Create the service. The bot registers its webhook automatically.

**Keep the free tier awake:** a free web service sleeps after ~15 min idle. Ping
it every 5–10 min with UptimeRobot or cron-job.org, using the **`/health`** URL
(`https://your-app.onrender.com/health`). The root URL returns 403/405 and shows
as "Down".

> `render.yaml` is only used by Render's **Blueprint** deploy. With a normal Web
> Service it is ignored and you type the Build/Start commands yourself.

---

## Bot usage

- **Private:** `/start` → join gate → "Check" → menu → type your question.
- **Group:** new member → welcome photo + caption. AI replies to `@mentions`.
- **Referral:** `/ref` → your link → share.
- **Admin:** `/stats`, `/broadcast <msg>`, `/ban <id>`, `/unban <id>`, `/getid`.

---

## Notes and limits

- **Rate limits:** 1 message/second per chat, ~20/minute per group,
  `getChatMember` ~30/second. The bot includes `AIORateLimiter`, a membership
  cache (60s), and broadcast jitter. A `429` response carries `retry_after` —
  respect it.
- **No auto-add:** there is no Bot API method to add members to a group. Any
  tool that claims to "auto-add members" logs in as a user account and spams —
  that gets the account banned. This bot only uses invite links.
- **Broadcast** only reaches users who have started the bot (opt-in). Cold DMs
  are not possible via the Bot API.
- **Data:** on Render's free tier, `data/db.json` can reset on restart/redeploy.
  For scale, move to Postgres/MongoDB.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `.gitignore` / `render.yaml` missing on GitHub | Hidden dotfiles — push with `git add -A`, not the website upload |
| `.env` got uploaded | Delete it and **rotate the token/key immediately** |
| `Updates were rejected...` | `git pull origin main --allow-unrelated-histories`, then push |
| `Conflict: terminated by other getUpdates` | More than one instance running — keep one service on Render |
| Gate always shows "join" | Bot is not admin in that chat, or the ID is wrong — check with `/getid` |
| AI not replying | `AI_API_KEY` invalid/expired or model ID wrong — check logs |
| Welcome photo missing | `welcome.png` missing — check the path in `config.py` |
| Bot sleeps | Set up the keep-alive ping (use the `/health` URL) |
