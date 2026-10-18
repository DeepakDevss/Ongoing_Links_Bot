"""
=====================================================================
  fonts.py  —  Unicode "fancy font" helpers
=====================================================================
  Telegram ka normal *bold* / _italic_ Markdown kabhi kabhi fail karta hai
  (naam me "_" ya "*" ho to message hi error de deta hai). Isliye hum Unicode
  styled characters use karte hain — ye har jagah, bina ParseMode, theek
  dikhte hain.

  Available:
    bold(text)        -> 𝗯𝗼𝗹𝗱  (sans-serif bold)
    bold_serif(text)  -> 𝐛𝐨𝐥𝐝  (serif bold)
    small_caps(text)  -> ꜱᴍᴀʟʟ ᴄᴀᴘꜱ
    mono(text)        -> 𝚖𝚘𝚗𝚘
    script(text)      -> 𝓼𝓬𝓻𝓲𝓹𝓽

  Sirf ASCII letters/digits style hote hain. Baaki characters (Hindi,
  emoji, punctuation and digits) passes through unchanged.
=====================================================================
"""

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"


def _seq(start: int, chars: str) -> dict:
    return {c: chr(start + i) for i, c in enumerate(chars)}


# Mathematical Alphanumeric Symbols block
BOLD = {
    **_seq(0x1D5D4, _UPPER),  # 𝗔
    **_seq(0x1D5EE, _LOWER),  # 𝗮
    **_seq(0x1D7EC, _DIGITS),  # 𝟬
}
BOLD_SERIF = {
    **_seq(0x1D400, _UPPER),  # 𝐀
    **_seq(0x1D41A, _LOWER),  # 𝐚
    **_seq(0x1D7CE, _DIGITS),  # 𝟎
}
MONO = {
    **_seq(0x1D670, _UPPER),  # 𝙰
    **_seq(0x1D68A, _LOWER),  # 𝚊
    **_seq(0x1D7F6, _DIGITS),  # 𝟶
}
SCRIPT = {
    **_seq(0x1D4D0, _UPPER),  # 𝓐
    **_seq(0x1D4EA, _LOWER),  # 𝓪
}

# Small caps (Unicode phonetic extensions — the classic Telegram look)
_SMALL_LOWER = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}
SMALL_CAPS = {**_SMALL_LOWER, **{k.upper(): v for k, v in _SMALL_LOWER.items()}}


def _apply(text: str, table: dict) -> str:
    if not text:
        return text or ""
    return "".join(table.get(ch, ch) for ch in text)


def bold(text: str) -> str:
    return _apply(text, BOLD)


def bold_serif(text: str) -> str:
    return _apply(text, BOLD_SERIF)


def small_caps(text: str) -> str:
    return _apply(text, SMALL_CAPS)


def mono(text: str) -> str:
    return _apply(text, MONO)


def script(text: str) -> str:
    return _apply(text, SCRIPT)
