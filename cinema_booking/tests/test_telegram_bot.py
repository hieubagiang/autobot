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


def test_cmd_remove_deletes_existing_item(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/remove {item_id}")
    assert "Đã xoá" in reply
    assert get_item(item_id, bot.state_file) is None


def test_dispatch_unknown_command_shows_help(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/not-a-real-command")
    assert "Lệnh" in reply or "cú pháp" in reply.lower()


def test_cmd_add_applies_default_cinema_priority_for_beta(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    assert get_item(item_id, bot.state_file)["cinema_priority"] == ["Beta Tây Sơn"]


def test_cmd_add_warns_when_no_default_cinema_priority(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch('/add unknownprovider "X" 12/08/2026')
    assert "⚠️" in reply
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    assert get_item(item_id, bot.state_file)["cinema_priority"] == []


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


def test_cmd_paid_marks_status_and_stops_instant(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    update_item(item_id, path=bot.state_file, status="pending_payment")

    # Actually start an instant thread (with a no-op camp loop so no real provider/
    # network code runs) so this test genuinely exercises the "stops instant" half of
    # its name, not just the status-update half.
    monkeypatch.setattr("cinema_booking.telegram_bot.instant_camp_loop", lambda *a, **k: None)
    bot.dispatch(f"/instant {item_id} on")
    stop_event = bot.instant_threads[item_id]["stop_event"]

    reply = bot.dispatch(f"/paid {item_id}")
    assert "thanh toán" in reply.lower()
    assert get_item(item_id, bot.state_file)["status"] == "paid"
    assert item_id not in bot.instant_threads
    assert stop_event.is_set()


def test_resume_instant_items_skips_item_with_pending_payment_status(tmp_path, monkeypatch):
    # Regression test for finding I3: an item whose lock already succeeded
    # (status="pending_payment") must NOT be re-camped on bot restart even if a stale
    # "instant": True is still sitting in state (e.g. from before instant_camp_loop was
    # fixed to clear it on success, or from an older version of the bot's state file).
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    update_item(item_id, path=bot.state_file, instant=True, status="pending_payment")

    started = []
    monkeypatch.setattr(bot, "_start_instant", lambda iid: started.append(iid))

    bot.resume_instant_items()

    assert started == []
    assert item_id not in bot.instant_threads


def test_resume_instant_items_resumes_pending_item(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    update_item(item_id, path=bot.state_file, instant=True)  # status stays "pending"

    started = []
    monkeypatch.setattr(bot, "_start_instant", lambda iid: started.append(iid))

    bot.resume_instant_items()

    assert started == [item_id]


def test_cmd_instant_off_unknown_id_reports_not_found(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/instant unknown-id off")
    assert "Không tìm thấy" in reply


def test_cmd_instant_on_starts_and_registers_thread(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]

    # instant_camp_loop is monkeypatched to a no-op so the thread it runs in never
    # touches a real provider/network — only dispatch()'s own bookkeeping is under test.
    monkeypatch.setattr("cinema_booking.telegram_bot.instant_camp_loop", lambda *a, **k: None)
    reply = bot.dispatch(f"/instant {item_id} on")

    assert "⚡" in reply
    assert item_id in bot.instant_threads
    assert get_item(item_id, bot.state_file)["instant"] is True

    bot.dispatch(f"/instant {item_id} off")  # cleanup: don't leave dict/state dangling


def test_cmd_instant_on_twice_short_circuits_second_call(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]

    monkeypatch.setattr("cinema_booking.telegram_bot.instant_camp_loop", lambda *a, **k: None)
    first_reply = bot.dispatch(f"/instant {item_id} on")
    second_reply = bot.dispatch(f"/instant {item_id} on")

    assert "⚡" in first_reply
    assert "đã đang bật" in second_reply.lower()
    assert len(bot.instant_threads) == 1  # second call must not have started another thread

    bot.dispatch(f"/instant {item_id} off")  # cleanup


def test_cmd_setcinemapriority_requires_id_and_names(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/setcinemapriority")
    assert "Cú pháp" in reply


def test_cmd_setcinemapriority_unknown_id_reports_not_found(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/setcinemapriority does-not-exist Beta Tây Sơn")
    assert "Không tìm thấy" in reply


def test_cmd_setcinemapriority_updates_item(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/setcinemapriority {item_id} Beta Tây Sơn, Beta Mỹ Đình")
    assert "Beta Tây Sơn" in reply and "Beta Mỹ Đình" in reply
    assert get_item(item_id, bot.state_file)["cinema_priority"] == ["Beta Tây Sơn", "Beta Mỹ Đình"]


def test_cmd_listcinemas_reports_provider_error_without_crashing(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)

    def broken_provider(name):
        raise ValueError(f"Unknown provider: {name}")

    monkeypatch.setattr("cinema_booking.telegram_bot.get_provider", broken_provider)
    reply = bot.dispatch("/listcinemas not-a-real-provider")
    assert "❌" in reply
