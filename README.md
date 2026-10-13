# 🤖 Telegram Gemini Bot — Force-Join + AI + Referral

Ek complete, deploy-ready Telegram bot.

**Features:**
1. **Force-join gate** — user ko pehle aapke channel/group join karna padega (3 buttons tak).
2. **Auto-approve join requests** — jo bhi request bheje, bot turant approve kar dega.
3. **Welcome message** — naye member par photo + styled caption + aapke 2nd group ka button.
4. **AI chat (Gemini 3.6 Flash / 3.5 Flash-Lite)** — private DM mein, aur group mein sirf **@mention** par. Auto-language (user jis bhasha mein likhe, usi mein jawab).
5. **Referral / invite growth** — har user ko apna link, count + leaderboard rank.

**Stack:** Python 3.12 · python-telegram-bot v22 · FastAPI (webhook) · Render · Google Gemini API.

---

## 🔗 Zaroori Links (URLs)

| Kaam | Link |
|---|---|
| 🤖 BotFather (bot token lene ke liye) | Telegram mein **`@BotFather`** search karo → `/newbot` |
| 🔑 Gemini API key (Google AI Studio) | **https://aistudio.google.com/apikey** |
| 🐙 GitHub | https://github.com |
| ☁️ Render (hosting) | https://dashboard.render.com |
| ⏰ UptimeRobot (bot ko jagta rakhne ke liye) | https://uptimerobot.com |
| ⏰ cron-job.org (alternative) | https://cron-job.org |

---

## 📁 Files

| File | Kaam | GitHub par jaati hai? |
|---|---|---|
| `config.py` | **Sirf yehi edit karni hai** — token, keys, links | ✅ Haan |
| `bot.py` | Poora bot (database + AI + handlers + server) | ✅ Haan |
| `requirements.txt` | Python packages | ✅ Haan |
| `render.yaml` | Render Blueprint config (optional) | ✅ Haan |
| `.env.example` | Env vars ka template (koi secret nahi) | ✅ Haan |
| `.gitignore` | Kaunsi files ignore karni hain | ✅ Haan |
| `README.md` | Ye file | ✅ Haan |
| `assets/welcome.png` | Welcome image | ✅ Haan |
| `.env` | Aapke asli secrets (token/key) | ❌ **NAHI** (auto-ignored) |
| `data/db.json` | Bot ka runtime data | ❌ **NAHI** (auto-ignored) |

> `database.py` aur `gemini_ai.py` ko `bot.py` ke andar merge kiya gaya hai — isliye sirf 2 code files hain.

---

## ⚡ STEP 1 — config.py edit karo (exact line numbers)

| Kya bharna hai | config.py ki line |
|---|---|
| 🔑 **BOT_TOKEN** (BotFather se) | **line 23** |
| 👤 **BOT_USERNAME** (jaise `@MyBot`) | **line 26** |
| 🔑 **GEMINI_API_KEY** (AI Studio se) | **line 32** |
| 🧠 Gemini model (default theek hai) | lines 37–38 |
| 👑 **ADMIN_IDS** (aapki Telegram id) | lines 45–47 |
| 📢 **REQUIRED_CHATS** (force-join ke 3 buttons) | lines 58–62 |
| 🚀 **WELCOME_BUTTON_TEXT / _URL** (welcome wala link) | lines 68–69 |
| 📝 **WELCOME_TEMPLATE** (message text) | line 77 |
| 🖼️ **WELCOME_IMAGE** (image path) | line 95 |
| ✅ **AUTO_APPROVE_JOIN_REQUESTS** | line 102 |

**BotFather se token:** Telegram mein **@BotFather** → `/newbot` → naam do → token milega (`123456:ABC-DEF...`) → **line 23**.
**Gemini key:** [aistudio.google.com](https://aistudio.google.com) → *Get API key* → copy → **line 32**.
**Apni admin ID:** bot ko `/id` bhejo → number aayega → **lines 45–47**.
**Channel/Group ID:** bot ko us chat mein **Admin** banao → us chat ka message bot ko **forward** karo → bot ID bata dega (`/getid`) → **lines 58–62** ke `"id"` mein daalo. (Public channel ke liye `"@channelusername"` bhi chalta hai.)

---

## 🐙 STEP 2 — GitHub par daalo (SAHI TAREEKA)

### ⚠️ Sabse pehle ye samajh lo (bahut log yahan galti karte hain)

1. **GitHub ki website ka "Upload files" button `.gitignore` ko IGNORE karta hai.** Us se upload karo to `.env` jaisi secret files bhi chali jaati hain — `.gitignore` kaam nahi karta. Isliye **git command-line se push karo**, website se nahi.
2. **`.gitignore` jaisi dotfiles (`.` se shuru) hidden hoti hain.** Windows File Explorer / Mac Finder mein dikhti nahi. Windows par *View → Show → Hidden items* on karo, Mac par `Cmd + Shift + .` dabao.
3. **`render.yaml` aur `.gitignore` sirf tab kaam karti hain jab repo mein hon.** Dot wali file galti se skip ho jaati hai — isliye niche wale command se push karo.

### Push karne ke commands (copy-paste)

Repo ka folder kholo aur:

```bash
git init
git add -A
git commit -m "Telegram Gemini bot"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

- `git add -A` **dotfiles bhi add karta hai** (`.gitignore`, `.env.example`).
- Verify karne ke liye: `git status` — list mein `.gitignore` aur `render.yaml` dikhni chahiye.

### ❗ Agar push reject ho jaye
Agar aapne GitHub par repo banate waqt **"Add a README file"** ya **".gitignore template"** tick kiya tha, to ye error aayega:
`Updates were rejected because the remote contains work that you do not have locally.`

**Fix (do options):**
```bash
# Option A (recommended) — remote ko apne saath merge karo
git pull origin main --allow-unrelated-histories
git push origin main
```
```bash
# Option B — sirf jab repo bilkul aapka ho aur kuch important na ho
git push -u origin main --force
```
> Best: repo banate waqt koi README/.gitignore template **tick na karo** — bas khaali repo banao.

---

## 💻 STEP 3 — Local test (optional)

```bash
pip install -r requirements.txt
cp .env.example .env      # phir .env mein apni values daalo
python bot.py             # WEBHOOK_URL khaali hai to polling mode chalega
```

---

## ☁️ STEP 4 — Render par deploy (EXACT settings)

1. [render.com](https://render.com) → **New +** → **Web Service** → GitHub repo connect karo.
2. Ye exact settings daalo:

| Field | Value |
|---|---|
| **Language / Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Instance Type** | `Free` |
| **Health Check Path** | `/health` |

3. **Environment Variables** (Dashboard → *Environment* → *+ Add Environment Variable*):

| Key | Value | Required? |
|---|---|---|
| `BOT_TOKEN` | BotFather ka token | ✅ Haan |
| `BOT_USERNAME` | `@YourBot` | ✅ Haan |
| `GEMINI_API_KEY` | AI Studio key | ✅ Haan |
| `ADMIN_IDS` | aapki id (comma se multiple) | ✅ Haan |
| `GEMINI_MODEL` | `gemini-3.6-flash` | optional |
| `GEMINI_MODEL_LITE` | `gemini-3.5-flash-lite` | optional |
| `AUTO_APPROVE_JOIN_REQUESTS` | `True` | optional |
| `PYTHON_VERSION` | `3.12.13` | optional (recommended) |

> ❌ **`WEBHOOK_URL` aur `PORT` set karne ki zaroorat NAHI** — Render inhe khud set karta hai (`RENDER_EXTERNAL_URL`, `PORT=10000`). Bot khud webhook register kar leta hai.

4. **Create Web Service** → 2–3 min mein deploy. Render URL milega — bot usi par webhook laga dega.

> 🎁 **`render.yaml` ke baare mein:** ye file sirf **Blueprint** deploy ke liye hai. Agar aap *New + → Blueprint* karte ho to Render is file se sab settings khud bhar dega. Agar normal **Web Service** karte ho to ye file ignore hoti hai — tab upar wale Build/Start command khud type karo.

### Render free tier ko jagta rakhna (zaroori!)
Free web service **15 min inactivity** par so jaati hai. Isliye [cron-job.org](https://cron-job.org) ya [UptimeRobot](https://uptimerobot.com) se har **5–10 min** mein ping karo.

> ⚠️ **URL mein `/health` lagana ZAROORI hai.** Root URL (`https://your-app.onrender.com`) par UptimeRobot ko `403 / 405 Method Not Allowed` milta hai aur monitor "Down" dikhata hai. Sahi URL:
> `https://your-app.onrender.com/health`

---

## 🎮 Bot kaise use hota hai
- **Private:** `/start` → membership check → join buttons → "✅ Main ne join kar liya" → menu → apna sawaal likho, AI jawab dega.
- **Group:** naya member aaye → welcome photo + text + button. AI ke liye bot ko **@mention** karo.
- **Referral:** `/ref` → apna link → share → count badhta jayega.
- **Admin:** `/stats`, `/broadcast <msg>`, `/ban <id>`, `/unban <id>`, `/getid`.

---

## ⚠️ Terms, Risk & Ban se bachna (IMPORTANT)

**Ye bot jo karta hai — sab allowed hai ✅**
- Force-join gate (user ko join karna padta hai) — normal, allowed practice.
- Auto-approve join requests — Telegram ka official API (`approveChatJoinRequest`) hai.
- Welcome messages, AI chat, referral links — sab fine.

**Ye Telegram ke rules todta hai — mat karo ❌**
- **Kisi ko uski marzi ke bina group mein auto-add / force-add karna = SPAM.** Ban pakka. Aur technically **Bot API mein aisa method hi nahi hai** — sirf *invite link* hai, jise user khud tap karta hai. Jo tools "auto-add members" bechte hain wo aapke **user account** (MTProto) se login karke spam karte hain — ban seedha us account par padta hai.
- Scraped usernames ko bulk DM, strangers ko invite-link bhejna — reports aur restriction laata hai.
- Fake/paid members khareedna — Telegram scam label laga sakta hai aur members hata sakta hai.

**Telegram ke rate limits (bot ke liye) — inhe respect karo:**
- Ek chat mein **1 message/second** se zyada nahi.
- Ek group mein **20 messages/minute** se zyada nahi.
- Bulk broadcast ~**30 messages/second** (paid broadcast se 1000/sec tak).
- `getChatMember` ~**30 requests/second**. Limit cross hui to `429` + `retry_after` milega — usse respect karo.
- `broadcast` command **sirf un users ko** bhejti hai jinhone bot ko `/start` kiya hai (opt-in). **Cold DM bot se possible hi nahi.**

Isliye is bot mein **AIORateLimiter** + **membership cache (60s)** + **broadcast jitter** built-in hai — flood se bachne ke liye.

**Safe growth (asli tarakki):**
- **Referral system** (already built) — link do, reward do.
- **Cross-promotion** — same-niche channels se collaboration.
- **Quality content + active group** — log khud aate hain.
- **Welcome engagement** — naye member ko turant value (ye bot karta hai).

**Aur dhyan:** Render free par **data (`db.json`) restart/redeploy par reset** ho sakta hai. Bade scale par MongoDB/Postgres par shift karo.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| GitHub par `.gitignore` / `render.yaml` nahi dikh rahi | Hidden file hai — `git add -A` se push karo (website upload se nahi). Windows: *Show hidden items*. |
| GitHub "Upload files" se `.env` chala gaya | Website upload `.gitignore` ko ignore karta hai. File delete karo + **token/key turant rotate (badal) do**. |
| `Updates were rejected... remote contains work` | `git pull origin main --allow-unrelated-histories` phir `git push origin main`. |
| `render.yaml` kaam nahi kar raha | Ye sirf **Blueprint** deploy par chalti hai. Normal Web Service par Build/Start command khud daalo. |
| `Conflict: terminated by other getUpdates` | Ek se zyada instance — Render par sirf ek service rakho. |
| Force-join hamesha "join karo" dikhata hai | Bot us chat mein **admin** nahi, ya `id` galat. `/getid` se check karo. |
| AI jawab nahi de raha | `GEMINI_API_KEY` galat/expired ya model name galat — logs dekho. |
| Welcome photo nahi aayi | `assets/welcome.png` missing — path check karo (`config.py` line 95). |
| Bot so jaata hai | Upar wala keep-alive ping setup karo (URL mein **`/health`** zaroor lagao). |
| UptimeRobot "Down / 403 Method Not Allowed" | Monitor URL mein `/health` lagao: `https://your-app.onrender.com/health` — root URL par nahi. |
| AI jawab nahi de raha, logs mein `500 UNAVAILABLE` / high demand | Ye Gemini ka **temporary** error hai. Naya `bot.py` khud **retry + doosra model** try karta hai. 1–2 min baad dobara bhejo. |
| `To use AIORateLimiter...` error | `requirements.txt` mein `python-telegram-bot[rate-limiter]` hona chahiye (already hai). |

---

Made with ❤️
