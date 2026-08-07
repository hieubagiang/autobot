"""Phase 1: poll api.tqtt.vn/concert/capacity and notify via Telegram when the "Tổ Quốc
Trong Tim" registration form opens (is_open flips true). The site currently only shows a
"coming soon" page — no form is mounted until this flips.

Usage:
    python tqtt_watch.py --once                 # single check, print + exit
    python tqtt_watch.py                         # poll forever, notify on open
    python tqtt_watch.py --interval 60 --jitter 10
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

from tqtt_client import TqttClient, load_env_file, send_telegram_message

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def notify(token: str | None, chat_id: str | None, text: str):
    print(text)
    if token and chat_id:
        send_telegram_message(token, chat_id, text)
    else:
        print("[WARN] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID chưa cấu hình, chỉ in ra console.")


def main():
    parser = argparse.ArgumentParser(description="Watch tqtt.vn registration status and notify via Telegram")
    parser.add_argument("--interval", type=int, default=60, help="Chu kỳ poll (giây), mặc định 60s")
    parser.add_argument("--jitter", type=int, default=10, help="Jitter ngẫu nhiên cộng thêm vào interval (giây)")
    parser.add_argument("--once", action="store_true", help="Chỉ kiểm tra 1 lần rồi thoát")
    parser.add_argument("--notify-closed", action="store_true", help="Cũng gửi Telegram khi vẫn chưa mở (để test)")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env_file(args.env_file)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    client = TqttClient()
    already_notified = False

    while True:
        try:
            result = client.get_capacity()
        except Exception as e:
            print(f"[ERROR] Không gọi được /concert/capacity: {e}")
            result = {}

        is_open = bool(result.get("is_open"))
        capacity_valid = bool(result.get("capacity_valid"))
        status_icon = "✅" if is_open else "🔒"
        text = f"{status_icon} tqtt.vn đăng ký: is_open={is_open}, capacity_valid={capacity_valid}"

        if is_open and not already_notified:
            notify(token, chat_id, "🎉 ĐÃ MỞ ĐĂNG KÝ VÉ TỔ QUỐC TRONG TIM!\n" + text +
                   "\n\nChạy tqtt_register.py --confirm-real-submit ngay để đăng ký.")
            already_notified = True
        elif args.notify_closed:
            notify(token, chat_id, text)
        else:
            print(text)

        if args.once:
            break
        if is_open:
            # Once open, this event doesn't reopen/reclose like a bus seat pool —
            # nothing more to watch for.
            break

        sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
        print(f"[INFO] Chờ {sleep_for}s trước lần kiểm tra tiếp theo...")
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
