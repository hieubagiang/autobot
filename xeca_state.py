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
import uuid

DEFAULT_STATE_FILE = "state.json"
DEFAULT_STATE = {"items": [], "passenger": None}

_lock = threading.Lock()


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
    state = load_state(path)
    before = len(state["items"])
    state["items"] = [i for i in state["items"] if i["id"] != item_id]
    save_state(state, path)
    return len(state["items"]) < before


def update_item(item_id: str, path: str = DEFAULT_STATE_FILE, **fields) -> dict | None:
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
