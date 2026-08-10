from cinema_booking.provider import CinemaProvider, FakeProvider
from cinema_booking.types import Cinema, LockResult, SeatMap, Showtime


def make_cinema():
    return Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")


def test_fake_provider_is_a_real_cinema_provider():
    assert isinstance(FakeProvider(), CinemaProvider)


def test_fake_provider_reports_configured_login_state():
    assert FakeProvider(logged_in=False).is_logged_in() is False
    assert FakeProvider(logged_in=True).is_logged_in() is True


def test_fake_provider_get_seat_map_pops_queue_then_repeats_last():
    cinema = make_cinema()
    showtime = Showtime(id="s1", movie="M", cinema=cinema, start_time="09:00", date="2026-08-12")
    empty_map = SeatMap(rows=[], seats_by_row={})
    full_map = SeatMap(rows=["A"], seats_by_row={"A": []})
    provider = FakeProvider(seat_maps=[empty_map, full_map])

    assert provider.get_seat_map(showtime) is empty_map
    assert provider.get_seat_map(showtime) is full_map
    assert provider.get_seat_map(showtime) is full_map  # queue exhausted, repeats last
    assert provider.get_seat_map_calls == [showtime, showtime, showtime]


def test_fake_provider_lock_seats_returns_configured_result_and_records_call():
    cinema = make_cinema()
    showtime = Showtime(id="s1", movie="M", cinema=cinema, start_time="09:00", date="2026-08-12")
    expected = LockResult(success=True, hold_expiry="2026-08-12T09:05:00")
    provider = FakeProvider(lock_result=expected)

    result = provider.lock_seats(showtime, [])
    assert result is expected
    assert provider.lock_seats_calls == [(showtime, [])]
