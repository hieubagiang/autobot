"""Parses signal/update/commentary messages from multiple crypto Telegram channels
(@crypto_vulture_signals, @ItsOwlPrints, ...) into structured dicts.

`parse_message()` is the single public entry point that always returns a dict, never
raises -- an unrecognized message becomes `{"type": "unknown", ...}` rather than an
exception, since these channels have no committed schema and can change format at any time.
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


_OWL_RE = re.compile(
    r"(?P<direction>SHORT|LONG)\s+\$(?P<coin>[A-Za-z0-9]+)/USDT.*?"
    r"(?P<leverage_num>\d+)X.*?"
    r"Entry:\s*(?P<entry>[\d.]+).*?"
    r"TP:\s*(?P<targets>[\d.\s-]+?)\s*(?:\n|$).*?"
    r"SL:\s*(?P<sl>[\d.]+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_owl_signal(text: str) -> dict | None:
    """@ItsOwlPrints's template -- e.g. '🔴 SHORT $HYPE/USDT | Cross 20X\\n\\n✅ Entry: 58.85\\n\\n
    🎯 TP: 58 - 57 -  55\\n\\n🛑 SL: 61'. Only the SHORT shape has been confirmed against real
    channel text (2026-08-19) -- LONG is inferred symmetric, not yet seen live."""
    m = _OWL_RE.search(text)
    if not m:
        return None
    d = m.groupdict()
    return {
        "type": "signal",
        "coin": normalize_coin(d["coin"]),
        "direction": d["direction"].upper(),
        "scalp": False,
        "entry": [float(d["entry"])],
        "targets": _parse_number_list(d["targets"], "-"),
        "targets_plus": False,
        "sl": float(d["sl"]),
        "leverage": f"{d['leverage_num']}x",
    }


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


def extract_commentary_coins(text: str) -> list[str]:
    coins = []
    m = _ANALYSIS_HEADER_RE.match(text)
    if m:
        coins.append(m.group(1).upper())
    lowered = text.lower()
    for alias, ticker in _COIN_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", lowered) and ticker not in coins:
            coins.append(ticker)
    return coins


def parse_message(text: str, channel_kind: str = "signal") -> dict:
    text = text.strip()
    for parse_fn in (_parse_scalp, _parse_structured, _parse_owl_signal, _parse_tp_hit, _parse_entry_filled):
        try:
            result = parse_fn(text)
        except Exception:
            # A malformed numeric capture (bare "," matched by a greedy [\d.,]+ group, or
            # multi-dot garbage failing float()) means this template didn't actually match --
            # fall through to the next candidate parser (or commentary/unknown) instead of
            # ever raising out of parse_message(), which must never raise (see module docstring).
            continue
        if result is not None:
            return result
    if channel_kind == "commentary":
        return {"type": "commentary", "coins": extract_commentary_coins(text), "raw": text}
    return {"type": "unknown", "raw": text}
