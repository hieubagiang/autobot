import pytest

from cinema_booking.control import rank_dates


def test_rank_dates_single_date_returns_just_that_date():
    assert rank_dates(["2026-08-12", "2026-08-12"]) == ["2026-08-12"]


def test_rank_dates_prefers_monday_and_wednesday():
    # 2026-08-10 is Mon, 11 Tue, 12 Wed, 13 Thu.
    ranked = rank_dates(["2026-08-10", "2026-08-13"])
    assert ranked == ["2026-08-10", "2026-08-12", "2026-08-11", "2026-08-13"]


def test_rank_dates_raises_on_reversed_range():
    with pytest.raises(ValueError, match="start date.*must be <= end date"):
        rank_dates(["2026-08-13", "2026-08-10"])
