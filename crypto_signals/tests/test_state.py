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
