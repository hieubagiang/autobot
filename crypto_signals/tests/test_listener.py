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
