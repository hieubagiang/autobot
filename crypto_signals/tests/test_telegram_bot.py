import pytest

from crypto_signals import control
from crypto_signals.telegram_bot import Bot, format_channel_list, format_open_signals


@pytest.fixture
def bot(tmp_path):
    return Bot(token="TOKEN", chat_id="42", state_file=str(tmp_path / "crypto_signals_state.json"))


def test_format_channel_list_empty():
    assert "rỗng" in format_channel_list([]).lower()


def test_format_channel_list_shows_username_and_kind():
    text = format_channel_list([{"username": "crypto_vulture_signals", "kind": "signal", "added_at": "x"}])
    assert "crypto_vulture_signals" in text
    assert "signal" in text


def test_format_open_signals_empty():
    assert "không có" in format_open_signals([]).lower()


def test_format_open_signals_shows_coin_and_direction():
    text = format_open_signals([{
        "coin": "UNI/USDT", "direction": "LONG", "channel": "crypto_vulture_signals",
        "entry": [3.28, 3.22], "targets": [3.3, 3.32], "hits": [],
    }])
    assert "UNI/USDT" in text
    assert "LONG" in text


def test_cmd_addchannel_default_kind(bot):
    reply = bot.dispatch("/addchannel crypto_vulture_signals")
    assert "crypto_vulture_signals" in reply
    channels = control.list_channels(bot.state_file)
    assert channels[0]["kind"] == "signal"


def test_cmd_addchannel_explicit_commentary_kind(bot):
    bot.dispatch("/addchannel CryptoVIPsignalTA commentary")
    channels = control.list_channels(bot.state_file)
    assert channels[0]["kind"] == "commentary"


def test_cmd_addchannel_rejects_invalid_kind(bot):
    reply = bot.dispatch("/addchannel some_channel bogus")
    assert "signal" in reply.lower() or "commentary" in reply.lower()
    assert control.list_channels(bot.state_file) == []


def test_cmd_removechannel(bot):
    bot.dispatch("/addchannel crypto_vulture_signals")
    reply = bot.dispatch("/removechannel crypto_vulture_signals")
    assert "✅" in reply
    assert control.list_channels(bot.state_file) == []


def test_cmd_listchannels(bot):
    bot.dispatch("/addchannel crypto_vulture_signals")
    reply = bot.dispatch("/listchannels")
    assert "crypto_vulture_signals" in reply


def test_cmd_open_with_no_signals(bot):
    reply = bot.dispatch("/open")
    assert "không có" in reply.lower()


def test_cmd_help_lists_commands(bot):
    reply = bot.dispatch("/help")
    assert "/addchannel" in reply
    assert "/removechannel" in reply
    assert "/listchannels" in reply
    assert "/open" in reply
    assert "/status" in reply


def test_dispatch_unknown_command_shows_help(bot):
    assert bot.dispatch("/bogus") == bot.cmd_help()
