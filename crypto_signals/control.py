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
