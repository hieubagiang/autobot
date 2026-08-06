"""Control operations shared by the Telegram bot and any future control surface (e.g. a
web UI): watchlist CRUD, systemd service actions, and triggering a booking attempt.
Kept separate from the bot/CLI so a future web API can import the same functions instead
of duplicating logic.

Runs on the server as root (systemctl/journalctl require it) — same trust boundary as the
rest of this deployment.
"""

import os
import subprocess
import sys

from xeca_client import XecaClient, get_direction, is_sale_open, select_preferred_bus_time
from xeca_state import DEFAULT_STATE_FILE, add_item, get_item, list_items, remove_item

WATCH_SERVICE = "xeca-watch.service"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_BOOK_SCRIPT = os.path.join(SCRIPT_DIR, "xeca_auto_book.py")


def add_ticket_request(direction: str, depart_date: int, quantity: int = 1,
                        pickup_name: str | None = None, dropoff_name: str | None = None,
                        state_file: str = DEFAULT_STATE_FILE) -> dict:
    get_direction(direction)  # validates, raises ValueError if unknown
    return add_item(direction, depart_date, quantity, pickup_name, dropoff_name, path=state_file)


def remove_ticket_request(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> bool:
    return remove_item(item_id, path=state_file)


def list_ticket_requests(state_file: str = DEFAULT_STATE_FILE) -> list[dict]:
    return list_items(state_file)


def check_item_sale_status(item: dict) -> dict:
    """Live-check whether an item's sale is open right now (read-only API calls)."""
    direction = get_direction(item["direction"])
    client = XecaClient()
    bus_times = client.get_bus_times(item["depart_date"], direction["from_province_id"], direction["to_province_id"])
    bus_time = select_preferred_bus_time(bus_times)
    if not bus_time:
        return {"open": False, "reason": "Không có chuyến nào trong ngày."}
    detail = client.get_detail_bus_time(
        depart_date=item["depart_date"], bus_time_id=bus_time["id"], bus_hop_id=bus_time["bus_hop_id"],
        bus_stage_id=bus_time["bus_stage_id"], from_province_id=direction["from_province_id"],
        to_province_id=direction["to_province_id"],
    )
    open_status, reason = is_sale_open(detail.get("busStageSpecialRules", []), item["depart_date"],
                                        bus_time.get("bus_stage_id"))
    return {"open": open_status, "reason": reason, "bus_time": bus_time}


def get_status(state_file: str = DEFAULT_STATE_FILE) -> dict:
    items = list_items(state_file)
    enriched = []
    for item in items:
        entry = dict(item)
        if item.get("status") == "pending":
            try:
                entry["live"] = check_item_sale_status(item)
            except Exception as e:
                entry["live"] = {"error": str(e)}
        enriched.append(entry)
    return {"service_active": systemctl_is_active(WATCH_SERVICE), "items": enriched}


def systemctl_is_active(service: str) -> str:
    result = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def service_control(action: str, service: str = WATCH_SERVICE) -> str:
    if action not in ("start", "stop", "restart"):
        raise ValueError("action phải là start/stop/restart")
    result = subprocess.run(["systemctl", action, service], capture_output=True, text=True, timeout=20)
    return (result.stdout + result.stderr).strip() or f"OK: {action} {service}"


def get_logs(n: int = 20, service: str = WATCH_SERVICE) -> str:
    result = subprocess.run(["journalctl", "-u", service, "-n", str(n), "--no-pager"],
                             capture_output=True, text=True, timeout=15)
    return result.stdout


def run_booking(item_id: str, confirm: bool, state_file: str = DEFAULT_STATE_FILE,
                 env_file: str = ".env", timeout: int = 60) -> tuple[int, str]:
    """Runs xeca_auto_book.py --item-id <id> --once [--confirm-real-booking] as a one-shot
    subprocess and returns (returncode, combined stdout+stderr)."""
    item = get_item(item_id, state_file)
    if not item:
        raise ValueError(f"Không tìm thấy item id={item_id}")

    args = [sys.executable, AUTO_BOOK_SCRIPT, "--item-id", item_id, "--once",
            "--state-file", state_file, "--env-file", env_file]
    if confirm:
        args.append("--confirm-real-booking")

    result = subprocess.run(args, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=timeout)
    return result.returncode, (result.stdout + result.stderr)
