import pytest

from cinema_booking.control import rank_dates, get_provider, rank_showtime_candidates
from cinema_booking.provider import FakeProvider
from cinema_booking.types import Cinema, Showtime


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
