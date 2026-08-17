"""Tiny Bot-API sendMessage wrapper shared by listener.py (relay) and telegram_bot.py
(control-bot replies) -- both live in this package, unlike xeca_client's helpers which
cinema_booking deliberately avoids importing."""

import requests


def send_message(token: str, chat_id: str, text: str) -> dict:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()
