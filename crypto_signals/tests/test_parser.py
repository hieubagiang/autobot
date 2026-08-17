from crypto_signals.parser import normalize_coin, _parse_scalp, _parse_structured, _parse_tp_hit, _parse_entry_filled

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
