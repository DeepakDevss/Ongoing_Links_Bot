"""
config.py
=========
All settings for the bot, read from environment variables (see .env.example).
Nothing here needs a code change to re-configure the bot.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _int_list(name: str) -> list:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    out = []
    for token in raw.replace(",", " ").split():
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def _str_list(name: str, default: list) -> list:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [x.strip() for x in raw.replace(",", " ").split() if x.strip()]


# --------------------------------------------------------------------------- #
# credentials / core
# --------------------------------------------------------------------------- #
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

# MongoDB is OPTIONAL. Leave MONGO_URI empty to run with in-memory storage
# (nothing to set up, but data is lost on restart). Set it to persist data.
MONGO_URI: str = os.getenv("MONGO_URI", "").strip()
DB_NAME: str = os.getenv("DB_NAME", "welcome_bot").strip()

OWNER_ID: int = _int("OWNER_ID", 0)
ADMINS: list = _int_list("ADMINS")

DB_CHANNEL: int = _int("DB_CHANNEL", 0)

# --------------------------------------------------------------------------- #
# destination group / channel + optional force-subscribe gate
# --------------------------------------------------------------------------- #
MAIN_GROUP_ID: int = _int("MAIN_GROUP_ID", 0)
FSUB_CHANNELS: list = _int_list("FSUB_CHANNELS")

# --------------------------------------------------------------------------- #
# web server
# --------------------------------------------------------------------------- #
PORT: int = _int("PORT", 8080)

# --------------------------------------------------------------------------- #
# welcome content
# --------------------------------------------------------------------------- #
WELCOME_IMAGE: str = os.getenv("WELCOME_IMAGE", "welcome.png").strip()

# Placeholders: {first_name} {full_name} {user_id} {group_name} {join_date}
DEFAULT_WELCOME_TEXT: str = (
    "Hᴇʏ ᴅᴇᴀʀ {first_name} 👋\n"
    "Wᴇʟᴄᴏᴍᴇ ᴛᴏ {group_name}\n\n"
    "┏━━━━━━━━━━━━»»❀\n"
    "♛ ɴᴀᴍᴇ : {full_name}\n"
    "⍟ ɪ'ᴅ : {user_id}\n"
    "⍟ ᴅᴀᴛᴇ ᴊᴏɪɴᴇᴅ : {join_date}\n"
    "┕━━━━━━━━━━━━»»❀\n\n"
    "Tʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴊᴏɪɴɪɴɢ ᴜs ❤️"
)

WELCOME_TEXT: str = os.getenv("WELCOME_TEXT", DEFAULT_WELCOME_TEXT) or DEFAULT_WELCOME_TEXT

JOIN_BUTTON_TEXT: str = os.getenv("JOIN_BUTTON_TEXT", "🎬 ᴊᴏɪɴ {group_name}")

FSUB_PROMPT_TEXT: str = os.getenv(
    "FSUB_PROMPT_TEXT",
    "📢 Please join the channel(s) below, then tap ♻️ Try Again.",
)

JOIN_CONFIRM_TEXT: str = os.getenv(
    "JOIN_CONFIRM_TEXT",
    "✅ {first_name}, you're in! Welcome to {group_name} ❤️",
)

HELP_TEXT: str = os.getenv(
    "HELP_TEXT",
    "Available commands:\n"
    "/start – join the group\n"
    "/help – show this message\n"
    "/reset – clear our chat memory (AI)",
)

# --------------------------------------------------------------------------- #
# AI (Gemini) — free tier
# --------------------------------------------------------------------------- #
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

# Tried in order; the first model that answers is used.  Default = the two you
# asked for (fast/cheap first).  If Google returns 404 for the 2.5 ids on a new
# key, switch these to e.g. "gemini-3.5-flash-lite gemini-3.5-flash".
GEMINI_MODELS: list = _str_list(
    "GEMINI_MODELS", ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
)

AI_SYSTEM_PROMPT: str = os.getenv(
    "AI_SYSTEM_PROMPT",
    "You are Linnet, a friendly, witty and helpful assistant living in a "
    "Telegram group. Keep replies short, warm and useful. Reply in the same "
    "language the user writes in (Hindi, English or Hinglish).",
)

AI_HISTORY_LEN: int = _int("AI_HISTORY_LEN", 6)  # messages kept per chat
AI_MAX_CHARS: int = _int("AI_MAX_CHARS", 4000)   # Telegram message limit is 4096

AI_ERROR_TEXT: str = os.getenv(
    "AI_ERROR_TEXT",
    "😔 Sorry, I couldn't reply right now. Please try again in a moment.",
)


def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in ADMINS
