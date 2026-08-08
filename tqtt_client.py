"""Shared client for api.tqtt.vn (Tổ Quốc Trong Tim registration) + province/ward lookup.

Reverse-engineered endpoints are documented in docs/tqtt_booking_mechanism.md.
"""

from __future__ import annotations

import datetime
import json
import os
import random
import re
import time

import requests

BASE_URL = "https://api.tqtt.vn/api"
ORIGIN = "https://tqtt.vn"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FIELD_KEYS_CACHE_FILE = os.path.join(DATA_DIR, "tqtt_field_keys_cache.json")

# The submit form field names are NOT stable — tqtt.vn prefixes every logical field with a
# hashed pair like "a26082_k9f3m_" that changes across frontend deploys (observed firsthand
# on 2026-08-08: registration opened with a new prefix mid-deploy, our hardcoded plain field
# names ("name", "email", ...) got HTTP 400 code=900 "Invalid Data" for all 4 people, and by
# the time it was caught manually the event had already hit its participant cap). These
# helpers re-derive the real field keys from the live frontend bundle before every submit
# instead of hardcoding them, and flag it via `notify` whenever the mapping actually changes.
FIELD_SUFFIXES = (
    "name", "email", "identifier", "phone", "date_of_birth",
    "living_area", "ward", "priority_group", "agree_receive_info",
)
# Anchor suffix used to locate the current prefix — picked because "agree_receive_info" is
# unlikely to collide with an unrelated identifier elsewhere in a ~1MB minified bundle,
# unlike e.g. "name" or "ward".
_PREFIX_ANCHOR_SUFFIX = "agree_receive_info"

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


def _fetch_bundle_url(sess: requests.Session) -> str:
    resp = sess.get(ORIGIN + "/", timeout=20)
    resp.raise_for_status()
    m = re.search(r'src="(/assets/index-[^"]+\.js)"', resp.text)
    if not m:
        raise RuntimeError("Không tìm thấy bundle JS trong trang tqtt.vn — cấu trúc trang có thể đã đổi hoàn toàn.")
    return m.group(1)


def _fetch_bundle_text(sess: requests.Session, bundle_url: str) -> str:
    resp = sess.get(ORIGIN + bundle_url, timeout=30)
    resp.raise_for_status()
    return resp.text


def discover_field_keys(bundle_text: str) -> dict:
    """Extracts the current {suffix: actual_wire_key} mapping straight from the frontend
    bundle's source text, e.g. {"name": "a26082_k9f3m_name", ...}. Anchors on
    `_PREFIX_ANCHOR_SUFFIX` to find the current hashed prefix, then verifies every other
    field's exact `prefix_suffix` string literal is also present — so a partial/garbled
    match (deeper schema change, not just a renamed prefix) fails loudly instead of
    silently producing a mapping that will still 400 at submit time."""
    m = re.search(r'"([A-Za-z0-9]+_[A-Za-z0-9]+)_' + re.escape(_PREFIX_ANCHOR_SUFFIX) + r'"', bundle_text)
    if not m:
        raise RuntimeError(
            f"Không tìm thấy field neo '...{_PREFIX_ANCHOR_SUFFIX}' trong bundle JS — "
            "cấu trúc form có thể đã đổi hoàn toàn, cần kiểm tra thủ công."
        )
    prefix = m.group(1)

    field_keys = {}
    missing = []
    for suffix in FIELD_SUFFIXES:
        full_key = f"{prefix}_{suffix}"
        if f'"{full_key}"' in bundle_text:
            field_keys[suffix] = full_key
        else:
            missing.append(suffix)
    if missing:
        raise RuntimeError(
            f"Tìm được prefix '{prefix}' nhưng thiếu field: {', '.join(missing)} — "
            "cấu trúc form có thể đã đổi, cần kiểm tra thủ công."
        )
    return field_keys


def load_field_keys_cache(path: str = FIELD_KEYS_CACHE_FILE) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_field_keys_cache(bundle_url: str, field_keys: dict, path: str = FIELD_KEYS_CACHE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"bundle_url": bundle_url, "field_keys": field_keys}, f, ensure_ascii=False, indent=2)


def refresh_field_keys(cache_path: str = FIELD_KEYS_CACHE_FILE, notify=None) -> dict:
    """Cheap on every call when nothing changed: fetches only the tqtt.vn homepage
    (small) to read the current bundle URL, and only re-downloads+re-parses the ~1MB JS
    bundle when that URL differs from the cached one (i.e. a real frontend deploy
    happened) or there's no cache yet. Returns the {suffix: wire_key} mapping to use for
    `remap_payload()`. Calls `notify(text)` — if given — the moment the mapping actually
    changes from a previously-cached one, so a form-schema change (see module docstring
    note above) gets flagged instead of silently causing every submit to 400."""
    sess = requests.Session()
    sess.headers["user-agent"] = COMMON_HEADERS["user-agent"]

    bundle_url = _fetch_bundle_url(sess)
    cache = load_field_keys_cache(cache_path)

    if cache and cache.get("bundle_url") == bundle_url:
        return cache["field_keys"]

    bundle_text = _fetch_bundle_text(sess, bundle_url)
    field_keys = discover_field_keys(bundle_text)

    if cache and cache.get("field_keys") != field_keys and notify:
        notify(
            "⚠️ tqtt.vn vừa đổi cấu trúc field trong form đăng ký "
            f"(bundle JS mới: {bundle_url}).\n"
            f"Mapping cũ: {cache['field_keys']}\n"
            f"Mapping mới: {field_keys}\n"
            "Đã tự động cập nhật để dùng mapping mới — không cần làm gì thêm."
        )

    save_field_keys_cache(bundle_url, field_keys, cache_path)
    return field_keys


def remap_payload(payload: dict, field_keys: dict) -> dict:
    """Translates a friendly-key payload (name/email/identifier/...) into the actual wire
    JSON body the server currently expects, using the mapping from `refresh_field_keys()`."""
    return {field_keys[suffix]: payload[suffix] for suffix in FIELD_SUFFIXES}


TIGHT_POLL_SECONDS = 0.4  # poll cadence inside the tight window around a known --target-time
TIGHT_WINDOW_BEFORE_SECONDS = 60  # start hammering this long before target
TIGHT_WINDOW_AFTER_SECONDS = 120  # give up hammering this long after target if still not
# open (the announced time might be off) and fall back to the normal --interval cadence,
# rather than polling at TIGHT_POLL_SECONDS forever.


def parse_target_time(value: str) -> float:
    """Parses 'HH:MM' or 'HH:MM:SS' (server-local time — the deployment box runs
    Asia/Ho_Chi_Minh, matching VN wall-clock time directly) as the next occurrence of that
    time from now, returned as a Unix timestamp. Used when the opening time is announced
    in advance, so polling can switch to a much tighter cadence right around it instead of
    relying on the same interval the whole time — see next_poll_interval()."""
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    now = datetime.datetime.now()
    target = now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target.timestamp()


def is_in_tight_window(target_ts: float | None) -> bool:
    if target_ts is None:
        return False
    now = time.time()
    return target_ts - TIGHT_WINDOW_BEFORE_SECONDS <= now <= target_ts + TIGHT_WINDOW_AFTER_SECONDS


def next_poll_interval(interval: int, jitter: int, target_ts: float | None) -> float:
    """Normal cadence (`interval` + random jitter) far from `target_ts`; switches to
    near-continuous polling (`TIGHT_POLL_SECONDS`, no jitter — every fraction of a second
    counts here) inside the window around it. Matters when the whole event's capacity can
    fill within the first minute of opening — the up-to-`interval+jitter` lag of constant
    polling becomes the bottleneck, not request speed itself."""
    if is_in_tight_window(target_ts):
        return TIGHT_POLL_SECONDS
    return interval + random.randint(0, max(jitter, 0))


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
