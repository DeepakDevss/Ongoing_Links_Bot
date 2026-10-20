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

- **Force-join gate, only on post links.** Chatting with the bot needs **no** joins at all. The gate appears only when someone opens the bot through a `/post` deep link — they must join (or request) the channels before the post's real link is handed over.
- **Sending a join request is enough.** The bot records `chat_join_request` events, so a user who has *requested* to join counts as joined, even before an admin approves.
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
| `POST_CHANNELS` / `POST_ASK_IMAGE` | Channel buttons for `/post`, and whether to ask for a banner |
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
| `/admins` | Team list — admins see only admins; the owner also sees the owner entry |
| `/post` | Build a channel post with a tracked deep link |
| `/posts` | List built posts, their code and link |

### Owner only

| Command | Description |
|---|---|
| `/stats` | Full overview: users who started the bot, reachable, banned, `/start` count, referrals — **plus each channel's live member count** (from `getChatMemberCount`) and the total across all channels, and every chat the bot is in grouped by type |
| `/getid` | Look up a chat ID from a forwarded message |
| `/setid` | Set a configured chat ID |
| `/diag` | Gate diagnostics, plus every chat the bot is in |
| `/chats` | Every chat the bot is in — ID, type, bot status, bound block |
| `/perms` | The bot's permissions in this chat, what each one means, and its command |
| `/broadcast` | Send a message to all known users. With no message it prints a summary first: total users, reachable by DM, banned, and every chat the bot is in (grouped by type) |
| `/ban` / `/unban` | Bot-wide ban / unban by user ID |

An admin who sends an owner-only command gets *"Only the owner can do this."*

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
2. **Pick the channels** — the buttons come from `POST_CHANNELS`. Tap to toggle (`✅`), tap as many as you want, then **➡️ Done**. The post goes to every channel you selected.
3. **Send the banner image.** The bot reads it with AI vision and shows what it read — as a hint only, nothing is filled in for you.
4. The bot then asks for each field **for that type** — see `POST_FIELDS_BY_TYPE`. Anime and Series ask Name, Season, Episodes, Quality, Language, Link; a Movie asks Name, Year, Quality, Language, Link (no Season or Episodes). Reply to each, or send `skip` to leave one out. `Link` is the join destination (see below) — it is not printed in the post.
5. Choose **Post now** or **Schedule** (time as `YYYY-MM-DD HH:MM`, or just `HH:MM` — IST).

The post goes out as a **photo** (your banner) with a styled caption and a **Download & Watch** button. The button is a deep link — `https://t.me/<bot>?start=p<code>` — so when a reader taps it,
their chat with the bot opens and the bot already knows which post it is.

The caption's detail lines are built from the type's own fields — drop `Name` and `Link` and the rest
become the `►` lines, so a Movie shows `►Year` and an Anime shows `►Season`/`►Episodes`. The episode
line is only generated for types that have a Season; a Movie gets `NEW MOVIE ADDED` instead.

The caption looks like this (sent with `parse_mode=HTML` so the credit and the download line are
real inline links):

```
▌❝ Attack On Titan ❞          <- <blockquote>

▌►Season :- 03                <- <blockquote> (the quote bar)
▌►Episodes :- 22
▌►Quality :- 480p, 720p, 1080p, 4K [ DUAL ]
▌►Language :- Hindi Dub [ #OFFICIAL ]

▌[ Creadit - Ongoing ]        <- <blockquote>; "Ongoing" links to POST_CREDIT_URL

━━━━━━━━━━━━━━━━━
   🫧 S03 EPISODE 01 ADDED 🫧  <- AI (POST_EP_LINE); emoji from POST_EP_EMOJI
━━━━━━━━━━━━━━━━━
   ★ WATCH & DOWNLOAD ★
━━━━━━━━━━━━━━━━━
```

(`▌` is the quote bar Telegram draws for `<blockquote>`.)

The episode line, the `WATCH & DOWNLOAD` line and the button **all point to the same bot deep
link**. Tapping any of them opens the bot chat and `/start` runs automatically, and the bot then
hands over the link — exactly like the button.

The `Link` field is **not printed as text**. It is the destination of the **Request to Join** button: the reader taps the post (or the button), the bot greets them, and that button opens the
link you gave.

The greeting is sent **immediately** — no AI wait — so the button appears the instant the deep link
opens. The AI-written welcome line is fetched in the background and swaps in a moment later. That
line is deliberately constrained: plain and respectful, no hype, no exclamation marks, no emoji, and
no cheesy filler like "sit back and enjoy" — it only welcomes the reader and points at the button
below. If you leave `Link` empty, the bot falls back to invite links for the channels you
selected.

There is no Genre line. The episode line is written by the AI from the name and season, with a
single episode number (never a range); set `POST_EP_LINE=False` to build it from the season alone.
Note the AI may guess the episode number — check the preview before posting.

`/posts` lists every post with its code and link.

**Links never depend on message text.** Every real link the bot hands out lives in an inline-keyboard
button, and buttons are not message text — so anti-spam bots, link filters and preview settings cannot
touch them. The caption's text links are decoration only: if a channel rejects the caption because of
them, the bot automatically re-sends the same post with those text links removed and the button intact.

**Three things worth knowing.** The bot can only post to a chat where it is an admin. Vision needs a
working AI key — if it fails, the bot just skips pre-filling and asks you normally. And on a free
Render instance the service sleeps when idle, so a scheduled post may fire late — keep the service
awake (a cron ping to `/health`) or post immediately.

`POST_COMMAND` renames the command; `POST_CHANNELS` sets the channel buttons; `POST_FIELDS` is the
fallback question list and `POST_FIELDS_BY_TYPE` sets the questions per type; `POST_BUTTON_TEXT` (default `Download & Watch`) and `JOIN_BUTTON_TEXT` (default
`Request to Join`) change the button labels; `POST_ASK_IMAGE` turns the banner step off.

### Button labels carry no emoji

Buttons are text only. The channel emojis still live in `LINK_BLOCKS` for reference but are not drawn
on the buttons, and the post channel picker marks a chosen channel with `√` rather than a coloured
emoji. If you had `POST_BUTTON_TEXT` or `JOIN_BUTTON_TEXT` set in your environment with an emoji,
delete that variable to pick up the new default.

---

## Reliability — how Telegram errors are handled

Every outgoing call that matters goes through one wrapper, `_safe()`, so a single Telegram error can
never kill a handler or leave a user with silence:

| Error | What it means | What the bot does |
| --- | --- | --- |
| `RetryAfter` (429) | Flood control | Waits `retry_after` seconds (capped at 60) and retries once |
| `Forbidden` (403) | User blocked the bot, account deleted, or the bot was kicked | Drops the send, logs it, moves on — retrying is pointless |
| `ChatMigrated` | A group became a supergroup, so the chat ID changed | Rewrites the stored chat ID for that block automatically |
| `TimedOut` | Network was slow | One retry |
| `BadRequest` | Malformed request | Logs the reason, no retry |
| `Conflict` (409) | Two bot instances polling at once | Logged as an error — a deploy usually leaves one behind |

`/broadcast` distinguishes the three outcomes — **sent**, **blocked the bot**, and **failed** — and
keeps a jittered delay between sends so it stays under Telegram's ~1 msg/sec per chat limit.

## Performance

The JSON store is read through an in-memory cache, so the whole file is parsed **once** per process
instead of on every call. Measured with 5,000 users:

| Operation | Before | After |
| --- | --- | --- |
| `_db_load()` | 20,123 µs | **1.2 µs** |
| `db_add_user()` (repeat user) | 101,744 µs | **1.0 µs** |
| `db_is_banned()` | 23 µs | **0.4 µs** |
| `has_request()` | 21.7 µs | **0.6 µs** |

The only remaining blocking write is a **new** user's first insert (~77 ms — a full JSON rewrite plus
`fsync`), which happens once per user and then not again for five minutes.

## Force-Join Gate

**The gate is not used for chatting.** Plain DMs, `/start` and `/ai` all work with zero joins. Plain
`/start` shows the five channel buttons **plus the same *Joined / Verify* button**, so anyone can check
their membership at any time — but nothing is withheld for chatting. The gate *runs* in exactly one
place: when someone arrives through a **`/post` deep link**.

That flow is:

1. The post carries a button whose URL is `https://t.me/<bot>?start=p<code>`.
2. Tapping it opens the bot and `/start` runs automatically.
3. The **gate** appears — the channel buttons plus **Joined / Verify**.
4. Tapping *Joined / Verify* checks the channels. If the user has not joined or requested them, the
   bot sends the join prompt again (same buttons, same verify button).
5. Once verified, the bot finally shows the post's **Request to Join** button, which opens the link.

A user passes if they are a member **or** have a join request on record (`chat_join_request`) — the
Bot API has no way to list pending requests, so the bot remembers them itself. That record is kept
honest in two ways:

- **Leaving clears it.** The bot listens for `chat_member` updates. The moment someone leaves or is
  kicked, their request record is dropped and the membership cache is invalidated — so joining and
  then leaving no longer passes the gate.
- **It expires after 24 hours.** A request older than `_REQUEST_TTL` stops counting, and only real
  membership will do after that.

`MIN_JOINS_REQUIRED` defaults to `all`, so **every** block with `require_join: true` must be
satisfied — currently the four anime channels **and the AI link** (five in total).

### Blocks the bot cannot check: `"verify": "tap"`

The AI link points at **another bot's chat**, not a channel. Telegram gives no way for one bot to learn
whether a user has started another bot — `getChatMember` does not work there at all. So that block is
marked `"verify": "tap"`: its button becomes a callback button, and tapping it records the block as
done and then hands over the link. Marking it `require_join: true` with a normal membership check would
have made the gate impossible to pass.

Everything else uses the default `"verify": "member"`, which means a real `getChatMember` check (or a
join request on record). It counts **every** `require_join` block in `LINK_BLOCKS` — not just the ones the
bot happens to be able to verify, which is what it used to do (so a channel with no ID was silently
excused and "all" meant fewer channels than configured).

### Making the gate actually enforce

The gate can only check a channel it has **both** of these for:

1. the channel's **numeric chat id**, and
2. the bot as an **admin** in that channel.

The Bot API cannot resolve a private invite link (`t.me/+hash`) to a chat id — `checkChatInvite` is a
client-API method, not a bot one. So a `require_join` block with `id: None` and no learned id **cannot
be checked at all**, and the gate becomes decorative.

The bot tells you this rather than pretending:

- **Startup log** — `GATE NOT ENFORCING — no chat id for: …`
- **`/diag`** — prints `gate : READY` or `NOT READY` at the very top, with the count of channels it
  can check.
- **`/chats`** — prints a ready-to-paste `/setid <block> <chat_id>` line for every block that has no
  id yet.
- **Owner DM** — when a user hits a gate that cannot check anything, the owner is told once, with the
  fix.
- **The user** is told plainly that the check is not set up yet and is let through, so a
  half-configured gate never locks real readers out of a post.

The easiest fix is to **add the bot as an admin in each channel** — it learns the id the moment it is
added. If the channel title does not match a block name, bind it by hand with `/setid` (get the id
from `/getid` by forwarding a message, or copy it from `/chats`).

The gate is **strict and fails closed**:

- *Request to Join* is handed over **only** when membership has actually been confirmed — joined **or**
  a join request on record.
- **Fail-closed.** If the bot cannot check at all — no chat id, **or it has an id but is not an admin
  there**, so `getChatMember` errors — it does **not** let anyone through. It says it could not confirm
  membership, keeps the gate and its *Joined / Verify* button on screen, and tells the owner. This was
  a real bug: with ids present but no admin rights every check came back "unknown", the required count
  collapsed to zero, and the gate silently passed people who had joined nothing.
- The deep-link check ignores the membership cache, so a stale result can never let someone in.
- **The keyboard shows progress, in place.** Each *Joined / Verify* tap edits the very message the
  button sits on — a channel that is joined **or has a join request on record** gets a `✓` and turns
  green, the rest stay blue. So the member sees at a glance what is done and what is still missing,
  without hunting for a new message.
- **A join request is counted without asking Telegram.** `chat_join_request` is the bot's own record,
  so it is trusted first — the channel is never queried for it. This matters: if the bot is not an
  admin it cannot call `getChatMember` at all, and before this the request was ignored and the `✓`
  never appeared.
- **The gate message is deleted once you pass.** Both the message the button was on and the earlier
  *5 links + Verify* message are removed (Telegram plays its own fade), and a short *Verified* line is
  sent instead — nothing stale is left behind.
- **The owner and admins never see the gate.** `/start` shows them the channel links for reference but
  no *Joined / Verify* button, and a `/post` deep link takes them straight to the download button.

- `LINK_BLOCKS` groups the required chats so they can be shown as buttons in the join prompt. Those buttons are sent with `style="primary"` (blue) and the verify button with `style="success"` (green) — button colours need Telegram clients from after 9 Feb 2026; older clients ignore the style.
- The verify button reads `GATE_BUTTON_TEXT` (default `Joined / Verify`).
- Colours are sent through `api_kwargs` (`_btn()` in `bot.py`), not PTB's `style=` parameter — that parameter only exists from PTB 22.7, and on older versions the colour used to disappear silently. The `api_kwargs` route works on any PTB version.
- **Every** button in the bot is coloured: the channel buttons, *Joined / Verify*, *Request to Join*,
  *Download & Watch*, the post builder's type/channel/Done/Post now/Schedule buttons, the *Share*
  button on `/ref`, and the Approve buttons.
- Every keyboard is built through `_btn()`, so a button without a colour cannot be introduced by
  accident. `/btnstyle` (owner only, kept out of the command list) prints the exact JSON the bot
  sends, so the payload can be checked directly.
- `style` needs **no** Telegram Premium and works in private chats, groups and supergroups alike —
  the only requirement is a client released after 9 Feb 2026.
- The startup log prints the real button payload, so you can see exactly what is being sent.
- `MIN_JOINS_REQUIRED` sets how many of the listed chats must be joined or requested (`all` by default).
- `JOIN_FAIL_MODE` defines what happens when the membership check can't be completed — for example, when the bot lacks permission to query membership or the Telegram API is unreachable. Two common modes are to fail open (allow access) or fail closed (deny access).
- `/diag` reports the current state of the gate so misconfiguration can be spotted quickly.
- `/getid` and `/setid` are used to discover and correct chat IDs used by the gate.
  **Forward a message from the channel/group** and the bot replies with the numeric id — and binds it
  automatically when the title matches a block. Owner and admins can also just forward the message
  with no command at all. This reads the new `forward_origin` field; `forward_from_chat` was removed
  in Bot API 7.0, which is why the old version silently never worked.
- The command list is registered with `setMyCommands` at startup, so the commands appear in
  Telegram's `/` menu — the member list by default, the admin list for each `ADMIN_IDS`, and every
  command for the owner. Without this the commands exist but stay invisible.

### The AI's instructions

`AI_SYSTEM_PROMPT` tells the bot it is **Ongoing**, built by Deepak working alone — it never claims a
team, company or studio, and never volunteers that it was "set up" or "installed" anywhere. It knows
two links (`AI_BOT_LINK`, `COMMUNITY_LINK`) and is given concrete triggers for when to share them:

- **Community** — when the user asks for more anime/episodes, a group or channel to join, where to
  find more, or what the community is.
- **AI** — when the user asks for another AI, more help, or a smarter assistant.

On any other question it must not bring them up at all — not on a random or unrelated question, not
as a footer or signature, never twice, at most once per chat, and never as a raw URL inside a
welcome. If a link looks blocked it falls back to plain `t.me/...` text.

The bot **replies in whatever language the user writes in** — Hinglish gets Hinglish, Hindi gets
Hindi, English gets English. It mirrors the user rather than switching on its own.

> **If you set `AI_SYSTEM_PROMPT` in your Render environment, it overrides this whole prompt** and
> none of the above applies. The bot logs which one is in use at startup — look for `AI prompt:` in
> the Render logs. Delete the env var if you want the built-in prompt.

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
- **Broadcast is opt-in for recipients.** Only users known to the bot receive broadcasts — the Bot API cannot list the members of a group or channel, so someone who has never started the bot cannot be reached.
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
| No welcome in a channel | Telegram sends no "member joined" event for channels | This is a platform limit — channels only report *join requests* (`chat_join_request`), which the bot already handles. Welcomes work in groups and supergroups |
| Welcome image missing | `welcome.png` not found from the working directory | The bot now searches the working directory, `assets/`, and the folder `bot.py` itself lives in, and falls back to a text-only welcome. Check the startup log for `Welcome image nahi mili` |
| Buttons have no colour | An old Telegram client, or you are looking at an **old message** | Button colour is a **client** feature — Telegram silently ignores `style` on clients older than 9 Feb 2026, and old messages keep the keyboard they were sent with. Two checks: (1) run `/btnstyle` — it sends three action buttons *and* two link buttons, all coloured, and prints the exact JSON being sent; (2) open **https://web.telegram.org** and run `/btnstyle` there — Telegram Web is always the newest build, so coloured buttons there prove the phone app is the old one. Also send `/start` fresh rather than scrolling back |
| Bot still behaves like an older version | `AI_SYSTEM_PROMPT` set in the environment overrides the built-in prompt | Delete `AI_SYSTEM_PROMPT` from the Render environment |
| Every AI reply says "busy" | `AI_API_KEY` is missing | The startup log warns about this explicitly — set the key |
| A command seems to do nothing | It is above your role | Member, admin and owner each see only their own commands in the `/` menu, and anything higher stays **silent** — the bot never reveals that a higher tier exists. Run `/help` to see what your role has |
| `/getid` always asks you to forward | You sent `/getid` with nothing attached | Forward the channel message **first**, or reply to a forwarded message with `/getid` |
| A channel shows "no chat ID yet" in `/stats` | The bot has no numeric ID for it | Add the bot as an admin there, or bind it with `/setid`. Only then can its member count be read |
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
