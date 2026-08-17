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
