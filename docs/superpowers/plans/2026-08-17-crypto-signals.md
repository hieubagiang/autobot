# Crypto Signals Listener (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `crypto_signals/`, a package that listens to one or more public Telegram channels via a personal-account Telethon client, parses trading-signal messages (entry/target/stop-loss) and free-text commentary, tracks signal lifecycle in a JSON state file, and relays formatted alerts to the user's own Telegram via a dedicated bot — with a second two-way control bot for managing the watched-channel list.

**Architecture:** Two independent long-running processes, mirroring the existing `xeca_*`/`cinema_booking` watch+bot pattern: `crypto_signals.listener` (Telethon client, only sends) and `crypto_signals.telegram_bot` (Bot-API long-poll control bot, manages channel list + restarts the listener via `systemctl`). Both share `crypto_signals_state.json` through a lock-protected `state.py`, with a `control.py` layer in between for CRUD + the signal/update matching decision, and a pure `format.py` for turning outcomes into message text.

**Tech Stack:** Python 3.13, `telethon` (MTProto, new dependency), `requests` (Bot API, already in `requirements.txt`), `pytest` (already used by `cinema_booking/tests`).

**Spec:** `docs/superpowers/specs/2026-08-17-crypto-signals-design.md`

## Global Constraints

- New package lives at `crypto_signals/` (repo root), same level as `cinema_booking/` — not inside `telegram-tools/`.
- Two systemd services: `crypto-signals-listen.service` (runs `python -m crypto_signals.listener`) and `crypto-signals-bot.service` (runs `python -m crypto_signals.telegram_bot`).
- `.env` vars: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (Telethon developer credentials — never hard-coded in source, unlike the older `telegram_bot_episode_grabber.py`), `CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN`, `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID`. The bot token **must** be different from `TELEGRAM_BOT_TOKEN`/`CINEMA_TELEGRAM_BOT_TOKEN` (two long-poll `getUpdates` connections sharing one token cause Telegram to return 409 and drop updates unpredictably).
- Telethon session file: `crypto_signals_session` — a name of its own, never reused from `telegram-tools/episode_grabber_session` (two processes opening the same Telethon SQLite session file lock-conflict).
- State file: `crypto_signals_state.json` (gitignored, same convention as `state.json`/`cinema_booking_state.json`).
- All user-facing bot replies are in Vietnamese, matching `xeca_telegram_bot.py`/`cinema_booking/telegram_bot.py`.
- `crypto_signals/` stays self-contained: no imports from root-level `xeca_*`/`tqtt_*` modules (same reasoning `cinema_booking/telegram_bot.py:300-303` documents for its own `_load_env_file` — don't reach across unrelated domains for a few lines of helper code).
- `parser.py` functions never raise on malformed/unexpected input — worst case is a `type: "unknown"` result, not an exception. This was verified against real, live-fetched channel text (see spec's research sections and the regex spike run during planning) rather than guessed field names.
- No automated test talks to real Telegram/Telethon. `listener.py`'s Telethon wiring is verified manually after deploy, exactly like `cinema_booking`'s Beta Cinemas provider.

---

## Task 1: Package skeleton + `state.py`

**Files:**
- Create: `crypto_signals/__init__.py`
- Create: `crypto_signals/state.py`
- Create: `crypto_signals/tests/__init__.py`
- Create: `crypto_signals/tests/test_state.py`

**Interfaces:**
- Produces: `state.DEFAULT_STATE_FILE: str`, `state.load_state(path=DEFAULT_STATE_FILE) -> dict`, `state.save_state(state: dict, path=DEFAULT_STATE_FILE) -> None`, `state.add_channel(username: str, kind: str = "signal", path=DEFAULT_STATE_FILE) -> dict`, `state.remove_channel(username: str, path=DEFAULT_STATE_FILE) -> bool`, `state.list_channels(path=DEFAULT_STATE_FILE) -> list[dict]`, `state.get_channel(username: str, path=DEFAULT_STATE_FILE) -> dict | None`, `state.add_signal(channel, coin, direction, entry, targets, targets_plus, sl, leverage, scalp, path=DEFAULT_STATE_FILE) -> dict`, `state.list_signals(path=DEFAULT_STATE_FILE) -> list[dict]`, `state.find_open_signal(channel: str, coin: str, path=DEFAULT_STATE_FILE) -> dict | None`, `state.append_hit(signal_id: str, hit: dict, path=DEFAULT_STATE_FILE) -> dict | None`.

- [ ] **Step 1: Create the package + empty test package**

```python
# crypto_signals/__init__.py
```

```python
# crypto_signals/tests/__init__.py
```

- [ ] **Step 2: Write the failing tests**

```python
# crypto_signals/tests/test_state.py
import pytest

from crypto_signals import state


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "crypto_signals_state.json")


def test_load_state_missing_file_returns_empty_shape(state_path):
    assert state.load_state(state_path) == {"channels": [], "signals": []}


def test_add_and_list_channels(state_path):
    state.add_channel("crypto_vulture_signals", "signal", path=state_path)
    state.add_channel("CryptoVIPsignalTA", "commentary", path=state_path)

    channels = state.list_channels(state_path)
    assert [c["username"] for c in channels] == ["crypto_vulture_signals", "CryptoVIPsignalTA"]
    assert channels[1]["kind"] == "commentary"
    assert "added_at" in channels[0]


def test_add_channel_duplicate_raises(state_path):
    state.add_channel("crypto_vulture_signals", path=state_path)
    with pytest.raises(ValueError):
        state.add_channel("crypto_vulture_signals", path=state_path)


def test_get_channel_found_and_missing(state_path):
    state.add_channel("crypto_vulture_signals", path=state_path)
    assert state.get_channel("crypto_vulture_signals", state_path)["kind"] == "signal"
    assert state.get_channel("nope", state_path) is None


def test_remove_channel(state_path):
    state.add_channel("crypto_vulture_signals", path=state_path)
    assert state.remove_channel("crypto_vulture_signals", state_path) is True
    assert state.list_channels(state_path) == []
    assert state.remove_channel("crypto_vulture_signals", state_path) is False


def test_add_signal_sets_defaults(state_path):
    signal = state.add_signal(
        channel="crypto_vulture_signals", coin="UNI/USDT", direction="LONG",
        entry=[3.28, 3.22], targets=[3.30, 3.32, 3.34, 3.37, 3.40], targets_plus=True,
        sl=3.16, leverage="60x", scalp=True, path=state_path,
    )
    assert signal["status"] == "open"
    assert signal["hits"] == []
    assert len(signal["id"]) == 8
    assert state.list_signals(state_path) == [signal]


def test_find_open_signal_matches_channel_and_coin(state_path):
    state.add_signal(channel="c1", coin="UNI/USDT", direction="LONG", entry=[1], targets=[2],
                      targets_plus=False, sl=0.5, leverage="10x", scalp=False, path=state_path)
    state.add_signal(channel="c1", coin="ETH/USDT", direction="LONG", entry=[1], targets=[2],
                      targets_plus=False, sl=0.5, leverage="10x", scalp=False, path=state_path)

    found = state.find_open_signal("c1", "UNI/USDT", state_path)
    assert found is not None
    assert found["coin"] == "UNI/USDT"
    assert state.find_open_signal("c1", "SOL/USDT", state_path) is None
    assert state.find_open_signal("other_channel", "UNI/USDT", state_path) is None


def test_find_open_signal_picks_most_recent_and_skips_closed(state_path):
    first = state.add_signal(channel="c1", coin="UNI/USDT", direction="LONG", entry=[1],
                              targets=[2], targets_plus=False, sl=0.5, leverage="10x",
                              scalp=False, path=state_path)
    second = state.add_signal(channel="c1", coin="UNI/USDT", direction="LONG", entry=[1],
                               targets=[2], targets_plus=False, sl=0.5, leverage="10x",
                               scalp=False, path=state_path)

    assert state.find_open_signal("c1", "UNI/USDT", state_path)["id"] == second["id"]

    # Close the newer one -- the older still-open one should now be the match.
    st = state.load_state(state_path)
    for s in st["signals"]:
        if s["id"] == second["id"]:
            s["status"] = "closed"
    state.save_state(st, state_path)
    assert state.find_open_signal("c1", "UNI/USDT", state_path)["id"] == first["id"]


def test_append_hit_updates_status_and_hits(state_path):
    signal = state.add_signal(channel="c1", coin="UNI/USDT", direction="LONG", entry=[1],
                               targets=[2], targets_plus=False, sl=0.5, leverage="10x",
                               scalp=False, path=state_path)
    hit = {"target_index": 1, "profit_pct": 36.5854, "period": "5 hr 26 min", "at": "now"}

    updated = state.append_hit(signal["id"], hit, state_path)
    assert updated["status"] == "tp_hit"
    assert updated["hits"] == [hit]
    assert state.append_hit("does-not-exist", hit, state_path) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_state.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'crypto_signals.state'` or similar — the module doesn't exist yet).

- [ ] **Step 4: Implement `state.py`**

```python
# crypto_signals/state.py
"""Shared JSON state file for crypto_signals: watched channels + tracked signals.

`listener.py` (writes continuously as new messages arrive) and `telegram_bot.py`
(reads for /status, /open, /listchannels; writes on /addchannel, /removechannel) are two
separate processes touching the same file -- same lock-file-based approach as
`xeca_state.py`'s `_StateFileLock` (exclusive-create + stale-lock reclaim), not just an
in-process threading.Lock.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

DEFAULT_STATE_FILE = "crypto_signals_state.json"

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05
LOCK_STALE_SECONDS = 30.0


class _StateFileLock:
    def __init__(self, path: str):
        self.lock_path = path + ".lock"
        self.fd = None

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > LOCK_STALE_SECONDS:
                        os.remove(self.lock_path)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Không lấy được lock cho {self.lock_path} sau {LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(LOCK_POLL_SECONDS)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.lock_path)
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: str = DEFAULT_STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {"channels": [], "signals": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"channels": data.get("channels", []), "signals": data.get("signals", [])}


def save_state(state: dict, path: str = DEFAULT_STATE_FILE) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def add_channel(username: str, kind: str = "signal", path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        st = load_state(path)
        if any(c["username"] == username for c in st["channels"]):
            raise ValueError(f"Kênh '{username}' đã có trong danh sách.")
        channel = {"username": username, "kind": kind, "added_at": _now_iso()}
        st["channels"].append(channel)
        save_state(st, path)
        return channel


def remove_channel(username: str, path: str = DEFAULT_STATE_FILE) -> bool:
    with _StateFileLock(path):
        st = load_state(path)
        before = len(st["channels"])
        st["channels"] = [c for c in st["channels"] if c["username"] != username]
        save_state(st, path)
        return len(st["channels"]) < before


def list_channels(path: str = DEFAULT_STATE_FILE) -> list:
    return load_state(path)["channels"]


def get_channel(username: str, path: str = DEFAULT_STATE_FILE) -> dict | None:
    for c in list_channels(path):
        if c["username"] == username:
            return c
    return None


def list_signals(path: str = DEFAULT_STATE_FILE) -> list:
    return load_state(path)["signals"]


def add_signal(channel: str, coin: str, direction: str, entry: list, targets: list,
                targets_plus: bool, sl: float, leverage: str, scalp: bool,
                path: str = DEFAULT_STATE_FILE) -> dict:
    with _StateFileLock(path):
        st = load_state(path)
        now = _now_iso()
        signal = {
            "id": uuid.uuid4().hex[:8],
            "channel": channel,
            "coin": coin,
            "direction": direction,
            "scalp": scalp,
            "entry": entry,
            "targets": targets,
            "targets_plus": targets_plus,
            "sl": sl,
            "leverage": leverage,
            "status": "open",
            "hits": [],
            "opened_at": now,
            "last_update_at": now,
        }
        st["signals"].append(signal)
        save_state(st, path)
        return signal


def find_open_signal(channel: str, coin: str, path: str = DEFAULT_STATE_FILE) -> dict | None:
    candidates = [
        s for s in load_state(path)["signals"]
        if s["channel"] == channel and s["coin"] == coin and s["status"] != "closed"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s["opened_at"])


def append_hit(signal_id: str, hit: dict, path: str = DEFAULT_STATE_FILE) -> dict | None:
    with _StateFileLock(path):
        st = load_state(path)
        for s in st["signals"]:
            if s["id"] == signal_id:
                s["hits"].append(hit)
                s["status"] = "tp_hit"
                s["last_update_at"] = _now_iso()
                save_state(st, path)
                return s
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_state.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 6: Commit**

```bash
git add crypto_signals/__init__.py crypto_signals/state.py crypto_signals/tests/__init__.py crypto_signals/tests/test_state.py
git commit -m "feat(crypto_signals): add package skeleton + state.py channel/signal store"
```

---

## Task 2: `parser.py` — structured signal templates

**Files:**
- Create: `crypto_signals/parser.py`
- Create: `crypto_signals/tests/test_parser.py`

**Interfaces:**
- Produces: `parser.normalize_coin(raw: str) -> str`, `parser._parse_scalp(text: str) -> dict | None`, `parser._parse_structured(text: str) -> dict | None`. Both return a dict shaped `{"type": "signal", "coin": str, "direction": "LONG"|"SHORT", "scalp": bool, "entry": list[float], "targets": list[float], "targets_plus": bool, "sl": float, "leverage": str}` on match, `None` otherwise.

- [ ] **Step 1: Write the failing tests**

These fixtures are the *exact* text captured live from `@crypto_vulture_signals` during spec research (2026-08-17) — not hand-written approximations. Note: Telethon's `message.raw_text` returns plain Unicode text; it does **not** contain the `_**bold**_`-style markdown wrappers a browser-preview scraper's HTML→markdown conversion adds around emoji — these fixtures reflect what the listener will actually receive.

```python
# crypto_signals/tests/test_parser.py
from crypto_signals.parser import normalize_coin, _parse_scalp, _parse_structured

SCALP_UNI = (
    "✅ SCALP TRADE - UNI 🏮 TYPE - LONG 👉 ENTRY - $3.28 - $3.22 👉 TARGET - $3.30, $3.32, "
    "$3.34, $3.37 & $3.40+ 👉 SL - $3.16 🚨LEVERAGE - 60x 🔴TRADE VALID ON"
)
SCALP_ENA_SHORT_NO_DOLLAR = (
    "✅ SCALP TRADE - ENA 🏮 TYPE - SHORT 👉 ENTRY - 0.08531-0.08583 👉 TARGET - 0.08470, "
    "0.08350, 0.08230, 0.08150 & 0.08079 👉 SL - 0.08923 🚨LEVERAGE - 50x 🔴TRADE VALID ON"
)
STRUCTURED_ETH = (
    "#ETHUSDT, #long, leverage - 50x\n"
    "📈 Entries: 1895\n\n"
    "🎯 Targets:\n1) 1910\n2) 1925\n3) 1940\n4) 1955\n\n"
    "🚫 Stop Loss:\n1) 1850"
)


def test_normalize_coin_bare_ticker_adds_usdt():
    assert normalize_coin("UNI") == "UNI/USDT"


def test_normalize_coin_glued_ticker_inserts_slash():
    assert normalize_coin("ETHUSDT") == "ETH/USDT"


def test_normalize_coin_already_slashed_is_unchanged():
    assert normalize_coin("UNI/USDT") == "UNI/USDT"


def test_parse_scalp_with_dollar_signs_and_plus_target():
    result = _parse_scalp(SCALP_UNI)
    assert result == {
        "type": "signal",
        "coin": "UNI/USDT",
        "direction": "LONG",
        "scalp": True,
        "entry": [3.28, 3.22],
        "targets": [3.30, 3.32, 3.34, 3.37, 3.40],
        "targets_plus": True,
        "sl": 3.16,
        "leverage": "60x",
    }


def test_parse_scalp_without_dollar_signs_short_no_plus():
    result = _parse_scalp(SCALP_ENA_SHORT_NO_DOLLAR)
    assert result["direction"] == "SHORT"
    assert result["coin"] == "ENA/USDT"
    assert result["entry"] == [0.08531, 0.08583]
    assert result["targets"] == [0.08470, 0.08350, 0.08230, 0.08150, 0.08079]
    assert result["targets_plus"] is False
    assert result["sl"] == 0.08923
    assert result["leverage"] == "50x"


def test_parse_scalp_returns_none_for_non_scalp_text():
    assert _parse_scalp(STRUCTURED_ETH) is None
    assert _parse_scalp("just a random sentence") is None


def test_parse_structured_eth_long():
    result = _parse_structured(STRUCTURED_ETH)
    assert result == {
        "type": "signal",
        "coin": "ETH/USDT",
        "direction": "LONG",
        "scalp": False,
        "entry": [1895.0],
        "targets": [1910.0, 1925.0, 1940.0, 1955.0],
        "targets_plus": False,
        "sl": 1850.0,
        "leverage": "50x",
    }


def test_parse_structured_returns_none_for_non_structured_text():
    assert _parse_structured(SCALP_UNI) is None
    assert _parse_structured("just a random sentence") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'crypto_signals.parser'`).

- [ ] **Step 3: Implement `parser.py` (signal-template portion)**

These regexes were verified against the real fixture strings above during planning (not written blind) — in particular, the `ENTRY`/`TARGET` boundaries anchor on the emoji delimiters the channel actually uses (👉/🚨/🔴), not on whitespace, and the `SL` group tolerates an optional `$`.

```python
# crypto_signals/parser.py
"""Parses @crypto_vulture_signals-style messages into structured dicts.

`parse_message()` (added in a later task) always returns a dict, never raises -- an
unrecognized message becomes `{"type": "unknown", ...}` rather than an exception, since
these channels have no committed schema and can change format at any time.
"""

import re

NUM_RE = re.compile(r"[\d.]+")

_SCALP_RE = re.compile(
    r"SCALP TRADE\s*-\s*(?P<coin>[A-Za-z0-9]+).*?"
    r"TYPE\s*-\s*(?P<direction>LONG|SHORT).*?"
    r"ENTRY\s*-\s*(?P<entry>.+?)(?:👉|🚨|🔴).*?"
    r"TARGET\s*-\s*(?P<targets>.+?)(?:👉|🚨|🔴).*?"
    r"SL\s*-\s*\$?(?P<sl>[\d.,]+).*?"
    r"LEVERAGE\s*-\s*(?P<leverage>\d+x)",
    re.IGNORECASE | re.DOTALL,
)

_HEADER_RE = re.compile(
    r"#(?P<coin>[A-Za-z0-9]+),\s*#(?P<direction>long|short),\s*leverage\s*-\s*(?P<leverage>\d+x)",
    re.IGNORECASE,
)
_ENTRIES_RE = re.compile(r"Entries:\s*(?P<entry>[^\n]+)", re.IGNORECASE)
_TARGETS_BLOCK_RE = re.compile(r"Targets:\s*(?P<block>(?:\s*\d+\)\s*[\d.,]+)+)", re.IGNORECASE)
_SL_BLOCK_RE = re.compile(r"Stop Loss:\s*(?P<block>(?:\s*\d+\)\s*[\d.,]+)+)", re.IGNORECASE)
_NUMBERED_VALUE_RE = re.compile(r"\d+\)\s*([\d.,]+)")


def normalize_coin(raw: str) -> str:
    """'UNI' -> 'UNI/USDT', 'ETHUSDT' -> 'ETH/USDT', 'UNI/USDT' -> unchanged."""
    raw = raw.upper().strip().lstrip("#")
    if raw.endswith("USDT") and "/" not in raw:
        return raw[:-4] + "/USDT"
    if "/" in raw:
        return raw
    return raw + "/USDT"


def _parse_number_list(raw: str, sep: str) -> list:
    parts = [p for p in raw.split(sep) if NUM_RE.search(p)]
    return [float(NUM_RE.search(p).group()) for p in parts]


def _parse_scalp(text: str) -> dict | None:
    m = _SCALP_RE.search(text)
    if not m:
        return None
    d = m.groupdict()
    targets_raw = d["targets"].replace("&", ",")
    targets = _parse_number_list(targets_raw, ",")
    plus_parts = [p for p in targets_raw.split(",") if NUM_RE.search(p)]
    targets_plus = bool(plus_parts) and "+" in plus_parts[-1]
    return {
        "type": "signal",
        "coin": normalize_coin(d["coin"]),
        "direction": d["direction"].upper(),
        "scalp": True,
        "entry": _parse_number_list(d["entry"], "-"),
        "targets": targets,
        "targets_plus": targets_plus,
        "sl": float(NUM_RE.search(d["sl"]).group()),
        "leverage": d["leverage"].lower(),
    }


def _parse_structured(text: str) -> dict | None:
    header = _HEADER_RE.search(text)
    entries = _ENTRIES_RE.search(text)
    targets_block = _TARGETS_BLOCK_RE.search(text)
    sl_block = _SL_BLOCK_RE.search(text)
    if not (header and entries and targets_block and sl_block):
        return None
    sl_values = [float(v) for v in _NUMBERED_VALUE_RE.findall(sl_block.group("block"))]
    return {
        "type": "signal",
        "coin": normalize_coin(header.group("coin")),
        "direction": header.group("direction").upper(),
        "scalp": False,
        "entry": _parse_number_list(entries.group("entry"), "-"),
        "targets": [float(v) for v in _NUMBERED_VALUE_RE.findall(targets_block.group("block"))],
        "targets_plus": False,
        "sl": sl_values[0],
        "leverage": header.group("leverage").lower(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add crypto_signals/parser.py crypto_signals/tests/test_parser.py
git commit -m "feat(crypto_signals): parse SCALP TRADE and structured signal templates"
```

---

## Task 3: `parser.py` — update messages (TP hit, entry filled)

**Files:**
- Modify: `crypto_signals/parser.py`
- Modify: `crypto_signals/tests/test_parser.py`

**Interfaces:**
- Consumes: `normalize_coin(raw: str) -> str` (Task 2).
- Produces: `parser._parse_tp_hit(text: str) -> dict | None`, `parser._parse_entry_filled(text: str) -> dict | None`. Both return `{"type": "update", "coin": str, "kind": "tp_hit"|"entry_filled", "target_index": int, "profit_pct": float | None, "period": str | None, "entry_price": float | None}` on match, `None` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
# append to crypto_signals/tests/test_parser.py
from crypto_signals.parser import _parse_tp_hit, _parse_entry_filled

TP_HIT_TEXT = "#UNI/USDT Take-Profit target 1 ✅\nProfit: 36.5854% 📈\nPeriod: 5 hr 26 min ⏰"
ENTRY_FILLED_TEXT = "#UNI/USDT Entry 1 ✅\nAverage Entry Price: 3.28 💵"


def test_parse_tp_hit():
    result = _parse_tp_hit(TP_HIT_TEXT)
    assert result == {
        "type": "update",
        "coin": "UNI/USDT",
        "kind": "tp_hit",
        "target_index": 1,
        "profit_pct": 36.5854,
        "period": "5 hr 26 min",
        "entry_price": None,
    }


def test_parse_tp_hit_returns_none_for_other_text():
    assert _parse_tp_hit(ENTRY_FILLED_TEXT) is None
    assert _parse_tp_hit(SCALP_UNI) is None


def test_parse_entry_filled():
    result = _parse_entry_filled(ENTRY_FILLED_TEXT)
    assert result == {
        "type": "update",
        "coin": "UNI/USDT",
        "kind": "entry_filled",
        "target_index": 1,
        "profit_pct": None,
        "period": None,
        "entry_price": 3.28,
    }


def test_parse_entry_filled_returns_none_for_other_text():
    assert _parse_entry_filled(TP_HIT_TEXT) is None
    assert _parse_entry_filled(STRUCTURED_ETH) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: FAIL (`ImportError: cannot import name '_parse_tp_hit'`).

- [ ] **Step 3: Implement the update parsers**

```python
# append to crypto_signals/parser.py
_TP_HIT_RE = re.compile(
    r"#(?P<coin>[A-Za-z0-9]+)/USDT\s+Take-Profit target\s*(?P<target_index>\d+).*?"
    r"Profit:\s*(?P<profit_pct>[\d.]+)%.*?"
    r"Period:\s*(?P<period>[^\n⏰]+)",
    re.IGNORECASE | re.DOTALL,
)
_ENTRY_FILLED_RE = re.compile(
    r"#(?P<coin>[A-Za-z0-9]+)/USDT\s+Entry\s*(?P<target_index>\d+).*?"
    r"Average Entry Price:\s*(?P<entry_price>[\d.]+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_tp_hit(text: str) -> dict | None:
    m = _TP_HIT_RE.search(text)
    if not m:
        return None
    return {
        "type": "update",
        "coin": normalize_coin(m.group("coin")),
        "kind": "tp_hit",
        "target_index": int(m.group("target_index")),
        "profit_pct": float(m.group("profit_pct")),
        "period": m.group("period").strip(),
        "entry_price": None,
    }


def _parse_entry_filled(text: str) -> dict | None:
    m = _ENTRY_FILLED_RE.search(text)
    if not m:
        return None
    return {
        "type": "update",
        "coin": normalize_coin(m.group("coin")),
        "kind": "entry_filled",
        "target_index": int(m.group("target_index")),
        "profit_pct": None,
        "period": None,
        "entry_price": float(m.group("entry_price")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: PASS (all 12 tests).

- [ ] **Step 5: Commit**

```bash
git add crypto_signals/parser.py crypto_signals/tests/test_parser.py
git commit -m "feat(crypto_signals): parse TP-hit and entry-filled update messages"
```

---

## Task 4: `parser.py` — commentary extraction, unknown fallback, `parse_message()` dispatcher

**Files:**
- Modify: `crypto_signals/parser.py`
- Modify: `crypto_signals/tests/test_parser.py`

**Interfaces:**
- Consumes: `_parse_scalp`, `_parse_structured` (Task 2), `_parse_tp_hit`, `_parse_entry_filled` (Task 3).
- Produces: `parser.extract_commentary_coins(text: str) -> list[str]`, `parser.parse_message(text: str, channel_kind: str = "signal") -> dict` — the single public entry point every other module calls. Always returns one of: `{"type": "signal", ...}`, `{"type": "update", ...}`, `{"type": "commentary", "coins": list[str], "raw": str}`, `{"type": "unknown", "raw": str}`.

- [ ] **Step 1: Write the failing tests**

These commentary fixtures are the exact text captured live from `@CryptoVIPsignalTA` during spec research.

```python
# append to crypto_signals/tests/test_parser.py
from crypto_signals.parser import extract_commentary_coins, parse_message

ZK_ANALYSIS = (
    "ZK analysis:\n"
    "Price is breaking out of the falling wedge pattern upward. We will open a long "
    "position after confirmation. We expect a significant upward move once the breakout "
    "is confirmed.\n"
    "Key Level to Hold: $0.007700"
)
BITCOIN_COMMENTARY = (
    "Bitcoin started the week with a strong green candle. I expect this upward movement "
    "to continue when the US market opens."
)
NO_COIN_COMMENTARY = "The market feels quiet today, nothing notable to report right now."


def test_extract_commentary_coins_from_analysis_header():
    assert extract_commentary_coins(ZK_ANALYSIS) == ["ZK"]


def test_extract_commentary_coins_from_known_alias():
    assert extract_commentary_coins(BITCOIN_COMMENTARY) == ["BTC"]


def test_extract_commentary_coins_empty_when_nothing_found():
    assert extract_commentary_coins(NO_COIN_COMMENTARY) == []


def test_parse_message_dispatches_to_scalp():
    assert parse_message(SCALP_UNI, channel_kind="signal")["type"] == "signal"


def test_parse_message_dispatches_to_structured():
    assert parse_message(STRUCTURED_ETH, channel_kind="signal")["type"] == "signal"


def test_parse_message_dispatches_to_update():
    result = parse_message(TP_HIT_TEXT, channel_kind="signal")
    assert result["type"] == "update"
    assert result["kind"] == "tp_hit"


def test_parse_message_commentary_channel_falls_back_to_commentary():
    result = parse_message(ZK_ANALYSIS, channel_kind="commentary")
    assert result == {"type": "commentary", "coins": ["ZK"], "raw": ZK_ANALYSIS}


def test_parse_message_signal_channel_falls_back_to_unknown():
    garbage = "just a random sentence with no known structure"
    assert parse_message(garbage, channel_kind="signal") == {"type": "unknown", "raw": garbage}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: FAIL (`ImportError: cannot import name 'extract_commentary_coins'`).

- [ ] **Step 3: Implement commentary extraction + dispatcher**

```python
# append to crypto_signals/parser.py
_ANALYSIS_HEADER_RE = re.compile(r"^\s*([A-Za-z0-9]{2,10})\s+analysis:", re.IGNORECASE)

# Small seed list -- expand as new coin names show up in real commentary (see spec's
# "Rủi ro / điểm còn mở"). A miss just means coins=[] for that message, not an error.
_COIN_ALIASES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "btc": "BTC",
    "eth": "ETH",
}


def extract_commentary_coins(text: str) -> list:
    coins = []
    m = _ANALYSIS_HEADER_RE.match(text)
    if m:
        coins.append(m.group(1).upper())
    lowered = text.lower()
    for alias, ticker in _COIN_ALIASES.items():
        if alias in lowered and ticker not in coins:
            coins.append(ticker)
    return coins


def parse_message(text: str, channel_kind: str = "signal") -> dict:
    text = text.strip()
    for parse_fn in (_parse_scalp, _parse_structured, _parse_tp_hit, _parse_entry_filled):
        result = parse_fn(text)
        if result is not None:
            return result
    if channel_kind == "commentary":
        return {"type": "commentary", "coins": extract_commentary_coins(text), "raw": text}
    return {"type": "unknown", "raw": text}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_parser.py -v`
Expected: PASS (all 20 tests).

- [ ] **Step 5: Commit**

```bash
git add crypto_signals/parser.py crypto_signals/tests/test_parser.py
git commit -m "feat(crypto_signals): add commentary extraction and parse_message dispatcher"
```

---

## Task 5: `control.py` — channel CRUD, signal/update matching, service control

**Files:**
- Create: `crypto_signals/control.py`
- Create: `crypto_signals/tests/test_control.py`

**Interfaces:**
- Consumes: `state.*` (Task 1), `parser.parse_message()` output shapes (Task 4, for shaping test fixtures — `control.py` itself only receives already-parsed dicts, it does not call `parser` directly).
- Produces: `control.DEFAULT_STATE_FILE: str`, `control.SERVICE_NAME: str`, `control.add_channel(username: str, kind: str = "signal", path=DEFAULT_STATE_FILE) -> dict`, `control.remove_channel(username: str, path=DEFAULT_STATE_FILE) -> bool`, `control.list_channels(path=DEFAULT_STATE_FILE) -> list[dict]`, `control.list_open_signals(path=DEFAULT_STATE_FILE) -> list[dict]`, `control.record_parsed_message(parsed: dict, channel: str, path=DEFAULT_STATE_FILE) -> dict` (an "outcome" dict — see below), `control.service_control(action: str) -> str`, `control.service_is_active() -> str`, `control.get_logs(n: int = 20) -> str`.
- `record_parsed_message()` outcome shapes: `{"kind": "new_signal", "signal": dict}`, `{"kind": "update_matched", "signal": dict, "update": dict}`, `{"kind": "update_unmatched", "update": dict}`, `{"kind": "commentary", "commentary": dict}`, `{"kind": "unknown", "raw": str}`.

- [ ] **Step 1: Write the failing tests**

```python
# crypto_signals/tests/test_control.py
from unittest.mock import patch

import pytest

from crypto_signals import control


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "crypto_signals_state.json")


def test_add_list_remove_channel(state_path):
    control.add_channel("crypto_vulture_signals", "signal", path=state_path)
    control.add_channel("CryptoVIPsignalTA", "commentary", path=state_path)
    assert [c["username"] for c in control.list_channels(state_path)] == [
        "crypto_vulture_signals", "CryptoVIPsignalTA",
    ]
    assert control.remove_channel("CryptoVIPsignalTA", state_path) is True
    assert [c["username"] for c in control.list_channels(state_path)] == ["crypto_vulture_signals"]


def test_add_channel_strips_leading_at(state_path):
    channel = control.add_channel("@crypto_vulture_signals", path=state_path)
    assert channel["username"] == "crypto_vulture_signals"


def test_add_channel_rejects_invalid_kind(state_path):
    with pytest.raises(ValueError):
        control.add_channel("some_channel", kind="bogus", path=state_path)


def test_record_new_signal_creates_open_signal(state_path):
    parsed = {
        "type": "signal", "coin": "UNI/USDT", "direction": "LONG", "scalp": True,
        "entry": [3.28, 3.22], "targets": [3.30, 3.32], "targets_plus": True,
        "sl": 3.16, "leverage": "60x",
    }
    outcome = control.record_parsed_message(parsed, "crypto_vulture_signals", path=state_path)
    assert outcome["kind"] == "new_signal"
    assert outcome["signal"]["status"] == "open"
    assert control.list_open_signals(state_path) == [outcome["signal"]]


def test_record_update_matches_most_recent_open_signal(state_path):
    signal_parsed = {
        "type": "signal", "coin": "UNI/USDT", "direction": "LONG", "scalp": True,
        "entry": [3.28, 3.22], "targets": [3.30, 3.32], "targets_plus": True,
        "sl": 3.16, "leverage": "60x",
    }
    new_outcome = control.record_parsed_message(signal_parsed, "crypto_vulture_signals", path=state_path)
    signal_id = new_outcome["signal"]["id"]

    update_parsed = {
        "type": "update", "coin": "UNI/USDT", "kind": "tp_hit", "target_index": 1,
        "profit_pct": 36.5854, "period": "5 hr 26 min", "entry_price": None,
    }
    outcome = control.record_parsed_message(update_parsed, "crypto_vulture_signals", path=state_path)
    assert outcome["kind"] == "update_matched"
    assert outcome["signal"]["id"] == signal_id
    assert outcome["signal"]["status"] == "tp_hit"
    assert outcome["signal"]["hits"][0]["profit_pct"] == 36.5854
    assert outcome["update"] == update_parsed


def test_record_update_unmatched_when_no_open_signal(state_path):
    update_parsed = {
        "type": "update", "coin": "SOL/USDT", "kind": "tp_hit", "target_index": 1,
        "profit_pct": 10.0, "period": "1 hr", "entry_price": None,
    }
    outcome = control.record_parsed_message(update_parsed, "crypto_vulture_signals", path=state_path)
    assert outcome == {"kind": "update_unmatched", "update": update_parsed}


def test_record_commentary_and_unknown_do_not_touch_state(state_path):
    commentary_parsed = {"type": "commentary", "coins": ["ZK"], "raw": "ZK analysis: ..."}
    unknown_parsed = {"type": "unknown", "raw": "huh"}

    c_outcome = control.record_parsed_message(commentary_parsed, "CryptoVIPsignalTA", path=state_path)
    u_outcome = control.record_parsed_message(unknown_parsed, "crypto_vulture_signals", path=state_path)

    assert c_outcome == {"kind": "commentary", "commentary": commentary_parsed}
    assert u_outcome == {"kind": "unknown", "raw": "huh"}
    assert control.list_open_signals(state_path) == []


@patch("crypto_signals.control.subprocess.run")
def test_service_control_success(mock_run):
    mock_run.return_value.returncode = 0
    result = control.service_control("restart")
    mock_run.assert_called_once_with(
        ["systemctl", "restart", control.SERVICE_NAME], capture_output=True, text=True,
    )
    assert "restart" in result.lower() or "✅" in result


@patch("crypto_signals.control.subprocess.run")
def test_service_control_failure_reports_stderr(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "unit not found"
    result = control.service_control("restart")
    assert "unit not found" in result


def test_service_control_rejects_invalid_action():
    with pytest.raises(ValueError):
        control.service_control("delete")


@patch("crypto_signals.control.subprocess.run")
def test_get_logs_returns_stdout(mock_run):
    mock_run.return_value.stdout = "log line 1\nlog line 2\n"
    assert control.get_logs(5) == "log line 1\nlog line 2\n"
    mock_run.assert_called_once_with(
        ["journalctl", "-u", control.SERVICE_NAME, "-n", "5", "--no-pager"],
        capture_output=True, text=True,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_control.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'crypto_signals.control'`).

- [ ] **Step 3: Implement `control.py`**

```python
# crypto_signals/control.py
"""Business-logic layer between the raw state store and both processes (`listener.py`,
`telegram_bot.py`) -- CRUD for channels, deciding what a parsed message does to state
(new signal vs. matched/unmatched update vs. no-op), and wrapping systemctl/journalctl so
neither caller shells out directly."""

import subprocess
from datetime import datetime, timezone

from . import state

DEFAULT_STATE_FILE = state.DEFAULT_STATE_FILE
SERVICE_NAME = "crypto-signals-listen.service"
VALID_CHANNEL_KINDS = ("signal", "commentary")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_channel(username: str, kind: str = "signal", path: str = DEFAULT_STATE_FILE) -> dict:
    if kind not in VALID_CHANNEL_KINDS:
        raise ValueError(f"kind không hợp lệ: {kind} (phải là 'signal' hoặc 'commentary')")
    return state.add_channel(username.strip().lstrip("@"), kind, path)


def remove_channel(username: str, path: str = DEFAULT_STATE_FILE) -> bool:
    return state.remove_channel(username.strip().lstrip("@"), path)


def list_channels(path: str = DEFAULT_STATE_FILE) -> list:
    return state.list_channels(path)


def list_open_signals(path: str = DEFAULT_STATE_FILE) -> list:
    return [s for s in state.list_signals(path) if s["status"] != "closed"]


def record_parsed_message(parsed: dict, channel: str, path: str = DEFAULT_STATE_FILE) -> dict:
    msg_type = parsed["type"]

    if msg_type == "signal":
        signal = state.add_signal(
            channel=channel, coin=parsed["coin"], direction=parsed["direction"],
            entry=parsed["entry"], targets=parsed["targets"],
            targets_plus=parsed["targets_plus"], sl=parsed["sl"],
            leverage=parsed["leverage"], scalp=parsed["scalp"], path=path,
        )
        return {"kind": "new_signal", "signal": signal}

    if msg_type == "update":
        existing = state.find_open_signal(channel, parsed["coin"], path)
        if existing is None:
            return {"kind": "update_unmatched", "update": parsed}
        hit = {
            "target_index": parsed["target_index"],
            "profit_pct": parsed.get("profit_pct"),
            "period": parsed.get("period"),
            "entry_price": parsed.get("entry_price"),
            "update_kind": parsed["kind"],
            "at": _now_iso(),
        }
        updated = state.append_hit(existing["id"], hit, path)
        return {"kind": "update_matched", "signal": updated, "update": parsed}

    if msg_type == "commentary":
        return {"kind": "commentary", "commentary": parsed}

    return {"kind": "unknown", "raw": parsed["raw"]}


def service_control(action: str) -> str:
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"action không hợp lệ: {action}")
    result = subprocess.run(
        ["systemctl", action, SERVICE_NAME], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return f"❌ systemctl {action} thất bại: {result.stderr.strip()}"
    return f"✅ Đã {action} {SERVICE_NAME}"


def service_is_active() -> str:
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True,
    )
    return result.stdout.strip() or "unknown"


def get_logs(n: int = 20) -> str:
    result = subprocess.run(
        ["journalctl", "-u", SERVICE_NAME, "-n", str(n), "--no-pager"],
        capture_output=True, text=True,
    )
    return result.stdout
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_control.py -v`
Expected: PASS (all 11 tests).

- [ ] **Step 5: Commit**

```bash
git add crypto_signals/control.py crypto_signals/tests/test_control.py
git commit -m "feat(crypto_signals): add control.py CRUD, signal/update matching, service control"
```

---

## Task 6: `format.py` — outcome-to-message-text formatting

**Files:**
- Create: `crypto_signals/format.py`
- Create: `crypto_signals/tests/test_format.py`

**Interfaces:**
- Consumes: outcome dict shapes from `control.record_parsed_message()` (Task 5).
- Produces: `format.format_outcome(channel: str, outcome: dict) -> str` — the single entry point `listener.py` calls.

- [ ] **Step 1: Write the failing tests**

```python
# crypto_signals/tests/test_format.py
from crypto_signals.format import format_outcome


def test_format_new_signal_scalp_with_plus_target():
    signal = {
        "coin": "UNI/USDT", "direction": "LONG", "scalp": True,
        "entry": [3.28, 3.22], "targets": [3.30, 3.32, 3.34, 3.37, 3.40],
        "targets_plus": True, "sl": 3.16, "leverage": "60x",
    }
    text = format_outcome("crypto_vulture_signals", {"kind": "new_signal", "signal": signal})
    assert "[crypto_vulture_signals]" in text
    assert "UNI/USDT" in text
    assert "LONG" in text
    assert "(scalp)" in text
    assert "3.28 - 3.22" in text
    assert "3.3, 3.32, 3.34, 3.37, 3.4+" in text
    assert "SL: 3.16" in text
    assert "60x" in text


def test_format_new_signal_structured_no_scalp_label():
    signal = {
        "coin": "ETH/USDT", "direction": "LONG", "scalp": False,
        "entry": [1895.0], "targets": [1910.0, 1925.0], "targets_plus": False,
        "sl": 1850.0, "leverage": "50x",
    }
    text = format_outcome("crypto_vulture_signals", {"kind": "new_signal", "signal": signal})
    assert "(scalp)" not in text
    assert "Entry: 1895" in text
    assert "Targets: 1910, 1925" in text


def test_format_update_matched_tp_hit():
    signal = {"coin": "UNI/USDT", "direction": "LONG"}
    update = {"kind": "tp_hit", "target_index": 1, "profit_pct": 36.5854, "period": "5 hr 26 min"}
    text = format_outcome("crypto_vulture_signals", {
        "kind": "update_matched", "signal": signal, "update": update,
    })
    assert "TP1 hit" in text
    assert "+36.59%" in text
    assert "5 hr 26 min" in text


def test_format_update_matched_entry_filled():
    signal = {"coin": "UNI/USDT", "direction": "LONG"}
    update = {"kind": "entry_filled", "target_index": 1, "entry_price": 3.28}
    text = format_outcome("crypto_vulture_signals", {
        "kind": "update_matched", "signal": signal, "update": update,
    })
    assert "Entry 1 filled" in text
    assert "3.28" in text


def test_format_update_unmatched():
    update = {"coin": "SOL/USDT", "kind": "tp_hit"}
    text = format_outcome("crypto_vulture_signals", {"kind": "update_unmatched", "update": update})
    assert "SOL/USDT" in text
    assert "không tìm thấy" in text


def test_format_commentary_with_coin():
    commentary = {"coins": ["ZK"], "raw": "ZK analysis: bullish breakout"}
    text = format_outcome("CryptoVIPsignalTA", {"kind": "commentary", "commentary": commentary})
    assert "[CryptoVIPsignalTA]" in text
    assert "#ZK" in text
    assert "bullish breakout" in text


def test_format_commentary_without_coin_uses_placeholder():
    commentary = {"coins": [], "raw": "quiet market today"}
    text = format_outcome("CryptoVIPsignalTA", {"kind": "commentary", "commentary": commentary})
    assert "❔" in text


def test_format_unknown():
    text = format_outcome("crypto_vulture_signals", {"kind": "unknown", "raw": "huh"})
    assert "Không nhận diện được định dạng" in text
    assert "huh" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_format.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'crypto_signals.format'`).

- [ ] **Step 3: Implement `format.py`**

```python
# crypto_signals/format.py
"""Pure functions turning a control.record_parsed_message() outcome into the exact text
sent to Telegram. No I/O here -- listener.py owns the actual send call."""


def _fmt_num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def format_new_signal(channel: str, signal: dict) -> str:
    entry_str = " - ".join(_fmt_num(v) for v in signal["entry"])
    targets_str = ", ".join(_fmt_num(v) for v in signal["targets"])
    if signal.get("targets_plus"):
        targets_str += "+"
    scalp_label = " (scalp)" if signal.get("scalp") else ""
    return (
        f"🆕 [{channel}] {signal['coin']} — {signal['direction']}{scalp_label}\n"
        f"Entry: {entry_str}\n"
        f"Targets: {targets_str}\n"
        f"SL: {_fmt_num(signal['sl'])}\n"
        f"Leverage: {signal['leverage']}"
    )


def format_update_matched(channel: str, signal: dict, update: dict) -> str:
    if update["kind"] == "tp_hit":
        detail = f"TP{update['target_index']} hit"
        if update.get("profit_pct") is not None:
            detail += f", +{update['profit_pct']:.2f}%"
        if update.get("period"):
            detail += f", {update['period']}"
    else:
        detail = f"Entry {update['target_index']} filled"
        if update.get("entry_price") is not None:
            detail += f" @ {_fmt_num(update['entry_price'])}"
    return f"✅ [{channel}] {signal['coin']} {signal['direction']} — {detail}"


def format_update_unmatched(channel: str, update: dict) -> str:
    return (
        f"⚠️ [{channel}] {update['coin']} — nhận update nhưng không tìm thấy signal gốc "
        f"đang mở tương ứng (loại update: {update['kind']})"
    )


def format_commentary(channel: str, commentary: dict) -> str:
    coins = commentary["coins"]
    tag = f"#{coins[0]}" if coins else "❔"
    return f"📰 [{channel}] {tag}: {commentary['raw']}"


def format_unknown(channel: str, raw: str) -> str:
    return f"⚠️ [{channel}] Không nhận diện được định dạng:\n{raw}"


def format_outcome(channel: str, outcome: dict) -> str:
    kind = outcome["kind"]
    if kind == "new_signal":
        return format_new_signal(channel, outcome["signal"])
    if kind == "update_matched":
        return format_update_matched(channel, outcome["signal"], outcome["update"])
    if kind == "update_unmatched":
        return format_update_unmatched(channel, outcome["update"])
    if kind == "commentary":
        return format_commentary(channel, outcome["commentary"])
    return format_unknown(channel, outcome["raw"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_format.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add crypto_signals/format.py crypto_signals/tests/test_format.py
git commit -m "feat(crypto_signals): add pure message-formatting layer"
```

---

## Task 7: `env.py`, `telegram_api.py`, `listener.py`

**Files:**
- Create: `crypto_signals/env.py`
- Create: `crypto_signals/telegram_api.py`
- Create: `crypto_signals/listener.py`
- Create: `crypto_signals/tests/test_env.py`
- Create: `crypto_signals/tests/test_telegram_api.py`
- Create: `crypto_signals/tests/test_listener.py`
- Modify: `requirements.txt` (add `telethon`)

**Interfaces:**
- Consumes: `parser.parse_message()` (Task 4), `control.record_parsed_message()`, `control.list_channels()`, `control.DEFAULT_STATE_FILE` (Task 5), `format.format_outcome()` (Task 6).
- Produces: `env.load_env_file(path: str = ".env") -> None` (sets `os.environ` via `setdefault`), `telegram_api.send_message(token: str, chat_id: str, text: str) -> dict`, `listener.route_message(raw_text: str, channel_username: str, channel_kind: str, state_path: str = control.DEFAULT_STATE_FILE) -> str` (the one fully unit-testable piece of the listener — parses, records, formats, returns the text to send), `listener.main()` (async Telethon entrypoint, not unit tested).

- [ ] **Step 1: Write the failing tests**

```python
# crypto_signals/tests/test_env.py
from crypto_signals.env import load_env_file


def test_load_env_file_sets_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("CRYPTO_SIGNALS_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nCRYPTO_SIGNALS_TEST_VAR=hello\n", encoding="utf-8")

    load_env_file(str(env_file))

    import os
    assert os.environ["CRYPTO_SIGNALS_TEST_VAR"] == "hello"


def test_load_env_file_missing_file_is_a_noop(tmp_path):
    load_env_file(str(tmp_path / "does_not_exist.env"))  # must not raise
```

```python
# crypto_signals/tests/test_telegram_api.py
from unittest.mock import Mock, patch

from crypto_signals.telegram_api import send_message


@patch("crypto_signals.telegram_api.requests.post")
def test_send_message_posts_expected_payload(mock_post):
    mock_post.return_value = Mock(json=lambda: {"ok": True})
    result = send_message("TOKEN123", "999", "hello world")

    mock_post.assert_called_once_with(
        "https://api.telegram.org/botTOKEN123/sendMessage",
        json={"chat_id": "999", "text": "hello world"},
        timeout=20,
    )
    assert result == {"ok": True}
```

```python
# crypto_signals/tests/test_listener.py
import pytest

from crypto_signals import control
from crypto_signals.listener import route_message


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "crypto_signals_state.json")


def test_route_message_new_signal(state_path):
    scalp_text = (
        "✅ SCALP TRADE - UNI 🏮 TYPE - LONG 👉 ENTRY - $3.28 - $3.22 👉 TARGET - $3.30, "
        "$3.32, $3.34, $3.37 & $3.40+ 👉 SL - $3.16 🚨LEVERAGE - 60x 🔴TRADE VALID ON"
    )
    text = route_message(scalp_text, "crypto_vulture_signals", "signal", state_path=state_path)
    assert "🆕" in text
    assert "UNI/USDT" in text
    assert len(control.list_open_signals(state_path)) == 1


def test_route_message_commentary_channel(state_path):
    text = route_message("ZK analysis: bullish breakout", "CryptoVIPsignalTA", "commentary",
                          state_path=state_path)
    assert "📰" in text
    assert "#ZK" in text
    assert control.list_open_signals(state_path) == []


def test_route_message_update_then_matches_prior_signal(state_path):
    scalp_text = (
        "✅ SCALP TRADE - UNI 🏮 TYPE - LONG 👉 ENTRY - $3.28 - $3.22 👉 TARGET - $3.30, "
        "$3.32, $3.34, $3.37 & $3.40+ 👉 SL - $3.16 🚨LEVERAGE - 60x 🔴TRADE VALID ON"
    )
    route_message(scalp_text, "crypto_vulture_signals", "signal", state_path=state_path)

    update_text = "#UNI/USDT Take-Profit target 1 ✅\nProfit: 36.5854% 📈\nPeriod: 5 hr 26 min ⏰"
    text = route_message(update_text, "crypto_vulture_signals", "signal", state_path=state_path)
    assert "TP1 hit" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_env.py crypto_signals/tests/test_telegram_api.py crypto_signals/tests/test_listener.py -v`
Expected: FAIL (modules don't exist yet).

- [ ] **Step 3: Implement `env.py`, `telegram_api.py`, `listener.py`**

```python
# crypto_signals/env.py
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
```

```python
# crypto_signals/telegram_api.py
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
```

```python
# crypto_signals/listener.py
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
```

- [ ] **Step 4: Add `telethon` to `requirements.txt`**

```
undetected-chromedriver
selenium
requests
beautifulsoup4
playwright
telethon
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pip install telethon` (if not already installed locally), then:
Run: `pytest crypto_signals/tests/test_env.py crypto_signals/tests/test_telegram_api.py crypto_signals/tests/test_listener.py -v`
Expected: PASS (2 + 1 + 3 = 6 tests).

- [ ] **Step 6: Commit**

```bash
git add crypto_signals/env.py crypto_signals/telegram_api.py crypto_signals/listener.py crypto_signals/tests/test_env.py crypto_signals/tests/test_telegram_api.py crypto_signals/tests/test_listener.py requirements.txt
git commit -m "feat(crypto_signals): add Telethon listener with unit-testable route_message core"
```

---

## Task 8: `telegram_bot.py` — control bot

**Files:**
- Create: `crypto_signals/telegram_bot.py`
- Create: `crypto_signals/tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `control.add_channel`, `control.remove_channel`, `control.list_channels`, `control.list_open_signals`, `control.service_control`, `control.service_is_active`, `control.get_logs` (Task 5), `env.load_env_file`, `telegram_api.send_message` (Task 7).
- Produces: `telegram_bot.Bot` class with `.dispatch(text: str) -> str` (the fully unit-testable core — no network), `.run()` (long-poll loop, not unit tested), `telegram_bot.format_channel_list(channels: list[dict]) -> str`, `telegram_bot.format_open_signals(signals: list[dict]) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# crypto_signals/tests/test_telegram_bot.py
import pytest

from crypto_signals import control
from crypto_signals.telegram_bot import Bot, format_channel_list, format_open_signals


@pytest.fixture
def bot(tmp_path):
    return Bot(token="TOKEN", chat_id="42", state_file=str(tmp_path / "crypto_signals_state.json"))


def test_format_channel_list_empty():
    assert "rỗng" in format_channel_list([]).lower()


def test_format_channel_list_shows_username_and_kind():
    text = format_channel_list([{"username": "crypto_vulture_signals", "kind": "signal", "added_at": "x"}])
    assert "crypto_vulture_signals" in text
    assert "signal" in text


def test_format_open_signals_empty():
    assert "không có" in format_open_signals([]).lower()


def test_format_open_signals_shows_coin_and_direction():
    text = format_open_signals([{
        "coin": "UNI/USDT", "direction": "LONG", "channel": "crypto_vulture_signals",
        "entry": [3.28, 3.22], "targets": [3.3, 3.32], "hits": [],
    }])
    assert "UNI/USDT" in text
    assert "LONG" in text


def test_cmd_addchannel_default_kind(bot):
    reply = bot.dispatch("/addchannel crypto_vulture_signals")
    assert "crypto_vulture_signals" in reply
    channels = control.list_channels(bot.state_file)
    assert channels[0]["kind"] == "signal"


def test_cmd_addchannel_explicit_commentary_kind(bot):
    bot.dispatch("/addchannel CryptoVIPsignalTA commentary")
    channels = control.list_channels(bot.state_file)
    assert channels[0]["kind"] == "commentary"


def test_cmd_addchannel_rejects_invalid_kind(bot):
    reply = bot.dispatch("/addchannel some_channel bogus")
    assert "signal" in reply.lower() or "commentary" in reply.lower()
    assert control.list_channels(bot.state_file) == []


def test_cmd_removechannel(bot):
    bot.dispatch("/addchannel crypto_vulture_signals")
    reply = bot.dispatch("/removechannel crypto_vulture_signals")
    assert "✅" in reply
    assert control.list_channels(bot.state_file) == []


def test_cmd_listchannels(bot):
    bot.dispatch("/addchannel crypto_vulture_signals")
    reply = bot.dispatch("/listchannels")
    assert "crypto_vulture_signals" in reply


def test_cmd_open_with_no_signals(bot):
    reply = bot.dispatch("/open")
    assert "không có" in reply.lower()


def test_cmd_help_lists_commands(bot):
    reply = bot.dispatch("/help")
    assert "/addchannel" in reply
    assert "/removechannel" in reply
    assert "/listchannels" in reply
    assert "/open" in reply
    assert "/status" in reply


def test_dispatch_unknown_command_shows_help(bot):
    assert bot.dispatch("/bogus") == bot.cmd_help()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest crypto_signals/tests/test_telegram_bot.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'crypto_signals.telegram_bot'`).

- [ ] **Step 3: Implement `telegram_bot.py`**

```python
# crypto_signals/telegram_bot.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest crypto_signals/tests/test_telegram_bot.py -v`
Expected: PASS (all 12 tests).

- [ ] **Step 5: Run the full crypto_signals test suite**

Run: `pytest crypto_signals/ -v`
Expected: PASS (all tests across every task — state, parser, control, format, env, telegram_api, listener, telegram_bot).

- [ ] **Step 6: Commit**

```bash
git add crypto_signals/telegram_bot.py crypto_signals/tests/test_telegram_bot.py
git commit -m "feat(crypto_signals): add two-way Telegram control bot"
```

---

## Task 9: `.env.example`, `deploy.sh`, deploy docs

**Files:**
- Modify: `.env.example`
- Modify: `deploy.sh`
- Create: `docs/crypto_signals_deploy.md`

**Interfaces:**
- Consumes: nothing new — this task wires up already-built modules for deployment. No automated test (matches how `docs/xeca_deploy.md`/`docs/cinema_booking_deploy.md` are documentation-only).

- [ ] **Step 1: Add new vars to `.env.example`**

```bash
# crypto_signals — Telethon (personal-account MTProto) credentials, from
# https://my.telegram.org/apps. Shared across crypto_signals only; never hard-code these
# in source (unlike telegram-tools/telegram_bot_episode_grabber.py's older approach).
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef

# crypto_signals's control bot -- MUST be a DIFFERENT BotFather bot/token than
# TELEGRAM_BOT_TOKEN/CINEMA_TELEGRAM_BOT_TOKEN above (two processes long-polling
# getUpdates with the same token collide, 409 Conflict, updates dropped unpredictably).
CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN=555555555:AAAnotherExampleTokenFromBotFather
CRYPTO_SIGNALS_TELEGRAM_CHAT_ID=123456789
```

- [ ] **Step 2: Add the two new services to `deploy.sh`**

```bash
#!/usr/bin/env bash
# Push local changes to GitHub (autobot) and pull + restart on the server.
# Usage: ./deploy.sh "optional commit message"
set -euo pipefail
cd "$(dirname "$0")"

SERVER="root@hieuit.top"
REMOTE_DIR="/opt/autobot"
MSG="${1:-chore: update xeca automation}"

echo "==> git add/commit/push (remote: autobot)"
git add -A
git commit -m "$MSG" || echo "(không có gì để commit)"
git push autobot main:main

echo "==> Pulling + restarting services on $SERVER"
ssh "$SERVER" "
  set -e
  cd $REMOTE_DIR
  git pull
  ./venv/bin/pip install --quiet requests beautifulsoup4 playwright telethon
  systemctl restart xeca-watch.service xeca-bot.service
  # cinema-booking-bot.service is enabled but only actually restarted here once
  # CINEMA_TELEGRAM_BOT_TOKEN/CINEMA_TELEGRAM_CHAT_ID exist in .env -- restarting it
  # before that just churns a harmless crash-loop, so this checks first.
  if grep -q '^CINEMA_TELEGRAM_BOT_TOKEN=' .env 2>/dev/null; then
    systemctl restart cinema-booking-bot.service
  fi
  # Same guard for crypto-signals-listen/bot -- both need TELEGRAM_API_ID (Telethon) and
  # a completed manual phone+OTP login (crypto_signals_session file must already exist on
  # the server, scp'd over after logging in locally) before they're safe to (re)start.
  if grep -q '^TELEGRAM_API_ID=' .env 2>/dev/null && [ -f crypto_signals_session.session ]; then
    systemctl restart crypto-signals-listen.service crypto-signals-bot.service
  fi
  sleep 1
  systemctl --no-pager --lines=0 status xeca-watch.service xeca-bot.service cinema-booking-xvfb.service
"

echo "==> Xong."
```

- [ ] **Step 3: Write `docs/crypto_signals_deploy.md`**

```markdown
# Crypto signals listener — kiến trúc, deploy & vận hành

## Kiến trúc

```
crypto_signals/
├── state.py           # crypto_signals_state.json: channels[], signals[] (lock file)
├── parser.py            # parse_message(text, channel_kind) -> dict, pure function
├── control.py             # CRUD channel, ghép update vào signal, systemctl/journalctl
├── format.py               # outcome -> text gửi Telegram, pure function
├── env.py                   # .env loader (độc lập, không import xeca_client)
├── telegram_api.py            # sendMessage dùng chung giữa listener + bot
├── listener.py                  # Telethon client, nghe realtime, chỉ GỬI
└── telegram_bot.py                # bot 2 chiều, quản lý danh sách kênh

crypto_signals_state.json  # không commit — xem .gitignore
.env                        # secrets: TELEGRAM_API_ID/HASH, CRYPTO_SIGNALS_TELEGRAM_*
crypto_signals_session.session  # Telethon session (không commit) — xem bước đăng nhập bên dưới
```

Services trên server (`root@hieuit.top`, thư mục `/opt/autobot`):
- **crypto-signals-listen.service** — chỉ *gửi* thông báo (không gọi Telegram getUpdates).
- **crypto-signals-bot.service** — bot 2 chiều, long-poll Telegram getUpdates, chỉ chấp
  nhận lệnh từ `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID` đã cấu hình.

## Đăng nhập Telethon lần đầu (bắt buộc làm ở máy có TTY, không phải qua deploy.sh)

Telethon (tài khoản cá nhân, không phải Bot API) cần đăng nhập số điện thoại + mã OTP một
lần duy nhất, y hệt `telegram-tools/telegram_bot_episode_grabber.py`:

```bash
python -c "
from telethon.sync import TelegramClient
import os
os.environ.setdefault('X', '')  # no-op, chỉ để import không lỗi nếu .env chưa load
from crypto_signals.env import load_env_file
load_env_file('.env')
client = TelegramClient('crypto_signals_session', int(os.environ['TELEGRAM_API_ID']), os.environ['TELEGRAM_API_HASH'])
client.start()
print('Đăng nhập xong, session đã lưu vào crypto_signals_session.session')
"
```

Chạy lệnh trên **ở máy local** (không phải SSH vào server, vì cần nhập OTP tương tác), rồi
copy file session thật lên server:

```bash
scp crypto_signals_session.session root@hieuit.top:/opt/autobot/crypto_signals_session.session
```

`deploy.sh` chỉ restart 2 service này nếu `crypto_signals_session.session` đã tồn tại trên
server — tránh crash-loop vô ích khi chưa đăng nhập.

## Quản lý qua Telegram bot

Nhắn cho bot (chỉ `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID` đã cấu hình mới được chấp nhận):

| Lệnh | Ý nghĩa |
|---|---|
| `/addchannel <username> [signal\|commentary]` | Thêm kênh cần nghe (mặc định `signal`) |
| `/removechannel <username>` | Bỏ nghe 1 kênh |
| `/listchannels` | Danh sách kênh đang nghe |
| `/open` | Danh sách signal đang mở (chưa closed) |
| `/status` | Trạng thái `crypto-signals-listen.service` |
| `/logs [n]` | n dòng log gần nhất |
| `/help` | Danh sách lệnh |

Sau `/addchannel`/`/removechannel`, restart tay listener để áp dụng danh sách kênh mới:

```bash
ssh root@hieuit.top "systemctl restart crypto-signals-listen.service"
```

(Chưa tự động restart từ trong bot ở Phase 1 — xem "Định hướng tương lai" bên dưới.)

## Kênh khởi tạo

- `crypto_vulture_signals` (`kind=signal`) — Entry/Target/SL có cấu trúc (2 khuôn mẫu:
  "SCALP TRADE" một dòng, và "Entries/Targets/Stop Loss" đánh số).
- `CryptoVIPsignalTA` (`kind=commentary`) — nhận định thị trường bằng văn xuôi, không có
  Entry/TP/SL — chỉ trích coin được nhắc tới (xem `parser.extract_commentary_coins`).

## Định hướng tương lai (chưa làm — xem spec)

- Bot tự `systemctl restart` listener ngay sau `/addchannel`/`/removechannel` thay vì cần
  restart tay.
- Tổng hợp "xu hướng"/"ghép cặp" xuyên kênh (đối chiếu signal có cấu trúc với commentary
  cùng coin) — xem mục "Ngoài phạm vi Phase 1" trong
  `docs/superpowers/specs/2026-08-17-crypto-signals-design.md`.

## Systemd unit mẫu

`/etc/systemd/system/crypto-signals-listen.service`:
```ini
[Unit]
Description=Crypto signals Telethon listener (Telegram notify)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python -m crypto_signals.listener
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/crypto-signals-bot.service`:
```ini
[Unit]
Description=Crypto signals Telegram control bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python -m crypto_signals.telegram_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```

- [ ] **Step 4: Commit**

```bash
git add .env.example deploy.sh docs/crypto_signals_deploy.md
git commit -m "docs(crypto_signals): add .env vars, deploy.sh services, deploy runbook"
```

---

## Post-plan manual verification (not automated — requires your real Telegram account)

1. Run the Telethon first-login command from Task 9 locally, confirm `crypto_signals_session.session` is created.
2. `python -m crypto_signals.telegram_bot` locally with a real `CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN`/`CRYPTO_SIGNALS_TELEGRAM_CHAT_ID`, send `/addchannel crypto_vulture_signals` and `/addchannel CryptoVIPsignalTA commentary`, confirm `/listchannels` shows both.
3. `python -m crypto_signals.listener` locally, wait for a real message from either channel, confirm you receive a correctly formatted Telegram alert. If nothing arrives after a reasonable wait, see the `JoinChannelRequest` fallback noted in `listener.py`'s `run()`.
4. Only after 2-3 confirms deploy to the server (`./deploy.sh "crypto signals phase 1"`) and repeat a quick `/status`/`/logs` check there.
