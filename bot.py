"""
Telegram auto-forward userbot.

- Logs in using your own account (API_ID / API_HASH / SESSION_STRING —
  generated once via generate_session.py).
- Control panel lives in your own "Saved Messages" chat, driven by inline
  buttons. Type .menu there to open it.
- Forwards every new message (text, photo, video, document, sticker, voice,
  poll, etc. — anything Telegram supports) from any added source channel to
  every added target channel.
- A queue + worker enforces the delay you set between forwards, so you don't
  trip Telegram's flood limits.
"""

import asyncio
import os
import time
import random
import logging
from collections import deque

from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat

import storage

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("forward-bot")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ.get("SESSION_STRING", "")
OWNER_ID_ENV = os.environ.get("OWNER_ID", "").strip()
DEFAULT_DELAY = int(os.environ.get("DEFAULT_DELAY", "5"))

if not SESSION_STRING:
    raise SystemExit(
        "SESSION_STRING is empty. Run `python generate_session.py` locally first, "
        "then set SESSION_STRING in your .env / Railway variables."
    )

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# in-memory conversation state: waiting for the user to send a channel to add
# e.g. {"mode": "add_source"} or {"mode": "add_target"} or {"mode": "set_delay"}
pending_action = {"mode": None}

forward_queue: "asyncio.Queue" = asyncio.Queue()
OWNER_ID = int(OWNER_ID_ENV) if OWNER_ID_ENV else None

# Safety knobs
MAX_CONSECUTIVE_FLOODS = 3      # auto-pause after this many flood waits in a row
send_timestamps: "deque" = deque()  # for max_per_hour throttling


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def main_menu_text(cfg):
    if cfg.get("auto_paused"):
        status = "🟠 AUTO-PAUSED (repeated flood waits — see below)"
    elif cfg["forwarding"]:
        status = "🟢 RUNNING"
    else:
        status = "🔴 STOPPED"
    cap = cfg.get("max_per_hour", 0)
    cap_text = f"`{cap}`/hr" if cap else "unlimited"
    return (
        "**📡 Auto-Forward Control Panel**\n\n"
        f"Status: {status}\n"
        f"Sources: `{len(cfg['sources'])}`   Targets: `{len(cfg['targets'])}`\n"
        f"Delay: `{cfg['delay']}s` between forwards   Cap: {cap_text}\n"
        f"Total forwarded: `{cfg.get('forwarded_count', 0)}`\n\n"
        "Use the buttons below 👇"
    )


def main_menu_buttons(cfg):
    if cfg.get("auto_paused"):
        toggle_row = [Button.inline("🟠 Resume (clear auto-pause)", b"resume")]
    else:
        toggle_label = "⏹ Stop Forwarding" if cfg["forwarding"] else "▶️ Start Forwarding"
        toggle_data = "stop" if cfg["forwarding"] else "start"
        toggle_row = [Button.inline(toggle_label, toggle_data.encode())]
    return [
        [Button.inline("➕ Add Source", b"add_source"), Button.inline("➕ Add Target", b"add_target")],
        [Button.inline("📋 List Sources", b"list_sources"), Button.inline("📋 List Targets", b"list_targets")],
        [Button.inline("⏱ Set Delay", b"set_delay_menu"), Button.inline("🚦 Set Hourly Cap", b"set_cap_menu")],
        toggle_row,
        [Button.inline("🔄 Refresh", b"refresh"), Button.inline("ℹ️ Status", b"status")],
    ]


def cap_menu_buttons():
    options = [0, 20, 50, 100, 200, 500]
    rows = []
    row = []
    for i, val in enumerate(options, 1):
        label = "∞" if val == 0 else str(val)
        row.append(Button.inline(label, f"cap_{val}".encode()))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Button.inline("⬅️ Back", b"refresh")])
    return rows


def delay_menu_buttons():
    options = [0, 3, 5, 10, 30, 60, 120, 300]
    rows = []
    row = []
    for i, val in enumerate(options, 1):
        row.append(Button.inline(f"{val}s", f"delay_{val}".encode()))
        if i % 4 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Button.inline("⬅️ Back", b"refresh")])
    return rows


async def channel_list_buttons(cfg, kind):
    items = cfg["sources"] if kind == "source" else cfg["targets"]
    rows = []
    for chat_id, name in items.items():
        label = f"❌ {name[:28]}"
        rows.append([Button.inline(label, f"rm_{kind}_{chat_id}".encode())])
    rows.append([Button.inline("⬅️ Back", b"refresh")])
    return rows


def is_owner(event):
    # Since this client is logged in as the account owner, any message the
    # account itself sends (outgoing=True) is trusted. If OWNER_ID is set,
    # also allow messages sent BY that exact user id (useful if you ever run
    # this as a bot account instead).
    if event.out:
        return True
    if OWNER_ID and event.sender_id == OWNER_ID:
        return True
    return False


async def resolve_chat(text):
    """Resolve a channel from @username, t.me link, numeric id, or a
    forwarded message's origin."""
    text = text.strip()
    try:
        entity = await client.get_entity(text)
    except Exception:
        return None
    if isinstance(entity, (Channel, Chat)):
        return entity
    return None


# ---------------------------------------------------------------------------
# Command: open menu
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(pattern=r"^\.menu$", outgoing=True))
@client.on(events.NewMessage(pattern=r"^/start$"))
async def open_menu(event):
    if not is_owner(event):
        return
    cfg = storage.get_config()
    await event.respond(main_menu_text(cfg), buttons=main_menu_buttons(cfg))


# ---------------------------------------------------------------------------
# Button callback handling
# ---------------------------------------------------------------------------

@client.on(events.CallbackQuery())
async def on_callback(event):
    if not is_owner(event):
        await event.answer("Not authorized.", alert=True)
        return

    data = event.data.decode()
    cfg = storage.get_config()

    if data == "refresh":
        pending_action["mode"] = None
        cfg = storage.get_config()
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))

    elif data == "add_source":
        pending_action["mode"] = "add_source"
        await event.edit(
            "📥 **Add Source Channel**\n\n"
            "Send me the channel now:\n"
            "• `@username`, or\n"
            "• a `t.me/...` link, or\n"
            "• the numeric channel ID\n\n"
            "You must already be a member of it (or its admin/owner).",
            buttons=[[Button.inline("⬅️ Cancel", b"refresh")]],
        )

    elif data == "add_target":
        pending_action["mode"] = "add_target"
        await event.edit(
            "📤 **Add Target Channel**\n\n"
            "Send me the channel now:\n"
            "• `@username`, or\n"
            "• a `t.me/...` link, or\n"
            "• the numeric channel ID\n\n"
            "The account must be able to post there (member/admin).",
            buttons=[[Button.inline("⬅️ Cancel", b"refresh")]],
        )

    elif data == "list_sources":
        buttons = await channel_list_buttons(cfg, "source")
        names = "\n".join(f"• {n} (`{i}`)" for i, n in cfg["sources"].items()) or "_none yet_"
        await event.edit(f"**📥 Source Channels**\n\n{names}\n\nTap to remove:", buttons=buttons)

    elif data == "list_targets":
        buttons = await channel_list_buttons(cfg, "target")
        names = "\n".join(f"• {n} (`{i}`)" for i, n in cfg["targets"].items()) or "_none yet_"
        await event.edit(f"**📤 Target Channels**\n\n{names}\n\nTap to remove:", buttons=buttons)

    elif data.startswith("rm_source_"):
        chat_id = data.replace("rm_source_", "", 1)
        cfg = storage.remove_source(chat_id)
        await refresh_subscriptions()
        buttons = await channel_list_buttons(cfg, "source")
        names = "\n".join(f"• {n} (`{i}`)" for i, n in cfg["sources"].items()) or "_none yet_"
        await event.edit(f"✅ Removed.\n\n**📥 Source Channels**\n\n{names}", buttons=buttons)

    elif data.startswith("rm_target_"):
        chat_id = data.replace("rm_target_", "", 1)
        cfg = storage.remove_target(chat_id)
        buttons = await channel_list_buttons(cfg, "target")
        names = "\n".join(f"• {n} (`{i}`)" for i, n in cfg["targets"].items()) or "_none yet_"
        await event.edit(f"✅ Removed.\n\n**📤 Target Channels**\n\n{names}", buttons=buttons)

    elif data == "set_delay_menu":
        await event.edit(
            f"⏱ **Set Forward Delay**\n\nCurrent: `{cfg['delay']}s`\n\n"
            "Pick how many seconds to wait between each forwarded message "
            "(higher = safer against Telegram flood limits):",
            buttons=delay_menu_buttons(),
        )

    elif data.startswith("delay_"):
        val = int(data.replace("delay_", "", 1))
        cfg = storage.set_delay(val)
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))
        await event.answer(f"Delay set to {val}s")

    elif data == "start":
        if not cfg["sources"] or not cfg["targets"]:
            await event.answer("Add at least one source and one target first.", alert=True)
            return
        cfg = storage.set_forwarding(True)
        await refresh_subscriptions()
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))
        await event.answer("Forwarding started ▶️")

    elif data == "stop":
        cfg = storage.set_forwarding(False)
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))
        await event.answer("Forwarding stopped ⏹")

    elif data == "status":
        qsize = forward_queue.qsize()
        floods = cfg.get("consecutive_floods", 0)
        await event.answer(
            f"Queue: {qsize} pending\n"
            f"Forwarded: {cfg.get('forwarded_count', 0)}\n"
            f"Consecutive flood waits: {floods}",
            alert=True,
        )

    elif data == "set_cap_menu":
        await event.edit(
            "🚦 **Hourly Forward Cap**\n\n"
            f"Current: {'unlimited' if not cfg.get('max_per_hour') else str(cfg['max_per_hour']) + '/hr'}\n\n"
            "This caps total forwards per hour across all targets, as an "
            "extra safety net on top of the per-message delay:",
            buttons=cap_menu_buttons(),
        )

    elif data.startswith("cap_"):
        val = int(data.replace("cap_", "", 1))
        cfg = storage.set_max_per_hour(val)
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))
        await event.answer(f"Hourly cap set to {'unlimited' if val == 0 else val}")

    elif data == "resume":
        cfg = storage.clear_auto_pause()
        cfg = storage.update_config(consecutive_floods=0)
        await event.edit(main_menu_text(cfg), buttons=main_menu_buttons(cfg))
        await event.answer("Auto-pause cleared. Tap ▶️ Start Forwarding when ready.")


# ---------------------------------------------------------------------------
# Handle the plain-text reply after "Add Source" / "Add Target" was tapped
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True))
async def on_text_reply(event):
    mode = pending_action.get("mode")
    if not mode or event.raw_text.startswith("."):
        return

    entity = await resolve_chat(event.raw_text)
    if not entity:
        await event.respond("⚠️ Couldn't resolve that as a channel/group. Try again, or tap Cancel.")
        return

    chat_id = str(entity.id if not isinstance(entity, Channel) else int(f"-100{entity.id}"))
    name = getattr(entity, "title", None) or getattr(entity, "username", None) or chat_id

    if mode == "add_source":
        cfg = storage.add_source(chat_id, name)
        pending_action["mode"] = None
        await refresh_subscriptions()
        await event.respond(f"✅ Added **{name}** as a source.", buttons=main_menu_buttons(cfg))
    elif mode == "add_target":
        cfg = storage.add_target(chat_id, name)
        pending_action["mode"] = None
        await event.respond(f"✅ Added **{name}** as a target.", buttons=main_menu_buttons(cfg))


# ---------------------------------------------------------------------------
# Forwarding engine
# ---------------------------------------------------------------------------

_source_handler = None


async def refresh_subscriptions():
    """(Re)subscribe the NewMessage event to the current list of source chats."""
    global _source_handler
    cfg = storage.get_config()
    source_ids = [int(cid) for cid in cfg["sources"].keys()]

    if _source_handler:
        client.remove_event_handler(_source_handler)
        _source_handler = None

    if not source_ids:
        return

    async def handler(event):
        cfg2 = storage.get_config()
        if not cfg2["forwarding"]:
            return
        await forward_queue.put(event.message)

    client.add_event_handler(handler, events.NewMessage(chats=source_ids))
    _source_handler = handler
    log.info("Subscribed to %d source channel(s).", len(source_ids))


async def wait_for_hourly_cap():
    """Blocks until sending is allowed under the current max_per_hour cap."""
    cfg = storage.get_config()
    cap = cfg.get("max_per_hour", 0)
    if not cap:
        return
    now = time.time()
    while send_timestamps and now - send_timestamps[0] > 3600:
        send_timestamps.popleft()
    while len(send_timestamps) >= cap:
        wait_s = 3600 - (now - send_timestamps[0]) + 1
        log.info("Hourly cap (%d/hr) reached, waiting %.0fs...", cap, wait_s)
        await asyncio.sleep(max(wait_s, 1))
        now = time.time()
        while send_timestamps and now - send_timestamps[0] > 3600:
            send_timestamps.popleft()


async def notify_owner(text):
    try:
        await client.send_message("me", text)
    except Exception:
        pass


async def forward_worker():
    """Pulls messages off the queue and forwards them to all targets.

    Safety measures against flood waits / account restrictions:
      - waits `delay` seconds (+ small random jitter) between every send,
        never sends in a tight burst
      - respects an optional hourly cap across all targets
      - on a FloodWaitError, sleeps the exact time Telegram asks for, then
        DOUBLES the configured delay going forward (adaptive backoff) so it
        stops repeating the same mistake
      - after 3 flood waits in a row, auto-pauses forwarding entirely and
        pings you in Saved Messages instead of hammering the API further
    """
    while True:
        message = await forward_queue.get()
        cfg = storage.get_config()

        if cfg.get("auto_paused") or not cfg["forwarding"]:
            forward_queue.task_done()
            continue

        targets = list(cfg["targets"].keys())
        for target_id in targets:
            cfg = storage.get_config()
            if cfg.get("auto_paused"):
                break

            await wait_for_hourly_cap()

            try:
                await client.forward_messages(int(target_id), message)
                storage.increment_forwarded()
                send_timestamps.append(time.time())

            except FloodWaitError as e:
                cfg = storage.register_flood(e.seconds)
                log.warning(
                    "FloodWaitError: sleeping %ss (consecutive floods: %d)",
                    e.seconds, cfg["consecutive_floods"],
                )

                if cfg["consecutive_floods"] >= MAX_CONSECUTIVE_FLOODS:
                    storage.auto_pause()
                    await notify_owner(
                        "🟠 **Auto-Forward paused itself**\n\n"
                        f"Hit {cfg['consecutive_floods']} flood waits in a row from Telegram "
                        "(a sign the current delay is too low for your channel volume).\n\n"
                        f"Waiting out the last one ({e.seconds}s), then stopping until you "
                        "tap Resume in .menu. Consider raising the delay or hourly cap before "
                        "resuming."
                    )
                    await asyncio.sleep(e.seconds)
                    break

                # adaptive backoff: bump the delay up so we stop tripping this
                new_delay = min(max(cfg["delay"] * 2, e.seconds), 300)
                storage.set_delay(new_delay)
                await asyncio.sleep(e.seconds)
                try:
                    await client.forward_messages(int(target_id), message)
                    storage.increment_forwarded()
                    send_timestamps.append(time.time())
                except Exception as e2:
                    log.error("Failed forwarding to %s after wait: %s", target_id, e2)

            except Exception as e:
                log.error("Failed forwarding to %s: %s", target_id, e)

            cfg = storage.get_config()
            delay = cfg["delay"]
            if delay > 0:
                jitter = random.uniform(0, min(2, delay * 0.2))
                await asyncio.sleep(delay + jitter)

        forward_queue.task_done()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def main():
    await client.start()
    me = await client.get_me()
    log.info("Logged in as %s (id=%s)", me.first_name, me.id)
    await refresh_subscriptions()
    asyncio.create_task(forward_worker())
    log.info("Bot is running. Send .menu in your Saved Messages to open the control panel.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
