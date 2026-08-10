import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def beta_home_html():
    return (FIXTURES / "beta_home.html").read_text(encoding="utf-8")


@pytest.fixture
def beta_showtimes_json():
    return json.loads((FIXTURES / "beta_showtimes_response.json").read_text(encoding="utf-8"))


def test_list_cinemas_parses_every_choosecinema_call(beta_home_html, monkeypatch):
    from cinema_booking.providers.beta import BetaProvider

    monkeypatch.setattr(
        "cinema_booking.providers.beta.requests.get",
        lambda url, **kwargs: type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})(),
    )
    provider = BetaProvider()
    cinemas = provider.list_cinemas()
    # beta_home.html has 4 ChooseCinema(...) calls: 1 real (Beta Tay Son) + 3 made-up
    # placeholders — assert on count and on the one real, known-good entry.
    assert len(cinemas) == 4
    assert any(c.name == "Beta Tây Sơn" and c.id == "381f745f-c110-4d0c-9117-3a79f36ba9c4"
               for c in cinemas)
    assert all(c.provider == "beta" for c in cinemas)
    assert all(c.city == "" for c in cinemas)


def test_find_film_id_matches_case_insensitive_substring(beta_home_html, monkeypatch):
    from cinema_booking.providers.beta import BetaProvider

    monkeypatch.setattr(
        "cinema_booking.providers.beta.requests.get",
        lambda url, **kwargs: type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})(),
    )
    provider = BetaProvider()

    found = provider._find_film_id("người nhện")  # lowercase, partial
    assert found is not None
    film_id, film_name = found
    assert film_id == "4d206616-6753-49e1-a21a-a95729e7e5fb"
    assert film_name == "Người Nhện: Khởi Đầu Mới"


def test_find_film_id_returns_none_when_no_match(beta_home_html, monkeypatch):
    from cinema_booking.providers.beta import BetaProvider

    monkeypatch.setattr(
        "cinema_booking.providers.beta.requests.get",
        lambda url, **kwargs: type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})(),
    )
    provider = BetaProvider()

    assert provider._find_film_id("Nonexistent Movie Title Xyz") is None


def test_list_showtimes_parses_bookingseat_calls_within_date_range(
    beta_home_html, beta_showtimes_json, monkeypatch,
):
    from cinema_booking.providers.beta import BetaProvider
    from cinema_booking.types import Cinema

    def fake_get(url, **kwargs):
        return type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})()

    def fake_post(url, **kwargs):
        return type("R", (), {"json": lambda self: beta_showtimes_json, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("cinema_booking.providers.beta.requests.get", fake_get)
    monkeypatch.setattr("cinema_booking.providers.beta.requests.post", fake_post)

    provider = BetaProvider()
    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    showtimes = provider.list_showtimes(cinema, movie_query="Người Nhện",
                                         date_range=("2026-08-11", "2026-08-11"))
    # Fixture has 3 bookingSeat(...) calls, all dated 11/08/2026, at 08:10/08:30/09:00.
    assert len(showtimes) == 3
    assert all(s.date == "2026-08-11" for s in showtimes)
    assert all(s.cinema is cinema for s in showtimes)
    assert all(s.movie == "Người Nhện: Khởi Đầu Mới" for s in showtimes)
    assert {s.start_time for s in showtimes} == {"08:10", "08:30", "09:00"}
    assert any(s.start_time == "09:00" for s in showtimes)


def test_list_showtimes_excludes_showtimes_outside_date_range(
    beta_home_html, beta_showtimes_json, monkeypatch,
):
    from cinema_booking.providers.beta import BetaProvider
    from cinema_booking.types import Cinema

    def fake_get(url, **kwargs):
        return type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})()

    def fake_post(url, **kwargs):
        return type("R", (), {"json": lambda self: beta_showtimes_json, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("cinema_booking.providers.beta.requests.get", fake_get)
    monkeypatch.setattr("cinema_booking.providers.beta.requests.post", fake_post)

    provider = BetaProvider()
    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    # All fixture showtimes are dated 2026-08-11; a range that excludes that date
    # should yield nothing.
    showtimes = provider.list_showtimes(cinema, movie_query="Người Nhện",
                                         date_range=("2026-08-12", "2026-08-13"))
    assert showtimes == []


def test_list_showtimes_returns_empty_when_movie_not_found(beta_home_html, monkeypatch):
    from cinema_booking.providers.beta import BetaProvider
    from cinema_booking.types import Cinema

    def fake_get(url, **kwargs):
        return type("R", (), {"text": beta_home_html, "raise_for_status": lambda self: None})()

    def fail_post(url, **kwargs):
        raise AssertionError("should not POST when the film could not be found")

    monkeypatch.setattr("cinema_booking.providers.beta.requests.get", fake_get)
    monkeypatch.setattr("cinema_booking.providers.beta.requests.post", fail_post)

    provider = BetaProvider()
    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    showtimes = provider.list_showtimes(cinema, movie_query="Nonexistent Movie Title Xyz",
                                         date_range=("2026-08-11", "2026-08-11"))
    assert showtimes == []


def test_beta_provider_placeholder_methods_raise_not_implemented():
    from cinema_booking.providers.beta import BetaProvider

    provider = BetaProvider()
    showtime = None
    with pytest.raises(NotImplementedError):
        provider.is_logged_in()
    with pytest.raises(NotImplementedError):
        provider.get_seat_map(showtime)
    with pytest.raises(NotImplementedError):
        provider.lock_seats(showtime, [])
