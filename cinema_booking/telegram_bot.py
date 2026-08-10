"""Telegram control bot for the cinema-ticket watchlist.

Long-polls Telegram getUpdates and only accepts commands from the whitelisted
TELEGRAM_CHAT_ID (.env) — anyone else messaging the bot is ignored. Shape mirrors
xeca_telegram_bot.py's Bot class, but stays independent of the unrelated bus-ticket
modules — this module never imports from xeca_*.

Commands (see cmd_help for the live list):
  /add <provider> "<tên phim>" <dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>
  /list
  /remove <id>
  /setcinemapriority <id> <rạp 1>, <rạp 2>, ...
  /setquantity <id> <n>
  /setsweetbox <id> on|off
  /listcinemas <provider>
  /instant <id> on|off
  /paid <id>
  /status
  /help

Note: /logs (systemd-service log tailing) is intentionally not implemented here —
no systemd service exists yet for cinema_booking (see task brief); add it alongside
that deployment work.

Usage:
    python -m cinema_booking.telegram_bot
"""

import re
import threading
import time
import traceback

import requests

from cinema_booking.control import DEFAULT_CINEMA_PRIORITY, get_provider, instant_camp_loop
from cinema_booking.state import (
    DEFAULT_STATE_FILE, add_ticket_request, get_item, list_ticket_requests,
    remove_ticket_request, update_item,
)

LONG_POLL_TIMEOUT = 25

DATE_SINGLE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
DATE_RANGE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})-(\d{2}/\d{2}/\d{4})$")


def _to_iso(ddmmyyyy: str) -> str:
    d, m, y = ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


def parse_date_arg(text: str) -> list[str] | None:
    if DATE_RANGE_RE.match(text):
        start, end = text.split("-")
        return [_to_iso(start), _to_iso(end)]
    if DATE_SINGLE_RE.match(text):
        iso = _to_iso(text)
        return [iso, iso]
    return None


class Bot:
    def __init__(self, token: str, chat_id: str, state_file: str = DEFAULT_STATE_FILE):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
        self.api = f"https://api.telegram.org/bot{token}"
        self.instant_threads: dict[str, dict] = {}

    def send(self, text: str) -> None:
        requests.post(f"{self.api}/sendMessage", json={"chat_id": self.chat_id, "text": text}, timeout=20)

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
                print(f"[BOT] Resuming instant camp for {item['id']} after restart")
                self._start_instant(item["id"])

    def run(self):
        requests.get(f"{self.api}/deleteWebhook", timeout=10)
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
                    message = update.get("message") or update.get("edited_message")
                    if message:
                        self.handle_message(message)
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

    def dispatch(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        handlers = {
            "/add": self.cmd_add,
            "/list": self.cmd_list,
            "/remove": self.cmd_remove,
            "/setcinemapriority": self.cmd_setcinemapriority,
            "/setquantity": self.cmd_setquantity,
            "/setsweetbox": self.cmd_setsweetbox,
            "/listcinemas": self.cmd_listcinemas,
            "/instant": self.cmd_instant,
            "/paid": self.cmd_paid,
            "/status": self.cmd_status,
            "/help": lambda r: self.cmd_help(),
        }
        handler = handlers.get(cmd)
        if not handler:
            return self.cmd_help()
        return handler(rest)

    def cmd_help(self) -> str:
        return (
            "Lệnh:\n"
            '/add <provider> "<tên phim>" <ngày dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>\n'
            "/list\n"
            "/remove <id>\n"
            "/setcinemapriority <id> <rạp 1>, <rạp 2>, ...\n"
            "/setquantity <id> <n>\n"
            "/setsweetbox <id> on|off\n"
            "/listcinemas <provider>\n"
            "/instant <id> on|off\n"
            "/paid <id>\n"
            "/status"
        )

    def cmd_add(self, rest: str) -> str:
        match = re.match(r'^(\S+)\s+"([^"]+)"\s+(\S+)$', rest.strip())
        if not match:
            return '❌ Cú pháp: /add <provider> "<tên phim>" <dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>'
        provider, movie_query, date_text = match.groups()
        date_range = parse_date_arg(date_text)
        if date_range is None:
            return "❌ Ngày không hợp lệ, dùng dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy."
        cinema_priority = DEFAULT_CINEMA_PRIORITY.get(provider, [])
        item = add_ticket_request(provider, movie_query, date_range,
                                   cinema_priority=cinema_priority, state_file=self.state_file)
        extra = "" if cinema_priority else (
            "\n⚠️ Chưa có rạp ưu tiên mặc định cho provider này — dùng /setcinemapriority trước khi /instant."
        )
        return f"✅ Đã thêm watchlist [{item['id']}]: {movie_query} ({date_text}){extra}"

    def cmd_list(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng."
        for item in items:
            self.send(f"[{item['id']}] {item['movie_query']} — {item['status']}")
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

    def cmd_setcinemapriority(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Cú pháp: /setcinemapriority <id> <rạp 1>, <rạp 2>, ..."
        item_id, names_str = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        update_item(item_id, path=self.state_file, cinema_priority=names)
        return f"✅ Đã đặt ưu tiên rạp cho [{item_id}]: {' > '.join(names)}"

    def cmd_setquantity(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or not parts[1].isdigit():
            return "❌ Cú pháp: /setquantity <id> <n> (n là số nguyên)"
        item_id, quantity_text = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        quantity = int(quantity_text)
        update_item(item_id, path=self.state_file, quantity=quantity)
        return f"✅ Đã đặt số ghế cho [{item_id}]: {quantity}"

    def cmd_setsweetbox(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            return "❌ Cú pháp: /setsweetbox <id> on|off"
        item_id, action = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        update_item(item_id, path=self.state_file, prefer_sweetbox=action.lower() == "on")
        return f"✅ Đã đặt sweetbox={action.lower()} cho [{item_id}]"

    def cmd_listcinemas(self, rest: str) -> str:
        provider_name = rest.strip()
        if not provider_name:
            return "Cú pháp: /listcinemas <provider>"
        try:
            provider = get_provider(provider_name)
            cinemas = provider.list_cinemas()
        except Exception as e:
            return f"❌ Lỗi khi lấy danh sách rạp: {e}"
        return "\n".join(f"- {c.name}" for c in cinemas) or "(không có rạp nào)"

    def cmd_instant(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            return "Cú pháp: /instant <id> on|off"
        item_id, action = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        if action.lower() == "off":
            entry = self.instant_threads.pop(item_id, None)
            if entry:
                entry["stop_event"].set()
            update_item(item_id, path=self.state_file, instant=False)
            return f"🛑 Đã tắt instant cho [{item_id}]."
        if item_id in self.instant_threads:
            return f"[{item_id}] đã đang bật rồi."
        update_item(item_id, path=self.state_file, instant=True)
        self._start_instant(item_id)
        return f"⚡ Đã bật instant cho [{item_id}]."

    def _start_instant(self, item_id: str) -> None:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=instant_camp_loop, args=(item_id, stop_event, self.send, self.state_file), daemon=True,
        )
        self.instant_threads[item_id] = {"stop_event": stop_event, "thread": thread}
        thread.start()

    def cmd_paid(self, rest: str) -> str:
        item_id = rest.strip()
        if not item_id:
            return "Cú pháp: /paid <id>"
        item = get_item(item_id, self.state_file)
        if item is None:
            return f"Không tìm thấy id={item_id}"
        update_item(item_id, path=self.state_file, status="paid", instant=False)
        entry = self.instant_threads.pop(item_id, None)
        if entry:
            entry["stop_event"].set()
        return f"✅ Đã đánh dấu [{item_id}] là đã thanh toán."

    def cmd_status(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng."
        return "\n".join(f"[{i['id']}] {i['movie_query']} — {i['status']}" for i in items)


def _load_env_file(path: str) -> None:
    """Minimal .env loader — sets os.environ from KEY=VALUE lines, skipping blanks/comments.
    Deliberately not importing xeca_client.load_env_file: cinema_booking stays independent of
    the unrelated bus-ticket modules rather than reaching across domains for a 5-line helper."""
    import os

    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main():
    import os

    _load_env_file(".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ERROR] Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID trong .env")
        return
    Bot(token, chat_id, state_file=DEFAULT_STATE_FILE).run()


if __name__ == "__main__":
    main()
