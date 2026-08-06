"""Two-way Telegram control bot for the Xeca automation.

Long-polls Telegram getUpdates and only accepts commands from the whitelisted
TELEGRAM_CHAT_ID (.env) — anyone else messaging the bot is ignored. Runs as its own
systemd service (xeca-bot.service), separate from xeca-watch.service (which only ever
*sends* notifications and never calls getUpdates, so the two don't conflict).

Commands:
  /add <HN-HT|HT-HN> <dd/mm/yyyy|yyyymmdd> [quantity=1]  - add a ticket request
  /setpickup <id> <tên điểm đón...>                       - override pickup point
  /setdropoff <id> <tên điểm trả...>                      - override dropoff point
  /list                                                    - list watchlist items
  /remove <id>                                             - remove an item
  /status                                                  - service + live sale-open check
  /book <id>                                               - preview plan, ask to /confirm
  /confirm <code>                                          - confirm the pending /book (real money!)
  /start /stop /restart                                    - control xeca-watch.service
  /logs [n]                                                - last n lines of xeca-watch logs
  /help                                                     - this list

Usage:
    python xeca_telegram_bot.py
"""

import html
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta

import requests

from xeca_client import DIRECTIONS, get_direction, load_env_file, send_telegram_message
from xeca_control import (
    add_ticket_request,
    get_logs,
    get_status,
    list_ticket_requests,
    remove_ticket_request,
    run_booking,
    service_control,
)
from xeca_auto_book import describe_plan, plan_booking
from xeca_state import DEFAULT_STATE_FILE, get_item

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIRM_TTL_SECONDS = 120
LONG_POLL_TIMEOUT = 25


def parse_date(text: str) -> int | None:
    text = text.strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", text)
    if m:
        d, mo, y = m.groups()
        return int(f"{y}{mo}{d}")
    if re.match(r"^\d{8}$", text):
        return int(text)
    return None


def format_item(item: dict, live: dict | None = None) -> str:
    direction = DIRECTIONS.get(item["direction"], {}).get("label", item["direction"])
    line = f"[{item['id']}] {direction} - {item['depart_date']} - x{item.get('quantity', 1)} - {item['status']}"
    if item.get("pickup_name"):
        line += f"\n  Đón: {item['pickup_name']}"
    if item.get("dropoff_name"):
        line += f"\n  Trả: {item['dropoff_name']}"
    if live:
        if live.get("error"):
            line += f"\n  ⚠️ {live['error']}"
        else:
            icon = "✅ Đã mở bán" if live.get("open") else "🔒 Chưa mở"
            line += f"\n  {icon}: {live.get('reason', '')}"
    return line


class Bot:
    def __init__(self, token: str, chat_id: str, state_file: str, env_file: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
        self.env_file = env_file
        self.api = f"https://api.telegram.org/bot{token}"
        self.pending_confirm = None  # {"item_id", "code", "expires_at"}

    def send(self, text: str):
        send_telegram_message(self.token, self.chat_id, text)

    def get_updates(self, offset: int | None):
        params = {"timeout": LONG_POLL_TIMEOUT}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(f"{self.api}/getUpdates", params=params, timeout=LONG_POLL_TIMEOUT + 10)
        resp.raise_for_status()
        return resp.json().get("result", [])

    def run(self):
        requests.get(f"{self.api}/deleteWebhook", timeout=10)
        print(f"[BOT] Listening for chat_id={self.chat_id} ...")
        offset = None
        while True:
            try:
                updates = self.get_updates(offset)
            except Exception as e:
                print(f"[BOT] getUpdates error: {e}")
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue
                from_id = str(message.get("from", {}).get("id"))
                text = (message.get("text") or "").strip()
                if not text:
                    continue
                if from_id != self.chat_id:
                    print(f"[BOT] Ignoring message from unauthorized chat_id={from_id}")
                    continue
                try:
                    reply = self.dispatch(text)
                except Exception as e:
                    traceback.print_exc()
                    reply = f"❌ Lỗi: {e}"
                if reply:
                    self.send(reply)

    def dispatch(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/add": self.cmd_add,
            "/setpickup": self.cmd_setpickup,
            "/setdropoff": self.cmd_setdropoff,
            "/list": self.cmd_list,
            "/remove": self.cmd_remove,
            "/status": self.cmd_status,
            "/book": self.cmd_book,
            "/confirm": self.cmd_confirm,
            "/stop": lambda r: service_control("stop"),
            "/restart": lambda r: service_control("restart"),
            "/logs": self.cmd_logs,
            "/help": lambda r: self.cmd_help(),
        }
        if cmd == "/start" and rest.strip() == "":
            return service_control("start")
        handler = handlers.get(cmd)
        if not handler:
            return self.cmd_help()
        return handler(rest)

    def cmd_help(self) -> str:
        return (
            "Lệnh:\n"
            "/add <HN-HT|HT-HN> <dd/mm/yyyy> [số lượng=1]\n"
            "/setpickup <id> <tên điểm đón>\n"
            "/setdropoff <id> <tên điểm trả>\n"
            "/list — danh sách vé đang theo dõi\n"
            "/remove <id>\n"
            "/status — trạng thái service + kiểm tra mở bán trực tiếp\n"
            "/book <id> — xem trước kế hoạch, cần /confirm để đặt thật\n"
            "/confirm <mã> — xác nhận đặt vé thật (có hiệu lực 2 phút)\n"
            "/start /stop /restart — điều khiển service theo dõi\n"
            "/logs [n]"
        )

    def cmd_add(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) < 2:
            return "Cú pháp: /add <HN-HT|HT-HN> <dd/mm/yyyy> [số lượng=1]"
        direction_code = parts[0].upper()
        depart_date = parse_date(parts[1])
        if depart_date is None:
            return "Ngày không hợp lệ, dùng dd/mm/yyyy hoặc yyyymmdd."
        quantity = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        try:
            item = add_ticket_request(direction_code, depart_date, quantity, state_file=self.state_file)
        except ValueError as e:
            return str(e)
        extra = ""
        direction = get_direction(direction_code)
        if not direction.get("default_pickup_name") or not direction.get("default_dropoff_name"):
            extra = f"\n⚠️ Chiều '{direction['label']}' chưa có điểm đón/trả mặc định — dùng /setpickup và /setdropoff trước khi /book."
        return f"✅ Đã thêm watchlist:\n{format_item(item)}{extra}"

    def cmd_setpickup(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Cú pháp: /setpickup <id> <tên điểm đón>"
        item_id, name = parts
        item = get_item(item_id, self.state_file)
        if not item:
            return f"Không tìm thấy id={item_id}"
        from xeca_state import update_item
        update_item(item_id, path=self.state_file, pickup_name=name)
        return f"✅ Đã set điểm đón cho [{item_id}]: {name}"

    def cmd_setdropoff(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Cú pháp: /setdropoff <id> <tên điểm trả>"
        item_id, name = parts
        item = get_item(item_id, self.state_file)
        if not item:
            return f"Không tìm thấy id={item_id}"
        from xeca_state import update_item
        update_item(item_id, path=self.state_file, dropoff_name=name)
        return f"✅ Đã set điểm trả cho [{item_id}]: {name}"

    def cmd_list(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng. Dùng /add để thêm."
        return "\n\n".join(format_item(i) for i in items)

    def cmd_remove(self, rest: str) -> str:
        item_id = rest.strip()
        if not item_id:
            return "Cú pháp: /remove <id>"
        ok = remove_ticket_request(item_id, self.state_file)
        return f"✅ Đã xoá {item_id}" if ok else f"Không tìm thấy id={item_id}"

    def cmd_status(self, rest: str) -> str:
        status = get_status(self.state_file)
        lines = [f"Service xeca-watch: {status['service_active']}"]
        if not status["items"]:
            lines.append("Watchlist rỗng.")
        for item in status["items"]:
            lines.append(format_item(item, item.get("live")))
        return "\n\n".join(lines)

    def cmd_logs(self, rest: str) -> str:
        n = int(rest.strip()) if rest.strip().isdigit() else 20
        logs = get_logs(n)
        return f"<pre>{html.escape(logs[-3500:])}</pre>" if logs else "(không có log)"

    def cmd_book(self, rest: str) -> str:
        item_id = rest.strip()
        item = get_item(item_id, self.state_file)
        if not item:
            return f"Không tìm thấy id={item_id}"

        direction = get_direction(item["direction"])
        try:
            from xeca_client import XecaClient
            client = XecaClient()
            plan = plan_booking(client, item["depart_date"], direction, item.get("quantity", 1),
                                 item.get("pickup_name"), item.get("dropoff_name"))
        except RuntimeError as e:
            return f"Chưa thể đặt: {e}"

        code = f"{random.randint(0, 9999):04d}"
        self.pending_confirm = {
            "item_id": item_id,
            "code": code,
            "expires_at": datetime.now() + timedelta(seconds=CONFIRM_TTL_SECONDS),
        }
        return (
            f"{describe_plan(plan, direction)}\n\n"
            f"⚠️ Đây là ĐẶT VÉ THẬT (tốn tiền thật). "
            f"Gõ /confirm {code} trong {CONFIRM_TTL_SECONDS}s để xác nhận."
        )

    def cmd_confirm(self, rest: str) -> str:
        code = rest.strip()
        if not self.pending_confirm:
            return "Không có yêu cầu /book nào đang chờ xác nhận."
        if datetime.now() > self.pending_confirm["expires_at"]:
            self.pending_confirm = None
            return "Mã xác nhận đã hết hạn. Chạy lại /book <id>."
        if code != self.pending_confirm["code"]:
            return "Mã không đúng."

        item_id = self.pending_confirm["item_id"]
        self.pending_confirm = None
        self.send(f"⏳ Đang đặt vé thật cho {item_id} ...")
        returncode, output = run_booking(item_id, confirm=True, state_file=self.state_file, env_file=self.env_file)
        status = "✅ Xong" if returncode == 0 else "❌ Có lỗi"
        return f"{status} (exit={returncode})\n<pre>{html.escape(output[-3500:])}</pre>"


def main():
    load_env_file(".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ERROR] Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID trong .env")
        return

    bot = Bot(token, chat_id, DEFAULT_STATE_FILE, ".env")
    bot.run()


if __name__ == "__main__":
    main()
