"""Two-way Telegram control bot for crypto_signals -- long-polls getUpdates, only accepts
commands from the whitelisted chat_id (.env), same pattern as xeca_telegram_bot.py.

Commands:
  /addchannel <username> [signal|commentary]  - watch a new channel (default kind: signal)
  /removechannel <username>                   - stop watching a channel
  /listchannels                               - show watched channels
  /open                                       - show currently open (un-closed) signals
  /status                                     - is crypto-signals-listen.service running?
  /logs [n]                                   - last n lines of the listener's log
  /help                                       - this list

Usage: python -m crypto_signals.telegram_bot
"""

import time
import traceback

import requests

from . import control
from .env import load_env_file
from .telegram_api import send_message

LONG_POLL_TIMEOUT = 25


def format_channel_list(channels: list) -> str:
    if not channels:
        return "Danh sách kênh rỗng. Dùng /addchannel để thêm."
    return "\n".join(f"- {c['username']} ({c['kind']})" for c in channels)


def format_open_signals(signals: list) -> str:
    if not signals:
        return "Không có signal nào đang mở."
    lines = []
    for s in signals:
        entry_str = " - ".join(str(v) for v in s["entry"])
        targets_str = ", ".join(str(v) for v in s["targets"])
        lines.append(
            f"[{s['channel']}] {s['coin']} {s['direction']}\n"
            f"  Entry: {entry_str} | Targets: {targets_str} | Hits: {len(s['hits'])}"
        )
    return "\n\n".join(lines)


class Bot:
    def __init__(self, token: str, chat_id: str, state_file: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
        self.api = f"https://api.telegram.org/bot{token}"

    def send(self, text: str):
        send_message(self.token, self.chat_id, text)

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
            "/addchannel": self.cmd_addchannel,
            "/removechannel": self.cmd_removechannel,
            "/listchannels": lambda r: self.cmd_listchannels(),
            "/open": lambda r: self.cmd_open(),
            "/status": lambda r: self.cmd_status(),
            "/logs": self.cmd_logs,
            "/help": lambda r: self.cmd_help(),
        }
        handler = handlers.get(cmd)
        if not handler:
            return self.cmd_help()
        return handler(rest)

    def cmd_addchannel(self, rest: str) -> str:
        parts = rest.split()
        if not parts:
            return "Cú pháp: /addchannel <username> [signal|commentary]"
        username = parts[0]
        kind = parts[1].lower() if len(parts) > 1 else "signal"
        try:
            control.add_channel(username, kind, path=self.state_file)
        except ValueError as e:
            return f"❌ {e}"
        return f"✅ Đã thêm kênh {username} (kind={kind}). Nhớ restart listener để áp dụng."

    def cmd_removechannel(self, rest: str) -> str:
        username = rest.strip()
        if not username:
            return "Cú pháp: /removechannel <username>"
        ok = control.remove_channel(username, path=self.state_file)
        return f"✅ Đã xoá kênh {username}" if ok else f"Không tìm thấy kênh {username}"

    def cmd_listchannels(self) -> str:
        return format_channel_list(control.list_channels(self.state_file))

    def cmd_open(self) -> str:
        return format_open_signals(control.list_open_signals(self.state_file))

    def cmd_status(self) -> str:
        return f"crypto-signals-listen.service: {control.service_is_active()}"

    def cmd_logs(self, rest: str) -> str:
        n = int(rest.strip()) if rest.strip().isdigit() else 20
        logs = control.get_logs(n)
        return logs if logs else "(không có log)"

    def cmd_help(self) -> str:
        return (
            "Lệnh:\n"
            "/addchannel <username> [signal|commentary] — thêm kênh cần nghe\n"
            "/removechannel <username> — bỏ nghe 1 kênh\n"
            "/listchannels — danh sách kênh đang nghe\n"
            "/open — danh sách signal đang mở\n"
            "/status — trạng thái service listener\n"
            "/logs [n] — n dòng log gần nhất\n"
            "/help — danh sách lệnh"
        )


def main():
    import os

    load_env_file(".env")
    token = os.environ.get("CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("CRYPTO_SIGNALS_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ERROR] Thiếu CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN/CRYPTO_SIGNALS_TELEGRAM_CHAT_ID trong .env")
        return
    bot = Bot(token, chat_id, control.DEFAULT_STATE_FILE)
    bot.run()


if __name__ == "__main__":
    main()
