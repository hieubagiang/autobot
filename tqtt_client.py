"""Shared client for api.tqtt.vn (Tổ Quốc Trong Tim registration) + province/ward lookup.

Reverse-engineered endpoints are documented in docs/tqtt_booking_mechanism.md.
"""

from __future__ import annotations

import json
import os

import requests

BASE_URL = "https://api.tqtt.vn/api"
ORIGIN = "https://tqtt.vn"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

COMMON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": ORIGIN,
    "referer": ORIGIN + "/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

PRIORITY_GROUPS = {"revolutionary", "wheelchair_user", "none"}


def load_env_file(path: str = ".env"):
    """Minimal .env loader (KEY=VALUE per line), no external dependency."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _load_json(filename: str) -> list[dict]:
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def load_provinces() -> list[dict]:
    return _load_json("tqtt_provinces.json")


def load_wards() -> list[dict]:
    return _load_json("tqtt_wards.json")


def _matches(needle: str, candidate: dict, name_keys: tuple[str, ...]) -> bool:
    for key in name_keys:
        value = candidate[key].lower()
        if needle == value or needle in value:
            return True
    return False


def find_province(name_or_value: str) -> dict | None:
    """Matches against value/slug (exact) or name_vi/name_en (exact or substring,
    e.g. 'Hà Nội' matches 'Thành phố Hà Nội'), case-insensitive."""
    needle = name_or_value.strip().lower()
    for p in load_provinces():
        if needle in (p["value"].lower(), p["slug"].lower()):
            return p
    for p in load_provinces():
        if _matches(needle, p, ("name_vi", "name_en")):
            return p
    return None


def find_ward(name_or_value: str, province_code: str) -> dict | None:
    """Matches against value/slug (exact) or name_vi/name_en (exact or substring,
    e.g. 'Phú Lợi' matches 'Phường Phú Lợi') within the given province's wards
    (`parent_code` == province `code`, NOT province `value`)."""
    needle = name_or_value.strip().lower()
    candidates = [w for w in load_wards() if w["parent_code"] == province_code]
    for w in candidates:
        if needle in (w["value"].lower(), w["slug"].lower()):
            return w
    for w in candidates:
        if _matches(needle, w, ("name_vi", "name_en")):
            return w
    return None


class TqttClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(COMMON_HEADERS)

    def get_capacity(self) -> dict:
        """Returns {"capacity_valid": bool, "is_open": bool}."""
        resp = self.session.get(f"{BASE_URL}/concert/capacity", timeout=20)
        resp.raise_for_status()
        return resp.json().get("result", {})

    def submit(self, payload: dict) -> requests.Response:
        """Fires the registration POST exactly once — no retry here; the frontend's own
        axios-retry policy explicitly excludes 409/429, and callers should not hammer
        harder on those either. Raises only on network-level failure; HTTP error status
        is left for the caller to inspect via the returned Response."""
        return self.session.post(f"{BASE_URL}/concert/submit", json=payload, timeout=20)


def send_telegram_message(token: str, chat_id: str, text: str, parse_mode: str | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()
