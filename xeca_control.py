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

from xeca_client import (
    XecaClient,
    get_direction,
    is_in_tight_window,
    is_sale_open,
    next_poll_interval,
    parse_target_time,
    select_preferred_bus_time,
)
from xeca_state import DEFAULT_STATE_FILE, add_item, get_item, list_items, remove_item, update_item

WATCH_SERVICE = "xeca-watch.service"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_BOOK_SCRIPT = os.path.join(SCRIPT_DIR, "xeca_auto_book.py")

INSTANT_RETRY_NOT_OPEN_SECONDS = 60  # sale not open at all — changes ~once/day, no rush
INSTANT_RETRY_SOLD_OUT_SECONDS = 15  # sale open but no matching seat — racing other buyers
INSTANT_RETRY_ERROR_SECONDS = 30
INSTANT_EXPIRY_BUFFER_SECONDS = 15
INSTANT_WARM_UP_LEAD_SECONDS = 8  # fire a cheap GET this long before a hold expires, so the
# re-lock POST that follows doesn't pay a TCP+TLS handshake on a connection the server almost
# certainly closed during the ~30 min idle wait — same technique as
# tqtt_register_batch.warm_up_clients().
INSTANT_REMINDER_LEAD_SECONDS = 300  # nudge the user this long before a hold expires, so a
# forgotten payment doesn't quietly lapse — never auto-detected as "paid", just a heads-up.
INSTANT_CAMP_NOTIFY_INTERVAL_SECONDS = 300  # while continuously camping a sold-out date,
# only re-notify this often — the retry loop itself still polls every
# INSTANT_RETRY_SOLD_OUT_SECONDS regardless, this only throttles how often we tell the user
# about it, since notifying on every single 15s retry (up to 240/hour) risks tripping
# Telegram's own flood limit — which, ironically, would then hit the exact failure mode
# _safe_notify() below exists to survive.


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
    sale isn't open at all (changes ~once/day, no rush) — unless the item has a
    `target_time` (set via `/instant <id> on <HH:MM>`), in which case the "not open" wait
    tightens way down (see xeca_client.next_poll_interval) in a window around that time,
    since a known opening instant is worth polling near-continuously for instead of
    waiting up to INSTANT_RETRY_NOT_OPEN_SECONDS to notice it flipped.

    If called for an item that's already `instant_holding` with an unexpired
    `booking.hold_expiry_ms` (e.g. this loop is being resumed after a bot restart via
    xeca_telegram_bot.resume_instant_items), resumes waiting on that existing hold instead
    of immediately locking a brand new seat — otherwise every restart would abandon a
    perfectly good hold and create a redundant real order on Văn Minh's system.

    Imported lazily (not at module top) to avoid a xeca_auto_book <-> xeca_control import
    cycle, since xeca_auto_book already imports xeca_state directly."""
    from xeca_auto_book import NoSeatsAvailableError, SaleNotOpenError, execute_booking, plan_booking
    from xeca_state import get_passenger_info

    def _safe_notify(text: str):
        # `notify` is Bot.send(), which does resp.raise_for_status() on the Telegram API
        # call — a flood-limit 429 or any other transient Telegram error would otherwise
        # propagate straight out of this loop and silently kill the background thread,
        # exactly the failure mode the plan_booking/execute_booking except-blocks below
        # already guard against. A notification failing to send must never do that.
        try:
            notify(text)
        except Exception as e:
            print(f"[WARN] [instant {item_id}] Gửi Telegram thất bại (không ảnh hưởng vòng lặp): {e}")

    client = XecaClient()
    last_camp_notify = 0.0
    was_tight = False
    target_time_str = None
    target_ts = None
    while not stop_event.is_set():
        item = get_item(item_id, state_file)
        if not item or not item.get("instant"):
            return
        direction = get_direction(item["direction"])

        # Optional /instant <id> on <HH:MM> — if the user knows the announced opening
        # time, tighten the SaleNotOpenError retry cadence way down around it (see
        # xeca_client.next_poll_interval) instead of waiting the full
        # INSTANT_RETRY_NOT_OPEN_SECONDS each cycle. Only re-parse when the stored
        # target_time string actually changes (so it can be set/changed live via a fresh
        # /instant on <HH:MM>) — NOT on every iteration: parse_target_time() resolves to
        # "the next occurrence from now", so re-parsing the SAME string every loop would
        # make the target silently jump to tomorrow the instant "now" ticks past it,
        # collapsing TIGHT_WINDOW_AFTER_SECONDS to ~0 right when it matters most.
        raw_target_time = item.get("target_time")
        if raw_target_time != target_time_str:
            target_time_str = raw_target_time
            target_ts = parse_target_time(raw_target_time) if raw_target_time else None
        tight_now = is_in_tight_window(target_ts)
        if tight_now and not was_tight:
            _safe_notify(f"⏱️ [instant {item_id}] Vào khung giờ gần target-time ({item['target_time']}) — chuyển sang poll dồn dập.")
        elif was_tight and not tight_now:
            _safe_notify(f"[instant {item_id}] Đã ra khỏi khung giờ target-time mà vẫn chưa mở — quay lại nhịp poll bình thường.")
        was_tight = tight_now

        # A hold from before a bot restart (xeca_telegram_bot.resume_instant_items runs this
        # loop fresh on every startup) may still be valid — resuming it instead of always
        # re-locking from scratch avoids abandoning a good hold and creating a redundant new
        # order, which is still a real side effect on Văn Minh's system each time.
        existing_booking = item.get("booking") or {}
        existing_expiry_ms = existing_booking.get("hold_expiry_ms")
        resuming_valid_hold = (
            item.get("status") == "instant_holding"
            and existing_expiry_ms
            and existing_expiry_ms / 1000 - time.time() > INSTANT_EXPIRY_BUFFER_SECONDS
        )

        if resuming_valid_hold:
            seat_names = ", ".join(existing_booking.get("seat_names") or [])
            payment_url = existing_booking.get("payment_url")
            expiry_ms = existing_expiry_ms
        else:
            cust_name, cust_mobile = get_passenger_info(state_file)
            if not cust_name or not cust_mobile:
                _safe_notify(f"⚠️ [instant {item_id}] Thiếu thông tin hành khách (/passenger) — tạm dừng, thử lại sau {INSTANT_RETRY_ERROR_SECONDS}s.")
                if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                    return
                continue

            try:
                plan = plan_booking(client, item["depart_date"], direction, item.get("quantity", 1),
                                     item.get("pickup_name"), item.get("dropoff_name"),
                                     allow_middle_seats=True)
            except SaleNotOpenError as e:
                wait_seconds = next_poll_interval(INSTANT_RETRY_NOT_OPEN_SECONDS, 0, target_ts)
                if not tight_now:
                    _safe_notify(f"⏳ [instant {item_id}] {e} (thử lại sau {int(wait_seconds)}s)")
                if stop_event.wait(wait_seconds):
                    return
                continue
            except NoSeatsAvailableError as e:
                # Retry every INSTANT_RETRY_SOLD_OUT_SECONDS regardless (that's the race), but
                # only *notify* about it every INSTANT_CAMP_NOTIFY_INTERVAL_SECONDS — see that
                # constant's comment for why spamming a message per retry is actively harmful.
                now = time.time()
                if now - last_camp_notify >= INSTANT_CAMP_NOTIFY_INTERVAL_SECONDS:
                    _safe_notify(f"🏕️ [instant {item_id}] Đang camp — {e} (đang thử lại mỗi {INSTANT_RETRY_SOLD_OUT_SECONDS}s)")
                    last_camp_notify = now
                if stop_event.wait(INSTANT_RETRY_SOLD_OUT_SECONDS):
                    return
                continue
            except RuntimeError as e:
                _safe_notify(f"⏳ [instant {item_id}] Chưa giữ được ghế: {e} (thử lại sau {INSTANT_RETRY_NOT_OPEN_SECONDS}s)")
                if stop_event.wait(INSTANT_RETRY_NOT_OPEN_SECONDS):
                    return
                continue
            except Exception as e:
                # A transient error here (network blip, unexpected API shape, ...) is not a
                # RuntimeError subclass, so without this catch-all it would propagate out of the
                # loop and silently kill this background thread — ending "always holding a seat"
                # until someone notices and restarts the bot. Treat it as just another retryable
                # failure instead, same as the SaleNotOpenError/NoSeatsAvailableError cases above.
                _safe_notify(f"⚠️ [instant {item_id}] Lỗi khi lập kế hoạch: {e} (thử lại sau {INSTANT_RETRY_ERROR_SECONDS}s)")
                if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                    return
                continue

            try:
                result = execute_booking(
                    client, plan, direction, item["depart_date"], cust_name, cust_mobile,
                    None, None, open_browser=False,
                    message_prefix="🔒 [instant] Đã tự động giữ ghế (chưa thanh toán):",
                )
            except Exception as e:
                _safe_notify(f"⚠️ [instant {item_id}] Lỗi khi giữ ghế: {e} (thử lại sau {INSTANT_RETRY_ERROR_SECONDS}s)")
                if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                    return
                continue

            update_item(item_id, path=state_file, status="instant_holding", order_id=result["order_id"],
                        booking=result["booking"])
            seat_names = ", ".join(s["seatDisplayName"] for s in plan["seats"])
            payment_url = result["payment_url"]
            expiry_ms = result["expiry"].get("expiredTime")
            _safe_notify(
                f"🔒 [instant {item_id}] Đã giữ ghế {seat_names}. Link: {payment_url}\n"
                f"Thanh toán xong thì /paid {item_id} (sẽ dừng tự relock). "
                f"Chưa thanh toán thì cứ để đó, hết hạn tôi tự giữ lại. /instant {item_id} off để dừng hẳn."
            )

        if not expiry_ms:
            if stop_event.wait(INSTANT_RETRY_ERROR_SECONDS):
                return
            continue
        wait_seconds = max(10, (expiry_ms / 1000) - time.time() + INSTANT_EXPIRY_BUFFER_SECONDS)

        reminder_lead = min(INSTANT_REMINDER_LEAD_SECONDS, wait_seconds)
        if wait_seconds > reminder_lead:
            if stop_event.wait(wait_seconds - reminder_lead):
                return
            _safe_notify(
                f"⏰ [instant {item_id}] Ghế {seat_names} sắp hết hạn giữ chỗ trong "
                f"~{int(reminder_lead // 60)} phút. Thanh toán ngay nếu muốn giữ ghế này: "
                f"{payment_url}\nChưa thanh toán thì cứ để đó, hết hạn tôi tự giữ lại."
            )
            wait_seconds = reminder_lead

        lead = min(INSTANT_WARM_UP_LEAD_SECONDS, wait_seconds)
        if stop_event.wait(wait_seconds - lead):
            return
        try:
            client.get_bus_times(item["depart_date"], direction["from_province_id"], direction["to_province_id"])
        except Exception:
            pass  # best-effort — a cold connection at re-lock time costs latency, not correctness
        if lead and stop_event.wait(lead):
            return

    _safe_notify(f"🛑 [instant {item_id}] Đã dừng.")
