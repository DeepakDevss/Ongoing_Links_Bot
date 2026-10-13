"""
database.py
===========
Storage layer with TWO back-ends, chosen automatically:

* **MongoDB**  — used only if ``MONGO_URI`` is set.  Data survives restarts.
* **In-memory** — used if ``MONGO_URI`` is empty.  Nothing to set up, but data
  (users, bans, admin-set welcome text) is lost when the bot restarts.

So MongoDB is **optional** — the bot runs fine with just the Telegram token.
The function names below are identical for both back-ends.
"""

import logging

import config

log = logging.getLogger(__name__)

_SETTINGS_ID = "main"

MONGO_URI = (config.MONGO_URI or "").strip()
USE_MONGO = bool(MONGO_URI) and MONGO_URI.lower() not in ("memory", "none", "off")

# --------------------------------------------------------------------------- #
# back-end selection
# --------------------------------------------------------------------------- #
_client = None
_db = None
users = None
channels_col = None
settings_col = None

if USE_MONGO:
    from motor.motor_asyncio import AsyncIOMotorClient

    _client = AsyncIOMotorClient(MONGO_URI)
    _db = _client[config.DB_NAME]
    users = _db["users"]
    channels_col = _db["channels"]
    settings_col = _db["settings"]
    log.info("Storage: MongoDB (%s)", config.DB_NAME)
else:
    log.warning(
        "MONGO_URI not set — using IN-MEMORY storage. "
        "Data will not persist across restarts."
    )

# simple in-memory stores (used only when USE_MONGO is False)
_mem_users: dict = {}
_mem_channels: dict = {}
_mem_settings: dict = {}


def is_persistent() -> bool:
    """True when data survives restarts (MongoDB back-end)."""
    return USE_MONGO


async def ensure_indexes() -> None:
    if USE_MONGO:
        await users.create_index("user_id", unique=True)
        await channels_col.create_index("channel_id", unique=True)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
async def get_setting(key: str, default=None):
    if USE_MONGO:
        doc = await settings_col.find_one({"_id": _SETTINGS_ID})
        if doc and key in doc:
            return doc[key]
        return default
    return _mem_settings.get(key, default)


async def set_setting(key: str, value) -> None:
    if USE_MONGO:
        await settings_col.update_one(
            {"_id": _SETTINGS_ID}, {"$set": {key: value}}, upsert=True
        )
        return
    _mem_settings[key] = value


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #
async def add_user(user_id: int, first_name: str, full_name: str, joined_date: str) -> None:
    if USE_MONGO:
        await users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "first_name": first_name,
                    "full_name": full_name,
                    "joined_date": joined_date,
                },
                "$setOnInsert": {"banned": False},
            },
            upsert=True,
        )
        return
    doc = _mem_users.setdefault(user_id, {"banned": False})
    doc["first_name"] = first_name
    doc["full_name"] = full_name
    doc["joined_date"] = joined_date


async def get_user(user_id: int):
    if USE_MONGO:
        return await users.find_one({"user_id": user_id})
    doc = _mem_users.get(user_id)
    return {"user_id": user_id, **doc} if doc is not None else None


async def is_banned(user_id: int) -> bool:
    if USE_MONGO:
        doc = await users.find_one({"user_id": user_id})
        return bool(doc and doc.get("banned"))
    return bool(_mem_users.get(user_id, {}).get("banned"))


async def set_ban(user_id: int, banned: bool) -> None:
    if USE_MONGO:
        await users.update_one(
            {"user_id": user_id}, {"$set": {"banned": banned}}, upsert=True
        )
        return
    _mem_users.setdefault(user_id, {})["banned"] = banned


async def count_users() -> int:
    if USE_MONGO:
        return await users.count_documents({})
    return len(_mem_users)


# --------------------------------------------------------------------------- #
# force-subscribe channels
# --------------------------------------------------------------------------- #
async def add_channel(channel_id: int, name: str = "") -> None:
    if USE_MONGO:
        await channels_col.update_one(
            {"channel_id": channel_id}, {"$set": {"name": name}}, upsert=True
        )
        return
    _mem_channels[channel_id] = {"channel_id": channel_id, "name": name}


async def remove_channel(channel_id: int) -> None:
    if USE_MONGO:
        await channels_col.delete_one({"channel_id": channel_id})
        return
    _mem_channels.pop(channel_id, None)


async def list_channels() -> list:
    if USE_MONGO:
        return [doc async for doc in channels_col.find({})]
    return list(_mem_channels.values())
