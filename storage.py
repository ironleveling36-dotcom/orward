"""
Simple JSON-backed storage for the forwarder's config.
Keeps: source channels, target channels, delay (seconds), forwarding on/off.
"""

import json
import os
import threading

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

_lock = threading.Lock()

DEFAULTS = {
    "sources": {},   # {"-1001234567890": "Channel Name"}
    "targets": {},   # {"-1009876543210": "Channel Name"}
    "delay": 5,       # seconds between each forwarded message
    "forwarding": False,
    "forwarded_count": 0,
    "consecutive_floods": 0,   # resets to 0 after a clean forward
    "auto_paused": False,      # true if the safety system paused forwarding
    "max_per_hour": 0,         # 0 = unlimited
}


def _load():
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def _save(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_config():
    with _lock:
        return _load()


def update_config(**kwargs):
    with _lock:
        data = _load()
        data.update(kwargs)
        _save(data)
        return data


def add_source(chat_id: str, name: str):
    with _lock:
        data = _load()
        data["sources"][str(chat_id)] = name
        _save(data)
        return data


def add_target(chat_id: str, name: str):
    with _lock:
        data = _load()
        data["targets"][str(chat_id)] = name
        _save(data)
        return data


def remove_source(chat_id: str):
    with _lock:
        data = _load()
        data["sources"].pop(str(chat_id), None)
        _save(data)
        return data


def remove_target(chat_id: str):
    with _lock:
        data = _load()
        data["targets"].pop(str(chat_id), None)
        _save(data)
        return data


def set_delay(seconds: int):
    with _lock:
        data = _load()
        data["delay"] = int(seconds)
        _save(data)
        return data


def set_forwarding(on: bool):
    with _lock:
        data = _load()
        data["forwarding"] = bool(on)
        _save(data)
        return data


def increment_forwarded():
    with _lock:
        data = _load()
        data["forwarded_count"] = data.get("forwarded_count", 0) + 1
        data["consecutive_floods"] = 0
        _save(data)
        return data


def register_flood(seconds: int):
    """Track a flood wait. Returns the updated config so the caller can
    decide whether to auto-pause."""
    with _lock:
        data = _load()
        data["consecutive_floods"] = data.get("consecutive_floods", 0) + 1
        _save(data)
        return data


def auto_pause():
    with _lock:
        data = _load()
        data["forwarding"] = False
        data["auto_paused"] = True
        _save(data)
        return data


def clear_auto_pause():
    with _lock:
        data = _load()
        data["auto_paused"] = False
        _save(data)
        return data


def set_max_per_hour(n: int):
    with _lock:
        data = _load()
        data["max_per_hour"] = int(n)
        _save(data)
        return data
