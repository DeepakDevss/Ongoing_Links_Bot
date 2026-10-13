"""
=====================================================================
  config.py  —  YAHAN SAB KUCH EDIT KARNA HAI (sirf yehi file)
=====================================================================
  Har setting ke aage NUMBER likha hai (# 1, # 2 ...).
  README.md mein wahi number diya hai — wahan se dhoondh lo.

  Render par aap Environment Variables bhi set kar sakte ho.
  Agar env variable mila to wahi use hoga, warna niche likhi value.
=====================================================================
"""

import os
from dotenv import load_dotenv

# .env file (local testing ke liye). Render par .env nahi chahiye.
load_dotenv()


# ---------------------------------------------------------------------
# #1  BOT TOKEN  (BotFather se — /newbot karke)
# ---------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# #1b Bot ka username (group mein @mention se AI trigger karne ke liye)
BOT_USERNAME = os.getenv("BOT_USERNAME", "@PASTE_YOUR_BOT_USERNAME")


# ---------------------------------------------------------------------
# #2  GEMINI API KEY  (Google AI Studio -> aistudio.google.com)
# ---------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "PASTE_YOUR_GEMINI_API_KEY_HERE")

# #2b Gemini model (Oct 2026 ke latest):
#     gemini-3.6-flash       = smart + fast (default)
#     gemini-3.5-flash-lite  = sabse sasta + sabse fast
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_MODEL_LITE = os.getenv("GEMINI_MODEL_LITE", "gemini-3.5-flash-lite")


# ---------------------------------------------------------------------
# #3  ADMIN IDs  (apna Telegram user id — bot ko /id bhejo)
#     Ek se zyada: "12345,67890"
# ---------------------------------------------------------------------
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().lstrip("-").isdigit()
]


# ---------------------------------------------------------------------
# #4  FORCE-JOIN CHANNELS / GROUPS  (3 buttons tak — kam/zyada bhi kar sakte ho)
#     "title" = button par jo dikhega
#     "url"   = button ka link (t.me/...)
#     "id"    = channel/group ka id YA "@username".
#               Bot ko yahan ADMIN hona ZAROORI hai.
#               (public channel: "@channelusername"; private: -1001234567890)
# ---------------------------------------------------------------------
REQUIRED_CHATS = [
    {"title": "Join Our Anime Channel", "url": "https://t.me/YourChannel", "id": "@YourChannel"},
    {"title": "Join Our Anime Group",   "url": "https://t.me/YourChannel",   "id": -1001234567890},
    {"title": "AI Al Community Group",      "url": "https://t.me/LinnetAICommunity",   "id": -1001234567890},
]


# ---------------------------------------------------------------------
# #5  WELCOME MESSAGE KA BUTTON  (aapka 2nd group/channel link)
# ---------------------------------------------------------------------
WELCOME_BUTTON_TEXT = os.getenv("WELCOME_BUTTON_TEXT", "Join Our Main Group")
WELCOME_BUTTON_URL = os.getenv("WELCOME_BUTTON_URL", "https://t.me/LinnetAICommunity")


# ---------------------------------------------------------------------
# #6  WELCOME MESSAGE KA TEXT  (photo ke neeche)
#     {first_name} {group_name} {full_name} {user_id} {join_date}
#     — apne aap bhar jaate hain. Inhe mat hatao.
# ---------------------------------------------------------------------
WELCOME_TEMPLATE = os.getenv(
    "WELCOME_TEMPLATE",
    """Hᴇʏ ᴅᴇᴀʀ {first_name} 👋
Wᴇʟᴄᴏᴍᴇ ᴛᴏ {group_name}

┏━━━━━━━━━━━━»»❀
♛ ɴᴀᴍᴇ : {full_name}
⍟ ɪ'ᴅ : {user_id}
⍟ ᴅᴀᴛᴇ ᴊᴏɪɴᴇᴅ : {join_date}
┕━━━━━━━━━━━━»»❀

Tʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴊᴏɪɴɪɴɢ ᴜs ❤️""",
)


# ---------------------------------------------------------------------
# #7  WELCOME PHOTO  (assets/welcome.png — apni image se replace kar sakte ho)
# ---------------------------------------------------------------------
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "welcome.png" if os.path.exists("welcome.png") else "assets/welcome.png")


# ---------------------------------------------------------------------
# #8  AUTO-APPROVE JOIN REQUESTS
#     True = join request turant approve. False = aap khud karoge.
# ---------------------------------------------------------------------
AUTO_APPROVE_JOIN_REQUESTS = os.getenv("AUTO_APPROVE_JOIN_REQUESTS", "True").lower() == "true"


# ---------------------------------------------------------------------
# #9  WEBHOOK / PORT  (Render khud set karta hai — chhedne ki zaroorat nahi)
# ---------------------------------------------------------------------
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "") or os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "10000"))

# #9b Database file (Render free par restart/redeploy par reset ho sakti hai)
DB_PATH = os.getenv("DB_PATH", "data/db.json")

# #9c Membership check cache (seconds) — API calls kam karne ke liye
MEMBERSHIP_CACHE_TTL = int(os.getenv("MEMBERSHIP_CACHE_TTL", "60"))


# ---------------------------------------------------------------------
# #10  AI ka behaviour (system prompt)
# ---------------------------------------------------------------------
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    (
        "You are a friendly, smart Telegram assistant for a community. "
        "Always reply in the SAME language the user wrote in "
        "(Hindi, English, Hinglish, or any other). Keep answers clear and "
        "reasonably short. Be helpful and polite. If you don't know "
        "something, say so honestly instead of making it up."
    ),
)

# Max characters of AI reply per message (Telegram limit ~4096)
AI_MAX_CHARS = int(os.getenv("AI_MAX_CHARS", "3800"))
