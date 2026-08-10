import json
import os
import uuid
from datetime import datetime, timezone

DEFAULT_STATE_FILE = "cinema_booking_state.json"


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"items": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_ticket_request(provider: str, movie_query: str, date_range: list[str],
                        quantity: int = 2, prefer_sweetbox: bool = False,
                        cinema_priority: list[str] | None = None,
                        state_file: str = DEFAULT_STATE_FILE) -> dict:
    data = _load(state_file)
    item = {
        "id": uuid.uuid4().hex[:8],
        "provider": provider,
        "movie_query": movie_query,
        "date_range": list(date_range),
        "quantity": quantity,
        "prefer_sweetbox": prefer_sweetbox,
        "cinema_priority": cinema_priority or [],
        "status": "pending",
        "instant": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["items"].append(item)
    _save(data, state_file)
    return item


def list_ticket_requests(state_file: str = DEFAULT_STATE_FILE) -> list[dict]:
    return _load(state_file)["items"]


def get_item(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> dict | None:
    for item in list_ticket_requests(state_file):
        if item["id"] == item_id:
            return item
    return None


def update_item(item_id: str, path: str = DEFAULT_STATE_FILE, **fields) -> dict | None:
    data = _load(path)
    for item in data["items"]:
        if item["id"] == item_id:
            item.update(fields)
            _save(data, path)
            return item
    return None


def remove_ticket_request(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> bool:
    data = _load(state_file)
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    _save(data, state_file)
    return len(data["items"]) < before
