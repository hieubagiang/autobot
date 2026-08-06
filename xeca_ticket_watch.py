"""Phase 1: poll api-pro.xeca.vn and notify via Telegram when ticket sale opens.

Usage:
    python xeca_ticket_watch.py --depart-date 20260827
    python xeca_ticket_watch.py --depart-date 20260808 --once   # test against an already-open date
"""

import argparse
import os
import random
import sys
import time

from xeca_client import XecaClient, is_sale_open, load_env_file, select_preferred_bus_time, send_telegram_message

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
        rules = detail.get("buxTimeExt", {})
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


def main():
    parser = argparse.ArgumentParser(description="Watch xeca.vn ticket sale status and notify via Telegram")
    parser.add_argument("--depart-date", required=True, type=int, help="Ngày khởi hành, format YYYYMMDD")
    parser.add_argument("--from-province", type=int, default=2, help="fromProvinceId (default: 2 = Hà Nội)")
    parser.add_argument("--to-province", type=int, default=3, help="toProvinceId (default: 3 = Hà Tĩnh)")
    parser.add_argument("--interval", type=int, default=300, help="Chu kỳ poll (giây), mặc định 300s")
    parser.add_argument("--jitter", type=int, default=30, help="Jitter ngẫu nhiên cộng thêm vào interval (giây)")
    parser.add_argument("--once", action="store_true", help="Chỉ kiểm tra 1 lần rồi thoát")
    parser.add_argument("--all-times", action="store_true", help="Kiểm tra tất cả chuyến trong ngày thay vì chỉ chuyến muộn nhất")
    parser.add_argument("--notify-closed", action="store_true", help="Cũng gửi Telegram khi vẫn chưa mở bán (để test)")
    parser.add_argument("--env-file", default=".env", help="Đường dẫn file .env")
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    client = XecaClient()
    was_open = False

    while True:
        result = check_once(client, args.depart_date, args.from_province, args.to_province, only_latest=not args.all_times)
        text = f"[Xeca] Ngày {args.depart_date}:\n" + "\n".join(result["lines"])

        if result["any_open"] and not was_open:
            notify(token, chat_id, "🎉 ĐÃ MỞ BÁN VÉ!\n" + text)
            was_open = True
        elif not result["any_open"] and args.notify_closed:
            notify(token, chat_id, text)
        else:
            print(text)

        if args.once:
            break

        sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
        print(f"[INFO] Chờ {sleep_for}s trước lần kiểm tra tiếp theo...")
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
