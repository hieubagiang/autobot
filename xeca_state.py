"""Shared JSON state file: the ticket-request watchlist. The watch service, the Telegram
bot, and any future control surface (e.g. a web UI) all read/write this same file instead
of each hard-coding a single CLI date/direction.

Each item: {id, direction ("HN-HT"|"HT-HN"), depart_date (YYYYMMDD int), quantity,
            status ("pending"|"notified"|"booked"|"cancelled"), pickup_name, dropoff_name}
`pickup_name`/`dropoff_name` are optional per-item overrides; when null the direction's
default from xeca_client.DIRECTIONS is used.
"""

import json
import os
import threading
import time
import uuid

DEFAULT_STATE_FILE = "state.json"
DEFAULT_STATE = {"items": [], "passenger": None}

_lock = threading.Lock()

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05
LOCK_STALE_SECONDS = 30.0  # a lock file older than this survived a crash, not a live holder


class _StateFileLock:
    """Cross-process advisory lock guarding the whole read-modify-write cycle around
    state.json, not just the write. The Telegram bot's instant-lock threads and the
    one-shot `xeca_auto_book.py` subprocess it spawns for /book and /confirm
    (see xeca_control.run_booking) can both read-modify-write this same file at once;
    the in-process `threading.Lock` around save_state() alone doesn't stop two callers
    from both loading a stale copy, editing it, and one's save clobbering the other's.

    Implemented via exclusive file creation (portable, no fcntl/msvcrt dependency) rather
    than flock/msvcrt.locking, since this file is shared across a bot process and
    subprocesses on both the deployment target (Linux/systemd) and local dev (Windows).
    A lock file older than LOCK_STALE_SECONDS is assumed to be left over from a crashed
    holder and is reclaimed rather than causing every future caller to hang forever."""

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
                    continue  # lock file vanished between the stat and remove — just retry
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Không lấy được lock cho {self.lock_path} sau {LOCK_TIMEOUT_SECONDS}s "
                        "(tiến trình khác đang giữ quá lâu?)"
                    )
                time.sleep(LOCK_POLL_SECONDS)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.lock_path)
        except OSError:
            pass


def load_state(path: str = DEFAULT_STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {"items": [], "passenger": None}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"items": data.get("items", []), "passenger": data.get("passenger")}


def save_state(state: dict, path: str = DEFAULT_STATE_FILE):
    with _lock:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def add_item(direction: str, depart_date: int, quantity: int = 1,
             pickup_name: str | None = None, dropoff_name: str | None = None,
             path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        state = load_state(path)
        item = {
            "id": uuid.uuid4().hex[:8],
            "direction": direction,
            "depart_date": depart_date,
            "quantity": quantity,
            "status": "pending",
            "pickup_name": pickup_name,
            "dropoff_name": dropoff_name,
        }
        state["items"].append(item)
        save_state(state, path)
        return item


def remove_item(item_id: str, path: str = DEFAULT_STATE_FILE) -> bool:
    with _StateFileLock(path):
        state = load_state(path)
        before = len(state["items"])
        state["items"] = [i for i in state["items"] if i["id"] != item_id]
        save_state(state, path)
        return len(state["items"]) < before


def update_item(item_id: str, path: str = DEFAULT_STATE_FILE, **fields) -> dict | None:
    with _StateFileLock(path):
        state = load_state(path)
        for item in state["items"]:
            if item["id"] == item_id:
                item.update(fields)
                save_state(state, path)
                return item
        return None


def list_items(path: str = DEFAULT_STATE_FILE) -> list[dict]:
    return load_state(path)["items"]


def get_item(item_id: str, path: str = DEFAULT_STATE_FILE) -> dict | None:
    for item in list_items(path):
        if item["id"] == item_id:
            return item
    return None


def get_passenger(path: str = DEFAULT_STATE_FILE) -> dict | None:
    return load_state(path).get("passenger")


def set_passenger(name: str, phone: str, path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        state = load_state(path)
        state["passenger"] = {"name": name, "phone": phone}
        save_state(state, path)
        return state["passenger"]


def get_passenger_info(path: str = DEFAULT_STATE_FILE) -> tuple[str | None, str | None]:
    """Passenger name/phone used when creating a real order. Prefers the value set via
    /passenger (state.json, changeable without touching the server), falls back to the
    .env defaults (XECA_PASSENGER_NAME/XECA_PASSENGER_PHONE) if never set."""
    p = get_passenger(path)
    if p and p.get("name") and p.get("phone"):
        return p["name"], p["phone"]
    return os.environ.get("XECA_PASSENGER_NAME"), os.environ.get("XECA_PASSENGER_PHONE")
