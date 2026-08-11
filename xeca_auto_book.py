"""Phase 2: auto-book Xeca (Văn Minh) ticket(s) the moment sale opens for a target date.

Picks the latest "Xe giường nằm" (regular bus) departure of the day, falling back to
Limousine only if no regular bus has seats. Picks the best seat(s) per the user's
preference (floor 2 > floor 1, letter E>A / F>B, seat number 3>2>4>1>5>6), preferring
adjacent seats when booking more than one. Creates the order and initiates VNPay payment,
then sends the payment link/QR to Telegram for the user to complete manually within the
transaction countdown — the bank/wallet confirmation step cannot be automated.

IMPORTANT: --dry-run only logs the chosen bus/seats/pickup/dropoff, it does NOT lock
seats or create an order. Creating a real order requires --confirm-real-booking, since it
has a real-world side effect (a pending order + temporary seat hold in Văn Minh's system).

Usage:
    python xeca_auto_book.py --depart-date 20260808 --dry-run
    python xeca_auto_book.py --depart-date 20260827 --quantity 2 --confirm-real-booking
    python xeca_auto_book.py --item-id ab12cd34 --confirm-real-booking   # from the watchlist
"""

import argparse
import os
import random
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from xeca_client import (
    XecaClient,
    filter_bus_times,
    find_boarding_point,
    get_direction,
    is_coastal_route,
    is_sale_open,
    load_env_file,
    rank_bus_times,
    select_seats,
    send_telegram_message,
    send_telegram_photo,
)
from xeca_state import DEFAULT_STATE_FILE, get_item, get_passenger_info, update_item

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


class SaleNotOpenError(RuntimeError):
    """Sale hasn't opened for this date yet. Changes ~once/day — safe to retry slowly."""


class NoSeatsAvailableError(RuntimeError):
    """Sale is open but no seat matching the preference is free right now (sold out, or
    only worse seats left). Seats free up when someone else's ~30 min unpaid hold lapses
    or an order is cancelled — worth retrying much faster than SaleNotOpenError to win the
    freed seat before someone else does."""


class BookingResponseShapeError(RuntimeError):
    """create_order()/initiate_payment() returned 200 OK but without the field we expect
    (order_id / redirect_url) — Xeca's backend response shape has drifted from what this
    code was reverse-engineered against. Same root cause class as the TQTT field-name
    incident (2026-08-08): a stale request/response-shape assumption silently produces
    garbage instead of failing loudly. Not a "not open yet" condition — retrying blindly
    won't fix a schema mismatch, so callers should surface this loudly instead of quietly
    treating it like any other retryable RuntimeError."""


def format_point_names(points: list[dict]) -> str:
    """Lists every point name find_boarding_point() could actually match on this bus/date
    (some route variants — a different operator, or a different bus_stage config, not just
    the coastal/regular split already handled below — drop the direction's usual default
    entirely; e.g. some Hà Tĩnh→Hà Nội buses use "BX YÊN NGHĨA" instead of the usual
    "Số 275 Nguyễn Trãi" home-zone). Surfaced in the not-found error so the user can
    immediately /setpickup or /setdropoff to one of these instead of guessing."""
    names = [p.get("boarding_point_name") or p.get("home_pickup_zone_name") for p in points]
    return ", ".join(n for n in names if n) or "(không có điểm nào)"


def _matched_point_name(point: dict) -> str:
    return point.get("home_pickup_zone_name") or point.get("boarding_point_name")


def resolve_points(client: XecaClient, bus_time: dict, depart_date: int, direction: dict,
                    pickup_override: str | list[str] | None,
                    dropoff_override: str | list[str] | None) -> tuple[dict, dict, str, str]:
    """`pickup_override`/`dropoff_override` (and the direction's default_*/coastal_* below)
    are each an ORDERED list of preferred point names (a single string is also accepted,
    treated as a 1-item list) — find_boarding_point() tries them in order and returns
    the first one actually available on THIS bus, so a route variant missing the usual
    top preference (see DIRECTIONS' "BX YÊN NGHĨA" comment) falls through to the next
    preference instead of failing outright."""
    bus_time_id = bus_time["id"]
    pickup_points = client.get_boarding_points(bus_time_id, depart_date, point_type=1)
    dropoff_points = client.get_boarding_points(bus_time_id, depart_date, point_type=2)

    coastal = is_coastal_route(bus_time)

    if pickup_override:
        pickup_names = pickup_override
    elif coastal and direction.get("coastal_pickup_name"):
        pickup_names = direction["coastal_pickup_name"]
    else:
        pickup_names = direction.get("default_pickup_name")
    if not pickup_names:
        raise RuntimeError(
            f"Chưa cấu hình điểm đón cho chiều '{direction['label']}'. "
            f"Dùng --pickup-name (hoặc /setpickup <id> <tên1>, <tên2>, ...)."
        )
    pickup = find_boarding_point(pickup_points, pickup_names)
    if not pickup:
        raise RuntimeError(
            f"Không tìm thấy điểm đón nào trong {pickup_names} cho chuyến {bus_time_id}. "
            f"Điểm đón khả dụng: {format_point_names(pickup_points)}"
        )
    pickup_name = _matched_point_name(pickup)

    if dropoff_override:
        dropoff_names = dropoff_override
    elif coastal and direction.get("coastal_dropoff_name"):
        dropoff_names = direction["coastal_dropoff_name"]
    else:
        dropoff_names = direction.get("default_dropoff_name")
    if not dropoff_names:
        raise RuntimeError(
            f"Chưa cấu hình điểm trả cho chiều '{direction['label']}'. "
            f"Dùng --dropoff-name (hoặc /setdropoff <id> <tên1>, <tên2>, ...)."
        )
    dropoff = find_boarding_point(dropoff_points, dropoff_names)
    if not dropoff:
        raise RuntimeError(
            f"Không tìm thấy điểm trả nào trong {dropoff_names} cho chuyến {bus_time_id}. "
            f"Điểm trả khả dụng: {format_point_names(dropoff_points)}"
        )
    dropoff_name = _matched_point_name(dropoff)

    return pickup, dropoff, pickup_name, dropoff_name


FIND_BEST_BUS_MAX_WORKERS = 8


def _fetch_detail(client: XecaClient, depart_date: int, direction: dict, bus_time: dict) -> dict:
    return client.get_detail_bus_time(
        depart_date=depart_date,
        bus_time_id=bus_time["id"],
        bus_hop_id=bus_time["bus_hop_id"],
        bus_stage_id=bus_time["bus_stage_id"],
        from_province_id=direction["from_province_id"],
        to_province_id=direction["to_province_id"],
    )


def find_best_available_bus(client: XecaClient, depart_date: int, direction: dict, quantity: int = 1,
                             allow_middle_seats: bool = False, depart_from: str | None = None,
                             depart_to: str | None = None, bus_type: str = "ca_hai"):
    """Tries EVERY bus_time of the day, in `rank_bus_times()` priority order (regular bus >
    Limousine, tối > chiều > sáng, latest first within a band), and returns the first one
    whose sale is open AND has >= quantity seats matching the seat preference. This is what
    lets "camping" a sold-out date retry the whole day's candidates each cycle instead of
    fixating on a single "best" pick that might specifically be full.

    `allow_middle_seats=True` (only ever passed from the instant-lock camping loop) also
    accepts middle-lane C/D seats as a last resort — see `select_seats()`.

    `depart_from`/`depart_to`/`bus_type` (see `xeca_client.filter_bus_times()`)
    narrow the candidate pool to a desired departure time-of-day window and/or bus type
    ("thuong"/"limousine"/"ca_hai") BEFORE ranking — set via /setdeparttime, so e.g.
    camping never locks a 05:00 bus just because it's the only one with seats when the
    user only wants an evening departure, or never locks a regular bus when only
    Limousine is wanted.

    Raises SaleNotOpenError if no candidate's sale has opened yet, or NoSeatsAvailableError
    if sale is open but nothing currently has enough matching seats (both retryable —
    callers should wait and try again; NoSeatsAvailableError especially, since seats free up
    when someone else's ~30 min unpaid hold lapses).

    On a day with many departures (docs note up to ~41), the old sequential fetch-then-check
    per candidate meant N request round-trips back-to-back before reaching a candidate that
    actually had room — costly exactly when "camping" a race for a freed seat is most time
    sensitive. `detail-bus-time` calls for all candidates are fetched concurrently (bounded
    by FIND_BEST_BUS_MAX_WORKERS) so wall-clock time is roughly one round-trip instead of N,
    while the final pick still walks the results in the same rank-priority order as before —
    fetch order (parallel, unordered) never changes which bus/seat gets chosen."""
    bus_times = client.get_bus_times(depart_date, direction["from_province_id"], direction["to_province_id"])
    if not bus_times:
        raise NoSeatsAvailableError("Không có chuyến nào trong ngày.")

    bus_times = filter_bus_times(bus_times, depart_from, depart_to, bus_type)
    if not bus_times:
        raise NoSeatsAvailableError(
            f"Không có chuyến nào khớp khung giờ khởi hành {depart_from or '?'}-{depart_to or '?'} "
            f"/ loại xe cho phép."
        )

    candidates = [b for b in rank_bus_times(bus_times) if int(b.get("empty_seat", 0) or 0) > 0]
    if not candidates:
        raise NoSeatsAvailableError("Tất cả chuyến khớp điều kiện đều đã hết chỗ.")

    details_by_id = {}
    with ThreadPoolExecutor(max_workers=min(FIND_BEST_BUS_MAX_WORKERS, len(candidates))) as pool:
        futures = {pool.submit(_fetch_detail, client, depart_date, direction, bt): bt for bt in candidates}
        for future in as_completed(futures):
            bus_time = futures[future]
            try:
                details_by_id[bus_time["id"]] = future.result()
            except Exception:
                pass  # treat like "couldn't confirm this one" — skipped below, not fatal

    any_open = False
    last_reason = None
    for bus_time in candidates:
        detail = details_by_id.get(bus_time["id"])
        if detail is None:
            continue

        # ALWAYS try selecting real seats first — never let is_sale_open() gate the
        # attempt. Confirmed live 2026-08-10: busStageSpecialRules reported
        # not_allow_book=1 (message "Vé thuộc dịp lễ...") purely as an informational
        # banner Xeca's own site shows on seat click — clicking through still booked
        # successfully on the real site the whole time. Trusting that flag as a hard
        # gate here meant the bot refused to even attempt a real, bookable seat from
        # 08:03-08:05, forcing a manual purchase instead. The seatMap response is the
        # actual authoritative "can I book this" signal; is_sale_open() is now only
        # used below, for the exception type / retry-cadence hint when NOTHING across
        # every candidate yields a seat.
        seats = select_seats(detail.get("seatMap", {}), quantity, allow_middle=allow_middle_seats)
        if len(seats) >= quantity:
            return bus_time, seats

        open_status, reason = is_sale_open(
            detail.get("busStageSpecialRules", []), depart_date, bus_time.get("bus_stage_id"),
        )
        if open_status:
            any_open = True
        else:
            last_reason = reason

    if not any_open:
        raise SaleNotOpenError(f"Chưa mở bán: {last_reason}")
    raise NoSeatsAvailableError(
        f"Đã mở bán nhưng không chuyến nào còn đủ {quantity} ghế phù hợp sở thích trong "
        f"{len(candidates)} chuyến còn chỗ."
    )


def plan_booking(client: XecaClient, depart_date: int, direction: dict, quantity: int = 1,
                  pickup_override: str | None = None, dropoff_override: str | None = None,
                  allow_middle_seats: bool = False, depart_from: str | None = None,
                  depart_to: str | None = None, bus_type: str = "ca_hai") -> dict:
    bus_time, seats = find_best_available_bus(
        client, depart_date, direction, quantity, allow_middle_seats,
        depart_from=depart_from, depart_to=depart_to, bus_type=bus_type,
    )

    pickup, dropoff, pickup_name, dropoff_name = resolve_points(
        client, bus_time, depart_date, direction, pickup_override, dropoff_override,
    )

    return {
        "bus_time": bus_time,
        "seats": seats,
        "pickup": pickup,
        "dropoff": dropoff,
        "pickup_name": pickup_name,
        "dropoff_name": dropoff_name,
    }


VN_TZ = timezone(timedelta(hours=7))


def format_expiry(expiry: dict) -> str:
    """`expiry` is the response of GET /v1/orders/get-book-expired-time, whose
    `expiredTime` is a Unix ms timestamp — the hard deadline to finish payment before the
    seat lock/order is released."""
    ms = expiry.get("expiredTime")
    if not ms:
        return "(không rõ hạn giữ chỗ)"
    dt = datetime.fromtimestamp(ms / 1000, tz=VN_TZ)
    return dt.strftime("%H:%M:%S %d/%m/%Y")


def _point_id(point: dict) -> str:
    zone_id = point.get("home_pickup_zone_id")
    point_id = point.get("boarding_point_id")
    return f"zone_id={zone_id}" if zone_id is not None else f"point_id={point_id}"


def describe_plan(plan: dict, direction: dict) -> str:
    bt = plan["bus_time"]
    seat_names = ", ".join(s.get("seatDisplayName") for s in plan["seats"])
    total = bt.get("price", 0) * len(plan["seats"])
    return (
        f"Chiều: {direction['label']}\n"
        f"Chuyến: {bt['start_time']} {bt.get('hop_name')} ({bt.get('bus_type_name')})\n"
        f"Giá: {bt.get('price'):,}đ/vé x{len(plan['seats'])} = {total:,}đ | bus_time_id={bt['id']}\n"
        f"Ghế: {seat_names}\n"
        f"Điểm đón: {plan['pickup_name']} ({_point_id(plan['pickup'])})\n"
        f"Điểm trả: {plan['dropoff_name']} ({_point_id(plan['dropoff'])})"
    )


def booking_snapshot(plan: dict, direction: dict, order_id, cust_name: str, cust_mobile: str,
                      expiry_text: str, payment_url: str | None, expiry_ms: int | None = None) -> dict:
    """Full ticket details worth persisting onto the watchlist item once a booking
    succeeds, so /list and /status can show everything (passenger, times, price, seat,
    pickup/drop-off) without the user having to dig up the original Telegram message.

    `hold_expiry_ms` (the raw Unix-ms deadline, alongside the human-readable `hold_expiry`
    text) and `expiry_reminder_sent` let xeca_ticket_watch.poll_expiry_reminders() send a
    one-time "hết hạn sắp tới" nudge for plain one-shot bookings — instant-lock holds get
    the same nudge from inside xeca_control.instant_lock_loop instead, which already tracks
    the deadline precisely, so this flag naturally resets every re-lock cycle since a fresh
    booking dict replaces the old one each time."""
    bt = plan["bus_time"]
    return {
        "order_id": order_id,
        "cust_name": cust_name,
        "cust_mobile": cust_mobile,
        "direction_label": direction["label"],
        "start_time": bt.get("start_time"),
        "end_time": bt.get("end_time"),
        "seat_names": [s.get("seatDisplayName") for s in plan["seats"]],
        "price_per_seat": bt.get("price"),
        "total_price": (bt.get("price") or 0) * len(plan["seats"]),
        "pickup_name": plan["pickup_name"],
        "dropoff_name": plan["dropoff_name"],
        "hold_expiry": expiry_text,
        "hold_expiry_ms": expiry_ms,
        "expiry_reminder_sent": False,
        "payment_url": payment_url,
    }


def execute_booking(client: XecaClient, plan: dict, direction: dict, depart_date: int,
                     cust_name: str, cust_mobile: str, token: str | None, chat_id: str | None,
                     open_browser: bool = False, message_prefix: str = "🎟️ Đã đặt vé, cần thanh toán ngay!") -> dict:
    """Locks seat(s), creates the order, and initiates VNPay payment. Returns
    {"order_id", "expiry", "payment_url", "booking"} so callers (one-shot CLI, or the
    instant-lock loop) can act on the hold deadline and persist full ticket details
    without re-fetching them."""
    bt = plan["bus_time"]
    seat_ids = [s["seatId"] for s in plan["seats"]]

    client.toggle_seat_lock(
        action="lock", bus_hop_id=bt["bus_hop_id"], bus_time_id=bt["id"],
        depart_date=depart_date, seat_ids=seat_ids, pre_status="empty",
    )
    print("[LOCK] Đã giữ ghế:", [s.get("seatDisplayName") for s in plan["seats"]])

    expiry = client.get_book_expired_time(bt["id"], depart_date, bt["start_time"], len(seat_ids))
    expiry_text = format_expiry(expiry)
    print("[EXPIRE] ", expiry, "->", expiry_text)

    order = client.create_order(
        depart_date=depart_date, bus_time_id=bt["id"], bus_hop_id=bt["bus_hop_id"],
        seat_ids=seat_ids, cust_name=cust_name, cust_mobile=cust_mobile,
        pickup_name=plan["pickup_name"], pickup_point=plan["pickup"],
        dropoff_name=plan["dropoff_name"], dropoff_point=plan["dropoff"],
    )
    print("[ORDER] ", order)
    order_id = order.get("id") or order.get("orderId")
    if not order_id:
        raise BookingResponseShapeError(
            f"Đơn đã tạo (HTTP 200) nhưng không tìm thấy order_id trong response — "
            f"cấu trúc API create_order có thể đã đổi: {order}"
        )

    payment = client.initiate_payment(order_id)
    print("[PAYMENT] ", payment)
    payment_url = (
        payment.get("redirect_url") or payment.get("paymentUrl")
        or payment.get("payUrl") or payment.get("url")
    )
    if not payment_url:
        raise BookingResponseShapeError(
            f"Order {order_id} đã tạo nhưng không tìm thấy link thanh toán trong response — "
            f"cấu trúc API initiate_payment có thể đã đổi: {payment}"
        )

    text = (
        f"{message_prefix}\n\n{describe_plan(plan, direction)}\n\n"
        f"Order ID: {order_id}\n"
        f"Link thanh toán: {payment_url}\n"
        f"⏰ Hạn giữ chỗ: {expiry_text} (giờ VN) — thanh toán trước giờ này."
    )
    if token and chat_id:
        send_telegram_message(token, chat_id, text)
        qr_field = payment.get("qrCode") or payment.get("qrImage")
        if qr_field:
            send_telegram_photo(token, chat_id, qr_field, caption="Quét mã để thanh toán")
    else:
        print(text)

    if open_browser and payment_url:
        try:
            webbrowser.open(payment_url)
        except Exception as e:
            print(f"[WARN] Không mở được trình duyệt tự động: {e}")

    booking = booking_snapshot(plan, direction, order_id, cust_name, cust_mobile, expiry_text, payment_url,
                                expiry_ms=expiry.get("expiredTime"))
    return {"order_id": order_id, "expiry": expiry, "payment_url": payment_url, "booking": booking}


def main():
    parser = argparse.ArgumentParser(description="Auto-book Xeca ticket(s) when sale opens")
    parser.add_argument("--item-id", default=None, help="ID vé trong watchlist (state.json) — ưu tiên hơn các cờ khác nếu có")
    parser.add_argument("--depart-date", type=int, default=None, help="Ad-hoc: ngày khởi hành YYYYMMDD")
    parser.add_argument("--direction", default="HN-HT", help="Ad-hoc: HN-HT hoặc HT-HN")
    parser.add_argument("--quantity", type=int, default=1, help="Số vé muốn đặt")
    parser.add_argument("--pickup-name", default=None, help="Ghi đè điểm đón mặc định của chiều")
    parser.add_argument("--dropoff-name", default=None, help="Ghi đè điểm trả mặc định của chiều")
    parser.add_argument("--depart-from", default=None, help="Ad-hoc: chỉ xét chuyến khởi hành từ giờ này (HH:MM)")
    parser.add_argument("--depart-to", default=None, help="Ad-hoc: chỉ xét chuyến khởi hành tới giờ này (HH:MM)")
    parser.add_argument("--bus-type", default="ca_hai", choices=["thuong", "limousine", "ca_hai"],
                         help="Ad-hoc: 'thuong' chỉ xe giường nằm, 'limousine' chỉ Limousine, 'ca_hai' (mặc định) cho phép cả 2")
    parser.add_argument("--interval", type=int, default=300, help="Chu kỳ poll (giây) khi chưa mở bán")
    parser.add_argument("--jitter", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ log kế hoạch, không giữ ghế/tạo đơn")
    parser.add_argument("--confirm-real-booking", action="store_true",
                         help="Bắt buộc phải có flag này để thực sự giữ ghế + tạo đơn thật")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở bán)")
    parser.add_argument("--open-browser", action="store_true",
                         help="Tự mở trình duyệt tới link thanh toán sau khi đặt thành công (chỉ dùng khi chạy local có GUI)")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    cust_name, cust_mobile = get_passenger_info(args.state_file)

    if not args.dry_run and not cust_name:
        print("[ERROR] Thiếu thông tin hành khách — set qua /passenger trên Telegram hoặc XECA_PASSENGER_NAME/XECA_PASSENGER_PHONE trong .env")
        return

    item = None
    if args.item_id:
        item = get_item(args.item_id, args.state_file)
        if not item:
            print(f"[ERROR] Không tìm thấy item id={args.item_id} trong {args.state_file}")
            return
        depart_date = item["depart_date"]
        direction = get_direction(item["direction"])
        quantity = item.get("quantity", 1)
        pickup_override = item.get("pickup_name")
        dropoff_override = item.get("dropoff_name")
        depart_from = item.get("depart_from")
        depart_to = item.get("depart_to")
        bus_type = item.get("bus_type", "ca_hai")
    else:
        if args.depart_date is None:
            print("[ERROR] Cần --item-id hoặc --depart-date")
            return
        depart_date = args.depart_date
        direction = get_direction(args.direction)
        quantity = args.quantity
        pickup_override = args.pickup_name
        dropoff_override = args.dropoff_name
        depart_from = args.depart_from
        depart_to = args.depart_to
        bus_type = args.bus_type

    client = XecaClient()

    while True:
        try:
            plan = plan_booking(client, depart_date, direction, quantity, pickup_override, dropoff_override,
                                 depart_from=depart_from, depart_to=depart_to, bus_type=bus_type)
            print("[PLAN]\n" + describe_plan(plan, direction))

            if args.dry_run:
                print("[DRY-RUN] Dừng ở đây, không giữ ghế/tạo đơn thật.")
            elif not args.confirm_real_booking:
                print("[SKIP] Đã mở bán và có kế hoạch hợp lệ, nhưng thiếu --confirm-real-booking nên KHÔNG đặt vé thật.")
                if token and chat_id:
                    send_telegram_message(token, chat_id, "🎉 ĐÃ MỞ BÁN, sẵn sàng đặt!\n\n" +
                                           describe_plan(plan, direction) +
                                           "\n\nChạy lại với --confirm-real-booking để đặt thật.")
            else:
                result = execute_booking(client, plan, direction, depart_date, cust_name, cust_mobile,
                                          token, chat_id, open_browser=args.open_browser)
                if item:
                    # "pending_payment": order created + seat held, but NOT yet confirmed paid —
                    # getting a redirect_url only means the order/hold succeeded, not that money
                    # actually changed hands. The user marks it "paid" themselves (button/command)
                    # once they've completed payment, since there's no confirmed webhook/poll for it.
                    update_item(item["id"], path=args.state_file, status="pending_payment",
                                order_id=result["order_id"], booking=result["booking"])
            break
        except BookingResponseShapeError as e:
            # Not a "try again later" condition like SaleNotOpenError/NoSeatsAvailableError —
            # a schema mismatch won't fix itself on retry. Surface it loudly and exit non-zero
            # so run_booking()/the Telegram bot reports "❌ Có lỗi" instead of silently looking
            # like success (exactly the TQTT-incident failure mode this class exists to avoid).
            print(f"[ERROR] {e}")
            if token and chat_id:
                send_telegram_message(token, chat_id, f"🚨 Đặt vé thất bại — cấu trúc API Xeca có thể đã đổi:\n{e}")
            sys.exit(1)
        except RuntimeError as e:
            print(f"[WAIT] {e}")
            if args.once:
                break
            sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
            print(f"[INFO] Chờ {sleep_for}s trước lần thử tiếp theo...")
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
