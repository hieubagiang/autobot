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
import time

from xeca_client import XecaClient, get_direction, is_sale_open, select_preferred_bus_time
from xeca_state import DEFAULT_STATE_FILE, add_item, get_item, list_items, remove_item, update_item

WATCH_SERVICE = "xeca-watch.service"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_BOOK_SCRIPT = os.path.join(SCRIPT_DIR, "xeca_auto_book.py")

INSTANT_RETRY_NOT_OPEN_SECONDS = 60  # sale not open at all — changes ~once/day, no rush
INSTANT_RETRY_SOLD_OUT_SECONDS = 15  # sale open but no matching seat — racing other buyers
INSTANT_RETRY_ERROR_SECONDS = 30
INSTANT_EXPIRY_BUFFER_SECONDS = 15


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


def instant_lock_loop(item_id: str, stop_event, notify,
                       state_file: str = DEFAULT_STATE_FILE, env_file: str = ".env"):
    """Runs until `stop_event` is set (or the item is removed / instant is turned off):
    immediately locks a seat + creates an (unpaid) order for `item_id`, then — instead of
    stopping — waits until that hold's ~30 min deadline passes (when Văn Minh auto-releases
    the seat back to "empty") and immediately re-locks a fresh seat, repeating forever.
    Payment is never automated here; the user pays within whichever hold window they catch,
    or lets it lapse and get relocked. Meant to guarantee "always holding *a* seat" on a
    route/date rather than a specific seat number — each cycle re-runs seat selection, so a
    different (still-preferred-order) seat may be picked if the previous one gets taken.

    Passenger name/phone are re-read from state.json (via /passenger) each cycle rather than
    captured once at thread-start, so an edit takes effect from the next re-lock onward.

    Retries fast (INSTANT_RETRY_SOLD_OUT_SECONDS) when sale is open but nothing matches the
    seat preference yet ("camping" a sold-out date for a freed seat — a race against other
    customers/bots, worth polling tightly) and slow (INSTANT_RETRY_NOT_OPEN_SECONDS) when
    sale isn't open at all (changes ~once/day, no rush).

    Imported lazily (not at module top) to avoid a xeca_auto_book <-> xeca_control import
    cycle, since xeca_auto_book already imports xeca_state directly."""
    from xeca_auto_book import NoSeatsAvailableError, SaleNotOpenError, execute_booking, plan_booking
    from xeca_state import get_passenger_info

    client = XecaClient()
    while not stop_event.is_set():
        item = get_item(item_id, state_file)
        if not item or not item.get("instant"):
            return
        direction = get_direction(item["direction"])

        cust_name, cust_mobile = get_passenger_info(state_file)
        if not cust_name or not cust_mobile:
            notify(f"⚠️ [instant {item_id}] Thiếu thông tin hành khách (/passenger) — tạm dừng, thử lại sau {INSTANT_RETRY_ERROR_SECONDS}s.")
            if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                return
            continue

        try:
            plan = plan_booking(client, item["depart_date"], direction, item.get("quantity", 1),
                                 item.get("pickup_name"), item.get("dropoff_name"),
                                 allow_middle_seats=True)
        except SaleNotOpenError as e:
            notify(f"⏳ [instant {item_id}] {e} (thử lại sau {INSTANT_RETRY_NOT_OPEN_SECONDS}s)")
            if stop_event.wait(INSTANT_RETRY_NOT_OPEN_SECONDS):
                return
            continue
        except NoSeatsAvailableError as e:
            notify(f"🏕️ [instant {item_id}] Đang camp — {e} (thử lại sau {INSTANT_RETRY_SOLD_OUT_SECONDS}s)")
            if stop_event.wait(INSTANT_RETRY_SOLD_OUT_SECONDS):
                return
            continue
        except RuntimeError as e:
            notify(f"⏳ [instant {item_id}] Chưa giữ được ghế: {e} (thử lại sau {INSTANT_RETRY_NOT_OPEN_SECONDS}s)")
            if stop_event.wait(INSTANT_RETRY_NOT_OPEN_SECONDS):
                return
            continue

        try:
            result = execute_booking(
                client, plan, direction, item["depart_date"], cust_name, cust_mobile,
                None, None, open_browser=False,
                message_prefix="🔒 [instant] Đã tự động giữ ghế (chưa thanh toán):",
            )
        except Exception as e:
            notify(f"⚠️ [instant {item_id}] Lỗi khi giữ ghế: {e} (thử lại sau {INSTANT_RETRY_ERROR_SECONDS}s)")
            if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                return
            continue

        update_item(item_id, path=state_file, status="instant_holding", order_id=result["order_id"],
                    booking=result["booking"])
        seat_names = ", ".join(s["seatDisplayName"] for s in plan["seats"])
        expiry_ms = result["expiry"].get("expiredTime")
        notify(
            f"🔒 [instant {item_id}] Đã giữ ghế {seat_names}. Link: {result['payment_url']}\n"
            f"Thanh toán xong thì /paid {item_id} (sẽ dừng tự relock). "
            f"Chưa thanh toán thì cứ để đó, hết hạn tôi tự giữ lại. /instant {item_id} off để dừng hẳn."
        )

        if not expiry_ms:
            if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                return
            continue
        wait_seconds = max(10, (expiry_ms / 1000) - time.time() + INSTANT_EXPIRY_BUFFER_SECONDS)
        if stop_event.wait(wait_seconds):
            return

    notify(f"🛑 [instant {item_id}] Đã dừng.")
