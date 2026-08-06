"""Phase 2: auto-book a Xeca (Văn Minh) ticket the moment sale opens for a target date.

Picks the latest "Xe giường nằm" (regular bus) departure of the day, falling back to
Limousine only if no regular bus has seats. Picks the best seat per the user's preference
(floor 2 > floor 1, letter E>A / F>B, seat number 3>2>4>1>5>6). Creates the order and
initiates VNPay payment, then sends the payment link/QR to Telegram for the user to
complete manually within the transaction countdown — the bank/wallet confirmation step
cannot be automated.

IMPORTANT: --dry-run only logs the chosen bus/seat/pickup/dropoff, it does NOT lock the
seat or create an order. Creating a real order requires --confirm-real-booking, since it
has a real-world side effect (a pending order + temporary seat hold in Văn Minh's system).

Usage:
    python xeca_auto_book.py --depart-date 20260808 --dry-run
    python xeca_auto_book.py --depart-date 20260827 --confirm-real-booking
"""

import argparse
import os
import random
import sys
import time

from xeca_client import (
    XecaClient,
    find_boarding_point,
    is_coastal_route,
    is_sale_open,
    load_env_file,
    select_preferred_bus_time,
    select_seat,
    send_telegram_message,
    send_telegram_photo,
    COASTAL_DROPOFF_NAME,
    DEFAULT_DROPOFF_NAME,
    DEFAULT_PICKUP_NAME,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def resolve_points(client: XecaClient, bus_time: dict, depart_date: int) -> tuple[dict, dict, str]:
    bus_time_id = bus_time["id"]
    pickup_points = client.get_boarding_points(bus_time_id, depart_date, point_type=1)
    dropoff_points = client.get_boarding_points(bus_time_id, depart_date, point_type=2)

    pickup = find_boarding_point(pickup_points, DEFAULT_PICKUP_NAME)
    if not pickup:
        raise RuntimeError(f"Không tìm thấy điểm đón '{DEFAULT_PICKUP_NAME}' cho chuyến {bus_time_id}")

    dropoff_name = COASTAL_DROPOFF_NAME if is_coastal_route(bus_time) else DEFAULT_DROPOFF_NAME
    dropoff = find_boarding_point(dropoff_points, dropoff_name)
    if not dropoff:
        raise RuntimeError(f"Không tìm thấy điểm trả '{dropoff_name}' cho chuyến {bus_time_id}")

    return pickup, dropoff, dropoff_name


def plan_booking(client: XecaClient, depart_date: int, from_province_id: int, to_province_id: int) -> dict:
    bus_times = client.get_bus_times(depart_date, from_province_id, to_province_id)
    bus_time = select_preferred_bus_time(bus_times)
    if not bus_time:
        raise RuntimeError("Không có chuyến nào trong ngày.")

    detail = client.get_detail_bus_time(
        depart_date=depart_date,
        bus_time_id=bus_time["id"],
        bus_hop_id=bus_time["bus_hop_id"],
        bus_stage_id=bus_time["bus_stage_id"],
        from_province_id=from_province_id,
        to_province_id=to_province_id,
    )
    special_rules = detail.get("busStageSpecialRules", [])
    open_status, reason = is_sale_open(special_rules, depart_date, bus_time.get("bus_stage_id"))
    if not open_status:
        raise RuntimeError(f"Chưa mở bán: {reason}")

    seat = select_seat(detail.get("seatMap", {}))
    if not seat:
        raise RuntimeError("Không còn ghế trống phù hợp sở thích.")

    pickup, dropoff, dropoff_name = resolve_points(client, bus_time, depart_date)

    return {
        "bus_time": bus_time,
        "seat": seat,
        "pickup": pickup,
        "dropoff": dropoff,
        "dropoff_name": dropoff_name,
    }


def describe_plan(plan: dict) -> str:
    bt = plan["bus_time"]
    seat = plan["seat"]
    return (
        f"Chuyến: {bt['start_time']} {bt.get('hop_name')} ({bt.get('bus_type_name')})\n"
        f"Giá: {bt.get('price'):,}đ | bus_time_id={bt['id']}\n"
        f"Ghế: {seat.get('seatDisplayName')} (seatId={seat.get('seatId')})\n"
        f"Điểm đón: {DEFAULT_PICKUP_NAME} (zone_id={plan['pickup'].get('home_pickup_zone_id')})\n"
        f"Điểm trả: {plan['dropoff_name']} (point_id={plan['dropoff'].get('boarding_point_id')})"
    )


def execute_booking(client: XecaClient, plan: dict, depart_date: int, cust_name: str, cust_mobile: str,
                     token: str | None, chat_id: str | None):
    bt = plan["bus_time"]
    seat = plan["seat"]
    seat_id = seat["seatId"]

    client.toggle_seat_lock(
        action="lock", bus_hop_id=bt["bus_hop_id"], bus_time_id=bt["id"],
        depart_date=depart_date, seat_ids=[seat_id], pre_status="empty",
    )
    print("[LOCK] Đã giữ ghế:", seat.get("seatDisplayName"))

    expiry = client.get_book_expired_time(bt["id"], depart_date, bt["start_time"])
    print("[EXPIRE] ", expiry)

    order = client.create_order(
        depart_date=depart_date, bus_time_id=bt["id"], bus_hop_id=bt["bus_hop_id"],
        seat_id=seat_id, cust_name=cust_name, cust_mobile=cust_mobile,
        pickup_name=DEFAULT_PICKUP_NAME, home_pickup_zone_id=plan["pickup"].get("home_pickup_zone_id"),
        dropoff_name=plan["dropoff_name"], dropoff_point_id=plan["dropoff"].get("boarding_point_id"),
    )
    print("[ORDER] ", order)
    order_id = order.get("orderId") or order.get("id")

    payment = client.initiate_payment(order_id)
    print("[PAYMENT] ", payment)
    payment_url = payment.get("paymentUrl") or payment.get("payUrl") or payment.get("url")

    text = (
        f"🎟️ Đã đặt vé, cần thanh toán ngay!\n\n{describe_plan(plan)}\n\n"
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


def main():
    parser = argparse.ArgumentParser(description="Auto-book a Xeca ticket when sale opens")
    parser.add_argument("--depart-date", required=True, type=int, help="Ngày khởi hành, format YYYYMMDD")
    parser.add_argument("--from-province", type=int, default=2)
    parser.add_argument("--to-province", type=int, default=3)
    parser.add_argument("--interval", type=int, default=300, help="Chu kỳ poll (giây) khi chưa mở bán")
    parser.add_argument("--jitter", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ log kế hoạch, không giữ ghế/tạo đơn")
    parser.add_argument("--confirm-real-booking", action="store_true",
                         help="Bắt buộc phải có flag này để thực sự giữ ghế + tạo đơn thật")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở bán)")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    cust_name = os.environ.get("XECA_PASSENGER_NAME")
    cust_mobile = os.environ.get("XECA_PASSENGER_PHONE")

    if not args.dry_run and not cust_name:
        print("[ERROR] Thiếu XECA_PASSENGER_NAME/XECA_PASSENGER_PHONE trong .env")
        return

    client = XecaClient()

    while True:
        try:
            plan = plan_booking(client, args.depart_date, args.from_province, args.to_province)
            print("[PLAN]\n" + describe_plan(plan))

            if args.dry_run:
                print("[DRY-RUN] Dừng ở đây, không giữ ghế/tạo đơn thật.")
            elif not args.confirm_real_booking:
                print("[SKIP] Đã mở bán và có kế hoạch hợp lệ, nhưng thiếu --confirm-real-booking nên KHÔNG đặt vé thật.")
                if token and chat_id:
                    send_telegram_message(token, chat_id, "🎉 ĐÃ MỞ BÁN, sẵn sàng đặt!\n\n" + describe_plan(plan) +
                                           "\n\nChạy lại với --confirm-real-booking để đặt thật.")
            else:
                execute_booking(client, plan, args.depart_date, cust_name, cust_mobile, token, chat_id)
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
