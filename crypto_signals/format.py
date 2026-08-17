"""Pure functions turning a control.record_parsed_message() outcome into the exact text
sent to Telegram. No I/O here -- listener.py owns the actual send call."""


def _fmt_num(v: float) -> str:
    text = f"{v:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


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
