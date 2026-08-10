from cinema_booking.state import get_item, list_ticket_requests, update_item
from cinema_booking.telegram_bot import Bot


def make_bot(tmp_path):
    return Bot(token="fake-token", chat_id="123", state_file=str(tmp_path / "state.json"))


def test_cmd_add_requires_provider_movie_and_date(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/add")
    assert "Cú pháp" in reply


def test_cmd_add_creates_watchlist_item(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    reply = bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    assert "Đã thêm" in reply

    # cmd_list sends one Telegram message per item via self.send(...) and returns "" —
    # monkeypatch send to capture what was actually sent, rather than the return value.
    sent = []
    monkeypatch.setattr(bot, "send", lambda text: sent.append(text))
    result = bot.dispatch("/list")
    assert result == ""
    assert any("Người Nhện" in text for text in sent)


def test_cmd_remove_unknown_id_reports_not_found(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/remove does-not-exist")
    assert "Không tìm thấy" in reply


def test_dispatch_unknown_command_shows_help(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/not-a-real-command")
    assert "Lệnh" in reply or "cú pháp" in reply.lower()


def test_cmd_add_applies_default_cinema_priority_for_beta(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    assert get_item(item_id, bot.state_file)["cinema_priority"] == ["Beta Tây Sơn"]


def test_cmd_setquantity_updates_item(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/setquantity {item_id} 4")
    assert "4" in reply
    assert get_item(item_id, bot.state_file)["quantity"] == 4


def test_cmd_setquantity_rejects_non_integer(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/setquantity {item_id} hai")
    assert "Cú pháp" in reply


def test_cmd_setsweetbox_toggles_flag(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    bot.dispatch(f"/setsweetbox {item_id} on")
    assert get_item(item_id, bot.state_file)["prefer_sweetbox"] is True
    bot.dispatch(f"/setsweetbox {item_id} off")
    assert get_item(item_id, bot.state_file)["prefer_sweetbox"] is False


def test_cmd_paid_marks_status_and_stops_instant(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    update_item(item_id, path=bot.state_file, status="pending_payment")
    reply = bot.dispatch(f"/paid {item_id}")
    assert "thanh toán" in reply.lower()
    assert get_item(item_id, bot.state_file)["status"] == "paid"


def test_cmd_listcinemas_reports_provider_error_without_crashing(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)

    def broken_provider(name):
        raise ValueError(f"Unknown provider: {name}")

    monkeypatch.setattr("cinema_booking.telegram_bot.get_provider", broken_provider)
    reply = bot.dispatch("/listcinemas not-a-real-provider")
    assert "❌" in reply
