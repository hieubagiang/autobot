"""Two-way Telegram control bot for the Xeca automation.

Long-polls Telegram getUpdates and only accepts commands/button-taps from the whitelisted
TELEGRAM_CHAT_ID (.env) — anyone else messaging the bot is ignored. Runs as its own
systemd service (xeca-bot.service), separate from xeca-watch.service (which only ever
*sends* notifications and never calls getUpdates, so the two don't conflict).

Commands (also exposed as the Telegram "/" menu via setMyCommands, and as inline buttons
under each /list item — buttons call the same handlers as the text commands):
  /add <HN-HT|HT-HN> <dd/mm/yyyy|yyyymmdd> [quantity=1]  - add a ticket request
  /setpickup <id> <tên điểm đón...>                       - override pickup point
  /setdropoff <id> <tên điểm trả...>                      - override dropoff point
  /list                                                    - list watchlist items (with buttons)
  /remove <id>                                             - remove an item
  /status                                                  - service + live sale-open check
  /book <id>                                               - preview plan, ask to /confirm
  /confirm <code>                                          - confirm the pending /book (real money!)
  /instant <id> on|off                                     - keep re-locking a seat until stopped
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
import threading
import time
import traceback
from datetime import datetime, timedelta

import requests

from xeca_client import DIRECTIONS, get_direction, load_env_file, send_telegram_message
from xeca_control import (
    add_ticket_request,
    get_logs,
    get_status,
    instant_lock_loop,
    list_ticket_requests,
    remove_ticket_request,
    run_booking,
    service_control,
)
from xeca_auto_book import describe_plan, plan_booking
from xeca_state import DEFAULT_STATE_FILE, get_item, update_item

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIRM_TTL_SECONDS = 120
LONG_POLL_TIMEOUT = 25

BOT_COMMANDS = [
    {"command": "add", "description": "Thêm vé cần theo dõi: <chiều> <ngày> [số lượng]"},
    {"command": "list", "description": "Xem watchlist"},
    {"command": "status", "description": "Trạng thái service + kiểm tra mở bán"},
    {"command": "book", "description": "Xem trước kế hoạch đặt vé (cần /confirm)"},
    {"command": "instant", "description": "Bật/tắt tự động giữ ghế liên tục: <id> on|off"},
    {"command": "remove", "description": "Xoá 1 vé khỏi watchlist"},
    {"command": "setpickup", "description": "Ghi đè điểm đón"},
    {"command": "setdropoff", "description": "Ghi đè điểm trả"},
    {"command": "logs", "description": "Xem log gần nhất"},
    {"command": "restart", "description": "Khởi động lại service theo dõi"},
    {"command": "help", "description": "Danh sách lệnh"},
]


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
    if item.get("instant"):
        line += " ⚡instant"
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


def item_keyboard(item: dict) -> dict:
    item_id = item["id"]
    instant_btn = (
        {"text": "⚡ Tắt instant", "callback_data": f"instoff:{item_id}"}
        if item.get("instant")
        else {"text": "⚡ Bật instant", "callback_data": f"inston:{item_id}"}
    )
    return {"inline_keyboard": [[
        {"text": "📖 Book", "callback_data": f"book:{item_id}"},
        instant_btn,
        {"text": "🗑 Xoá", "callback_data": f"remove:{item_id}"},
    ]]}


class Bot:
    def __init__(self, token: str, chat_id: str, state_file: str, env_file: str,
                 cust_name: str | None, cust_mobile: str | None):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
        self.env_file = env_file
        self.cust_name = cust_name
        self.cust_mobile = cust_mobile
        self.api = f"https://api.telegram.org/bot{token}"
        self.pending_confirm = None  # {"item_id", "code", "expires_at"}
        self.instant_threads = {}  # item_id -> {"stop_event": Event, "thread": Thread}

    def send(self, text: str, parse_mode: str | None = None, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = requests.post(f"{self.api}/sendMessage", json=payload, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def set_commands(self):
        try:
            requests.post(f"{self.api}/setMyCommands", json={"commands": BOT_COMMANDS}, timeout=10)
        except Exception:
            traceback.print_exc()

    def get_updates(self, offset: int | None):
        params = {"timeout": LONG_POLL_TIMEOUT}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(f"{self.api}/getUpdates", params=params, timeout=LONG_POLL_TIMEOUT + 10)
        resp.raise_for_status()
        return resp.json().get("result", [])

    def resume_instant_items(self):
        for item in list_ticket_requests(self.state_file):
            if item.get("instant"):
                print(f"[BOT] Resuming instant-lock for {item['id']} after restart")
                self.start_instant(item["id"])

    def run(self):
        requests.get(f"{self.api}/deleteWebhook", timeout=10)
        self.set_commands()
        self.resume_instant_items()
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
                try:
                    if "callback_query" in update:
                        self.handle_callback_query(update["callback_query"])
                    elif "message" in update or "edited_message" in update:
                        self.handle_message(update.get("message") or update.get("edited_message"))
                except Exception:
                    traceback.print_exc()

    def handle_message(self, message: dict):
        from_id = str(message.get("from", {}).get("id"))
        text = (message.get("text") or "").strip()
        if not text or from_id != self.chat_id:
            if text:
                print(f"[BOT] Ignoring message from unauthorized chat_id={from_id}")
            return
        try:
            reply = self.dispatch(text)
        except Exception as e:
            traceback.print_exc()
            reply = f"❌ Lỗi: {e}"
        if reply:
            try:
                self.send(reply)
            except Exception:
                traceback.print_exc()

    def handle_callback_query(self, cq: dict):
        from_id = str(cq.get("from", {}).get("id"))
        data = cq.get("data", "")
        try:
            requests.post(f"{self.api}/answerCallbackQuery", json={"callback_query_id": cq["id"]}, timeout=10)
        except Exception:
            traceback.print_exc()
        if from_id != self.chat_id:
            print(f"[BOT] Ignoring callback from unauthorized chat_id={from_id}")
            return

        action, _, item_id = data.partition(":")
        try:
            if action == "book":
                reply = self.cmd_book(item_id)
            elif action == "remove":
                reply = self.cmd_remove(item_id)
            elif action == "inston":
                reply = self.set_instant(item_id, True)
            elif action == "instoff":
                reply = self.set_instant(item_id, False)
            else:
                reply = f"Không hiểu hành động: {data}"
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
            "/instant": self.cmd_instant,
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
            "/list — danh sách vé đang theo dõi (kèm nút bấm)\n"
            "/remove <id>\n"
            "/status — trạng thái service + kiểm tra mở bán trực tiếp\n"
            "/book <id> — xem trước kế hoạch, cần /confirm để đặt thật\n"
            "/confirm <mã> — xác nhận đặt vé thật (có hiệu lực 2 phút)\n"
            "/instant <id> on|off — tự động giữ ghế liên tục (chưa thanh toán, tự giữ lại khi hết hạn)\n"
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
        update_item(item_id, path=self.state_file, dropoff_name=name)
        return f"✅ Đã set điểm trả cho [{item_id}]: {name}"

    def cmd_list(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng. Dùng /add để thêm."
        for item in items:
            self.send(format_item(item), reply_markup=item_keyboard(item))
        return ""

    def cmd_remove(self, rest: str) -> str:
        item_id = rest.strip()
        if not item_id:
            return "Cú pháp: /remove <id>"
        entry = self.instant_threads.pop(item_id, None)
        if entry:
            entry["stop_event"].set()
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
        if not logs:
            return "(không có log)"
        self.send(f"<pre>{html.escape(logs[-3500:])}</pre>", parse_mode="HTML")
        return ""

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
        self.send(f"{status} (exit={returncode})\n<pre>{html.escape(output[-3500:])}</pre>", parse_mode="HTML")
        return ""

    def cmd_instant(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            return "Cú pháp: /instant <id> on|off"
        item_id, action = parts[0], parts[1].lower()
        return self.set_instant(item_id, action == "on")

    def set_instant(self, item_id: str, enable: bool) -> str:
        item = get_item(item_id, self.state_file)
        if not item:
            return f"Không tìm thấy id={item_id}"

        if enable:
            if item_id in self.instant_threads:
                return f"[instant {item_id}] đã đang bật rồi."
            if not self.cust_name or not self.cust_mobile:
                return "Thiếu XECA_PASSENGER_NAME/XECA_PASSENGER_PHONE trong .env, không thể bật instant."
            update_item(item_id, path=self.state_file, instant=True)
            return self.start_instant(item_id)
        else:
            update_item(item_id, path=self.state_file, instant=False)
            entry = self.instant_threads.pop(item_id, None)
            if entry:
                entry["stop_event"].set()
            return f"🛑 Đã tắt instant-book cho [{item_id}]."

    def start_instant(self, item_id: str) -> str:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=instant_lock_loop,
            args=(item_id, stop_event, self.send, self.cust_name, self.cust_mobile,
                  self.state_file, self.env_file),
            daemon=True,
        )
        self.instant_threads[item_id] = {"stop_event": stop_event, "thread": thread}
        thread.start()
        return (
            f"⚡ Đã bật instant-book cho [{item_id}]. Sẽ tự giữ ghế liên tục (chưa thanh toán) "
            f"và tự giữ lại khi hết hạn, cho đến khi bạn /instant {item_id} off."
        )


def main():
    load_env_file(".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ERROR] Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID trong .env")
        return
    cust_name = os.environ.get("XECA_PASSENGER_NAME")
    cust_mobile = os.environ.get("XECA_PASSENGER_PHONE")

    bot = Bot(token, chat_id, DEFAULT_STATE_FILE, ".env", cust_name, cust_mobile)
    bot.run()


if __name__ == "__main__":
    main()
