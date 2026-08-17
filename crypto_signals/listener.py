"""Telethon (personal-account MTProto) client: listens for new messages on every channel
in state.json, parses them, updates state, and relays a formatted alert via the Bot API.
Only ever sends -- never calls getUpdates (that's telegram_bot.py's job), so the two
services don't conflict.

route_message() is the fully unit-testable core (parse -> record -> format); run()/main()
are thin async glue around it and are verified manually after deploy, same as
cinema_booking's Beta Cinemas provider -- no CI test connects to real Telegram/Telethon.
"""

import asyncio
import os

from telethon import TelegramClient, events

from . import control, format, parser
from .env import load_env_file
from .telegram_api import send_message

SESSION_NAME = "crypto_signals_session"


def route_message(raw_text: str, channel_username: str, channel_kind: str,
                   state_path: str = control.DEFAULT_STATE_FILE) -> str:
    parsed = parser.parse_message(raw_text, channel_kind=channel_kind)
    outcome = control.record_parsed_message(parsed, channel_username, path=state_path)
    return format.format_outcome(channel_username, outcome)


async def run() -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    bot_token = os.environ["CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["CRYPTO_SIGNALS_TELEGRAM_CHAT_ID"]

    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.start()

    # Open risk (spec's "Rủi ro / điểm còn mở"): unconfirmed whether get_entity() alone is
    # enough to receive NewMessage events for a public channel this account has never
    # joined. If manual verification (see "Post-plan manual verification" below) shows no
    # events arrive, add `from telethon.tl.functions.channels import JoinChannelRequest`
    # and `await client(JoinChannelRequest(username))` here instead.
    channels_by_username = {c["username"]: c["kind"] for c in control.list_channels()}
    for username in channels_by_username:
        try:
            await client.get_entity(username)
        except Exception as e:
            print(f"[LISTENER] Không resolve được kênh '{username}': {e}")

    print(f"[LISTENER] Đang nghe {len(channels_by_username)} kênh: {list(channels_by_username)}")

    @client.on(events.NewMessage())
    async def handler(event):
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        if username not in channels_by_username:
            return
        text = route_message(event.raw_text, username, channels_by_username[username])
        try:
            send_message(bot_token, chat_id, text)
        except Exception as e:
            print(f"[LISTENER] Gửi Telegram thất bại: {e}")

    await client.run_until_disconnected()


def main():
    load_env_file(".env")
    asyncio.run(run())


if __name__ == "__main__":
    main()
