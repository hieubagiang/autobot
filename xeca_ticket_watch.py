"""Phase 1: poll api-pro.xeca.vn and notify via Telegram when ticket sale opens.

Two modes:
- Ad-hoc single check (manual testing): --depart-date [--direction HN-HT|HT-HN] --once
- Watchlist mode (the persistent systemd service): no --depart-date given, polls every
  "pending" item in state.json (managed via xeca_control.py / the Telegram bot) and
  marks each "notified" once its sale opens, so it isn't re-announced every cycle.

Usage:
    python xeca_ticket_watch.py --depart-date 20260808 --once   # ad-hoc test
    python xeca_ticket_watch.py                                  # watchlist mode (service)
"""

import argparse
import os
import random
import sys
import time
import traceback

from xeca_client import (
    XecaClient,
    get_direction,
    is_sale_open,
    load_env_file,
    payment_status_changed,
    select_preferred_bus_time,
    send_telegram_message,
)
from xeca_state import DEFAULT_STATE_FILE, list_items, update_item

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def format_bus_time_line(bus_time: dict, open_status: bool, reason: str) -> str:
    status_icon = "✅" if open_status else "🔒"
    return (
        f"{status_icon} {bus_time.get('start_time')} - {bus_time.get('hop_name')} "
        f"({bus_time.get('bus_type_name')}) | Giá: {bus_time.get('price'):,}đ | "
        f"Trống: {bus_time.get('empty_seat')} | {reason}"
    )


def check_once(client: XecaClient, depart_date: int, from_province_id: int, to_province_id: int,
               only_latest: bool) -> dict:
    bus_times = client.get_bus_times(depart_date, from_province_id, to_province_id)

    if not bus_times:
        return {
            "any_open": False,
            "lines": [f"Không tìm thấy chuyến nào cho ngày {depart_date} (route {from_province_id}->{to_province_id})."],
            "open_bus_times": [],
        }

    if only_latest:
        preferred = select_preferred_bus_time(bus_times)
        bus_times = [preferred] if preferred else []

    lines = []
    open_bus_times = []
    for bt in bus_times:
        detail = client.get_detail_bus_time(
            depart_date=depart_date,
            bus_time_id=bt["id"],
            bus_hop_id=bt["bus_hop_id"],
            bus_stage_id=bt["bus_stage_id"],
            from_province_id=from_province_id,
            to_province_id=to_province_id,
        )
        special_rules = detail.get("busStageSpecialRules", [])
        open_status, reason = is_sale_open(special_rules, depart_date, bt.get("bus_stage_id"))
        lines.append(format_bus_time_line(bt, open_status, reason))
        if open_status:
            open_bus_times.append(bt)

    return {"any_open": len(open_bus_times) > 0, "lines": lines, "open_bus_times": open_bus_times}


def notify(token: str | None, chat_id: str | None, text: str):
    print(text)
    if token and chat_id:
        send_telegram_message(token, chat_id, text)
    else:
        print("[WARN] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID chưa cấu hình, chỉ in ra console.")


AWAITING_PAYMENT_STATUSES = ("pending_payment", "instant_holding")


def check_payment_status(client: XecaClient, item: dict) -> int | None:
    """Best-effort nudge, not an authoritative check — see
    xeca_client.payment_status_changed()'s docstring for why this can only flag a change
    away from the known-unpaid baseline, never assert that an order IS paid. Returns the
    changed paymentStatus value, or None if there's nothing to flag (still unpaid, no
    order_id yet, or the lookup itself failed)."""
    order_id = item.get("order_id")
    if not order_id:
        return None
    detail = client.get_ticket_detail(order_id)
    return payment_status_changed(detail)


def poll_payment_statuses(client: XecaClient, token: str | None, chat_id: str | None, state_file: str):
    items = [i for i in list_items(state_file) if i.get("status") in AWAITING_PAYMENT_STATUSES]
    for item in items:
        try:
            changed = check_payment_status(client, item)
        except Exception as e:
            print(f"[ERROR] item {item['id']}: lỗi khi kiểm tra trạng thái thanh toán — {e}")
            continue
        if changed is not None:
            notify(token, chat_id,
                   f"💳 [{item['id']}] Trạng thái thanh toán có vẻ đã thay đổi (paymentStatus={changed}). "
                   f"Nếu bạn đã thanh toán xong, bấm /paid {item['id']} để dừng tự động giữ/relock ghế "
                   f"— hệ thống KHÔNG tự đánh dấu đã thanh toán, cần bạn xác nhận.")


EXPIRY_REMINDER_LEAD_SECONDS = 300  # nudge the user this long before a hold expires


def poll_expiry_reminders(token: str | None, chat_id: str | None, state_file: str):
    """One-time "hết hạn sắp tới" nudge for plain one-shot bookings (status=pending_payment,
    from /book + /confirm) that no background thread is actively watching — unlike
    instant_holding items, which get the same nudge with tighter precision straight from
    xeca_control.instant_lock_loop's own wait timer. Relies on hold_expiry_ms/
    expiry_reminder_sent being persisted on the booking snapshot (xeca_auto_book.
    booking_snapshot); this poll's own interval (default 300s) is coarse, so this is a
    best-effort heads-up, not a precise T-minus-5:00 alarm."""
    items = [i for i in list_items(state_file) if i.get("status") == "pending_payment"]
    now = time.time()
    for item in items:
        booking = item.get("booking") or {}
        expiry_ms = booking.get("hold_expiry_ms")
        if not expiry_ms or booking.get("expiry_reminder_sent"):
            continue
        remaining = expiry_ms / 1000 - now
        if remaining > EXPIRY_REMINDER_LEAD_SECONDS:
            continue
        if remaining > 0:
            notify(token, chat_id,
                   f"⏰ [{item['id']}] Ghế sắp hết hạn giữ chỗ trong ~{max(1, int(remaining // 60))} phút. "
                   f"Link thanh toán: {booking.get('payment_url')}")
        update_item(item["id"], path=state_file, booking={**booking, "expiry_reminder_sent": True})


def poll_watchlist(client: XecaClient, token: str | None, chat_id: str | None, state_file: str,
                    notify_closed: bool):
    poll_payment_statuses(client, token, chat_id, state_file)
    poll_expiry_reminders(token, chat_id, state_file)

    items = [i for i in list_items(state_file) if i.get("status") == "pending"]
    if not items:
        print("[INFO] Watchlist rỗng hoặc không còn vé nào đang chờ (status=pending).")
        return

    for item in items:
        try:
            direction = get_direction(item["direction"])
        except ValueError as e:
            print(f"[ERROR] item {item['id']}: {e}")
            continue

        try:
            result = check_once(client, item["depart_date"], direction["from_province_id"],
                                 direction["to_province_id"], only_latest=True)
        except Exception as e:
            # One item's transient network error must not stop the rest of the watchlist
            # from being checked this cycle.
            print(f"[ERROR] item {item['id']}: lỗi khi kiểm tra — {e}")
            continue
        header = (f"[{item['id']}] {direction['label']} ngày {item['depart_date']} "
                  f"x{item.get('quantity', 1)} vé:")
        text = header + "\n" + "\n".join(result["lines"])

        if result["any_open"]:
            notify(token, chat_id,
                   f"🎉 ĐÃ MỞ BÁN VÉ!\n{text}\n\nDùng /book {item['id']} trên Telegram để đặt.")
            update_item(item["id"], path=state_file, status="notified")
        elif notify_closed:
            notify(token, chat_id, text)
        else:
            print(text)


def main():
    parser = argparse.ArgumentParser(description="Watch xeca.vn ticket sale status and notify via Telegram")
    parser.add_argument("--depart-date", type=int, default=None,
                         help="Ad-hoc: ngày khởi hành YYYYMMDD. Nếu bỏ trống sẽ dùng watchlist trong state.json")
    parser.add_argument("--direction", default="HN-HT", help="Chiều cho chế độ ad-hoc: HN-HT hoặc HT-HN")
    parser.add_argument("--interval", type=int, default=300, help="Chu kỳ poll (giây), mặc định 300s")
    parser.add_argument("--jitter", type=int, default=30, help="Jitter ngẫu nhiên cộng thêm vào interval (giây)")
    parser.add_argument("--once", action="store_true", help="Chỉ kiểm tra 1 lần rồi thoát")
    parser.add_argument("--all-times", action="store_true", help="Kiểm tra tất cả chuyến trong ngày thay vì chỉ chuyến muộn nhất")
    parser.add_argument("--notify-closed", action="store_true", help="Cũng gửi Telegram khi vẫn chưa mở bán (để test)")
    parser.add_argument("--env-file", default=".env", help="Đường dẫn file .env")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Đường dẫn file state.json (watchlist)")
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    client = XecaClient()

    while True:
        try:
            if args.depart_date is not None:
                direction = get_direction(args.direction)
                result = check_once(client, args.depart_date, direction["from_province_id"],
                                     direction["to_province_id"], only_latest=not args.all_times)
                text = f"[Xeca] {direction['label']} ngày {args.depart_date}:\n" + "\n".join(result["lines"])
                if result["any_open"]:
                    notify(token, chat_id, "🎉 ĐÃ MỞ BÁN VÉ!\n" + text)
                elif args.notify_closed:
                    notify(token, chat_id, text)
                else:
                    print(text)
            else:
                poll_watchlist(client, token, chat_id, args.state_file, args.notify_closed)
        except Exception:
            # This loop backs a long-running systemd service (xeca-watch.service) — an
            # unexpected error on one cycle must not take the whole service down and stop
            # checking every other watchlist item until someone notices and restarts it.
            # For an ad-hoc --once test run, surface the failure loudly instead of hiding it.
            traceback.print_exc()
            if args.once:
                raise

        if args.once:
            break

        sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
        print(f"[INFO] Chờ {sleep_for}s trước lần kiểm tra tiếp theo...")
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
