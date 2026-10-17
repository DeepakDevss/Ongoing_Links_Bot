"""
=====================================================================
  bot.py — ONGOING BOT (poora bot ek hi file mein)
=====================================================================
  Isme sab kuch hai:
    • Database (users / referrals / ban)   -> section "DATABASE"
    • AI wrapper                            -> section "AI"
    • Telegram handlers + webhook server    -> baaki sab

  Sirf "config.py" edit karni hai. Is file ko chhedne ki zaroorat nahi.

  Features:
    1) Force-join gate (config.py ke LINK_BLOCKS se) + join requests owner ko
    2) Welcome message (photo + styled caption)
    3) AI chat — DM mein, aur group mein sirf @mention par (membership check ke saath)
    4) Referral / invite growth system

  Text styling fonts.py se hoti hai (Unicode small-caps / bold) — isliye
  ParseMode ki zaroorat nahi, aur naam mein "_" ya "*" hone par bhi
  message fail nahi hota.

  NOTE: bot apni identity (kaunse model par chalta hai) kabhi reveal nahi
  karta — wo AI_SYSTEM_PROMPT se handle hota hai.
=====================================================================
"""

import asyncio
import difflib
import json
import logging
import os
import random
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    AIORateLimiter,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

from google import genai
from google.genai import types

import fonts
from config import (
    BOT_TOKEN,
    BOT_USERNAME,
    BOT_BRAND,
    ADMIN_IDS,
    LINK_BLOCKS,
    JOIN_FAIL_MODE,
    WELCOME_TEMPLATE,
    WELCOME_IMAGE,
    WEBHOOK_URL,
    WEBHOOK_SECRET,
    PORT,
    DB_PATH,
    MEMBERSHIP_CACHE_TTL,
    MIN_JOINS_REQUIRED,
    OWNER_ID,
    OWNER_NAME,
    admin_label,
    RULES_TEXT,
    NOTIFY_OWNER,
    POST_COMMAND,
    POST_TYPES,
    POST_FIELDS,
    POST_BUTTON_TEXT,
    JOIN_BUTTON_TEXT,
    GATE_MESSAGE,
    GATE_BUTTON_TEXT,
    JOIN_PROMPT,
    AI_API_KEY,
    AI_MODEL,
    AI_MODEL_LITE,
    AI_SYSTEM_PROMPT,
    AI_MAX_CHARS,
    AI_THINKING_LEVEL,
    AI_BUSY_MESSAGE,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
# httpx ke GET/POST logs chhupa do (shor kam)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NOT_MEMBER_STATUSES = {ChatMember.LEFT, ChatMember.BANNED}


# =====================================================================
#  DATABASE  (simple JSON store)
# =====================================================================
_db_lock = threading.Lock()


def _db_default():
    return {"users": {}, "referrals": {}, "banned": [],
            "stats": {"total_started": 0}, "chat_ids": {}, "chats": {}}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _today():
    return datetime.now(timezone.utc).strftime("%d %b %Y")


def _db_load():
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = _db_default()
            base.update(data)
            return base
        except Exception as e:
            # Corrupt DB ko CHUPCHAAP reset MAT karo — warna saara data ud jata
            # hai aur pata bhi nahi chalta. File alag rakho + log karo.
            log.error("DB load fail (%s): %s", DB_PATH, e)
            try:
                bad = DB_PATH + ".corrupt-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                os.replace(DB_PATH, bad)
                log.error("Corrupt DB ko %s par bhej diya", bad)
            except Exception:
                pass
    return _db_default()


def _db_save(data):
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())   # power-loss par aadhi likhi file na bane
    os.replace(tmp, DB_PATH)


_chat_ids_cache = None


def _learned_ids():
    """Runtime me seekhi gayi chat ids ({block name: chat id})."""
    global _chat_ids_cache
    if _chat_ids_cache is None:
        with _db_lock:
            _chat_ids_cache = dict(_db_load().get("chat_ids", {}))
    return _chat_ids_cache


def db_set_chat_id(name, chat_id):
    """Block name -> numeric chat id yaad rakho (config edit ki zaroorat nahi)."""
    global _chat_ids_cache
    with _db_lock:
        data = _db_load()
        data.setdefault("chat_ids", {})[name] = int(chat_id)
        _db_save(data)
        _chat_ids_cache = dict(data["chat_ids"])


def db_upsert_chat(chat_id, title, ctype, status=None):
    """Bot jis bhi chat me hai uski detail yaad rakho (id, title, type, status)."""
    with _db_lock:
        data = _db_load()
        c = data.setdefault("chats", {}).get(str(chat_id), {})
        c.update({"id": int(chat_id), "title": title or "", "type": ctype or "",
                  "seen": _now()})
        if status:
            c["status"] = status
        data["chats"][str(chat_id)] = c
        _db_save(data)


def db_get_chats():
    with _db_lock:
        return dict(_db_load().get("chats", {}))


_seen_cache: dict[int, float] = {}


def db_add_user(user, bump_start=False):
    """Naya user ho to True return karega.
    bump_start=True (/start) -> 'Total started' bhi badhao, USI ek write me
    (pehle alag db_count_start() doosra read+write karta tha — ab ek hi).
    Normal messages par 5 min tak write skip hoti hai (speed)."""
    if not bump_start and time.time() - _seen_cache.get(user.id, 0) < 300:
        return False
    _seen_cache[user.id] = time.time()
    _prune(_seen_cache, 10000)
    with _db_lock:
        data = _db_load()
        uid = str(user.id)
        is_new = uid not in data["users"]
        entry = data["users"].get(uid, {})
        entry.update({
            "id": user.id,
            "first_name": user.first_name or "",
            "username": user.username or "",
            "last_seen": _now(),
        })
        if is_new:
            entry["joined_at"] = _now()
        data["users"][uid] = entry
        if bump_start:
            data["stats"]["total_started"] = data["stats"].get("total_started", 0) + 1
        _db_save(data)
        return is_new


def db_user_exists(uid) -> bool:
    with _db_lock:
        return str(uid) in _db_load()["users"]


def db_all_users():
    with _db_lock:
        return list(_db_load()["users"].values())


_ban_cache = None


def db_is_banned(uid):
    """In-memory cache — har message par file read na ho (bottleneck fix)."""
    global _ban_cache
    if _ban_cache is None:
        with _db_lock:
            _ban_cache = set(_db_load()["banned"])
    return int(uid) in _ban_cache


def db_set_ban(uid, value=True):
    global _ban_cache
    with _db_lock:
        data = _db_load()
        uid = int(uid)
        if value and uid not in data["banned"]:
            data["banned"].append(uid)
        if not value and uid in data["banned"]:
            data["banned"].remove(uid)
        _db_save(data)
        _ban_cache = set(data["banned"])


def db_add_referral(referrer_id, new_user_id):
    """Naye user ko referrer ke khaate mein jodo (ek user sirf ek baar)."""
    with _db_lock:
        data = _db_load()
        new_uid = str(new_user_id)
        entry = data["users"].get(new_uid, {})
        if entry.get("ref_by"):
            return False
        entry["ref_by"] = int(referrer_id)
        data["users"][new_uid] = entry
        ref = data["referrals"].get(str(referrer_id), {"count": 0, "users": []})
        if int(new_user_id) not in ref["users"]:
            ref["users"].append(int(new_user_id))
            ref["count"] = len(ref["users"])
        data["referrals"][str(referrer_id)] = ref
        _db_save(data)
        return True


def db_ref_count(uid):
    with _db_lock:
        ref = _db_load()["referrals"].get(str(uid))
        return ref["count"] if ref else 0


def db_ref_rank(uid):
    with _db_lock:
        refs = _db_load()["referrals"]
        ordered = sorted(refs.items(), key=lambda kv: kv[1].get("count", 0), reverse=True)
        for i, (k, _) in enumerate(ordered, start=1):
            if k == str(uid):
                return i
        return 0


def db_stats():
    with _db_lock:
        data = _db_load()
        return {
            "total_users": len(data["users"]),
            "total_started": data["stats"].get("total_started", 0),
            "banned": len(data["banned"]),
            "referrals": sum(v.get("count", 0) for v in data["referrals"].values()),
        }


# =====================================================================
#  AI  (provider/identity kabhi expose nahi hoti)
# =====================================================================
_ai_client = None


def _get_ai():
    global _ai_client
    if _ai_client is None:
        _ai_client = genai.Client(api_key=AI_API_KEY)
    return _ai_client


_ai_config = None
_thinking_off = False


def _get_ai_config():
    """GenerateContentConfig ek hi baar banao (thinking level ke saath).
    Gemini 3 Flash default 'medium' thinking par slow chalta hai — isliye
    'minimal' set karte hain, se delay kam hota hai."""
    global _ai_config
    if _ai_config is not None:
        return _ai_config
    sys_prompt = AI_SYSTEM_PROMPT
    if OWNER_NAME:
        sys_prompt += (
            f"\n\nThe owner of this bot is {OWNER_NAME}. If asked who owns or runs "
            f"this bot, say it is {OWNER_NAME}. Never share any user's private data."
        )
    kwargs = {"system_instruction": sys_prompt}
    level = (AI_THINKING_LEVEL or "").strip().lower()
    if level and not _thinking_off:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
        except Exception:
            pass
    try:
        _ai_config = types.GenerateContentConfig(**kwargs)
    except Exception:
        # SDK/model shape alag -> thinking ke bina
        _ai_config = types.GenerateContentConfig(system_instruction=AI_SYSTEM_PROMPT)
    return _ai_config


def _disable_thinking():
    """Model ne thinking config reject kiya -> bina thinking par switch (self-heal)."""
    global _thinking_off, _ai_config
    if not _thinking_off:
        _thinking_off = True
        _ai_config = None
        log.warning("Thinking config hata di — model ne accept nahi kiya.")


# --- chhoti conversation memory (per USER, per chat) -------------------
#   private chat -> key "u<user_id>"
#   group        -> key "g<chat_id>:u<user_id>"
#   (pehle sirf chat_id par key thi, isliye EK GROUP me sabki baatein ek hi
#    history me mix ho jaati thi — ab har user ki apni memory hai)
_HISTORY_TURNS = 6          # per key kitne user+bot turns yaad rakhe
_HISTORY_CHATS = 1000       # kitni keys ki memory (bound, memory leak nahi)
_history: dict = {}


def _hist(mem_key):
    """COPY return karo — warna background thread me iterate karte waqt
    event loop list badal de to 'list changed size' error aa sakta hai."""
    return list(_history.get(mem_key, []))


def _hist_add(mem_key, role, text):
    h = _history.setdefault(mem_key, [])
    h.append((role, text))
    if len(h) > _HISTORY_TURNS * 2:
        del h[: len(h) - _HISTORY_TURNS * 2]
    _prune(_history, _HISTORY_CHATS)


_MD_PAIRS = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"~~(.+?)~~", re.S), r"\1"),
    (re.compile(r"`+([^`]+)`+"), r"\1"),
    (re.compile(r"\*(.+?)\*", re.S), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
]


def _strip_md(t: str) -> str:
    """AI ke jawab se markdown hata do. Bot parse_mode use nahi karta, isliye
    * aur ` seedhe dikhte hain (user ko ganda lagta hai)."""
    if not t:
        return t or ""
    for rx, rp in _MD_PAIRS:
        t = rx.sub(rp, t)
    t = t.replace("**", "").replace("`", "")
    # bache hue * hatao, par "2*3" jaisa kuch bacha rahe
    t = re.sub(r"(?<!\d)\*(?!\d)", "", t)
    return t.strip()


def _run_ai(model: str, prompt: str, history=None) -> str:
    client = _get_ai()
    contents = []
    for role, text in (history or []):
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=_get_ai_config(),
    )
    return (getattr(resp, "text", None) or "").strip()


def busy_message() -> str:
    """AI busy/down hone par user ko bhejne wala message."""
    ai_block = next((b for b in LINK_BLOCKS if b["name"].strip().lower() == "ai"), None)
    if "{ai_link}" not in AI_BUSY_MESSAGE:
        return AI_BUSY_MESSAGE
    return AI_BUSY_MESSAGE.replace("{ai_link}", ai_block["url"] if ai_block else "AI")


async def ask_ai(prompt: str, lite: bool = False, where: str = "", mem_key=None,
                 note: str = "") -> str:
    """Prompt bhejo, reply lo. lite=True se sasta/fast model use hoga.
    where   = context line (group ka naam ya 'a private chat').
    note    = extra context (jaise 'this user is the owner').
    mem_key = iske hisaab se thodi conversation memory rakhi jaati hai."""
    raw = prompt
    bits = []
    if where:
        bits.append(f"you are in {where}")
    if note:
        bits.append(note)
    if bits:
        prompt = f"(Context: {'; '.join(bits)}.)\n\n{prompt}"
    history = _hist(mem_key) if mem_key is not None else []
    # Model kabhi "500 UNAVAILABLE / high demand" deta hai -> retry + doosra model.
    # Fail-fast: kam attempts, chhota backoff (delay kam karne ke liye).
    models = [AI_MODEL_LITE, AI_MODEL] if lite else [AI_MODEL, AI_MODEL_LITE]
    last_err = None
    for model in models:
        for attempt in range(2):
            try:
                text = await asyncio.to_thread(_run_ai, model, prompt, history)
                if text:
                    text = _strip_md(text)      # markdown hatao (* aur ` )
                    if len(text) > AI_MAX_CHARS:
                        text = text[: AI_MAX_CHARS - 20].rstrip() + "\n\n… (shortened)"
                    if mem_key is not None:
                        _hist_add(mem_key, "user", raw)
                        _hist_add(mem_key, "model", text)
                    return text
            except Exception as e:
                last_err = e
                log.warning("AI fail (model=%s try=%s): %s", model, attempt + 1, e)
                low = str(e).lower()
                if "thinking" in low or "unsupported" in low or "invalid" in low:
                    _disable_thinking()
                await asyncio.sleep(0.6 * (attempt + 1))  # chhota backoff
    log.error("AI poora fail: %s", last_err)
    # User ko kabhi provider ka naam nahi dikhna chahiye.
    return busy_message()


# =====================================================================
#  HELPERS
# =====================================================================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def is_owner(uid: int) -> bool:
    return OWNER_ID != 0 and int(uid) == OWNER_ID


def is_privileged(uid: int) -> bool:
    """Owner ya admin — inhe force-join gate NAHI dikhta."""
    return is_owner(uid) or is_admin(uid)


def role_of(uid) -> str:
    """Owner / Admin 1 / Admin 2 / Member — dikhane ke liye."""
    if is_owner(uid):
        return "Owner"
    if is_admin(uid):
        return admin_label(uid)
    return "Member"


def _owner_note(uid) -> str:
    """Owner bol raha hai to AI ko bata do (gate ke baahar)."""
    if is_owner(uid):
        return "the person talking to you is the owner of this bot"
    return ""


async def _notify_owner(bot, text):
    """Owner ko DM — SIRF jab NOTIFY_OWNER on ho. Warna sirf log (spam nahi).
    Sab jaankari /diag se kabhi bhi mil jaati hai."""
    log.info("NOTIFY: %s", text.replace("\n", " | "))
    if not NOTIFY_OWNER:
        return
    target = OWNER_ID or (ADMIN_IDS[0] if ADMIN_IDS else 0)
    if not target:
        return
    try:
        await bot.send_message(target, text)
    except Exception:
        pass


def _norm(s) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _clean_title(t) -> str:
    """Group title AI ko bhejne se pehle saaf karo — warna koi apne group ka naam
    'ignore all previous instructions...' rakh ke prompt-inject kar sakta hai."""
    if not t:
        return ""
    t = " ".join(str(t).split())              # newline / tab hata do
    t = t.replace('"', "'").replace("(", "[").replace(")", "]")
    return t[:64]


def _tokens(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) >= 3]


def _fuzzy_in(needle, hay, cutoff=0.8):
    return any(difflib.SequenceMatcher(None, needle, h).ratio() >= cutoff for h in hay)


def match_block_by_title(title):
    """Title ko LINK_BLOCKS ke naam se match karo.
    1) exact (normalized)   2) substring   3) token-based (naam thoda alag ho)
    #3 isliye: block "ANIME WORLD HINDI" aur asli channel ka naam
    "ANIME WORLD OFFICIAL HINDI DUBBED" — pehle ye bind hi nahi hota tha."""
    t = _norm(title)
    if not t:
        return None
    for b in LINK_BLOCKS:
        if _norm(b["name"]) == t:
            return b
    for b in LINK_BLOCKS:
        n = _norm(b["name"])
        if len(n) >= 6 and (n in t or t in n):
            return b
    hay = _tokens(title)
    if not hay:
        return None
    hits = []
    for b in LINK_BLOCKS:
        toks = _tokens(b["name"])
        if toks and all(_fuzzy_in(w, hay) for w in toks):
            hits.append(b)
    return hits[0] if len(hits) == 1 else None   # ambiguous ho to kuch na karo


def enforced_blocks():
    """Jo blocks force-join ke liye verify ho sakte hain.
    id config se aati hai, ya runtime me seekhi gayi id se."""
    learned = _learned_ids()
    out = []
    for b in LINK_BLOCKS:
        if not b.get("require_join"):
            continue
        cid = b.get("id") or learned.get(b["name"])
        if cid:
            out.append({**b, "id": cid})
    return out


# chhota membership cache: getChatMember API calls kam karne ke liye
_member_cache: dict[tuple[int, int], tuple[bool, float]] = {}

# Check button ka anti-spam cooldown (user_id -> last time)
_check_cd: dict = {}

# AI ke liye per-user rate limit (paid quota bachane ke liye)
_ai_hits: dict = {}
_AI_WINDOW = 60
_AI_MAX = 15


def _ai_allowed(uid) -> bool:
    """Ek user 60 second me max _AI_MAX AI calls kar sakta hai."""
    now = time.time()
    hits = _ai_hits.setdefault(uid, [])
    while hits and now - hits[0] > _AI_WINDOW:
        hits.pop(0)
    if len(hits) >= _AI_MAX:
        return False
    hits.append(now)
    _prune(_ai_hits, 5000)
    return True


def _prune(cache: dict, keep: int):
    """Cache ko bounded rakho — memory leak se bachne ke liye."""
    if len(cache) > keep:
        for k in list(cache.keys())[: len(cache) - keep]:
            cache.pop(k, None)

# bot ka apna info — har group message par get_me() API call na ho (speed)
_bot_me = None


async def _get_me(context):
    global _bot_me
    if _bot_me is None:
        _bot_me = await context.bot.get_me()
    return _bot_me


async def _is_member(context, chat_id, user_id, force=False):
    """True = member, False = not member, None = check hi nahi ho paya."""
    key = (int(user_id), str(chat_id))
    now = time.time()
    cached = _member_cache.get(key)
    if not force and cached and now - cached[1] < MEMBERSHIP_CACHE_TTL:
        return cached[0]
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        ok = member.status not in NOT_MEMBER_STATUSES
    except Exception as e:
        # Bot us chat mein admin nahi / id galat -> verify nahi ho paya.
        # None = "pata nahi". JOIN_FAIL_MODE "open" ho to joined maano.
        log.warning("Membership check fail for %s: %s", chat_id, e)
        result = True if JOIN_FAIL_MODE == "open" else None
        _member_cache[key] = (result, now)
        return result
    _member_cache[key] = (ok, now)
    _prune(_member_cache, 5000)
    return ok


def required_count(total=None) -> int:
    """Kitne VERIFIABLE blocks join karna zaroori hai.
    total=None -> enforced_blocks() ka count. 0 -> koi requirement nahi."""
    if total is None:
        total = len(enforced_blocks())
    if total <= 0:
        return 0
    val = str(MIN_JOINS_REQUIRED).strip().lower()
    if val == "all":
        return total
    try:
        return max(1, min(int(val), total))
    except ValueError:
        return total


def has_required_blocks() -> bool:
    """config me koi bhi require_join block hai?"""
    return any(b.get("require_join") for b in LINK_BLOCKS)


async def join_status(context, user_id, force=False):
    """(joined, missing, unknown, need).
    unknown = jinhe check hi nahi kar paye (bot admin nahi / id galat).
    need SIRF un blocks par count hota hai jo sach me verify ho sake — isliye
    agar kuch bhi verify na ho to need 0, aur gate user ko block nahi karta.
    force=True -> cache ignore karo, fresh check (Check button ke liye)."""
    joined, missing, unknown = [], [], []
    for chat in enforced_blocks():
        r = await _is_member(context, chat["id"], user_id, force=force)
        if r is True:
            joined.append(chat)
        elif r is False:
            missing.append(chat)
        else:
            unknown.append(chat)
    need = required_count(len(joined) + len(missing))
    return joined, missing, unknown, need


# jin users ne gate ek baar paar kar liya (jab verify possible hi nahi hai)
_passed: set = set()

# chat_id -> bot ka last dekha status (duplicate notification rokne ke liye)
_chat_status: dict = {}


async def needs_join(context, user_id, force=False) -> bool:
    """User ko gate dikhana hai? Owner/admin ko nahi."""
    if is_privileged(user_id):
        return False
    if not has_required_blocks():
        return False
    joined, _missing, _unknown, need = await join_status(context, user_id, force=force)
    if need > 0:
        return len(joined) < need
    # Kuch bhi verify nahi ho sakta (IDs/bot-admin missing) -> gate sirf EK BAAR
    # dikhao. Warna har message par gate dikhta rehta aur bot kabhi na chalta.
    return user_id not in _passed


def link_keyboard(extra_rows=None):
    """config.py ke LINK_BLOCKS se buttons banao.
    Jinke "row" number same hai, wo ek hi row mein (side-by-side) aate hain."""
    rows = []
    by_row: dict = {}
    order = []
    for b in LINK_BLOCKS:
        r = b.get("row", len(order) + 1)
        if r not in by_row:
            by_row[r] = []
            order.append(r)
        label = f"{b['emoji']} {fonts.bold(b['name'])}" if b.get("emoji") else fonts.bold(b["name"])
        by_row[r].append(InlineKeyboardButton(label, url=b["url"]))
    for r in order:
        rows.append(by_row[r])
    if extra_rows:
        rows.extend(extra_rows)
    return InlineKeyboardMarkup(rows)


def build_welcome(member, chat_title: str) -> str:
    """Styled welcome caption (small-caps + bold)."""
    sc, b = fonts.small_caps, fonts.bold
    name = member.first_name or "Member"
    full = member.full_name or name
    return (
        f"{sc('Welcome')}, {b(name)}\n\n"
        f"{sc('You have joined')} {b(chat_title or 'the group')}\n\n"
        f"{sc('Name')}   : {b(full)}\n"
        f"{sc('ID')}     : {b(str(member.id))}\n"
        f"{sc('Joined')} : {b(_today())}\n\n"
        f"{sc('Ask anything any time')} — {b(BOT_BRAND)} {sc('is here to help')}."
    )


async def send_join_gate(update: Update, context, edit=False):
    text = GATE_MESSAGE
    kb = link_keyboard(extra_rows=[
        [InlineKeyboardButton(GATE_BUTTON_TEXT, callback_data="check_join")],
    ])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


async def verify_blocks_on_startup(app: Application):
    """Start par check: har enforced chat ki id hai? bot admin hai?
    Jo problem mile wo admin ko DM bhi bhejo — sirf log me chhodo mat."""
    try:
        me = await app.bot.get_me()
    except Exception as e:
        log.warning("Startup check skip — get_me fail: %s", e)
        return
    learned = _learned_ids()
    problems = []
    for b in LINK_BLOCKS:
        if not b.get("require_join"):
            continue
        cid = b.get("id") or learned.get(b["name"])
        if not cid:
            log.warning("Block '%s' ki numeric id nahi hai — verify nahi hoga.", b["name"])
            problems.append(f"• {b['name']}: ID nahi hai")
            continue
        try:
            member = await app.bot.get_chat_member(chat_id=cid, user_id=me.id)
            if member.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                log.warning("Bot '%s' mein ADMIN nahi hai.", b["name"])
                problems.append(f"• {b['name']}: bot admin nahi hai")
        except Exception as e:
            log.warning("Block '%s' verify fail (id=%s): %s", b["name"], cid, e)
            problems.append(f"• {b['name']}: check fail ({type(e).__name__})")
    if problems:
        text = (
            "⚠️ Force-join gate abhi poori tarah kaam nahi karega:\n\n"
            + "\n".join(problems)
            + "\n\nFix: bot ko us channel/group me ADMIN banao. Bot add karte hi "
            "mujhe chat id khud mil jayegi. Ya /getid (message forward) / /setid."
        )
        await _notify_owner(app.bot, text)


# =====================================================================
#  COMMANDS
# =====================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_add_user(user, bump_start=True)

    if db_is_banned(user.id):
        await update.effective_message.reply_text("You have been blocked from using this bot.")
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            f"Hi {user.first_name} — please message me in private: @{context.bot.username}"
        )
        return

    # /post ka deep link: /start p<code>
    if context.args and context.args[0].startswith("p"):
        p = db_post_get(context.args[0][1:])
        if p:
            await _post_welcome(update, context, p)
            return

    # Referral: /start ref_12345
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                # Referrer asli hona chahiye aur banned na ho — warna koi bhi
                # fake id bhej ke referral count badha sakta hai.
                if (referrer_id != user.id
                        and db_user_exists(referrer_id)
                        and not db_is_banned(referrer_id)
                        and db_add_referral(referrer_id, user.id)):
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            f"Someone joined through your link. Total invites: {db_ref_count(referrer_id)}",
                        )
                    except Exception:
                        pass
            except ValueError:
                pass

    if await needs_join(context, user.id):
        await send_join_gate(update, context)
    else:
        # Menu nahi — seedha chat.
        await update.effective_message.reply_text(
            f"{fonts.bold('Welcome, ' + (user.first_name or 'friend'))}\n\n"
            f"I am {fonts.bold(BOT_BRAND)}. Just type your question."
        )


# (command, short description, min role)   role: member | admin | owner
# POSTCMD = POST_COMMAND ka placeholder (naam config se aata hai).
_COMMANDS = [
    ("start",       "bot start / force-join gate",                 "member"),
    ("help",        "ye message",                                  "member"),
    ("id",          "apni Telegram ID",                            "member"),
    ("ref",         "referral link + invite count",                "member"),
    ("rules",       "rules dekho",                                 "member"),
    ("ai",          "AI se seedha jawab:  /ai <sawaal>",           "member"),
    ("report",      "kisi message par reply karke report",         "member"),
    ("stats",       "bot ki statistics",                           "owner"),
    ("getid",       "chat ki ID nikaalo (message forward karke)",  "owner"),
    ("setid",       "block ki ID set karo:  /setid <block> <id>",  "owner"),
    ("diag",        "gate + chats ka poora status",                "owner"),
    ("chats",       "bot jin chats me hai, unki list",             "owner"),
    ("perms",       "is chat me bot ki permissions + matlab",      "owner"),
    ("admins",      "team ki list",                               "admin"),
    ("list_admins", "same as /admins",                             "admin"),
    ("settitle",    "group ka naam badlo:  /settitle <naam>",      "admin"),
    ("invitelink",  "chat ka invite link banao",                   "admin"),
    ("kick",        "member ko kick/ban:  /kick <user_id>",        "admin"),
    ("unkick",      "kick/ban hatao:  /unkick <user_id>",          "admin"),
    ("del",         "reply wala message delete karo",              "admin"),
    ("pin",         "reply wala message pin karo",                 "admin"),
    ("promote",     "member ko admin banao:  /promote <user_id>",  "admin"),
    ("POSTCMD",     "naya post banao (deep link ke saath)",        "admin"),
    ("posts",       "bane hue posts ki list",                      "admin"),
    ("broadcast",   "sab users ko message bhejo",                  "owner"),
    ("ban",         "bot-wide ban:  /ban <user_id>",               "owner"),
    ("unban",       "ban hatao:  /unban <user_id>",                "owner"),
]


def _help_rows(want):
    return [((POST_COMMAND if c == "POSTCMD" else c), d)
            for c, d, r in _COMMANDS if r == want]


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Role ke hisaab se commands — FLAT list, koi tier label nahi.
    Isse member/admin ko pata nahi chalta ki unke paas kam commands hain;
    unki list poori lagti hai. Owner ko sab dikhta hai (★ = sirf owner)."""
    uid = update.effective_user.id
    sc, bd = fonts.small_caps, fonts.bold
    lvl = 2 if is_owner(uid) else (1 if is_admin(uid) else 0)
    rank = {"member": 0, "admin": 1, "owner": 2}
    out = [bd("COMMANDS"), ""]
    for c, d, r in _COMMANDS:
        if rank[r] > lvl:
            continue
        name = POST_COMMAND if c == "POSTCMD" else c
        star = "★ " if (lvl == 2 and rank[r] == 2) else ""
        out.append(f"{star}/{name} — {d}")
    out.append("")
    out.append(f"{sc('chat')}: DM me seedha likho. Group me {BOT_USERNAME} ko mention karo.")
    if lvl >= 1:
        out.append(f"{sc('group commands sirf group me — bot ko wahan admin hona chahiye')}")
    if lvl == 2:
        out.append("")
        out.append(f"{sc('★ = sirf owner')}  •  {sc('aapke paas sabhi permissions hain')}")
    await _send_long(update.effective_message, "\n".join(out).strip())


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"{fonts.small_caps('Your Telegram ID')}: {fonts.bold(str(update.effective_user.id))}"
    )


async def cmd_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    link = f"https://t.me/{context.bot.username}?start=ref_{user.id}"
    count = db_ref_count(user.id)
    rank = db_ref_rank(user.id)
    rank_line = f"\n{fonts.small_caps('Rank')}: {fonts.bold('#' + str(rank))}" if rank else ""
    text = (
        f"{fonts.small_caps('Your referral link')}\n\n"
        f"{link}\n\n"
        f"{fonts.small_caps('Your invites')}: {fonts.bold(str(count))}{rank_line}\n\n"
        "Share this link — when people join, your count goes up."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "Share", url=f"https://t.me/share/url?url={link}&text=Try this bot!")]])
    await update.effective_message.reply_text(text, reply_markup=kb)


# -------- admin --------
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _need_owner(update):
        return
    s = db_stats()
    await update.effective_message.reply_text(
        f"{fonts.small_caps('Bot stats')}\n\n"
        f"{fonts.small_caps('Total users')}: {fonts.bold(str(s['total_users']))}\n"
        f"{fonts.small_caps('Total started')}: {fonts.bold(str(s['total_started']))}\n"
        f"{fonts.small_caps('Total referrals')}: {fonts.bold(str(s['referrals']))}\n"
        f"{fonts.small_caps('Banned')}: {fonts.bold(str(s['banned']))}"
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sirf un users ko bhejta hai jinhone bot ko /start kiya hai (opt-in).
    Ye OWNER-only hai (poori list par mass DM — bada asar)."""
    if not await _need_owner(update):
        return
    msg = update.effective_message
    if not context.args and not msg.reply_to_message:
        await msg.reply_text("Use: /broadcast <message>  (or reply to a message with /broadcast)")
        return
    if context.args:
        text = " ".join(context.args)
    else:
        r = msg.reply_to_message
        text = (r.text or r.caption or "").strip()   # media par text None hota hai
    if not text:
        await msg.reply_text(
            "Kuch text nahi mila (media par caption bhi nahi). Use: /broadcast <message>"
        )
        return
    users = db_all_users()
    sent = failed = 0
    for u in users:
        if db_is_banned(u["id"]):
            continue
        try:
            await context.bot.send_message(u["id"], text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(random.uniform(0.05, 0.15))  # flood se bachne ke liye jitter
    await msg.reply_text(f"Broadcast done. Sent: {sent}, Failed: {failed}")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot-wide ban — OWNER-only."""
    if not await _need_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Use: /ban <user_id>")
        return
    try:
        uid = int(context.args[0])
        db_set_ban(uid, True)
        await update.effective_message.reply_text(f"Banned: {uid}")
    except ValueError:
        await update.effective_message.reply_text("Invalid user id.")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot-wide unban — OWNER-only."""
    if not await _need_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Use: /unban <user_id>")
        return
    try:
        uid = int(context.args[0])
        db_set_ban(uid, False)
        await update.effective_message.reply_text(f"Unbanned: {uid}")
    except ValueError:
        await update.effective_message.reply_text("Invalid user id.")


async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: channel/group ka message bot ko FORWARD karo -> numeric id milega
    (aur seedha save bhi ho jayega — config edit karne ki zaroorat nahi)."""
    if not await _need_owner(update):
        return
    msg = update.effective_message
    fwd = getattr(msg, "forward_from_chat", None)
    if fwd:
        b = match_block_by_title(fwd.title)
        saved = ""
        if b:
            db_set_chat_id(b["name"], fwd.id)
            saved = (
                f"\n\nSaved: \"{b['name']}\" -> {fwd.id}\n"
                "Ab force-join verify ise check karega."
            )
        await msg.reply_text(
            f"Chat: {fwd.title}\n"
            f"Numeric id: {fwd.id}\n"
            f"Type: {fwd.type}{saved}"
        )
    else:
        await msg.reply_text(
            "Us channel/group ka koi message yahan forward karo — main numeric id "
            "bata dunga aur khud save kar dunga.\n"
            "(Bot us chat me admin hona chahiye.)"
        )


async def cmd_setid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /setid <block name> <numeric id> — id yaad rakho (config edit nahi)."""
    if not await _need_owner(update):
        return
    args = context.args or []
    if len(args) < 2:
        names = "\n".join(f"  - {b['name']}" for b in LINK_BLOCKS)
        await update.effective_message.reply_text(
            "Use: /setid <block name> <numeric id>\n\nBlocks:\n" + names
        )
        return
    *name_parts, raw_id = args
    name = " ".join(name_parts)
    try:
        cid = int(raw_id)
    except ValueError:
        await update.effective_message.reply_text("Numeric id chahiye, jaise -1001234567890")
        return
    b = match_block_by_title(name)
    if not b:
        await update.effective_message.reply_text(
            f"Block '{name}' nahi mila. /setid bina kuch likhe bhejo — list aa jayegi."
        )
        return
    db_set_chat_id(b["name"], cid)
    await update.effective_message.reply_text(
        f"Saved: \"{b['name']}\" -> {cid}\nAb verify ise check karega."
    )


_RIGHTS = (
    "can_invite_users", "can_delete_messages", "can_restrict_members",
    "can_pin_messages", "can_manage_chat", "can_post_messages", "can_edit_messages",
    "can_promote_members",
)


def _err_hint(e) -> str:
    """Telegram error ko simple bhasha me."""
    s = str(e).lower()
    if "forbidden" in s:
        return "bot us chat me nahi hai (remove ho gaya?)"
    if "not found" in s:
        return "id galat hai"
    if "inaccessible" in s:
        return "bot admin nahi hai"
    if "not enough rights" in s or "chat_admin_required" in s:
        return "bot ke paas ye permission nahi hai"
    if "method is available only" in s or "chat_write_forbidden" in s:
        return "ye command is chat me nahi chalti"
    if "user not found" in s or "participant" in s:
        return "wo user is chat me nahi hai"
    return type(e).__name__


_SEP = "━━━━━━━━━━━━━━━━━"


async def _send_long(message, text, limit=3800):
    """Telegram ki 4096 limit — lamba text tukdon me bhejo, warna error aata hai."""
    if len(text) <= limit:
        await message.reply_text(text)
        return
    part = ""
    for line in text.split("\n"):
        while len(line) > limit:          # ek hi line bahut lambi -> tod do
            await message.reply_text(line[:limit])
            line = line[limit:]
        if part and len(part) + len(line) + 1 > limit:
            await message.reply_text(part)
            part = line
        else:
            part = f"{part}\n{line}" if part else line
    if part:
        await message.reply_text(part)


async def _known_chats_lines(context, me) -> list:
    """Bot jin chats me hai unki LIVE list (id, title, type, bot ka status,
    aur kaunse block se juda hai)."""
    known = db_get_chats()
    sc, bd = fonts.small_caps, fonts.bold
    out = [_SEP, f"{bd('CHATS BOT IS IN')} ({len(known)})", _SEP]
    if not known:
        out.append(f"{sc('abhi koi chat record nahi hui')}.")
        out.append("")
        out.append(f"{sc('kaise bhare')}:")
        out.append("  • us group me koi message bhejo (bot admin hai to milega)")
        out.append("  • channel me koi post karo")
        out.append("  • ya bot ko dobara admin banao — turant pakad lega")
        out.append("")
        return out
    learned = _learned_ids()
    bound = {}
    for blk in LINK_BLOCKS:
        c = blk.get("id") or learned.get(blk["name"])
        if c:
            bound[int(c)] = blk["name"]
    items = sorted(known.items(), key=lambda kv: kv[1].get("title", "").lower())
    for _k, c in items[:15]:
        cid = c.get("id")
        try:
            bm = await context.bot.get_chat_member(chat_id=cid, user_id=me.id)
            st = bm.status
        except Exception as e:
            st = f"FAIL - {_err_hint(e)}"
        out.append(f"{c.get('title') or '?'}  [{c.get('type') or '?'}]")
        out.append(f"   {sc('id')}    : {cid}")
        out.append(f"   {sc('bot')}   : {st}")
        out.append(f"   {sc('block')} : {bound.get(int(cid), '-') if cid else '-'}")
    if len(items) > 15:
        out.append(f"… +{len(items) - 15} more")
    out.append("")
    return out


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: bot jin chats me hai, unki poori list."""
    if not await _need_owner(update):
        return
    me = await _get_me(context)
    lines = await _known_chats_lines(context, me)
    await _send_long(update.effective_message, "\n".join(lines).strip())


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: poora status — har channel ALAG BLOCK me, spacing + font ke saath,
    aur neeche total kitne channel join karne hain wo summary."""
    if not await _need_owner(update):
        return
    me = await _get_me(context)
    learned = _learned_ids()
    uid = update.effective_user.id
    sc, bd = fonts.small_caps, fonts.bold
    sep = "━━━━━━━━━━━━━━━━━"
    req_blocks = [x for x in LINK_BLOCKS if x.get("require_join")]
    joined_count = 0
    verifiable = 0
    out = [bd("BOT STATUS"), ""]
    for blk in LINK_BLOCKS:
        emoji = blk.get("emoji", "")
        out.append(sep)
        out.append(f"{emoji} {bd(blk['name'])}".strip())
        out.append(sep)
        if not blk.get("require_join"):
            out.append(f"{sc('type')} : {sc('link only')}")
            out.append("")
            continue
        cid = blk.get("id") or learned.get(blk["name"])
        if not cid:
            out.append(f"{sc('id')}   : {bd('NOT FOUND')}")
            out.append(f"{sc('fix')}  : /getid  {sc('or')}  /setid")
            out.append("")
            continue
        out.append(f"{sc('id')}   : {cid}")   # plain rakha - copy karne me aasan
        try:
            bm = await context.bot.get_chat_member(chat_id=cid, user_id=me.id)
            st = bm.status
            out.append(f"{sc('bot')}  : {bd(st)}")
            if st in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                on = [r.replace("can_", "") for r in _RIGHTS if getattr(bm, r, None)]
                off = [r.replace("can_", "") for r in _RIGHTS if not getattr(bm, r, None)]
                out.append(f"{sc('on')}   : {', '.join(on) if on else '-'}")
                out.append(f"{sc('off')}  : {', '.join(off) if off else '-'}")
            else:
                out.append(f"{sc('note')} : {sc('bot admin nahi - verify nahi hoga')}")
        except Exception as e:
            out.append(f"{sc('bot')}  : {bd('FAIL')} - {_err_hint(e)}")
        try:
            um = await context.bot.get_chat_member(chat_id=cid, user_id=uid)
            is_in = um.status not in NOT_MEMBER_STATUSES
            out.append(f"{sc('you')}  : {bd('joined' if is_in else 'not joined')}")
            if is_in:
                joined_count += 1
            verifiable += 1
        except Exception as e:
            out.append(f"{sc('you')}  : {bd('?')} - {_err_hint(e)}")
        out.append("")
    # Bot jin chats me hai (chahe block se match kare ya na) — poora section.
    out.extend(await _known_chats_lines(context, me))

    need = required_count(verifiable)
    out.append(sep)
    out.append(f"{sc('total channels')} : {bd(str(len(req_blocks)))}")
    out.append(f"{sc('you joined')}     : {bd(str(joined_count))}")
    out.append(f"{sc('you need')}       : {bd(str(need))}")
    out.append(sep)
    await _send_long(update.effective_message, "\n".join(out))


# =====================================================================
#  CALLBACKS
# =====================================================================
_warned: set = set()


async def _warn_admin_unverifiable(context, blocks):
    """Bot in chats ko verify nahi kar paya -> admin ko ek baar batao."""
    names = sorted(b["name"] for b in blocks)
    key = tuple(names)
    if key in _warned:
        return
    _warned.add(key)
    text = (
        "⚠️ Gate verify nahi ho paya — in chats me bot ADMIN nahi hai (ya id galat):\n\n"
        + "\n".join(f"- {n}" for n in names)
        + "\n\nBot ko wahan admin banao, tabhi force-join lagega."
    )
    await _notify_owner(context.bot, text)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    data = q.data or ""

    # Join-request ka Approve button (sirf owner/admin dabaa sakta hai)
    if data.startswith("jr_ok:"):
        # Approve sirf ADMIN 1 (ADMIN_IDS[0]) ya OWNER kar sakta hai.
        approver = ADMIN_IDS[0] if ADMIN_IDS else 0
        if not (user.id == approver or is_owner(user.id)):
            await q.answer("Sirf Admin 1 (ya owner) approve kar sakta hai.", show_alert=True)
            return
        try:
            _, cid, uid = data.split(":")
            await context.bot.approve_chat_join_request(chat_id=int(cid), user_id=int(uid))
            await q.answer("Approved")
            try:
                await q.edit_message_text((q.message.text or "") + "\n\n✅ APPROVED")
            except Exception:
                pass
            try:
                await context.bot.send_message(
                    int(uid),
                    "✅ Aapki join request approve ho gayi. Ab bot me wapas jaake "
                    "neeche wala button dabao.",
                )
            except Exception:
                pass
        except Exception as e:
            log.warning("Join approve fail: %s", e)
            await q.answer(f"Approve fail: {e}", show_alert=True)
        return

    if data.startswith("promo_ok:"):
        approver = ADMIN_IDS[0] if ADMIN_IDS else 0
        if not (user.id == approver or is_owner(user.id)):
            await q.answer("Sirf Admin 1 (ya owner) approve kar sakta hai.", show_alert=True)
            return
        try:
            _, cid, tuid = data.split(":")
            await context.bot.promote_chat_member(
                int(cid), int(tuid),
                can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
                can_invite_users=True, can_pin_messages=True, can_change_info=True)
            await q.answer("Approved")
            try:
                await q.edit_message_text((q.message.text or "") + "\n\n✅ APPROVED")
            except Exception:
                pass
        except Exception as e:
            log.warning("Promote approve fail: %s", e)
            await q.answer(f"Fail: {_err_hint(e)}", show_alert=True)
        return

    if data.startswith("posttype:"):
        if not is_privileged(user.id):
            await q.answer()
            return
        t = data.split(":", 1)[1]
        _post_flow[user.id] = {"type": t, "step": 0, "data": {}}
        await q.answer()
        try:
            await q.edit_message_text(f"{t} — chalo shuru karein.")
        except Exception:
            pass
        await _post_ask(update.effective_chat.id, context, user.id)
        return

    if data.startswith("postgo:"):
        if not is_privileged(user.id):
            await q.answer()
            return
        fl = _post_flow.get(user.id)
        if not fl or "channel_id" not in fl:
            await q.answer("Flow khatam ho gaya — /post dobara bhejo.", show_alert=True)
            return
        await q.answer()
        if data.split(":", 1)[1] == "sched":
            fl["await_time"] = True
            await context.bot.send_message(
                update.effective_chat.id,
                "Kab post karni hai? Aise likho:\n2026-10-18 20:30   (ya sirf 20:30 — IST)")
            return
        code = _new_code()
        data2 = dict(fl["data"])
        data2.update({"code": code, "type": fl["type"], "channel_id": fl["channel_id"],
                      "channel_url": fl["channel_url"], "posted": False, "created": _now()})
        db_post_save(code, data2)
        ok = await _send_post(context, code)
        _post_flow.pop(user.id, None)
        await context.bot.send_message(
            update.effective_chat.id,
            ("Post ho gaya ✅\n" if ok else "Post bhejne me fail hua (bot us channel me admin hai?)\n")
            + f"Link: {_deep_link(context, code)}")
        return

    if data != "check_join":
        await q.answer()
        return

    # Anti-spam: ek user 2 second me sirf ek baar check kar sakta hai
    # (warna Check dabate rehne se getChatMember flood ho jayega).
    now = time.time()
    if now - _check_cd.get(user.id, 0) < 2:
        await q.answer("Thoda ruko, phir try karo.")
        return
    _check_cd[user.id] = now
    _prune(_check_cd, 5000)

    # Strict: cache ignore karke fresh check — join karke turant chhod diya
    # ho, ya cache purana ho, to bhi pakda jaye.
    joined, missing, unknown, need = await join_status(context, user.id, force=True)

    if need > 0 and len(joined) < need:
        # Bina join kiye Check dabaya -> naya message: caption + links + Check.
        await q.answer()
        text = JOIN_PROMPT
        if missing:
            text += "\n\n" + ", ".join(b["name"] for b in missing)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=link_keyboard(extra_rows=[
                [InlineKeyboardButton(GATE_BUTTON_TEXT, callback_data="check_join")],
            ]),
        )
        return

    # Pass. (Ek hi baar answer karo — dobara answer karne par Telegram error deta hai.)
    await q.answer()
    _passed.add(user.id)
    _prune(_passed, 10000)
    if unknown and not joined and not missing:
        await _warn_admin_unverifiable(context, unknown)
    try:
        await q.message.delete()
    except Exception as e:
        log.warning("Gate delete fail: %s", e)
        try:
            await q.edit_message_text("Verified. Ab seedha apna sawaal likho.")
        except Exception:
            pass


# =====================================================================
#  WELCOME  (naye member par)
# =====================================================================
async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Service message = bot ko HAMESHA milti hai (chahe privacy on ho). Isliye
    # jab bhi koi group me join kare, hum us group ki id seekh lete hain.
    learn_chat(update)
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        if WELCOME_TEMPLATE:
            caption = WELCOME_TEMPLATE.format(
                first_name=member.first_name or "Member",
                group_name=msg.chat.title or "the group",
                full_name=member.full_name or member.first_name or "Member",
                user_id=member.id,
                join_date=_today(),
                bot_brand=BOT_BRAND,
            )
        else:
            caption = build_welcome(member, msg.chat.title)
        try:
            with open(WELCOME_IMAGE, "rb") as photo:
                await msg.reply_photo(photo=photo, caption=caption)
        except FileNotFoundError:
            await msg.reply_text(caption)
        except Exception as e:
            log.warning("Welcome send fail: %s", e)


# =====================================================================
#  JOIN REQUESTS  (owner approval, auto nahi)
# =====================================================================
async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join request aaya -> OWNER ko details + Approve button bhejo.
    AUTO-approve NAHI hota — owner khud approve kare tabhi banda join karta hai."""
    req = update.chat_join_request
    chat, usr = req.chat, req.from_user
    log.info("Join request: %s (id=%s) from %s (%s)",
             chat.title, chat.id, usr.id, usr.full_name)
    # Chat ki asli numeric id SILENTLY seekh lo (koi DM spam nahi).
    b = match_block_by_title(chat.title) if chat.title else None
    if b:
        db_set_chat_id(b["name"], chat.id)

    # Join request OWNER ko nahi — ADMIN 1 (ADMIN_IDS[0]) ko jaati hai.
    target = ADMIN_IDS[0] if ADMIN_IDS else (OWNER_ID or 0)
    if not target:
        return
    sc, bd = fonts.small_caps, fonts.bold
    uname = ("@" + usr.username) if usr.username else "-"
    lines = [
        bd("NEW JOIN REQUEST"),
        "",
        f"{sc('chat')}     : {chat.title}",
        f"{sc('chat id')}  : {chat.id}",
        f"{sc('name')}     : {usr.full_name}",
        f"{sc('user id')}  : {usr.id}",
        f"{sc('username')} : {uname}",
    ]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"jr_ok:{chat.id}:{usr.id}"),
    ]])
    try:
        await context.bot.send_message(target, "\n".join(lines), reply_markup=kb)
    except Exception as e:
        log.warning("Owner DM fail: %s", e)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot ko jab kisi channel/group me add ya admin banaya jaye -> Telegram
    turant us chat ki id + title bhejta hai. Yahi sabse aasan tareeka hai."""
    cm = update.my_chat_member
    if not cm or not cm.chat:
        return
    chat = cm.chat
    # PRIVATE chat = kisi ne bot ko /start kiya. Ye notify karne layak NAHI —
    # warna har naye user par owner ko DM spam hota hai.
    if chat.type == ChatType.PRIVATE:
        return
    status = getattr(cm.new_chat_member, "status", "")
    log.info("my_chat_member: %s (id=%s) status=%s", chat.title, chat.id, status)
    if status in (ChatMember.LEFT, ChatMember.BANNED):
        db_upsert_chat(chat.id, chat.title, str(chat.type or ""), status)
        return  # bot nikaal diya gaya

    # Poori chat detail + status yaad rakho (title match kare to block bhi bind).
    learn_chat(update, status=status, force=True)

    # Sirf tab notify karo jab status badla ho (same event baar baar na aaye).
    if _chat_status.get(chat.id) == status:
        return
    _chat_status[chat.id] = status
    _prune(_chat_status, 500)

    b = match_block_by_title(chat.title) if chat.title else None
    lines = [f"Chat: {chat.title}", f"ID: {chat.id}", f"Bot status: {status}"]
    if b:
        lines.append(f"Block match: {b['name']} — id SAVE ho gayi")
    else:
        lines.append("Title kisi block se match nahi hua — /setid <block name> <id>")
    if status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        lines.append("⚠️ Bot ADMIN nahi — is chat ka verify nahi hoga")
    await _notify_owner(context.bot, "\n".join(lines))


# =====================================================================
#  AI CHAT
# =====================================================================
def _strip_mention(text: str, username: str) -> str:
    """Mention hata do — case ki parwah nahi (@Bot, @BOT, @bot sab)."""
    if not text or not username:
        return (text or "").strip()
    return re.sub(rf"@{re.escape(username)}", " ", text, flags=re.IGNORECASE).strip()


# chat_id -> last DB write time (har message par DB write na ho)
_chat_seen: dict = {}


async def _learn_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sirf chat yaad rakho, kuch reply na karo (group 1 ka catch-all)."""
    learn_chat(update)


def learn_chat(update, status=None, force=False):
    """Bot jis chat me hai, uski POORI detail yaad rakho — id, title, type, status.
    Pehle sirf wahi chats yaad hoti thi jinka title kisi block se match kare,
    isliye /diag me baaki channels 'NOT FOUND' dikhate the — ab har chat
    record hoti hai, chahe title match kare ya na kare.
    Title match kare to block ki id bhi apne aap set kar dete hain."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        return
    cid = chat.id
    now = time.time()
    if not force and now - _chat_seen.get(cid, 0) < 3600:
        return   # 1 ghante me ek hi baar DB write
    _chat_seen[cid] = now
    _prune(_chat_seen, 5000)
    title = chat.title or ""
    db_upsert_chat(cid, title, str(chat.type or ""), status)
    b = match_block_by_title(title) if title else None
    if b and _learned_ids().get(b["name"]) != cid:
        db_set_chat_id(b["name"], cid)
        log.info("Block '%s' -> chat id %s (title match)", b["name"], cid)


async def on_chat_seen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Channel post par bhi chat yaad rakho (bot us channel me admin ho to)."""
    learn_chat(update)


async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private chat mein AI bina mention chalta hai — seedha sawaal likho."""
    user = update.effective_user
    db_add_user(user)
    if db_is_banned(user.id):
        return
    if user.id in _post_flow:
        await _post_step(update, context)
        return
    if await needs_join(context, user.id):
        await send_join_gate(update, context)
        return
    if not _ai_allowed(user.id):
        await update.effective_message.reply_text(
            f"{fonts.small_caps('Slow down')} — {fonts.small_caps('bahut jaldi messages, thodi der baad try karo')}."
        )
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    await update.effective_message.reply_text(
        await ask_ai(update.effective_message.text, where="a private chat",
                     mem_key=f"u{user.id}",
                     note=_owner_note(user.id))
    )


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group/channel mein AI SIRF @mention par chalta hai (private mein bina
    mention). Membership bhi yahan check hoti hai (warna group se gate bypass
    ho jata)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    learn_chat(update)
    me = await _get_me(context)
    mentioned = False
    for e in (msg.entities or []):
        if e.type == "mention":
            if msg.text[e.offset:e.offset + e.length].lower() == f"@{me.username.lower()}":
                mentioned = True
        elif e.type == "text_mention" and e.user and e.user.id == me.id:
            mentioned = True
    if not mentioned:
        return
    if await needs_join(context, update.effective_user.id):
        await msg.reply_text(
            f"{fonts.small_caps('Please join our channels first')} — "
            "tap the buttons below, then ask me again.",
            reply_markup=link_keyboard(),
        )
        return
    if not _ai_allowed(update.effective_user.id):
        return   # group me chupchaap skip (group spam na ho)
    prompt = _strip_mention(msg.text, me.username) or "The user mentioned you. Greet them warmly."
    title = _clean_title(msg.chat.title)
    where = f'the Telegram group "{title}"' if title else "a Telegram group"
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    await msg.reply_text(await ask_ai(
        prompt, where=where,
        mem_key=f"g{msg.chat.id}:u{update.effective_user.id}",
        note=_owner_note(update.effective_user.id),
    ))


# =====================================================================
#  ERROR HANDLER
# =====================================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Update error: %s", context.error)


# =====================================================================
#  MEMBER COMMANDS
# =====================================================================
async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(RULES_TEXT)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kisi message par reply karke /report — admins ko DM jaata hai."""
    msg = update.effective_message
    r = msg.reply_to_message
    if not r:
        await msg.reply_text("Jis message ki report karni hai, us par reply karke /report bhejo.")
        return
    who = update.effective_user
    sc, bd = fonts.small_caps, fonts.bold
    text = (
        f"🚩 {bd('REPORT')}\n\n"
        f"{sc('from')} : {who.full_name} (@{who.username or '-'})\n"
        f"{sc('id')}   : {who.id}\n"
        f"{sc('chat')} : {msg.chat.title or 'private'} [{msg.chat.id}]\n"
        f"{sc('msg')}  : {(r.text or r.caption or '[media]')[:300]}"
    )
    targets = list(ADMIN_IDS) or ([OWNER_ID] if OWNER_ID else [])
    sent = 0
    for a in targets:
        try:
            await context.bot.send_message(a, text)
            sent += 1
        except Exception:
            pass
    await msg.reply_text("Report bhej di gayi. Dhanyavaad."
                         if sent else "Report abhi kisi admin tak nahi pahunchi.")


# =====================================================================
#  ADMIN / MODERATION COMMANDS  (bot ki chat permissions use karte hain)
# =====================================================================
#   (permission key, dikhne wala naam, iska matlab, kaunsi command)
_PERM_MEANING = [
    ("can_change_info",      "Change Group Info",    "group ka naam / photo badal sakta hai",     "/settitle"),
    ("can_invite_users",     "Invite Users via Link", "invite link bana kar log jod sakta hai",   "/invitelink"),
    ("can_restrict_members", "Ban Users",            "kisi ko group se ban / kick kar sakta hai", "/kick  /unkick"),
    ("can_delete_messages",  "Delete Messages",      "kisi ka bhi message delete kar sakta hai",  "/del"),
    ("can_promote_members",  "Add New Admins",       "naya admin bana sakta hai",                 "/promote"),
    ("can_pin_messages",     "Pin Messages",         "message pin kar sakta hai",                 "/pin"),
    ("can_manage_chat",      "Manage Chat",          "chat ko manage kar sakta hai",              ""),
    ("can_post_messages",    "Post Messages",        "channel me post kar sakta hai",             ""),
    ("can_edit_messages",    "Edit Messages",        "channel ka post edit kar sakta hai",        ""),
]


def _is_group(update) -> bool:
    return update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def _target_uid(update, context):
    """Reply se ya pehle argument se user id nikaalo."""
    r = update.effective_message.reply_to_message
    if r and r.from_user:
        return r.from_user.id
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    return None


async def _need_owner(update):
    """Sirf owner — warna saaf message."""
    if is_owner(update.effective_user.id):
        return True
    if is_privileged(update.effective_user.id):
        await update.effective_message.reply_text("Sirf owner ye kar sakta hai.")
    return False


async def _need(update, group_only=True):
    """Admin check + chat type check. True = aage badho."""
    if not is_privileged(update.effective_user.id):
        return False
    if group_only and not _is_group(update):
        await update.effective_message.reply_text("Ye command sirf group me chalti hai.")
        return False
    return True


async def cmd_settitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group ka naam badlo — 'Change Group Info' permission chahiye."""
    if not await _need(update):
        return
    title = " ".join(context.args or []).strip()
    if not title:
        await update.effective_message.reply_text("Use: /settitle <naya naam>")
        return
    try:
        await context.bot.set_chat_title(update.effective_chat.id, title[:128])
        await update.effective_message.reply_text(f"Naam badal diya: {title}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Fail: {_err_hint(e)}\n(Bot ko 'Change Group Info' permission chahiye.)")


async def cmd_invitelink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invite link banao — 'Invite Users via Link' permission chahiye."""
    if not is_privileged(update.effective_user.id):
        return
    if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        await update.effective_message.reply_text("Ye command sirf group/channel me chalti hai.")
        return
    try:
        link = await context.bot.create_chat_invite_link(update.effective_chat.id, name=BOT_BRAND)
        await update.effective_message.reply_text(
            f"{fonts.small_caps('invite link')}:\n{link.invite_link}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Fail: {_err_hint(e)}\n(Bot ko 'Invite Users' permission chahiye.)")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ko group se ban/kick — 'Ban Users' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /kick <user_id>  (ya uske message par reply karke /kick)")
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, uid)
        await update.effective_message.reply_text(f"Kick kar diya: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Fail: {_err_hint(e)}\n(Bot ko 'Ban Users' permission chahiye.)")


async def cmd_unkick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban hatana — 'Ban Users' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /unkick <user_id>  (ya uske message par reply karke /unkick)")
        return
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
        await update.effective_message.reply_text(f"Ban hata diya: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Fail: {_err_hint(e)}\n(Bot ko 'Ban Users' permission chahiye.)")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply wala message delete — 'Delete Messages' permission chahiye."""
    if not await _need(update):
        return
    msg = update.effective_message
    r = msg.reply_to_message
    if not r:
        await msg.reply_text("Jis message ko delete karna hai, us par reply karke /del bhejo.")
        return
    try:
        await context.bot.delete_message(r.chat_id, r.message_id)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        await msg.reply_text(f"Fail: {_err_hint(e)}\n(Bot ko 'Delete Messages' permission chahiye.)")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply wala message pin — 'Pin Messages' permission chahiye."""
    if not await _need(update):
        return
    msg = update.effective_message
    r = msg.reply_to_message
    if not r:
        await msg.reply_text("Jis message ko pin karna hai, us par reply karke /pin bhejo.")
        return
    try:
        await context.bot.pin_chat_message(r.chat_id, r.message_id, disable_notification=True)
        await msg.reply_text("Pin kar diya.")
    except Exception as e:
        await msg.reply_text(f"Fail: {_err_hint(e)}\n(Bot ko 'Pin Messages' permission chahiye.)")


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Naya admin banao (group me) — 'Add New Admins' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /promote <user_id>  (ya uske message par reply karke /promote)")
        return
    chat = update.effective_chat
    # Admin ka /promote -> pehle ADMIN 1 ki approval. Owner seedha kar sakta hai.
    if not is_owner(update.effective_user.id):
        approver = ADMIN_IDS[0] if ADMIN_IDS else 0
        if approver and approver != update.effective_user.id:
            sc, bd = fonts.small_caps, fonts.bold
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                "✅ Approve", callback_data=f"promo_ok:{chat.id}:{uid}")]])
            try:
                await context.bot.send_message(
                    approver,
                    f"{bd('NEW ADMIN REQUEST')}\n\n"
                    f"{sc('chat')} : {chat.title or chat.id}\n"
                    f"{sc('by')}   : {update.effective_user.full_name}\n"
                    f"{sc('user')} : {uid}\n\n"
                    f"{sc('approve karne par promote chala jayega')}",
                    reply_markup=kb)
                await update.effective_message.reply_text(
                    "Approval ke liye Admin 1 ko bhej diya.")
            except Exception as e:
                await update.effective_message.reply_text(
                    f"Approval bhejne me fail: {_err_hint(e)}")
            return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, uid,
            can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
            can_invite_users=True, can_pin_messages=True, can_change_info=True,
        )
        await update.effective_message.reply_text(f"Admin bana diya: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Fail: {_err_hint(e)}\n(Bot ko 'Add New Admins' permission chahiye.)")


async def cmd_perms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Is chat me bot ke paas kaunsi permission hai — matlab + command ke saath."""
    if not await _need_owner(update):
        return
    if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        await update.effective_message.reply_text("Ye command sirf group/channel me chalti hai.")
        return
    chat = update.effective_chat
    me = await _get_me(context)
    sc, bd = fonts.small_caps, fonts.bold
    try:
        m = await context.bot.get_chat_member(chat_id=chat.id, user_id=me.id)
    except Exception as e:
        await update.effective_message.reply_text(f"Check fail: {_err_hint(e)}")
        return
    out = [bd("BOT PERMISSIONS"), "",
           f"{sc('chat')} : {chat.title or 'private'}",
           f"{sc('role')} : {bd(m.status)}", ""]
    if m.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        out.append("Bot is chat me ADMIN nahi hai — koi permission nahi.")
    else:
        for key, label, meaning, cmd in _PERM_MEANING:
            have = bool(getattr(m, key, False))
            out.append(f"{'✅' if have else '❌'} {bd(label)}")
            out.append(f"   {meaning}")
            if have and cmd:
                out.append(f"   {sc('cmd')}: {cmd}")
    await _send_long(update.effective_message, "\n".join(out))


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ai <sawaal> — AI se seedha jawab (gate + rate limit ke saath)."""
    q = " ".join(context.args or []).strip()
    if not q:
        await update.effective_message.reply_text("Use: /ai <sawaal>")
        return
    uid = update.effective_user.id
    if await needs_join(context, uid):
        await send_join_gate(update, context)
        return
    if not _ai_allowed(uid):
        await update.effective_message.reply_text(
            f"{fonts.small_caps('Slow down')} — {fonts.small_caps('thodi der baad try karo')}.")
        return
    chat = update.effective_chat
    priv = chat.type == ChatType.PRIVATE
    mem = f"u{uid}" if priv else f"g{chat.id}:u{uid}"
    try:
        await context.bot.send_chat_action(chat.id, "typing")
    except Exception:
        pass
    await update.effective_message.reply_text(
        await ask_ai(q, where="a private chat" if priv else "a Telegram group",
                     mem_key=mem, note=_owner_note(uid)))


async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner -> owner + admins. Admin -> sirf admins (owner ki id nahi dikhti)."""
    if not is_privileged(update.effective_user.id):
        return
    viewer = update.effective_user.id
    sc, bd = fonts.small_caps, fonts.bold
    out = [bd("TEAM"), ""]
    if is_owner(viewer) and OWNER_ID:
        out.append(f"👑 {bd('Owner')} : {OWNER_NAME or 'Owner'}")
        out.append(f"   {sc('id')} : {OWNER_ID}")
        out.append("")
    shown = 0
    for a in ADMIN_IDS:
        if not is_owner(viewer) and a == OWNER_ID:
            continue          # admin ko owner ki id nahi dikhani
        out.append(f"🛡 {bd(admin_label(a))}")
        out.append(f"   {sc('id')} : {a}")
        shown += 1
    if not shown:
        out.append("Koi admin set nahi hai — Render me ADMIN_IDS daalo.")
    await _send_long(update.effective_message, "\n".join(out))


# =====================================================================
#  /POST  —  post builder (Owner/Admin) + deep-link tracking
# =====================================================================
#   Flow:  /post -> type chuno (Anime/Movie/Series) -> AI ek-ek sawaal
#          poochhta hai (POST_FIELDS) -> channel link/number -> abhi ya
#          schedule -> channel par post (button = bot ka deep link).
#   User jab wo button dabata hai -> bot chat khulta hai -> bot ko pata
#   chalta hai kaunsa post + kaunsa channel -> 1-2 line text + "Request
#   to Join" button (us channel ka link).
# ---------------------------------------------------------------------
_post_flow: dict = {}      # user_id -> {"type","step","data","channel_id","channel_url","await_time"}


def db_post_save(code, data):
    with _db_lock:
        d = _db_load()
        d.setdefault("posts", {})[code] = data
        _db_save(d)


def db_post_get(code):
    with _db_lock:
        return _db_load().get("posts", {}).get(code)


def db_post_all():
    with _db_lock:
        return dict(_db_load().get("posts", {}))


def db_post_update(code, **kw):
    with _db_lock:
        d = _db_load()
        p = d.setdefault("posts", {}).get(code)
        if not p:
            return False
        p.update(kw)
        _db_save(d)
        return True


def _new_code() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:10]


def _deep_link(context, code) -> str:
    return f"https://t.me/{context.bot.username}?start=p{code}"


def _post_text(p) -> str:
    sc, bd = fonts.small_caps, fonts.bold
    name = p.get("Name") or "-"
    out = [f"{bd(str(p.get('type', 'Post')))}  —  {bd(name)}"]
    for k, v in p.items():
        if k in ("Name", "code", "type", "channel_id", "channel_url", "posted",
                 "posted_at", "created", "post_at") or not v:
            continue
        out.append(f"{sc(str(k))}: {bd(str(v))}")
    return "\n".join(out)


def _parse_when(txt):
    """'YYYY-MM-DD HH:MM' ya 'HH:MM' (IST) -> aware datetime (IST)."""
    txt = (txt or "").strip()
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$", txt):
            dt = datetime.strptime(txt, "%Y-%m-%d %H:%M").replace(tzinfo=ist)
        elif re.match(r"^\d{1,2}:\d{2}$", txt):
            h, m = (int(x) for x in txt.split(":"))
            dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
        else:
            return None
        return dt if dt > now else None
    except Exception:
        return None


async def _channel_join_url(context, chat_id):
    """Us channel ka aisa link jise user join kar sake (public naam ya invite link)."""
    try:
        ch = await context.bot.get_chat(chat_id)
        if getattr(ch, "username", None):
            return f"https://t.me/{ch.username}"
    except Exception:
        pass
    try:
        link = await context.bot.create_chat_invite_link(chat_id, name=BOT_BRAND[:32])
        return link.invite_link
    except Exception:
        return None


async def _known_channels(context, me):
    """Bot jin chats me ADMIN hai (ya channel hai) — list."""
    out = []
    for _k, c in db_get_chats().items():
        try:
            m = await context.bot.get_chat_member(chat_id=c["id"], user_id=me.id)
            if m.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                out.append(c)
        except Exception:
            pass
    return sorted(out, key=lambda c: c.get("title", "").lower())


async def _resolve_channel(update, context, txt):
    """Number (known list) ya t.me/<username> se channel nikalo."""
    txt = (txt or "").strip()
    if txt.isdigit():
        me = await _get_me(context)
        lst = await _known_channels(context, me)
        n = int(txt) - 1
        if 0 <= n < len(lst):
            c = lst[n]
            return c["id"], await _channel_join_url(context, c["id"]), c.get("title", "")
        return None, None, None
    m = re.search(r"t\.me/(?!\+)([A-Za-z0-9_]{4,})", txt)
    if m:
        try:
            ch = await context.bot.get_chat("@" + m.group(1))
            url = f"https://t.me/{m.group(1)}"
            return ch.id, url, getattr(ch, "title", m.group(1))
        except Exception:
            return None, None, None
    return None, None, None


async def _post_ask(chat_id, context, uid):
    """Flow ka agla sawaal bhejo."""
    fl = _post_flow.get(uid)
    if not fl:
        return
    i = fl["step"]
    if i < len(POST_FIELDS):
        f = POST_FIELDS[i]
        await context.bot.send_message(
            chat_id, f"{fonts.bold(f)} bhejo  ({fonts.small_caps('skip')} likho to chhod denge)")
        return
    me = await _get_me(context)
    lst = await _known_channels(context, me)
    lines = ["Ab channel bhejo — jahan post karni hai:",
             "  • public link: https://t.me/ChannelName",
             "  • ya neeche wale number me se ek:"]
    for n, c in enumerate(lst[:15], start=1):
        lines.append(f"   {n}) {c.get('title') or '?'}  [{c['id']}]")
    if not lst:
        lines.append("   (abhi koi channel yaad nahi — pehle bot ko us channel me admin banao)")
    await context.bot.send_message(chat_id, "\n".join(lines))


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post builder — sirf Owner/Admin."""
    if not is_privileged(update.effective_user.id):
        return
    _post_flow.pop(update.effective_user.id, None)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=f"posttype:{t}")] for t in POST_TYPES])
    await update.effective_message.reply_text(
        f"{fonts.bold('Kya post karna hai?')}", reply_markup=kb)


async def _post_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Flow ke dauraan user ka text — jawab maan lo."""
    uid = update.effective_user.id
    fl = _post_flow.get(uid)
    if not fl:
        return
    txt = (update.effective_message.text or "").strip()

    if fl.get("await_time"):
        dt = _parse_when(txt)
        if not dt:
            await update.effective_message.reply_text(
                "Samajh nahi aaya. Aise likho:  2026-10-18 20:30   (ya sirf 20:30 — IST)")
            return
        fl.pop("await_time", None)
        code = _new_code()
        data = dict(fl["data"])
        data.update({"code": code, "type": fl["type"], "channel_id": fl["channel_id"],
                     "channel_url": fl["channel_url"], "posted": False,
                     "created": _now(), "post_at": dt.strftime("%Y-%m-%d %H:%M")})
        db_post_save(code, data)
        jq = getattr(context.application, "job_queue", None)
        if jq is None:
            await update.effective_message.reply_text(
                "Scheduling available nahi hai (job-queue install nahi). Abhi post kar raha hoon.")
            await _send_post(context, code)
        else:
            jq.run_once(_job_post, when=dt, data=code, name=f"post:{code}")
            await update.effective_message.reply_text(
                f"Schedule ho gaya: {dt.strftime('%Y-%m-%d %H:%M')} IST\nCode: {code}")
        _post_flow.pop(uid, None)
        return

    i = fl["step"]
    if i < len(POST_FIELDS):
        fl["data"][POST_FIELDS[i]] = "" if txt.lower() == "skip" else txt
        fl["step"] += 1
        await _post_ask(update.effective_chat.id, context, uid)
        return

    cid, url, title = await _resolve_channel(update, context, txt)
    if not cid:
        await update.effective_message.reply_text(
            "Ye channel nahi mila. Public link (https://t.me/xxxx) ya list ka number bhejo.")
        return
    fl["channel_id"], fl["channel_url"] = cid, url
    fl["step"] += 1
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Abhi post karo", callback_data="postgo:now"),
        InlineKeyboardButton("🕒 Schedule", callback_data="postgo:sched"),
    ]])
    preview = {"type": fl["type"], **fl["data"]}
    await update.effective_message.reply_text(
        f"{fonts.bold('PREVIEW')}\n\n{_post_text(preview)}"
        f"\n\n{fonts.small_caps('channel')}: {title} [{cid}]",
        reply_markup=kb)


async def _send_post(context, code):
    p = db_post_get(code)
    if not p:
        return False
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        POST_BUTTON_TEXT, url=_deep_link(context, code))]])
    try:
        await context.bot.send_message(p["channel_id"], _post_text(p), reply_markup=kb)
    except Exception as e:
        log.warning("Post bhejne me fail (%s): %s", p.get("channel_id"), e)
        return False
    db_post_update(code, posted=True, posted_at=_now())
    return True


async def _job_post(context: ContextTypes.DEFAULT_TYPE):
    code = context.job.data
    ok = await _send_post(context, code)
    await _notify_owner(context.bot, f"Scheduled post {'bhej di' if ok else 'FAIL'}: {code}")


async def _rearm_posts(app):
    """Restart ke baad pending scheduled posts dobara set karo."""
    jq = getattr(app, "job_queue", None)
    if jq is None:
        return
    for code, p in db_post_all().items():
        if p.get("post_at") and not p.get("posted"):
            dt = _parse_when(p["post_at"])
            if dt:
                jq.run_once(_job_post, when=dt, data=code, name=f"post:{code}")
                log.info("Re-armed scheduled post %s at %s", code, dt)


async def _post_welcome(update, context, p):
    """Deep link se aaya user — 1-2 line text + 'Request to Join' button."""
    name = p.get("Name") or "this"
    prompt = (
        f"Ek banda {p.get('type', 'post').lower()} '{name}' dekhne aaya hai. "
        "Uske liye 1-2 line ka chhota warm welcome likho (uski bhasha me). "
        "Koi markdown nahi, emoji kam se kam."
    )
    line = ""
    try:
        line = await ask_ai(prompt, mem_key=None)
    except Exception:
        line = ""
    if not line or line == busy_message():
        line = f"{name} ke liye ye channel join karo."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        JOIN_BUTTON_TEXT, url=p.get("channel_url") or "https://t.me")]])
    await update.effective_message.reply_text(
        f"{line}\n\n{fonts.bold(name)}", reply_markup=kb)


async def cmd_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: bane hue posts ki list."""
    if not is_privileged(update.effective_user.id):
        return
    allp = db_post_all()
    if not allp:
        await update.effective_message.reply_text("Abhi koi post nahi bani. /post se banao.")
        return
    sc, bd = fonts.small_caps, fonts.bold
    out = [bd("POSTS"), ""]
    for code, p in sorted(allp.items(), key=lambda kv: kv[1].get("created", ""), reverse=True)[:20]:
        st = "✅ posted" if p.get("posted") else ("🕒 " + str(p.get("post_at") or "draft"))
        out.append(f"{bd(p.get('Name') or '-')}  [{p.get('type')}]")
        out.append(f"   {sc('code')} : {code}   {st}")
        out.append(f"   {sc('link')} : {_deep_link(context, code)}")
    await _send_long(update.effective_message, "\n".join(out))


# =====================================================================
#  BUILD APPLICATION
# =====================================================================
def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())  # flood-limit se bachne ke liye
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("ref", cmd_ref))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("getid", cmd_getid))
    app.add_handler(CommandHandler("setid", cmd_setid))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("perms", cmd_perms))
    app.add_handler(CommandHandler("admins", cmd_admins))
    app.add_handler(CommandHandler("settitle", cmd_settitle))
    app.add_handler(CommandHandler("invitelink", cmd_invitelink))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("unkick", cmd_unkick))
    app.add_handler(CommandHandler("del", cmd_del))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("promote", cmd_promote))
    app.add_handler(CommandHandler("ai", cmd_ai))
    app.add_handler(CommandHandler("list_admins", cmd_admins))
    app.add_handler(CommandHandler(POST_COMMAND, cmd_post))
    app.add_handler(CommandHandler("posts", cmd_posts))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_chat_seen))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, on_group_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_private_message))

    # Group 1: jo bhi group update group 0 me handle na ho (photo, sticker,
    # command, koi bhi cheez) — sirf chat yaad rakhne ke liye. Isse bot ko
    # us group ka pata chal jata hai, chahe koi bhi tarah ka message aaye.
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _learn_only), group=1)

    app.add_error_handler(on_error)
    return app


# =====================================================================
#  RUN  —  WEBHOOK (Render)  ya  POLLING (local)
# =====================================================================
def run_webhook(app: Application):
    from contextlib import asynccontextmanager
    from fastapi import FastAPI, Request, Response
    import uvicorn

    # RANDOM per-boot secret. BOT_TOKEN se derive NAHI karte — warna token
    # leak hone par webhook bhi forge ho sakta hai. Har boot par set_webhook
    # dobara hota hai, isliye random secret theek chalta hai.
    secret = WEBHOOK_SECRET or secrets.token_urlsafe(32)
    webhook_path = f"/tg/{secret}"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await app.initialize()
        await verify_blocks_on_startup(app)
        await _rearm_posts(app)
        await app.bot.set_webhook(
            url=f"{WEBHOOK_URL}{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            secret_token=secret,
        )
        await app.start()
        log.info("Webhook set: %s/tg/*** (path chhupa diya)", WEBHOOK_URL)
        yield
        await app.stop()
        await app.shutdown()

    api = FastAPI(lifespan=lifespan)

    @api.post(webhook_path)
    async def telegram_webhook(request: Request):
        # Telegram har request me ye header bhejta hai — match na ho to reject.
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != secret:
            return Response(status_code=403)
        update = Update.de_json(await request.json(), app.bot)
        await app.process_update(update)
        return Response(status_code=200)

    @api.api_route("/", methods=["GET", "HEAD"])
    @api.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        return {"status": "ok", "bot": "running"}

    # access_log=False -> webhook ka secret path logs me print nahi hota.
    uvicorn.run(api, host="0.0.0.0", port=PORT, log_level="info", access_log=False)


async def _polling_post_init(app: Application):
    await verify_blocks_on_startup(app)
    await _rearm_posts(app)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("BOT_TOKEN set nahi hai. config.py / Environment Variables check karo.")
    app = build_application()
    if WEBHOOK_URL:
        log.info("Starting in WEBHOOK mode (Render).")
        run_webhook(app)
    else:
        log.info("Starting in POLLING mode (local).")
        app.post_init = _polling_post_init
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
