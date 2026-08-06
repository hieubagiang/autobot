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

from xeca_client import (
    XecaClient,
    find_boarding_point,
    get_direction,
    is_coastal_route,
    is_sale_open,
    load_env_file,
    select_preferred_bus_time,
    select_seats,
    send_telegram_message,
    send_telegram_photo,
)
from xeca_state import DEFAULT_STATE_FILE, get_item, update_item

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def resolve_points(client: XecaClient, bus_time: dict, depart_date: int, direction: dict,
                    pickup_override: str | None, dropoff_override: str | None) -> tuple[dict, dict, str, str]:
    bus_time_id = bus_time["id"]
    pickup_points = client.get_boarding_points(bus_time_id, depart_date, point_type=1)
    dropoff_points = client.get_boarding_points(bus_time_id, depart_date, point_type=2)

    coastal = is_coastal_route(bus_time)

    if pickup_override:
        pickup_name = pickup_override
    elif coastal and direction.get("coastal_pickup_name"):
        pickup_name = direction["coastal_pickup_name"]
    else:
        pickup_name = direction.get("default_pickup_name")
    if not pickup_name:
        raise RuntimeError(
            f"Chưa cấu hình điểm đón cho chiều '{direction['label']}'. "
            f"Dùng --pickup-name (hoặc /setpickup <id> <tên>)."
        )
    pickup = find_boarding_point(pickup_points, pickup_name)
    if not pickup:
        raise RuntimeError(f"Không tìm thấy điểm đón '{pickup_name}' cho chuyến {bus_time_id}")

    if dropoff_override:
        dropoff_name = dropoff_override
    elif coastal and direction.get("coastal_dropoff_name"):
        dropoff_name = direction["coastal_dropoff_name"]
    else:
        dropoff_name = direction.get("default_dropoff_name")
    if not dropoff_name:
        raise RuntimeError(
            f"Chưa cấu hình điểm trả cho chiều '{direction['label']}'. "
            f"Dùng --dropoff-name (hoặc /setdropoff <id> <tên>)."
        )
    dropoff = find_boarding_point(dropoff_points, dropoff_name)
    if not dropoff:
        raise RuntimeError(f"Không tìm thấy điểm trả '{dropoff_name}' cho chuyến {bus_time_id}")

    return pickup, dropoff, pickup_name, dropoff_name


def plan_booking(client: XecaClient, depart_date: int, direction: dict, quantity: int = 1,
                  pickup_override: str | None = None, dropoff_override: str | None = None) -> dict:
    bus_times = client.get_bus_times(depart_date, direction["from_province_id"], direction["to_province_id"])
    bus_time = select_preferred_bus_time(bus_times)
    if not bus_time:
        raise RuntimeError("Không có chuyến nào trong ngày.")

    detail = client.get_detail_bus_time(
        depart_date=depart_date,
        bus_time_id=bus_time["id"],
        bus_hop_id=bus_time["bus_hop_id"],
        bus_stage_id=bus_time["bus_stage_id"],
        from_province_id=direction["from_province_id"],
        to_province_id=direction["to_province_id"],
    )
    special_rules = detail.get("busStageSpecialRules", [])
    open_status, reason = is_sale_open(special_rules, depart_date, bus_time.get("bus_stage_id"))
    if not open_status:
        raise RuntimeError(f"Chưa mở bán: {reason}")

    seats = select_seats(detail.get("seatMap", {}), quantity)
    if len(seats) < quantity:
        raise RuntimeError(f"Chỉ còn {len(seats)}/{quantity} ghế trống phù hợp sở thích.")

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


def execute_booking(client: XecaClient, plan: dict, direction: dict, depart_date: int,
                     cust_name: str, cust_mobile: str, token: str | None, chat_id: str | None):
    bt = plan["bus_time"]
    seat_ids = [s["seatId"] for s in plan["seats"]]

    client.toggle_seat_lock(
        action="lock", bus_hop_id=bt["bus_hop_id"], bus_time_id=bt["id"],
        depart_date=depart_date, seat_ids=seat_ids, pre_status="empty",
    )
    print("[LOCK] Đã giữ ghế:", [s.get("seatDisplayName") for s in plan["seats"]])

    expiry = client.get_book_expired_time(bt["id"], depart_date, bt["start_time"], len(seat_ids))
    print("[EXPIRE] ", expiry)

    order = client.create_order(
        depart_date=depart_date, bus_time_id=bt["id"], bus_hop_id=bt["bus_hop_id"],
        seat_ids=seat_ids, cust_name=cust_name, cust_mobile=cust_mobile,
        pickup_name=plan["pickup_name"], pickup_point=plan["pickup"],
        dropoff_name=plan["dropoff_name"], dropoff_point=plan["dropoff"],
    )
    print("[ORDER] ", order)
    order_id = order.get("orderId") or order.get("id")

    payment = client.initiate_payment(order_id)
    print("[PAYMENT] ", payment)
    payment_url = payment.get("paymentUrl") or payment.get("payUrl") or payment.get("url")

    text = (
        f"🎟️ Đã đặt vé, cần thanh toán ngay!\n\n{describe_plan(plan, direction)}\n\n"
        f"Order ID: {order_id}\n"
        f"Link thanh toán: {payment_url}\n"
        f"⏰ Vui lòng thanh toán trong thời gian giữ chỗ (~20 phút)."
    )
    if token and chat_id:
        send_telegram_message(token, chat_id, text)
        qr_field = payment.get("qrCode") or payment.get("qrImage")
        if qr_field:
            send_telegram_photo(token, chat_id, qr_field, caption="Quét mã để thanh toán")
    else:
        print(text)

    return order_id


def main():
    parser = argparse.ArgumentParser(description="Auto-book Xeca ticket(s) when sale opens")
    parser.add_argument("--item-id", default=None, help="ID vé trong watchlist (state.json) — ưu tiên hơn các cờ khác nếu có")
    parser.add_argument("--depart-date", type=int, default=None, help="Ad-hoc: ngày khởi hành YYYYMMDD")
    parser.add_argument("--direction", default="HN-HT", help="Ad-hoc: HN-HT hoặc HT-HN")
    parser.add_argument("--quantity", type=int, default=1, help="Số vé muốn đặt")
    parser.add_argument("--pickup-name", default=None, help="Ghi đè điểm đón mặc định của chiều")
    parser.add_argument("--dropoff-name", default=None, help="Ghi đè điểm trả mặc định của chiều")
    parser.add_argument("--interval", type=int, default=300, help="Chu kỳ poll (giây) khi chưa mở bán")
    parser.add_argument("--jitter", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ log kế hoạch, không giữ ghế/tạo đơn")
    parser.add_argument("--confirm-real-booking", action="store_true",
                         help="Bắt buộc phải có flag này để thực sự giữ ghế + tạo đơn thật")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở bán)")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    cust_name = os.environ.get("XECA_PASSENGER_NAME")
    cust_mobile = os.environ.get("XECA_PASSENGER_PHONE")

    if not args.dry_run and not cust_name:
        print("[ERROR] Thiếu XECA_PASSENGER_NAME/XECA_PASSENGER_PHONE trong .env")
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
    else:
        if args.depart_date is None:
            print("[ERROR] Cần --item-id hoặc --depart-date")
            return
        depart_date = args.depart_date
        direction = get_direction(args.direction)
        quantity = args.quantity
        pickup_override = args.pickup_name
        dropoff_override = args.dropoff_name

    client = XecaClient()

    while True:
        try:
            plan = plan_booking(client, depart_date, direction, quantity, pickup_override, dropoff_override)
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
                order_id = execute_booking(client, plan, direction, depart_date, cust_name, cust_mobile, token, chat_id)
                if item:
                    update_item(item["id"], path=args.state_file, status="booked", order_id=order_id)
            break
        except RuntimeError as e:
            print(f"[WAIT] {e}")
            if args.once:
                break
            sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
            print(f"[INFO] Chờ {sleep_for}s trước lần thử tiếp theo...")
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
