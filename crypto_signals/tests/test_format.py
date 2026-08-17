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


def test_format_new_signal_large_price_precision():
    """Regression: ensure large prices like 65432.15 don't get rounded to 65432.2"""
    signal = {
        "coin": "BTC/USDT", "direction": "LONG", "scalp": False,
        "entry": [65432.15], "targets": [65500.0], "targets_plus": False,
        "sl": 65000.0, "leverage": "10x",
    }
    text = format_outcome("crypto_vulture_signals", {"kind": "new_signal", "signal": signal})
    assert "65432.15" in text
    assert "65432.2" not in text  # Should not be rounded incorrectly


def test_format_new_signal_very_large_price():
    """Regression: ensure very large prices preserve full decimal precision"""
    signal = {
        "coin": "BTC/USDT", "direction": "LONG", "scalp": False,
        "entry": [123456.789], "targets": [124000.0], "targets_plus": False,
        "sl": 120000.0, "leverage": "5x",
    }
    text = format_outcome("crypto_vulture_signals", {"kind": "new_signal", "signal": signal})
    assert "123456.789" in text


def test_format_new_signal_very_small_price_no_scientific():
    """Regression: ensure very small prices don't use scientific notation"""
    signal = {
        "coin": "SHIB/USDT", "direction": "LONG", "scalp": True,
        "entry": [0.00001234], "targets": [0.00002], "targets_plus": False,
        "sl": 0.00001, "leverage": "20x",
    }
    text = format_outcome("crypto_vulture_signals", {"kind": "new_signal", "signal": signal})
    # Should NOT contain scientific notation
    assert "e-" not in text.lower()
    assert "1.234e" not in text.lower()
    # Should contain the actual small number
    assert "0.00001234" in text
