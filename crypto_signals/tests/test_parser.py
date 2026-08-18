from crypto_signals.parser import normalize_coin, _parse_scalp, _parse_structured, _parse_owl_signal, _parse_tp_hit, _parse_entry_filled, extract_commentary_coins, parse_message

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


# Real messages captured live from @ItsOwlPrints (t.me/s/ItsOwlPrints, 2026-08-19).
OWL_SHORT_HYPE = "🔴 SHORT $HYPE/USDT | Cross 20X\n\n✅ Entry: 58.85\n\n🎯 TP: 58 - 57 -  55\n\n🛑 SL: 61"
OWL_SHORT_NEAR = "🔴 SHORT $NEAR/USDT | Cross 20X\n\n✅ Entry: 1.62\n\n🎯 TP: 1.59 - 1.55 -  1.50\n\n🛑 SL: 1.70"


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


def test_parse_owl_signal_short_hype():
    result = _parse_owl_signal(OWL_SHORT_HYPE)
    assert result == {
        "type": "signal",
        "coin": "HYPE/USDT",
        "direction": "SHORT",
        "scalp": False,
        "entry": [58.85],
        "targets": [58.0, 57.0, 55.0],
        "targets_plus": False,
        "sl": 61.0,
        "leverage": "20x",
    }


def test_parse_owl_signal_short_near():
    result = _parse_owl_signal(OWL_SHORT_NEAR)
    assert result["coin"] == "NEAR/USDT"
    assert result["entry"] == [1.62]
    assert result["targets"] == [1.59, 1.55, 1.50]
    assert result["sl"] == 1.70


def test_parse_owl_signal_long_inferred_symmetric():
    # Not yet confirmed against a live LONG message from this channel -- inferred
    # symmetric with the confirmed SHORT template (see parser.py's docstring caveat).
    text = "🟢 LONG $BTC/USDT | Cross 10X\n\n✅ Entry: 65000\n\n🎯 TP: 66000 - 67000\n\n🛑 SL: 63000"
    result = _parse_owl_signal(text)
    assert result["direction"] == "LONG"
    assert result["coin"] == "BTC/USDT"
    assert result["targets"] == [66000.0, 67000.0]
    assert result["leverage"] == "10x"


def test_parse_owl_signal_returns_none_for_non_owl_text():
    assert _parse_owl_signal(SCALP_UNI) is None
    assert _parse_owl_signal(STRUCTURED_ETH) is None
    assert _parse_owl_signal("just a random sentence") is None


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


def test_parse_message_dispatches_to_owl_signal():
    result = parse_message(OWL_SHORT_HYPE, channel_kind="signal")
    assert result["type"] == "signal"
    assert result["coin"] == "HYPE/USDT"


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


def test_extract_commentary_coins_no_false_positive_from_eth_in_regroup():
    # Regression: "eth" is substring of "regroup", should not match
    text = "We will regroup together and reassess the market next week."
    assert extract_commentary_coins(text) == []


def test_extract_commentary_coins_no_false_positive_from_eth_in_method():
    # Regression: "eth" is substring of "method" and "the", should not match
    text = "The method for entries needs a review before we act."
    assert extract_commentary_coins(text) == []


def test_extract_commentary_coins_matches_whole_word_bitcoin():
    # "bitcoin" as whole word should match despite being in other words
    text = "Bitcoin is looking bullish this week. Bitcoin price is up."
    assert extract_commentary_coins(text) == ["BTC"]


def test_extract_commentary_coins_matches_whole_word_ethereum():
    # "ethereum" as whole word should match
    text = "Ethereum is consolidating before a major move."
    assert extract_commentary_coins(text) == ["ETH"]


def test_parse_message_never_raises_on_malformed_numeric_capture():
    # Regression: the `sl` group [\d.,]+ can match a bare "," with no actual digit, and
    # NUM_RE.search(...).group() on that then raises AttributeError on None. parse_message()
    # must never raise -- a malformed candidate parse should just fall through to "unknown".
    malformed = (
        "SCALP TRADE - X TYPE - LONG ENTRY - 1 👉 TARGET - 2 👉 SL - , 🚨LEVERAGE - 5x"
    )
    assert parse_message(malformed, channel_kind="signal") == {"type": "unknown", "raw": malformed}
