"""
=====================================================================
  bot.py — LINNET BOT (poora bot ek hi file mein)
=====================================================================
  Isme sab kuch hai:
    • Database (users / referrals / ban)   -> section "DATABASE"
    • AI wrapper                            -> section "AI"
    • Telegram handlers + webhook server    -> baaki sab

  Sirf "config.py" edit karni hai. Is file ko chhedne ki zaroorat nahi.

  Features:
    1) Force-join gate (config.py ke LINK_BLOCKS se) + auto-approve join requests
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
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone

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
    AUTO_APPROVE_JOIN_REQUESTS,
    WEBHOOK_URL,
    PORT,
    DB_PATH,
    MEMBERSHIP_CACHE_TTL,
    AI_API_KEY,
    AI_MODEL,
    AI_MODEL_LITE,
    AI_SYSTEM_PROMPT,
    AI_MAX_CHARS,
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
    return {"users": {}, "referrals": {}, "banned": [], "stats": {"total_started": 0}}


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
        except Exception:
            pass
    return _db_default()


def _db_save(data):
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_PATH)


def db_add_user(user):
    """Naya user ho to True return karega."""
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
            data["stats"]["total_started"] = data["stats"].get("total_started", 0) + 1
        data["users"][uid] = entry
        _db_save(data)
        return is_new


def db_all_users():
    with _db_lock:
        return list(_db_load()["users"].values())


def db_is_banned(uid):
    with _db_lock:
        return int(uid) in _db_load()["banned"]


def db_set_ban(uid, value=True):
    with _db_lock:
        data = _db_load()
        uid = int(uid)
        if value and uid not in data["banned"]:
            data["banned"].append(uid)
        if not value and uid in data["banned"]:
            data["banned"].remove(uid)
        _db_save(data)


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


def _run_ai(model: str, prompt: str) -> str:
    client = _get_ai()
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=AI_SYSTEM_PROMPT),
    )
    return (getattr(resp, "text", None) or "").strip()


async def ask_ai(prompt: str, lite: bool = False) -> str:
    """Prompt bhejo, reply lo. lite=True se sasta/fast model use hoga."""
    # Model kabhi "500 UNAVAILABLE / high demand" deta hai -> retry + doosra model
    models = [AI_MODEL_LITE, AI_MODEL] if lite else [AI_MODEL, AI_MODEL_LITE]
    last_err = None
    for model in models:
        for attempt in range(3):
            try:
                text = await asyncio.to_thread(_run_ai, model, prompt)
                if text:
                    if len(text) > AI_MAX_CHARS:
                        text = text[: AI_MAX_CHARS - 20].rstrip() + "\n\n… (shortened)"
                    return text
            except Exception as e:
                last_err = e
                log.warning("AI fail (try=%s): %s", attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))  # backoff
    log.error("AI poora fail: %s", last_err)
    # User ko kabhi provider ka naam nahi dikhna chahiye.
    return "Abhi thoda load hai. Kuch der baad dobara bhejein."


# =====================================================================
#  HELPERS
# =====================================================================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def enforced_blocks():
    """Jo blocks sach mein force-join ke liye verify ho sakte hain."""
    return [b for b in LINK_BLOCKS if b.get("require_join") and b.get("id")]


# chhota membership cache: getChatMember API calls kam karne ke liye
_member_cache: dict[tuple[int, int], tuple[bool, float]] = {}


async def _is_member(context, chat_id, user_id) -> bool:
    key = (int(user_id), str(chat_id))
    now = time.time()
    cached = _member_cache.get(key)
    if cached and now - cached[1] < MEMBERSHIP_CACHE_TTL:
        return cached[0]
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        ok = member.status not in NOT_MEMBER_STATUSES
    except Exception as e:
        # Bot us chat mein admin nahi / id galat -> verify nahi hua.
        # Fail mode: "closed" = andar mat aane do (safe); "open" = andar aane do.
        log.warning("Membership check fail for %s: %s", chat_id, e)
        if JOIN_FAIL_MODE == "open":
            return True
        return False
    _member_cache[key] = (ok, now)
    return ok


async def missing_chats(context, user_id):
    """Jo required chats user ne join nahi kiye, unki list."""
    missing = []
    for chat in enforced_blocks():
        if not await _is_member(context, chat["id"], user_id):
            missing.append(chat)
    return missing


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
        by_row[r].append(InlineKeyboardButton(b["name"], url=b["url"]))
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
    text = (
        f"{fonts.small_caps('Hold on a moment')}\n\n"
        "To use this bot, please join the channels and groups below first.\n\n"
        f"After joining, tap {fonts.bold('Check')}."
    )
    kb = link_keyboard(extra_rows=[
        [InlineKeyboardButton("Check", callback_data="check_join")],
    ])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


async def send_main_menu(update: Update, context, edit=False):
    user = update.effective_user
    text = (
        f"{fonts.bold('Welcome, ' + (user.first_name or 'friend'))}\n\n"
        f"I am {fonts.bold(BOT_BRAND)}, your AI assistant.\n"
        "Ask me anything — just type your question, or use the buttons below."
    )
    kb = link_keyboard()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


async def verify_blocks_on_startup(app: Application):
    """Start par check karo ki bot har enforced chat mein admin hai."""
    me = await app.bot.get_me()
    for b in LINK_BLOCKS:
        if not b.get("require_join"):
            continue
        if not b.get("id"):
            log.warning(
                "Block '%s' par require_join=True hai par id khaali — "
                "force-join verify NAHI hoga. /getid se numeric id bharo.",
                b["name"],
            )
            continue
        try:
            member = await app.bot.get_chat_member(chat_id=b["id"], user_id=me.id)
            if member.status not in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                log.warning("Bot '%s' mein ADMIN nahi hai — gate enforce nahi hoga.", b["name"])
        except Exception as e:
            log.warning("Block '%s' verify fail (id=%s): %s", b["name"], b["id"], e)


# =====================================================================
#  COMMANDS
# =====================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_add_user(user)

    if db_is_banned(user.id):
        await update.effective_message.reply_text("You have been blocked from using this bot.")
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            f"Hi {user.first_name} — please message me in private: @{context.bot.username}"
        )
        return

    # Referral: /start ref_12345
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != user.id and db_add_referral(referrer_id, user.id):
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            f"Someone joined through your link. Total invites: {db_ref_count(referrer_id)}",
                        )
                    except Exception:
                        pass
            except ValueError:
                pass

    if await missing_chats(context, user.id):
        await send_join_gate(update, context)
    else:
        await send_main_menu(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"{fonts.small_caps('Help and commands')}\n\n"
        "/start — start the bot\n"
        "/ref — get your referral link\n"
        "/id — see your Telegram ID\n"
        "/help — this message\n\n"
        "Chat with AI: in private, just type your question.\n"
        f"In a group, mention the bot (e.g. {BOT_USERNAME} hello).\n\n"
        f"{fonts.small_caps('Admin')}: /stats /broadcast /ban /unban /getid"
    )
    await update.effective_message.reply_text(text)


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
    if not is_admin(update.effective_user.id):
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
    """Sirf un users ko bhejta hai jinhone bot ko /start kiya hai (opt-in)."""
    if not is_admin(update.effective_user.id):
        return
    msg = update.effective_message
    if not context.args and not msg.reply_to_message:
        await msg.reply_text("Use: /broadcast <message>  (or reply to a message with /broadcast)")
        return
    text = " ".join(context.args) if context.args else msg.reply_to_message.text
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
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
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
    """Admin: channel/group ka message bot ko FORWARD karo -> ID milega."""
    if not is_admin(update.effective_user.id):
        return
    msg = update.effective_message
    fwd = getattr(msg, "forward_from_chat", None)
    if fwd:
        await msg.reply_text(
            f"Chat: {fwd.title}\nID: {fwd.id}\nType: {fwd.type}\n\n"
            "Is id ko config.py ke LINK_BLOCKS mein daal do."
        )
    else:
        await msg.reply_text(
            "Forward a message from that channel/group here, and I'll give its ID.\n"
            "(The bot must be an admin in that chat.)"
        )


# =====================================================================
#  CALLBACKS
# =====================================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    data = q.data

    if data == "check_join":
        if await missing_chats(context, user.id):
            await q.answer("You still haven't joined everything. Join, then try again.", show_alert=True)
        else:
            await send_main_menu(update, context, edit=True)


# =====================================================================
#  WELCOME  (naye member par)
# =====================================================================
async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
#  AUTO-APPROVE JOIN REQUESTS
# =====================================================================
async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AUTO_APPROVE_JOIN_REQUESTS:
        return
    req = update.chat_join_request
    try:
        await req.approve()
        log.info("Approved join request from %s in %s", req.from_user.id, req.chat.id)
    except Exception as e:
        log.warning("Approve fail: %s", e)


# =====================================================================
#  AI CHAT
# =====================================================================
def _strip_mention(text: str, username: str) -> str:
    if not text:
        return ""
    for token in (f"@{username}", f"@{username.lower()}"):
        text = text.replace(token, " ")
    return text.strip()


async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private chat mein AI bina mention chalta hai — seedha sawaal likho."""
    user = update.effective_user
    db_add_user(user)
    if db_is_banned(user.id):
        return
    if await missing_chats(context, user.id):
        await send_join_gate(update, context)
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    await update.effective_message.reply_text(await ask_ai(update.effective_message.text))


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group/channel mein AI SIRF @mention par chalta hai (private mein bina
    mention). Membership bhi yahan check hoti hai (warna group se gate bypass
    ho jata)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    me = await context.bot.get_me()
    mentioned = False
    for e in (msg.entities or []):
        if e.type == "mention":
            if msg.text[e.offset:e.offset + e.length].lower() == f"@{me.username.lower()}":
                mentioned = True
        elif e.type == "text_mention" and e.user and e.user.id == me.id:
            mentioned = True
    if not mentioned:
        return
    if await missing_chats(context, update.effective_user.id):
        await msg.reply_text(
            f"{fonts.small_caps('Please join our channels first')} — "
            "tap the buttons below, then ask me again.",
            reply_markup=link_keyboard(),
        )
        return
    prompt = _strip_mention(msg.text, me.username) or "The user mentioned you. Greet them warmly."
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    await msg.reply_text(await ask_ai(prompt))


# =====================================================================
#  ERROR HANDLER
# =====================================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Update error: %s", context.error)


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

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, on_group_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_private_message))

    app.add_error_handler(on_error)
    return app


# =====================================================================
#  RUN  —  WEBHOOK (Render)  ya  POLLING (local)
# =====================================================================
def run_webhook(app: Application):
    from contextlib import asynccontextmanager
    from fastapi import FastAPI, Request, Response
    import uvicorn

    webhook_path = f"/telegram/{BOT_TOKEN}"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await app.initialize()
        await verify_blocks_on_startup(app)
        await app.bot.set_webhook(
            url=f"{WEBHOOK_URL}{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        await app.start()
        log.info("Webhook set: %s%s", WEBHOOK_URL, webhook_path)
        yield
        await app.stop()
        await app.shutdown()

    api = FastAPI(lifespan=lifespan)

    @api.post(webhook_path)
    async def telegram_webhook(request: Request):
        update = Update.de_json(await request.json(), app.bot)
        await app.process_update(update)
        return Response(status_code=200)

    @api.api_route("/", methods=["GET", "HEAD"])
    @api.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        return {"status": "ok", "bot": "running"}

    uvicorn.run(api, host="0.0.0.0", port=PORT, log_level="info")


async def _polling_post_init(app: Application):
    await verify_blocks_on_startup(app)


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
