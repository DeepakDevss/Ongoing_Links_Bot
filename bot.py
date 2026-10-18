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
    1) Force-join gate (config.py ke LINK_BLOCKS se) + join requests Admin 1 ko
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

import telegram
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
    AI_PROMPT_FROM_ENV,
    POST_COMMAND,
    POST_CHANNELS,
    POST_ASK_IMAGE,
    POST_CREDIT_TEXT,
    POST_CREDIT_URL,
    POST_EP_EMOJI,
    POST_EP_LINE,
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
            "stats": {"total_started": 0}, "chat_ids": {}, "chats": {},
            "requests": {}}


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
    """JSON likho. Disk full / permission error par CRASH na ho — log karo."""
    try:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        tmp = DB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())   # power-loss par aadhi likhi file na bane
        os.replace(tmp, DB_PATH)
    except OSError as e:
        log.error("DB save fail (disk full / permission?): %s", e)
    except Exception as e:
        log.error("DB save fail: %s", e)


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


_requests_cache = None


def _requests():
    """{user_id: [chat_id, ...]} — jinhone join REQUEST bhej di hai."""
    global _requests_cache
    if _requests_cache is None:
        with _db_lock:
            _requests_cache = dict(_db_load().get("requests", {}))
    return _requests_cache


def db_add_request(user_id, chat_id):
    global _requests_cache
    with _db_lock:
        d = _db_load()
        r = d.setdefault("requests", {})
        lst = r.setdefault(str(user_id), [])
        if int(chat_id) not in lst:
            lst.append(int(chat_id))
        for k in list(r.keys())[: max(0, len(r) - 5000)]:
            r.pop(k, None)                      # bound rakho
        _db_save(d)
        _requests_cache = dict(r)


def has_request(user_id, chat_id) -> bool:
    """User ne is chat me join request bhej di hai? (join na hua ho tab bhi)."""
    return int(chat_id) in _requests().get(str(user_id), [])


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
        log.warning("Thinking config disabled — the model rejected it.")


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
    event loop list badal de to 'list changed size' error aa sakta hai.
    LRU: padhne par key ko dict ke END me le jao, taaki active user
    _prune() se evict na ho (pehle ye FIFO tha)."""
    h = _history.get(mem_key)
    if h is not None:
        _history.pop(mem_key, None)
        _history[mem_key] = h
    return list(h or [])


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
    """Ek user 60 second me max _AI_MAX AI calls kar sakta hai.
    LRU: access par key end me — active user evict na ho."""
    now = time.time()
    hits = _ai_hits.pop(uid, None)
    if hits is None:
        hits = []
    _ai_hits[uid] = hits
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
        if r is False and has_request(user_id, chat["id"]):
            r = True      # join REQUEST bhej di -> pass (join hona zaroori nahi)
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


def gate_report():
    """Gate ki ASLI haalat: kaunse require_join blocks verify ho sakte hain,
    aur kaunse nahi (unki chat id hi nahi hai).
    Bina id ke bot us chat ki membership check hi nahi kar sakta — isliye gate
    wahan sirf dikhawe ka hota hai. Ye function us baat ko saaf karta hai."""
    learned = _learned_ids()
    ready, no_id = [], []
    for blk in LINK_BLOCKS:
        if not blk.get("require_join"):
            continue
        cid = blk.get("id") or learned.get(blk["name"])
        (ready if cid else no_id).append(blk["name"])
    return ready, no_id


async def needs_join(context, user_id, force=False) -> bool:
    """User ko gate dikhana hai? Owner/admin ko nahi."""
    if is_privileged(user_id):
        return False
    if not has_required_blocks():
        return False
    joined, _missing, _unknown, need = await join_status(context, user_id, force=force)
    if need > 0:
        return len(joined) < need
    # Kuch bhi verify nahi ho paya (chat id nahi / bot admin nahi).
    # Gate ko CHHUPAO MAT — pehle ye `_passed` dekh kar ek hi baar dikhta tha,
    # isliye "Joined / Verify" button ek baar dikh kar hamesha gayab ho jata tha.
    # Ab har baar dikhta hai, aur user ko aage bhi badhne dete hain (atkega nahi).
    return True


# Kya button colour sach me payload me ja raha hai? (PTB version ki parwah nahi)
def _style_ok() -> bool:
    try:
        b = InlineKeyboardButton("x", url="https://t.me", api_kwargs={"style": "primary"})
        return b.to_dict().get("style") == "primary"
    except Exception:
        return False


_STYLE_OK = _style_ok()


def _btn(label, **kw):
    """InlineKeyboardButton + button colour.

    `style` ko api_kwargs ke through bhejte hain, PTB ke `style=` param se nahi.
    Wajah: `style=` param sirf PTB >= 22.7 me hai — purane PTB par wo TypeError
    deta (aur colour chupchaap gayab ho jata). api_kwargs purane PTB par bhi
    seedha payload me chala jata hai, isliye colour har version par jaata hai.
    Values: "primary" (blue), "success" (green), "danger" (red)."""
    style = kw.pop("style", None)
    api_kwargs = dict(kw.pop("api_kwargs", None) or {})
    if style:
        api_kwargs["style"] = style
    if api_kwargs:
        kw["api_kwargs"] = api_kwargs
    return InlineKeyboardButton(label, **kw)


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
        label = fonts.bold(b["name"])   # buttons par emoji nahi
        by_row[r].append(_btn(label, url=b["url"], style="primary"))   # blue
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
        [_btn(GATE_BUTTON_TEXT, callback_data="check_join", style="success")],
    ])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


async def verify_blocks_on_startup(app: Application):
    """Start par check: har enforced chat ki id hai? bot admin hai?
    Jo problem mile wo admin ko DM bhi bhejo — sirf log me chhodo mat."""
    log.info("PTB %s | button colour: %s | AI prompt: %s",
             telegram.__version__,
             "supported" if _STYLE_OK else "NOT supported",
             "ENV OVERRIDE (Render me AI_SYSTEM_PROMPT set hai!)"
             if AI_PROMPT_FROM_ENV else "built-in")
    try:
        _sample = link_keyboard(extra_rows=[
            [_btn(GATE_BUTTON_TEXT, callback_data="check_join", style="success")]]).to_dict()
        log.info("button payload jo Telegram ko ja raha hai: %s", json.dumps(_sample))
    except Exception as e:
        log.warning("Button payload sample fail: %s", e)
    _ready, _no_id = gate_report()
    if _no_id:
        log.warning("GATE NOT ENFORCING — no chat id for: %s. "
                    "Add the bot as ADMIN there (or /setid). Until then the gate "
                    "shows but cannot block anyone.", ", ".join(_no_id))
    else:
        log.info("Gate is READY — checking %s channel(s).", len(_ready))
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
            problems.append(f"• {b['name']}: no ID")
            continue
        try:
            member = await app.bot.get_chat_member(chat_id=cid, user_id=me.id)
            if member.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                log.warning("Bot '%s' mein ADMIN nahi hai.", b["name"])
                problems.append(f"• {b['name']}: the bot is not an admin")
        except Exception as e:
            log.warning("Block '%s' verify fail (id=%s): %s", b["name"], cid, e)
            problems.append(f"• {b['name']}: check failed ({type(e).__name__})")
    # DB corrupt hui thi? (rename ho ke <db>.corrupt-* bani hai) -> owner ko batao
    try:
        _d = os.path.dirname(DB_PATH) or "."
        _base = os.path.basename(DB_PATH)
        _bad = sorted(f for f in os.listdir(_d) if f.startswith(_base + ".corrupt-"))
        if _bad:
            problems.append(
                f"• The DB was corrupt — {len(_bad)} file(s) set aside (your data is safe): "
                + ", ".join(_bad[-3:]))
    except Exception:
        pass
    if problems:
        text = (
            "⚠️ The force-join gate will not fully work yet:\n\n"
            + "\n".join(problems)
            + "\n\nFix: make the bot an ADMIN in that channel/group — it learns the id "
            "the moment it is added. Or use /getid (forward a message) / /setid."
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
    #   Flow: link click -> bot chat (auto start) -> PEHLE channel gate
    #   (4 links join/request) -> pass hone par hi asli "Request to Join" link.
    if context.args and context.args[0].startswith("p"):
        code = context.args[0][1:]
        p = db_post_get(code)
        if p:
            if await needs_join(context, user.id):
                _pending_post[user.id] = code
                _prune(_pending_post, 5000)
                await send_join_gate(update, context)
            else:
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

    # DM me bot se baat karne ke liye koi join/request zaroori NAHI.
    await update.effective_message.reply_text(
        f"{fonts.bold('Welcome, ' + (user.first_name or 'friend'))}\n\n"
        f"I am {fonts.bold(BOT_BRAND)}. Just type your question."
    )


# (command, short description, min role)   role: member | admin | owner
# POSTCMD = POST_COMMAND ka placeholder (naam config se aata hai).
_COMMANDS = [
    ("start",       "bot start / force-join gate",                 "member"),
    ("help",        "this message",                                  "member"),
    ("id",          "your Telegram ID",                            "member"),
    ("ref",         "referral link + invite count",                "member"),
    ("rules",       "view the rules",                                 "member"),
    ("ai",          "ask the AI directly:  /ai <question>",           "member"),
    ("report",      "reply to a message to report it",         "member"),
    ("stats",       "bot statistics",                           "owner"),
    ("getid",       "get a chat ID (forward a message)",  "owner"),
    ("setid",       "set a block ID:  /setid <block> <id>",  "owner"),
    ("diag",        "full gate and chat status",                "owner"),
    ("chats",       "list every chat the bot is in",             "owner"),
    ("perms",       "bot permissions in this chat, with meanings",      "owner"),
    ("admins",      "team list",                               "admin"),
    ("list_admins", "same as /admins",                             "admin"),
    ("settitle",    "rename the group:  /settitle <name>",      "admin"),
    ("invitelink",  "create an invite link",                   "admin"),
    ("kick",        "kick/ban a member:  /kick <user_id>",        "admin"),
    ("unkick",      "remove a kick/ban:  /unkick <user_id>",          "admin"),
    ("del",         "delete the replied-to message",              "admin"),
    ("pin",         "pin the replied-to message",                 "admin"),
    ("promote",     "promote a member:  /promote <user_id>",  "admin"),
    ("POSTCMD",     "build a post (with a tracked deep link)",        "admin"),
    ("posts",       "list built posts",                      "admin"),
    ("btnstyle",    "button colour test",                     "owner"),
    ("broadcast",   "message all users",                  "owner"),
    ("ban",         "bot-wide ban:  /ban <user_id>",               "owner"),
    ("unban",       "remove a ban:  /unban <user_id>",                "owner"),
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
    out.append(f"{sc('chat')}: just type in DM. In a group, mention {BOT_USERNAME}.")
    if lvl >= 1:
        out.append(f"{sc('group commands only work in a group — the bot must be an admin there')}")
    if lvl == 2:
        out.append("")
        out.append(f"{sc('★ = owner only')}  •  {sc('you have all permissions')}")
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
            "No text found (no caption on the media either). Use: /broadcast <message>"
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
                "Force-join verification will use this now."
            )
        await msg.reply_text(
            f"Chat: {fwd.title}\n"
            f"Numeric id: {fwd.id}\n"
            f"Type: {fwd.type}{saved}"
        )
    else:
        await msg.reply_text(
            "Forward any message from that channel/group here — I will give you the "
            "numeric id and save it myself.\n"
            "(The bot must be an admin in that chat.)"
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
        await update.effective_message.reply_text("A numeric id is needed, e.g. -1001234567890")
        return
    b = match_block_by_title(name)
    if not b:
        await update.effective_message.reply_text(
            f"Block '{name}' not found. Send /setid with nothing after it to see the list."
        )
        return
    db_set_chat_id(b["name"], cid)
    await update.effective_message.reply_text(
        f"Saved: \"{b['name']}\" -> {cid}\nVerification will use this now."
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
        return "the bot is not in that chat (removed?)"
    if "not found" in s:
        return "the id is wrong"
    if "inaccessible" in s:
        return "the bot is not an admin"
    if "not enough rights" in s or "chat_admin_required" in s:
        return "the bot does not have that permission"
    if "method is available only" in s or "chat_write_forbidden" in s:
        return "that command does not work in this chat"
    if "user not found" in s or "participant" in s:
        return "that user is not in this chat"
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
        out.append(f"{sc('no chat recorded yet')}.")
        out.append("")
        out.append(f"{sc('how to fill it')}:")
        out.append("  • send any message in that group (works if the bot is an admin)")
        out.append("  • post something in the channel")
        out.append("  • or re-promote the bot — it will pick it up at once")
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
    _ready, _no_id = gate_report()
    if _no_id:
        out.append(_SEP)
        out.append(f"{bd('SET THE GATE IDS')}")
        out.append(_SEP)
        out.append(f"{sc('these blocks have no chat id yet, so the gate cannot check them')}:")
        for n in _no_id:
            out.append(f"  - {n}")
        out.append("")
        out.append(f"{sc('copy a chat id from above, then paste')}:")
        for n in _no_id:
            out.append(f"  /setid {n} <chat_id>")
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
    _ready, _no_id = gate_report()
    gate_ok = bool(_ready) and not _no_id
    out = [bd("BOT STATUS"), "", sep,
           f"{sc('gate')}   : {bd('READY') if gate_ok else bd('NOT READY')}",
           f"{sc('checks')} : {len(_ready)}/{len(_ready) + len(_no_id)} {sc('channels')}",
           sep, ""]
    if not gate_ok:
        out.append(f"{sc('the gate cannot block anyone yet')}.")
        out.append(f"{sc('add the bot as an ADMIN in')}: {', '.join(_no_id) if _no_id else '-'}")
        out.append(f"{sc('or bind by hand')} — /chats {sc('shows the ready-made /setid line')}.")
        out.append("")
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
                out.append(f"{sc('note')} : {sc('the bot is not an admin - this chat cannot be verified')}")
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
    """Bot in chats ko verify nahi kar paya -> owner ko ek baar saaf-saaf batao."""
    names = sorted(b["name"] for b in blocks)
    key = tuple(names)
    if key in _warned:
        return
    _warned.add(key)
    text = (
        "⚠️ GATE IS NOT ENFORCING — I cannot check membership in these chats:\n\n"
        + "\n".join(f"- {n}" for n in names)
        + "\n\nThe gate is showing, but it cannot block anyone yet.\n\n"
        "Two things fix it:\n"
        "1. Add the bot as an ADMIN in each of those channels (it learns the id "
        "the moment it is added).\n"
        "2. Or bind the id by hand — run /chats to see the ready-made /setid line.\n\n"
        "Send /diag any time to see whether the gate is ready."
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
            await q.answer("Only Admin 1 (or the owner) can approve this.", show_alert=True)
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
                    "✅ Your join request was approved. Go back to the bot and "
                    "tap the button below.",
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
            await q.answer("Only Admin 1 (or the owner) can approve this.", show_alert=True)
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

    if data.startswith("bs:"):
        await q.answer("Colour OK" if _STYLE_OK else "Colour is not supported")
        return

    if data.startswith("posttype:"):
        if not is_privileged(user.id):
            await q.answer()
            return
        t = data.split(":", 1)[1]
        _post_flow[user.id] = {"type": t, "step": 0, "data": {}, "channels": []}
        await q.answer()
        try:
            await q.edit_message_text(f"{t} — let us begin.")
        except Exception:
            pass
        await _post_channel_kb(update.effective_chat.id, context, user.id)
        return

    if data.startswith("postch:"):
        if not is_privileged(user.id):
            await q.answer()
            return
        fl = _post_flow.get(user.id)
        if not fl:
            await q.answer("Flow expired — send /post again.", show_alert=True)
            return
        cid = int(data.split(":", 1)[1])
        sel = fl.setdefault("channels", [])
        if cid in sel:
            sel.remove(cid)
            await q.answer("Removed")
        else:
            sel.append(cid)
            await q.answer("Selected")
        await _post_channel_kb(update.effective_chat.id, context, user.id, edit_msg=q.message)
        return

    if data == "postch_done":
        if not is_privileged(user.id):
            await q.answer()
            return
        fl = _post_flow.get(user.id)
        if not fl or not fl.get("channels"):
            await q.answer("Pick at least one channel first.", show_alert=True)
            return
        await q.answer()
        try:
            await q.edit_message_text(f"Selected channels: {len(fl['channels'])}")
        except Exception:
            pass
        if POST_ASK_IMAGE:
            fl["await_image"] = True
            await context.bot.send_message(
                update.effective_chat.id,
                f"{fonts.bold('Send the banner image')}")
        else:
            await _post_ask(update.effective_chat.id, context, user.id)
        return

    if data.startswith("postgo:"):
        if not is_privileged(user.id):
            await q.answer()
            return
        fl = _post_flow.get(user.id)
        if not fl or not fl.get("channels"):
            await q.answer("Flow expired — send /post again.", show_alert=True)
            return
        await q.answer()
        if data.split(":", 1)[1] == "sched":
            fl["await_time"] = True
            await context.bot.send_message(
                update.effective_chat.id,
                "When should it go out? Format:\n2026-10-18 20:30   (or just 20:30 — IST)")
            return
        code = _new_code()
        data2 = dict(fl["data"])
        data2.update({"code": code, "type": fl["type"],
                      "channel_ids": list(fl.get("channels") or []),
                      "ep_line": fl.get("ep_line"),
                      "image": fl.get("image"), "posted": False, "created": _now()})
        db_post_save(code, data2)
        ok = await _send_post(context, code)
        _post_flow.pop(user.id, None)
        await context.bot.send_message(
            update.effective_chat.id,
            ("Posted ✅\n" if ok else "Could not post (is the bot an admin in that channel?)\n")
            + f"Link: {_deep_link(context, code)}")
        return

    if data != "check_join":
        await q.answer()
        return

    # Anti-spam: ek user 2 second me sirf ek baar check kar sakta hai
    # (warna Check dabate rehne se getChatMember flood ho jayega).
    now = time.time()
    if now - _check_cd.get(user.id, 0) < 2:
        await q.answer("Please wait a moment, then try again.")
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
                [_btn(GATE_BUTTON_TEXT, callback_data="check_join", style="success")],
            ]),
        )
        return

    # need == 0 -> bot kisi bhi required channel ki membership check hi nahi kar
    # paya (chat id nahi hai / bot admin nahi). Fake "verified" na dikhao —
    # sach batao, owner ko turant alert karo, aur user ko aage badhne do
    # (warna wo hamesha atka rahega).
    ready, no_id = gate_report()
    if need == 0 and not ready and no_id:
        await q.answer()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=("The channel check is not set up yet, so I cannot confirm your "
                  "membership right now. Please do join the channels below — I am "
                  "letting you through this time."),
            reply_markup=link_keyboard(extra_rows=[
                [_btn(GATE_BUTTON_TEXT, callback_data="check_join", style="success")],
            ]),
        )
        await _warn_admin_unverifiable(
            context, [blk for blk in LINK_BLOCKS if blk["name"] in no_id])

    # Pass. (Ek hi baar answer karo — dobara answer karne par Telegram error deta hai.)
    await q.answer()
    _passed.add(user.id)
    _prune(_passed, 10000)
    if unknown and not joined and not missing:
        await _warn_admin_unverifiable(context, unknown)

    # Post deep link se aaya tha? Ab asli link (Request to Join) do.
    code = _pending_post.pop(user.id, None)
    p = db_post_get(code) if code else None
    if p:
        await _post_welcome(update, context, p, chat_id=update.effective_chat.id)
    try:
        await q.message.delete()
    except Exception as e:
        log.warning("Gate delete fail: %s", e)
    if not p:
        try:
            await q.edit_message_text("Verified. Now just type your question.")
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
        sent = False
        try:
            with open(WELCOME_IMAGE, "rb") as photo:
                await context.bot.send_photo(chat_id=msg.chat_id, photo=photo, caption=caption)
            sent = True
        except FileNotFoundError:
            log.warning("Welcome image nahi mili: %s", WELCOME_IMAGE)
        except Exception as e:
            log.warning("Welcome photo fail (%s): %s", msg.chat_id, e)
        if not sent:
            try:
                await context.bot.send_message(chat_id=msg.chat_id, text=caption)
            except Exception as e:
                log.warning("Welcome text fail (%s): %s", msg.chat_id, e)


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
    # Request bhej diya -> gate is chat ke liye pass ho jayega.
    db_add_request(usr.id, chat.id)

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
        _btn("Approve", callback_data=f"jr_ok:{chat.id}:{usr.id}", style="success"),
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
        lines.append(f"Block match: {b['name']} — id saved")
    else:
        lines.append("Title did not match any block — /setid <block name> <id>")
    if status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
        lines.append("⚠️ Bot is not ADMIN — this chat cannot be verified")
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
    if not _ai_allowed(user.id):
        await update.effective_message.reply_text(
            f"{fonts.small_caps('Slow down')} — {fonts.small_caps('too many messages, try again in a little while')}."
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
    # Group/channel me gate NAHI — wahan seedha jawab do (gate sirf DM me).
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
        await msg.reply_text("Reply to the message you want to report, then send /report.")
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
    await msg.reply_text("Report sent. Thank you."
                         if sent else "The report did not reach any admin.")


# =====================================================================
#  ADMIN / MODERATION COMMANDS  (bot ki chat permissions use karte hain)
# =====================================================================
#   (permission key, dikhne wala naam, iska matlab, kaunsi command)
_PERM_MEANING = [
    ("can_change_info",      "Change Group Info",    "can change the group name / photo",     "/settitle"),
    ("can_invite_users",     "Invite Users via Link", "can create invite links to add people",   "/invitelink"),
    ("can_restrict_members", "Ban Users",            "can ban / kick someone from the group", "/kick  /unkick"),
    ("can_delete_messages",  "Delete Messages",      "can delete anyone's message",  "/del"),
    ("can_promote_members",  "Add New Admins",       "can promote someone to admin",                 "/promote"),
    ("can_pin_messages",     "Pin Messages",         "can pin a message",                 "/pin"),
    ("can_manage_chat",      "Manage Chat",          "can manage the chat",              ""),
    ("can_post_messages",    "Post Messages",        "can post in the channel",             ""),
    ("can_edit_messages",    "Edit Messages",        "can edit channel posts",        ""),
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
        await update.effective_message.reply_text("Only the owner can do this.")
    return False


async def _need(update, group_only=True):
    """Admin check + chat type check. True = aage badho."""
    if not is_privileged(update.effective_user.id):
        return False
    if group_only and not _is_group(update):
        await update.effective_message.reply_text("This command only works in a group.")
        return False
    return True


async def cmd_settitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group ka naam badlo — 'Change Group Info' permission chahiye."""
    if not await _need(update):
        return
    title = " ".join(context.args or []).strip()
    if not title:
        await update.effective_message.reply_text("Use: /settitle <new name>")
        return
    try:
        await context.bot.set_chat_title(update.effective_chat.id, title[:128])
        await update.effective_message.reply_text(f"Name changed to: {title}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Failed: {_err_hint(e)}\n(The bot needs the 'Change Group Info' permission.)")


async def cmd_invitelink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invite link banao — 'Invite Users via Link' permission chahiye."""
    if not is_privileged(update.effective_user.id):
        return
    if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        await update.effective_message.reply_text("This command only works in a group or channel.")
        return
    try:
        link = await context.bot.create_chat_invite_link(update.effective_chat.id, name=BOT_BRAND)
        await update.effective_message.reply_text(
            f"{fonts.small_caps('invite link')}:\n{link.invite_link}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Failed: {_err_hint(e)}\n(The bot needs the 'Invite Users' permission.)")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ko group se ban/kick — 'Ban Users' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /kick <user_id>  (or reply to their message with /kick)")
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, uid)
        await update.effective_message.reply_text(f"Kicked: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Failed: {_err_hint(e)}\n(The bot needs the 'Ban Users' permission.)")


async def cmd_unkick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban hatana — 'Ban Users' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /unkick <user_id>  (or reply to their message with /unkick)")
        return
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
        await update.effective_message.reply_text(f"Unbanned: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Failed: {_err_hint(e)}\n(The bot needs the 'Ban Users' permission.)")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply wala message delete — 'Delete Messages' permission chahiye."""
    if not await _need(update):
        return
    msg = update.effective_message
    r = msg.reply_to_message
    if not r:
        await msg.reply_text("Reply to the message you want to delete, then send /del.")
        return
    try:
        await context.bot.delete_message(r.chat_id, r.message_id)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        await msg.reply_text(f"Failed: {_err_hint(e)}\n(The bot needs the 'Delete Messages' permission.)")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply wala message pin — 'Pin Messages' permission chahiye."""
    if not await _need(update):
        return
    msg = update.effective_message
    r = msg.reply_to_message
    if not r:
        await msg.reply_text("Reply to the message you want to pin, then send /pin.")
        return
    try:
        await context.bot.pin_chat_message(r.chat_id, r.message_id, disable_notification=True)
        await msg.reply_text("Pinned.")
    except Exception as e:
        await msg.reply_text(f"Failed: {_err_hint(e)}\n(The bot needs the 'Pin Messages' permission.)")


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Naya admin banao (group me) — 'Add New Admins' permission chahiye."""
    if not await _need(update):
        return
    uid = _target_uid(update, context)
    if not uid:
        await update.effective_message.reply_text(
            "Use: /promote <user_id>  (or reply to their message with /promote)")
        return
    chat = update.effective_chat
    # Admin ka /promote -> pehle ADMIN 1 ki approval. Owner seedha kar sakta hai.
    if not is_owner(update.effective_user.id):
        approver = ADMIN_IDS[0] if ADMIN_IDS else 0
        if approver and approver != update.effective_user.id:
            sc, bd = fonts.small_caps, fonts.bold
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                "Approve", callback_data=f"promo_ok:{chat.id}:{uid}",
                style="success")]])
            try:
                await context.bot.send_message(
                    approver,
                    f"{bd('NEW ADMIN REQUEST')}\n\n"
                    f"{sc('chat')} : {chat.title or chat.id}\n"
                    f"{sc('by')}   : {update.effective_user.full_name}\n"
                    f"{sc('user')} : {uid}\n\n"
                    f"{sc('approving will run the promotion')}",
                    reply_markup=kb)
                await update.effective_message.reply_text(
                    "Sent to Admin 1 for approval.")
            except Exception as e:
                await update.effective_message.reply_text(
                    f"Could not send the approval request: {_err_hint(e)}")
            return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, uid,
            can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
            can_invite_users=True, can_pin_messages=True, can_change_info=True,
        )
        await update.effective_message.reply_text(f"Promoted: {uid}")
    except Exception as e:
        await update.effective_message.reply_text(
            f"Failed: {_err_hint(e)}\n(The bot needs the 'Add New Admins' permission.)")


async def cmd_perms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Is chat me bot ke paas kaunsi permission hai — matlab + command ke saath."""
    if not await _need_owner(update):
        return
    if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        await update.effective_message.reply_text("This command only works in a group or channel.")
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
        out.append("The bot is not an ADMIN in this chat — no permissions.")
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
        await update.effective_message.reply_text("Use: /ai <question>")
        return
    uid = update.effective_user.id
    if not _ai_allowed(uid):
        await update.effective_message.reply_text(
            f"{fonts.small_caps('Slow down')} — {fonts.small_caps('try again in a little while')}.")
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
        out.append("No admins configured — set ADMIN_IDS on Render.")
    await _send_long(update.effective_message, "\n".join(out))


# =====================================================================
#  /POST  —  post builder (Owner/Admin) + deep-link tracking
# =====================================================================
#   Flow:  /post -> type (Anime/Movie/Series) -> CHANNEL buttons (multi-select
#          + Done) -> banner image (AI vision se padhi jaati hai, skip bhi kar
#          sakte ho) -> fields (POST_FIELDS) -> abhi/schedule -> post.
#   Post us/ un channels par image + caption + "Download" button ke saath
#   jaata hai. Button = bot ka deep link. Us par click karte hi bot chat
#   khulta hai, bot ko pata chalta hai kaunsa post + kaunse channels, aur
#   wo 1-2 line text + har channel ka "Request to Join" button bhejta hai.
# ---------------------------------------------------------------------
_post_flow: dict = {}      # user_id -> {"type","step","data","channels","image","await_*"}
# Post deep link se aaye par abhi gate paar na kiya ho -> user_id: code
_pending_post: dict = {}


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


def _as_url(v) -> str:
    """Link field ko pura URL banao (https://t.me/xxx). Khali/galat -> ""."""
    v = str(v or "").strip()
    if not v:
        return ""
    if v.startswith(("http://", "https://")):
        return v
    if v.startswith(("t.me/", "www.")):
        return "https://" + v
    if re.match(r"^@?[A-Za-z0-9_]{4,}$", v):
        return "https://t.me/" + v.lstrip("@")
    return ""


def _norm_chat_id(x) -> int:
    """1002549849997 -> -1002549849997 (jo config me diya hai usko theek karo)."""
    cid = int(x)
    return cid if cid < 0 else -cid


def _esc(t) -> str:
    """HTML parse_mode ke liye & < > aur QUOTES escape karo.
    Quotes zaroori hain: user ka Link field `href="..."` me jaata hai — usme ek
    `"` aa gaya to HTML toot jata aur Telegram post reject kar deta."""
    return (str(t or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#x27;"))


async def _ep_line(p) -> str:
    """'S03 EPISODE 01-02 ADDED' jaisi ek line — AI banata hai, warna season se."""
    season = str(p.get("Season") or "").strip()
    if POST_EP_LINE:
        try:
            t = await ask_ai(
                f"Anime: {p.get('Name', '')}. Season: {season}. "
                "Write one short line in exactly this style: 'S03 EPISODE 01 ADDED'. "
                "Use ONE episode number only — never a range like 01-02. "
                "Only that line, nothing else, no markdown, no quotes.",
                mem_key=None)
            if t and t != busy_message():
                line = t.strip().splitlines()[0].strip().strip('"').strip()
                line = re.sub(r"(\d+)\s*-\s*\d+", r"\1", line)   # 01-02 -> 01
                if line:
                    return line[:60]
        except Exception:
            pass
    s = re.sub(r"\D", "", season)
    return f"S{s} EPISODE ADDED" if s else "NEW EPISODE ADDED"


def _post_caption(p, ep_line="", link_url="", text_links=True) -> str:
    """Post ka caption (HTML) — blockquote (quote) style ke saath.
    Ep line aur WATCH & DOWNLOAD dono `link_url` par jaate hain (bot ka deep
    link — wahi jo neeche wale button me hai)."""
    bd = fonts.bold
    sep = "━━━━━━━━━━━━━━━━━"
    out = [f"<blockquote>❝ {_esc(bd(str(p.get('Name') or '-')))} ❞</blockquote>", ""]
    det = []
    for k in ("Season", "Episodes", "Quality", "Language"):
        v = p.get(k)
        if v:
            det.append(f"►{k} :- {_esc(str(v)[:80])}")
    if det:
        out.append("<blockquote>" + "\n".join(det) + "</blockquote>")
        out.append("")
    _ct = _esc(bd(POST_CREDIT_TEXT))
    if text_links:
        out.append('<blockquote>[ Creadit - <a href="' + _esc(POST_CREDIT_URL)
                   + '">' + _ct + "</a> ]</blockquote>")
    else:
        out.append(f"<blockquote>[ Creadit - {_ct} ]</blockquote>")
    out.append("")
    if link_url and text_links:
        if ep_line:
            out.append(sep)
            em = f"{POST_EP_EMOJI} " if POST_EP_EMOJI else ""
            tail = f" {POST_EP_EMOJI}" if POST_EP_EMOJI else ""
            out.append(f'   {em}<a href="{_esc(link_url)}">{_esc(bd(ep_line))}</a>{tail}')
        out.append(sep)
        out.append(f'   ★ <a href="{_esc(link_url)}">{_esc(bd("WATCH & DOWNLOAD"))}</a> ★')
        out.append(sep)
    elif ep_line or link_url:
        if ep_line:
            out.append(sep)
            em = f"{POST_EP_EMOJI} " if POST_EP_EMOJI else ""
            tail = f" {POST_EP_EMOJI}" if POST_EP_EMOJI else ""
            out.append(f"   {em}{_esc(bd(ep_line))}{tail}")
        if link_url:
            out.append(sep)
            out.append(f"   ★ {_esc(bd('WATCH & DOWNLOAD'))} ★")
        out.append(sep)
    elif ep_line:
        out.append(sep)
        em = f"{POST_EP_EMOJI} " if POST_EP_EMOJI else ""
        tail = f" {POST_EP_EMOJI}" if POST_EP_EMOJI else ""
        out.append(f"   {em}{_esc(bd(ep_line))}{tail}")
        out.append(sep)
    return "\n".join(out)


def _post_text(p) -> str:
    sc, bd = fonts.small_caps, fonts.bold
    name = p.get("Name") or "-"
    out = [f"{bd(str(p.get('type', 'Post')))}  —  {bd(name)}"]
    # "Link" yahan jaan-boojh kar skip hai — wo post par dikhta nahi,
    # sirf "Request to Join" button ka destination banta hai.
    skip = {"Name", "Link", "code", "type", "channel_id", "channel_ids", "channel_url",
            "image", "posted", "posted_at", "created", "post_at", "ep_line"}
    for k, v in p.items():
        if k in skip or not v:
            continue
        val = str(v)
        if val.startswith(("http://", "https://", "t.me/", "www.")):
            out.append(f"{sc(str(k))}: {val}")          # plain — clickable rahe
        else:
            out.append(f"{sc(str(k))}: {bd(val)}")
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
    """Us channel ka aisa link jise user join kar sake."""
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


# ---------- Gemini vision (image padhna) ----------
def _run_ai_vision(data: bytes, prompt: str) -> str:
    client = _get_ai()
    part = types.Part.from_bytes(data=data, mime_type="image/jpeg")
    resp = client.models.generate_content(
        model=AI_MODEL, contents=[part, prompt], config=_get_ai_config())
    return (getattr(resp, "text", None) or "").strip()


async def _vision_read(context, file_id, prompt) -> str:
    """Telegram se image ka data laao aur AI se padhwao."""
    try:
        f = await context.bot.get_file(file_id)
        data = bytes(await f.download_as_bytearray())
        txt = await asyncio.to_thread(_run_ai_vision, data, prompt)
        return _strip_md(txt)
    except Exception as e:
        log.warning("Vision fail: %s", e)
        return ""


_URL_RX = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.I)


def _strip_urls(t: str) -> str:
    """Text se URL hata do (welcome line me raw link nahi dikhna chahiye —
    link to neeche wale button me hai)."""
    out = _URL_RX.sub("", t or "")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(ln.rstrip() for ln in out.splitlines()).strip()


VISION_PROMPT = (
    "Look at this banner image and give only these lines (only what is visible; "
    "use ? for anything you cannot read). No extra talk, no markdown:\n"
    "Name: \nSeason: \nQuality: \nLanguage: "
)


# ---------- channel chunne wale buttons ----------
def _channel_rows(sel) -> list:
    rows = []
    for row in POST_CHANNELS:
        r = []
        for name, cid in row:
            c = _norm_chat_id(cid)
            mark = "√ " if c in sel else ""
            r.append(_btn(f"{mark}{name}", callback_data=f"postch:{c}",
                          style="success" if c in sel else "primary"))
        rows.append(r)
    rows.append([_btn("Done", callback_data="postch_done", style="success")])
    return rows


async def _post_channel_kb(chat_id, context, uid, edit_msg=None):
    fl = _post_flow.get(uid)
    if not fl:
        return
    kb = InlineKeyboardMarkup(_channel_rows(set(fl.get("channels") or [])))
    txt = (f"{fonts.bold('Where should it be posted?')}\n"
           f"{fonts.small_caps('pick one or more, then tap Done')}")
    if edit_msg is not None:
        try:
            await edit_msg.edit_reply_markup(kb)
            return
        except Exception:
            pass
    await context.bot.send_message(chat_id, txt, reply_markup=kb)


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post builder — sirf Owner/Admin."""
    if not is_privileged(update.effective_user.id):
        return
    _post_flow.pop(update.effective_user.id, None)
    kb = InlineKeyboardMarkup(
        [[_btn(t, callback_data=f"posttype:{t}", style="primary")] for t in POST_TYPES])
    await update.effective_message.reply_text(
        f"{fonts.bold('What are you posting?')}", reply_markup=kb)


async def _post_ask(chat_id, context, uid):
    """Flow ka agla sawaal — jo fields pehle se bhari hain unhe chhod do."""
    fl = _post_flow.get(uid)
    if not fl:
        return
    while fl["step"] < len(POST_FIELDS) and POST_FIELDS[fl["step"]] in fl["data"]:
        fl["step"] += 1
    if fl["step"] < len(POST_FIELDS):
        f = POST_FIELDS[fl["step"]]
        await context.bot.send_message(
            chat_id, f"Send {fonts.bold(f)}  ({fonts.small_caps('type')} skip {fonts.small_caps('to leave it out')})")
        return
    preview = {"type": fl["type"], **fl["data"]}
    if not fl.get("ep_line"):
        fl["ep_line"] = await _ep_line(preview)
    kb = InlineKeyboardMarkup([[
        _btn("Post now", callback_data="postgo:now", style="success"),
        _btn("Schedule", callback_data="postgo:sched", style="primary"),
    ]])
    chans = ", ".join(str(c) for c in (fl.get("channels") or []))
    img = "✅" if fl.get("image") else "—"
    await context.bot.send_message(
        chat_id,
        f"{fonts.bold('PREVIEW')}\n\n{_post_caption(preview, fl['ep_line'], 'https://t.me/' + str(context.bot.username))}"
        f"\n\n{fonts.small_caps('channels')}: {chans}\n"
        f"{fonts.small_caps('image')}: {img}",
        reply_markup=kb, parse_mode="HTML")


async def _post_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Flow ke dauraan user ka text — jawab maan lo."""
    uid = update.effective_user.id
    fl = _post_flow.get(uid)
    if not fl:
        return
    txt = (update.effective_message.text or "").strip()

    # --- banner image ka intezaar ---
    if fl.get("await_image"):
        fl.pop("await_image", None)
        if txt.lower() != "skip" and txt:
            # image nahi bheji par kuch likha -> usko pehla field maan lo
            fl["data"].setdefault(POST_FIELDS[0], txt)
        await _post_ask(update.effective_chat.id, context, uid)
        return

    # --- schedule ka time ---
    if fl.get("await_time"):
        dt = _parse_when(txt)
        if not dt:
            await update.effective_message.reply_text(
                "Could not read that. Use:  2026-10-18 20:30   (or just 20:30 — IST)")
            return
        fl.pop("await_time", None)
        code = _new_code()
        data = dict(fl["data"])
        data.update({"code": code, "type": fl["type"], "channel_ids": list(fl.get("channels") or []),
                     "ep_line": fl.get("ep_line"),
                     "image": fl.get("image"), "posted": False,
                     "created": _now(), "post_at": dt.strftime("%Y-%m-%d %H:%M")})
        db_post_save(code, data)
        jq = getattr(context.application, "job_queue", None)
        if jq is None:
            await update.effective_message.reply_text(
                "Scheduling is unavailable (job-queue not installed). Posting now instead.")
            await _send_post(context, code)
        else:
            jq.run_once(_job_post, when=dt, data=code, name=f"post:{code}")
            await update.effective_message.reply_text(
                f"Scheduled for {dt.strftime('%Y-%m-%d %H:%M')} IST\nCode: {code}")
        _post_flow.pop(uid, None)
        return

    # --- normal field ---
    if fl["step"] < len(POST_FIELDS):
        fl["data"][POST_FIELDS[fl["step"]]] = "" if txt.lower() == "skip" else txt
        fl["step"] += 1
        await _post_ask(update.effective_chat.id, context, uid)
        return


async def on_private_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private chat me photo — agar /post flow chal raha hai to banner maano."""
    user = update.effective_user
    fl = _post_flow.get(user.id)
    if not fl or not fl.get("await_image"):
        return
    msg = update.effective_message
    photos = getattr(msg, "photo", None) or []
    if not photos:
        return
    fl["image"] = photos[-1].file_id
    fl.pop("await_image", None)
    await msg.reply_text(f"{fonts.small_caps('image received — reading it with AI')} …")
    read = await _vision_read(context, fl["image"], VISION_PROMPT)
    if read:
        await msg.reply_text(
            f"{fonts.bold('AI read from the image')} — "
            f"{fonts.small_caps('hint only, nothing is filled in')}:\n{read}")
    await _post_ask(update.effective_chat.id, context, user.id)


def _fit_caption(cap: str) -> str:
    """1024 limit — poore line par kaato, taaki HTML tag beech me na kate."""
    if len(cap) <= 1024:
        return cap
    keep = []
    for line in cap.split("\n"):
        if len("\n".join(keep + [line])) > 1024:
            break
        keep.append(line)
    return "\n".join(keep)


async def _post_send_one(context, cid, p, cap, kb, html=True):
    kw = {"parse_mode": "HTML"} if html else {}
    if p.get("image"):
        await context.bot.send_photo(cid, p["image"], caption=cap, reply_markup=kb, **kw)
    else:
        await context.bot.send_message(cid, cap, reply_markup=kb, **kw)


async def _send_post(context, code):
    """Post bhejo — saare chune channels par, image + caption + Download button.

    Mastermind: bot ke saare asli links BUTTON me hain (inline keyboard buttons
    message text nahi hote, isliye koi anti-spam / link filter unhe nahi rokta).
    Caption me sirf decoration ke liye text-links hote hain — agar kisi channel ne
    link wala text block kar rakha ho aur pehli koshish fail ho, to wahi caption
    BINA text-link ke dobara bhejte hain (button phir bhi kaam karta hai)."""
    p = db_post_get(code)
    if not p:
        return False
    kb = InlineKeyboardMarkup([[_btn(
        POST_BUTTON_TEXT, url=_deep_link(context, code), style="primary")]])
    ep = p.get("ep_line") or await _ep_line(p)
    deep = _deep_link(context, code)
    cap = _fit_caption(_post_caption(p, ep, deep))
    chans = p.get("channel_ids") or ([p["channel_id"]] if p.get("channel_id") else [])
    ok = False
    failed = []
    for cid in chans:
        try:
            await _post_send_one(context, cid, p, cap, kb, html=True)
            ok = True
        except Exception as e:
            log.warning("Post fail (%s): %s", cid, e)
            failed.append(cid)
    if failed:
        plain = _fit_caption(_post_caption(p, ep, deep, text_links=False))
        still = []
        for cid in failed:
            try:
                await _post_send_one(context, cid, p, plain, kb, html=False)
                ok = True
                log.info("Post plain fallback chal gaya (%s) — links buttons me hain.", cid)
            except Exception as e:
                log.warning("Post plain fallback bhi fail (%s): %s", cid, e)
                still.append(cid)
        if still:
            log.error("Post in channels me nahi gaya: %s", still)
    if ok:
        db_post_update(code, posted=True, posted_at=_now())
    return ok


async def _job_post(context: ContextTypes.DEFAULT_TYPE):
    code = context.job.data
    ok = await _send_post(context, code)
    await _notify_owner(context.bot, f"Scheduled post {'sent' if ok else 'FAILED'}: {code}")


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


async def _welcome_upgrade(context, msg, name, p, kb):
    """Background me AI se behtar line lo aur message edit kar do.
    Message pehle hi turant ja chuka hota hai — isliye koi delay nahi."""
    try:
        line = await ask_ai(
            f"Someone just opened this bot to watch the {p.get('type', 'post').lower()} "
            f'titled "{name}". Write ONE short, plain, respectful welcome line '
            "(at most two short sentences), in English. Say only that they are "
            "welcome and that the channel link is right below. "
            "Do NOT use cheesy filler or hype, no exclamation marks, no emoji, and "
            'never lines like "sit back and enjoy", "relax", "buckle up", "you are '
            'in the right place" or "enjoy the show". Do not repeat the title back '
            "as a sentence. Do NOT paste any URL — the link is already on the "
            "button below. No markdown.",
            mem_key=None)
        if line and line != busy_message():
            line = _strip_urls(line)          # raw link text me nahi dikhana
            if line:
                await msg.edit_text(f"{line}\n\n{fonts.bold(name)}", reply_markup=kb)
    except Exception as e:
        log.info("Welcome AI upgrade skip: %s", e)


async def _post_welcome(update, context, p, chat_id=None):
    """Deep link se aaya user — TURANT text + Join button (koi AI wait nahi).
    AI ki line background me aati hai aur baad me edit ho jaati hai.
    chat_id diya ho (gate callback se) to wahan bhejo, warna reply karo."""
    name = p.get("Name") or "this"
    rows = []
    link = _as_url(p.get("Link"))          # user ne jo Link diya wahi join target
    if link:
        rows = [[_btn(JOIN_BUTTON_TEXT, url=link, style="primary")]]
    else:
        for cid in (p.get("channel_ids") or []):
            url = await _channel_join_url(context, cid)
            if url:
                rows.append([_btn(JOIN_BUTTON_TEXT, url=url, style="primary")])
    if not rows:
        rows = [[_btn(JOIN_BUTTON_TEXT, url="https://t.me", style="primary")]]
    kb = InlineKeyboardMarkup(rows)
    txt = (f"{fonts.bold(name)}\n\n"
           f"{fonts.small_caps('tap below to open the channel')}")
    if chat_id:
        sent = await context.bot.send_message(chat_id, txt, reply_markup=kb)
    else:
        sent = await update.effective_message.reply_text(txt, reply_markup=kb)
    try:
        asyncio.create_task(_welcome_upgrade(context, sent, name, p, kb))
    except Exception:
        pass


async def cmd_btnstyle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: button colour test — 3 rangeen buttons. Agar ye rangeen dikhein to
    sab theek hai; sab plain dikhein to Telegram app purana hai (9 Feb 2026 se
    pehle ka)."""
    if not is_owner(update.effective_user.id):
        return
    kb = InlineKeyboardMarkup([
        [_btn("Blue — primary", callback_data="bs:p", style="primary")],
        [_btn("Green — success", callback_data="bs:s", style="success")],
        [_btn("Red — danger", callback_data="bs:d", style="danger")],
    ])
    payload = json.dumps(kb.to_dict())
    await update.effective_message.reply_text(
        f"{fonts.bold('Button colour test')}\n\n"
        "The three buttons should look coloured (blue, green, red).\n"
        "If they all look plain, your Telegram app is out of date.\n\n"
        f"{fonts.small_caps('this is the payload the bot is sending')}:\n{payload[:280]}",
        reply_markup=kb)


async def cmd_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: bane hue posts ki list."""
    if not is_privileged(update.effective_user.id):
        return
    allp = db_post_all()
    if not allp:
        await update.effective_message.reply_text("No posts yet. Create one with /post.")
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
    app.add_handler(CommandHandler("btnstyle", cmd_btnstyle))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_chat_seen))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, on_group_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_private_message))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_private_photo))

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
        raise SystemExit("BOT_TOKEN is not set. Check config.py / your Environment Variables.")
    if not AI_API_KEY or AI_API_KEY == "PASTE_YOUR_AI_API_KEY_HERE":
        log.warning("AI_API_KEY is not set — the bot will start, but every AI reply "
                    "will be the 'busy' message. Set AI_API_KEY in your environment.")
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
