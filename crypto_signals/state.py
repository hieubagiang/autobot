"""Shared JSON state file for crypto_signals: watched channels + tracked signals.

`listener.py` (writes continuously as new messages arrive) and `telegram_bot.py`
(reads for /status, /open, /listchannels; writes on /addchannel, /removechannel) are two
separate processes touching the same file -- same lock-file-based approach as
`xeca_state.py`'s `_StateFileLock` (exclusive-create + stale-lock reclaim), not just an
in-process threading.Lock.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

DEFAULT_STATE_FILE = "crypto_signals_state.json"

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05
LOCK_STALE_SECONDS = 30.0


class _StateFileLock:
    def __init__(self, path: str):
        self.lock_path = path + ".lock"
        self.fd = None

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > LOCK_STALE_SECONDS:
                        os.remove(self.lock_path)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Không lấy được lock cho {self.lock_path} sau {LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(LOCK_POLL_SECONDS)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.lock_path)
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: str = DEFAULT_STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {"channels": [], "signals": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"channels": data.get("channels", []), "signals": data.get("signals", [])}


def save_state(state: dict, path: str = DEFAULT_STATE_FILE) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def add_channel(username: str, kind: str = "signal", path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        st = load_state(path)
        if any(c["username"] == username for c in st["channels"]):
            raise ValueError(f"Kênh '{username}' đã có trong danh sách.")
        channel = {"username": username, "kind": kind, "added_at": _now_iso()}
        st["channels"].append(channel)
        save_state(st, path)
        return channel


def remove_channel(username: str, path: str = DEFAULT_STATE_FILE) -> bool:
    with _StateFileLock(path):
        st = load_state(path)
        before = len(st["channels"])
        st["channels"] = [c for c in st["channels"] if c["username"] != username]
        save_state(st, path)
        return len(st["channels"]) < before


def list_channels(path: str = DEFAULT_STATE_FILE) -> list:
    return load_state(path)["channels"]


def get_channel(username: str, path: str = DEFAULT_STATE_FILE) -> dict | None:
    for c in list_channels(path):
        if c["username"] == username:
            return c
    return None


def list_signals(path: str = DEFAULT_STATE_FILE) -> list:
    return load_state(path)["signals"]


def add_signal(channel: str, coin: str, direction: str, entry: list, targets: list,
                targets_plus: bool, sl: float, leverage: str, scalp: bool,
                path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        st = load_state(path)
        now = _now_iso()
        signal = {
            "id": uuid.uuid4().hex[:8],
            "channel": channel,
            "coin": coin,
            "direction": direction,
            "scalp": scalp,
            "entry": entry,
            "targets": targets,
            "targets_plus": targets_plus,
            "sl": sl,
            "leverage": leverage,
            "status": "open",
            "hits": [],
            "opened_at": now,
            "last_update_at": now,
        }
        st["signals"].append(signal)
        save_state(st, path)
        return signal


def find_open_signal(channel: str, coin: str, path: str = DEFAULT_STATE_FILE) -> dict | None:
    candidates = [
        s for s in load_state(path)["signals"]
        if s["channel"] == channel and s["coin"] == coin and s["status"] != "closed"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s["opened_at"])


def append_hit(signal_id: str, hit: dict, path: str = DEFAULT_STATE_FILE) -> dict | None:
    with _StateFileLock(path):
        st = load_state(path)
        for s in st["signals"]:
            if s["id"] == signal_id:
                s["hits"].append(hit)
                if hit.get("update_kind") == "tp_hit":
                    s["status"] = "tp_hit"
                s["last_update_at"] = _now_iso()
                save_state(st, path)
                return s
        return None
