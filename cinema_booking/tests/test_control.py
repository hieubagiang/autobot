import threading

import pytest

from cinema_booking.control import rank_dates, get_provider, rank_showtime_candidates, instant_camp_loop
from cinema_booking.provider import FakeProvider
from cinema_booking.state import add_ticket_request, get_item, update_item
from cinema_booking.types import Cinema, LockResult, Seat, SeatMap, SeatStatus, SeatZone, Showtime


def test_rank_dates_single_date_returns_just_that_date():
    assert rank_dates(["2026-08-12", "2026-08-12"]) == ["2026-08-12"]


def test_rank_dates_prefers_monday_and_wednesday():
    # 2026-08-10 is Mon, 11 Tue, 12 Wed, 13 Thu.
    ranked = rank_dates(["2026-08-10", "2026-08-13"])
    assert ranked == ["2026-08-10", "2026-08-12", "2026-08-11", "2026-08-13"]


def test_rank_dates_raises_on_reversed_range():
    with pytest.raises(ValueError, match="start date.*must be <= end date"):
        rank_dates(["2026-08-13", "2026-08-10"])


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError):
        get_provider("not-a-real-provider")


def test_default_cinema_priority_has_beta_tay_son_for_beta():
    from cinema_booking.control import DEFAULT_CINEMA_PRIORITY

    assert DEFAULT_CINEMA_PRIORITY["beta"] == ["Beta Tây Sơn"]


def make_showtime(cinema, date, sid):
    return Showtime(id=sid, movie="Người Nhện", cinema=cinema, start_time="09:00", date=date)


def test_rank_showtime_candidates_orders_by_cinema_priority_first():
    cinema_a = Cinema(id="a", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    cinema_b = Cinema(id="b", name="Beta Mỹ Đình", city="Hà Nội", provider="beta")
    # cinema_b's Monday showtime would out-rank cinema_a's Tuesday one on date alone,
    # but cinema_a is higher cinema-priority so ALL of its candidates must come first.
    showtimes = [
        make_showtime(cinema_a, "2026-08-11", "a-tue"),   # Tuesday
        make_showtime(cinema_b, "2026-08-10", "b-mon"),   # Monday
    ]
    provider = FakeProvider(cinemas=[cinema_a, cinema_b], showtimes=showtimes)

    candidates = rank_showtime_candidates(
        provider, cinema_priority=["Beta Tây Sơn", "Beta Mỹ Đình"],
        movie_query="Người Nhện", date_range=["2026-08-10", "2026-08-11"],
    )
    assert [s.id for s in candidates] == ["a-tue", "b-mon"]


def test_rank_showtime_candidates_skips_unknown_cinema_names():
    cinema_a = Cinema(id="a", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtimes = [make_showtime(cinema_a, "2026-08-10", "a-mon")]
    provider = FakeProvider(cinemas=[cinema_a], showtimes=showtimes)

    candidates = rank_showtime_candidates(
        provider, cinema_priority=["Rạp Không Tồn Tại", "Beta Tây Sơn"],
        movie_query="Người Nhện", date_range=["2026-08-10", "2026-08-10"],
    )
    assert [s.id for s in candidates] == ["a-mon"]


def make_seat(label, status=SeatStatus.AVAILABLE, zone=SeatZone.STANDARD, col=1):
    return Seat(id=label, label=label, row="A", col=col, zone=zone, price=100000, status=status)


def test_camp_loop_stops_immediately_if_already_stopped(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    # cinema_priority and FakeProvider are set up so a lockable candidate WOULD be found
    # if the loop body ever ran — this is what lets the assertions below actually catch
    # an implementation that checks stop_event too late (e.g. after ranking candidates).
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="2026-08-10T09:05:00", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime], seat_maps=[seat_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    stop_event.set()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file)

    assert notifications == []
    assert provider.get_seat_map_calls == []
    assert provider.lock_seats_calls == []


def test_camp_loop_notifies_and_waits_when_not_logged_in(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-10", "2026-08-10"], state_file=state_file)
    provider = FakeProvider(logged_in=False)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()

    def notify_then_stop(message):
        notifications.append(message)
        stop_event.set()  # simulate the operator being told and stopping the camp

    notifications = []
    instant_camp_loop(item["id"], stop_event, notify_then_stop, state_file=state_file,
                       poll_interval_seconds=0)

    assert len(notifications) == 1
    assert "chưa đăng nhập" in notifications[0]


def test_camp_loop_locks_best_block_and_updates_state_on_success(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="2026-08-10T09:05:00", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime], seat_maps=[seat_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file)

    assert len(provider.lock_seats_calls) == 1
    assert len(notifications) == 1
    assert "A1" in notifications[0] and "A2" in notifications[0]
    updated = get_item(item["id"], state_file)
    assert updated["status"] == "pending_payment"
    assert updated["hold_expiry"] == "2026-08-10T09:05:00"
    assert updated["seat_labels"] == ["A1", "A2"]


def test_camp_loop_survives_exception_and_notifies_then_continues(tmp_path, monkeypatch):
    # Regression test for finding C1: a transient error inside the loop body (here,
    # simulated as get_provider raising on the first iteration -- the same failure mode
    # as an unknown provider name, or a requests/Playwright error surfacing through
    # get_provider's lazy import path) must not kill the loop. It should notify and keep
    # polling, succeeding on a later iteration instead of leaving the thread dead while
    # instant_threads/state still claim the camp is running.
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="2026-08-10T09:05:00", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime], seat_maps=[seat_map],
                             lock_result=lock_result, logged_in=True)

    call_count = {"n": 0}

    def flaky_get_provider(name):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("boom: simulated transient provider error")
        return provider

    monkeypatch.setattr("cinema_booking.control.get_provider", flaky_get_provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file,
                       poll_interval_seconds=0)

    assert any("Lỗi camp loop" in n and "boom" in n for n in notifications)
    assert any("A1" in n and "A2" in n for n in notifications)
    updated = get_item(item["id"], state_file)
    assert updated["status"] == "pending_payment"


def test_camp_loop_clears_instant_flag_on_successful_lock(tmp_path, monkeypatch):
    # Regression test for finding I3: once a lock succeeds, the item must not stay
    # "instant": True, or a bot restart would re-camp it and could double-lock real
    # seats for an item the user is already deciding whether to pay for.
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    update_item(item["id"], path=state_file, instant=True)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="2026-08-10T09:05:00", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime], seat_maps=[seat_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file)

    updated = get_item(item["id"], state_file)
    assert updated["instant"] is False


def test_get_provider_returns_cached_instance_on_repeated_calls():
    # Regression test for finding I5: two concurrent /instant camps for the same
    # provider must share ONE BetaProvider instance (and therefore one Playwright
    # persistent-context profile), not each spin up their own and collide.
    first = get_provider("beta")
    second = get_provider("beta")
    assert first is second


def test_camp_loop_keeps_polling_until_a_seat_frees_up(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    sold_out_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1", status=SeatStatus.SOLD),
                                                            make_seat("A2", status=SeatStatus.SOLD)]})
    free_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="soon", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime],
                             seat_maps=[sold_out_map, free_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file,
                       poll_interval_seconds=0)

    assert len(provider.get_seat_map_calls) == 2
    assert len(notifications) == 1
