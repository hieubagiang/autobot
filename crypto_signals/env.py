"""Minimal .env loader, kept local to crypto_signals rather than importing xeca_client's
version -- same reasoning cinema_booking/telegram_bot.py documents for its own copy:
stay independent of unrelated root-level modules rather than reach across domains."""

import os


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
