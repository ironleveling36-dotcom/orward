# Telegram Auto-Forward Userbot

Logs in with **your own Telegram account** (API ID + API hash + phone number)
and auto-forwards every new message — text, photos, videos, documents,
stickers, voice notes, polls, anything — from any number of **source**
channels to any number of **target** channels. Fully controlled with inline
buttons from your own "Saved Messages" chat. No coding needed after setup.

## What you get

- ✅ Add/remove unlimited source channels via buttons
- ✅ Add/remove unlimited target channels via buttons
- ✅ Start / Stop forwarding with one tap
- ✅ Adjustable delay (0–300s) between forwards, to avoid Telegram flood limits
- ✅ Forwards **all** message types, not just text
- ✅ Persists your settings (`config.json`) across restarts
- ✅ Deploys straight to Railway from GitHub

## 1. Get your API credentials

1. Go to https://my.telegram.org → log in → **API Development Tools**.
2. Create an app, copy the **api_id** and **api_hash**.

## 2. Generate your session string (one-time, run locally)

You can't do interactive phone login on a server, so do this once on your own
computer:

```bash
git clone <this-repo-url>
cd telegram-forward-bot
python -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python generate_session.py
```

Enter your `API_ID`, `API_HASH`, phone number, the login code Telegram sends
you, and your 2FA password if you have one. It prints a `SESSION_STRING` —
copy it.

⚠️ **Treat SESSION_STRING like a password.** Anyone who has it can log in as
you without needing your phone. Never commit it to GitHub.

## 3. Configure environment variables

Copy `.env.example` to `.env` and fill in:

```
API_ID=...
API_HASH=...
PHONE_NUMBER=...
SESSION_STRING=...
DEFAULT_DELAY=5
```

Test locally:

```bash
python bot.py
```

Then open Telegram → **Saved Messages** → send `.menu` to open the control
panel.

## 4. Deploy to Railway (GitHub → Railway)

1. Push this project to a **new GitHub repo** (make sure `.env` and
   `config.json` are NOT included — `.gitignore` already excludes them).
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
   → select your repo.
3. Railway auto-detects Python via `requirements.txt` and `Procfile`
   (`railway.json` also pins the start command, so it works even if Railway
   picks the "web" template by mistake).
4. In Railway → your service → **Variables**, add:
   - `API_ID`
   - `API_HASH`
   - `SESSION_STRING`
   - `DEFAULT_DELAY` (optional, default `5`)
5. Deploy. Check the **Deploy Logs** — you should see:
   `Logged in as <your name> ... Bot is running.`
6. In Telegram, open **Saved Messages** and send `.menu`.

### Common Railway errors and fixes

| Error | Fix |
|---|---|
| `SystemExit: SESSION_STRING is empty` | You forgot to set the `SESSION_STRING` variable in Railway. |
| `sqlite3.OperationalError: database is locked` | Only run **one** instance of the bot at a time (don't also run it locally with the same session while it's live on Railway). |
| Service keeps restarting / sleeping | Make sure the service type is a **Worker**, not exposed as a web service with a health check — this bot has no HTTP server. `railway.json` sets this, but double check under Settings → the service isn't expecting a public port. |
| `FloodWaitError` in logs | Telegram is rate-limiting you — raise the delay in the control panel (⏱ Set Delay). The bot auto-sleeps and retries, this isn't fatal. |
| Can't resolve channel when adding source/target | Make sure the logged-in account has actually joined that channel/group first. |

## 5. Using the control panel

Send `.menu` in Saved Messages to bring up:

- **➕ Add Source / ➕ Add Target** — tap, then send a `@username`, `t.me/...`
  link, or numeric ID of the channel.
- **📋 List Sources / 📋 List Targets** — tap any entry to remove it.
- **⏱ Set Delay** — pick 0/3/5/10/30/60/120/300 seconds between forwards.
- **▶️ Start Forwarding / ⏹ Stop Forwarding** — toggle the whole thing on/off.
- **ℹ️ Status** — quick popup with queue size and total forwarded count.

## Notes on safety / avoiding flood waits & account restrictions

This uses your **real account**, so Telegram's normal anti-spam limits apply.
The bot has three built-in layers of protection:

1. **Delay + jitter** between every single forward (not just per batch) —
   set via ⏱ Set Delay. Start at 10s+ if you have multiple targets or
   busy source channels.
2. **Hourly cap** (🚦 Set Hourly Cap) — a hard ceiling on total forwards per
   hour across all targets, independent of the delay.
3. **Adaptive auto-pause** — if Telegram throttles you (`FloodWaitError`),
   the bot sleeps the exact time Telegram demands and automatically
   *doubles* your delay so it stops repeating the mistake. If that happens
   3 times in a row, it fully pauses itself and messages you in Saved
   Messages — it will not keep hammering Telegram's API unattended. Tap
   "Resume" in `.menu` after raising the delay/cap.

Practical guidance to avoid ever reaching a freeze/restriction:

- Keep delay ≥ 10s if forwarding into more than 1–2 targets.
- Set a hourly cap (e.g. 100–200) as a backstop, especially for busy sources.
- Only forward into channels/groups you actually own or admin — forwarding
  into channels where you're just a regular member is a much more common
  cause of account restrictions than raw speed.
- If you see `FloodWaitError` in the logs occasionally, that's normal and
  handled automatically. If you see the auto-pause message repeatedly, your
  settings are too aggressive for your channel volume — raise delay/cap.
- Only forward content you have the right to re-share.
