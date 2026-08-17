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
