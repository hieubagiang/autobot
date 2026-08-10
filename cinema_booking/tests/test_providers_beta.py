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


def test_list_showtimes_records_film_session_id_per_show_id_for_get_seat_map(
    beta_home_html, beta_showtimes_json, monkeypatch,
):
    # get_seat_map needs the "f" URL param, which is the film_session_id captured from
    # bookingSeat(...) -- a DIFFERENT guid than either showtime.id ("s") or
    # cinema.id. list_showtimes is the only place this value is available, so it must
    # stash it (keyed by show_id) for get_seat_map to look up later.
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
    assert len(showtimes) == 3
    # All 3 fixture showtimes share one film_session_id (a single film session's tab).
    for showtime in showtimes:
        assert provider._film_session_ids[showtime.id] == "842328a0-c4e3-4e1f-8597-20d35312d126"
    # And it must NOT be confused with the cinema (theater) guid used to call it.
    assert provider._film_session_ids[showtimes[0].id] != cinema.id


def test_beta_provider_placeholder_methods_raise_not_implemented():
    from cinema_booking.providers.beta import BetaProvider

    provider = BetaProvider()
    showtime = None
    # get_seat_map is implemented as of Task 13 (see the parse_seat_map tests below) and
    # is exercised separately; only the still-unimplemented Task 14/15 methods remain here.
    with pytest.raises(NotImplementedError):
        provider.is_logged_in()
    with pytest.raises(NotImplementedError):
        provider.lock_seats(showtime, [])


@pytest.fixture
def beta_seat_map_html():
    return (FIXTURES / "beta_seat_map.html").read_text(encoding="utf-8")


def test_parse_seat_map_excludes_walkway_and_broken_cells(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map

    seat_map = parse_seat_map(beta_seat_map_html)
    all_labels = {s.label for row in seat_map.seats_by_row.values() for s in row}
    # A15/A14/C15/L11 are seat-for-way walkway placeholders; "Lối vào" is a seat-broken
    # entrance marker. None of these are real bookable seats, so none should appear.
    assert all_labels == {"A1", "A2", "A3", "A4", "C1", "C2", "L1", "L2"}


def test_parse_seat_map_maps_all_confirmed_status_classes(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map
    from cinema_booking.types import SeatStatus

    seat_map = parse_seat_map(beta_seat_map_html)
    by_label = {s.label: s for row in seat_map.seats_by_row.values() for s in row}
    assert by_label["A1"].status == SeatStatus.AVAILABLE  # seat-empty
    # seat-select means someone -- possibly the logged-in user themselves -- is actively
    # mid-click on this seat right now. It is not free to grab, so we treat it as HELD
    # rather than AVAILABLE (there is no SELECTING value in the shared SeatStatus enum).
    assert by_label["A2"].status == SeatStatus.HELD  # seat-select
    assert by_label["A3"].status == SeatStatus.HELD  # seat-hold (constructed, from source)
    assert by_label["A4"].status == SeatStatus.SOLD  # seat-sold (constructed, from source)


def test_parse_seat_map_maps_zone_classes(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map
    from cinema_booking.types import SeatZone

    seat_map = parse_seat_map(beta_seat_map_html)
    by_label = {s.label: s for row in seat_map.seats_by_row.values() for s in row}
    assert by_label["A1"].zone == SeatZone.STANDARD  # seat-normal
    assert by_label["C1"].zone == SeatZone.VIP  # seat-vip
    assert by_label["L1"].zone == SeatZone.SWEETBOX  # seat-double


def test_parse_seat_map_uses_seat_index_as_id_and_reads_name_attr_not_merged_text(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map

    seat_map = parse_seat_map(beta_seat_map_html)
    by_label = {s.label: s for row in seat_map.seats_by_row.values() for s in row}
    # data-seat-index is the opaque id needed to call the real lock endpoint -- distinct
    # from the printed seat number, which lands in Seat.col instead.
    assert by_label["A1"].id == "4"
    assert by_label["A1"].col == 1
    assert by_label["A1"].price == 50000
    # L2's onclick JSON / textContent renders the merged "L1 - L2" label for the double
    # seat pair, but data-seat-name is still the plain "L2" -- parse_seat_map must read
    # the attribute, not get_text(), to avoid picking up the merged text.
    assert by_label["L2"].id == "189"
    assert by_label["L2"].label == "L2"


def test_parse_seat_map_rows_are_in_front_to_back_screen_order(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map

    seat_map = parse_seat_map(beta_seat_map_html)
    # data-seat-row is numeric and front-to-back: A=0, C=2, L=11. The row *letters* are
    # not contiguous (no B or D in this fixture), so this only coincidentally matches
    # alphabetical order -- the parser must sort by the numeric row, not the letter.
    assert seat_map.rows == ["A", "C", "L"]


def test_parse_seat_map_orders_seats_within_row_by_dom_order_left_to_right(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map

    seat_map = parse_seat_map(beta_seat_map_html)
    # Seats are kept in DOM order (the order they appear in the fixture's markup), which
    # is the site's actual visual left-to-right layout -- NOT sorted by data-seat-index,
    # which is not a reliable position signal (see parse_seat_map's docstring: row C's
    # own walkway cell C15 has a lower index than C1/C2 despite appearing after them).
    assert [s.label for s in seat_map.seats_by_row["A"]] == ["A1", "A2", "A3", "A4"]
    assert [s.label for s in seat_map.seats_by_row["C"]] == ["C1", "C2"]
    assert [s.label for s in seat_map.seats_by_row["L"]] == ["L1", "L2"]


def test_parse_seat_map_finds_exactly_the_eight_real_seats_with_expected_statuses(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map
    from cinema_booking.types import SeatStatus

    seat_map = parse_seat_map(beta_seat_map_html)
    all_seats = [s for row in seat_map.seats_by_row.values() for s in row]
    # 8 real seat-used cells in the fixture (5 non-seat placeholders excluded). Only 3 of
    # the 4 confirmed status classes are distinct SeatStatus values here, since both
    # seat-select and seat-hold map to HELD.
    assert len(all_seats) == 8
    statuses_seen = {s.status for s in all_seats}
    assert SeatStatus.AVAILABLE in statuses_seen
    assert SeatStatus.SOLD in statuses_seen
    assert SeatStatus.HELD in statuses_seen


class _FakePage:
    """Minimal stand-in for a Playwright Page, for get_seat_map tests."""

    def __init__(self, content_html: str):
        self._content_html = content_html
        self.goto_calls: list[str] = []

    def goto(self, url: str):
        self.goto_calls.append(url)

    def content(self) -> str:
        return self._content_html


def test_get_seat_map_builds_url_with_film_session_id_not_cinema_id(
    beta_home_html, beta_showtimes_json, beta_seat_map_html, monkeypatch,
):
    # Regression test for a bug where get_seat_map used showtime.cinema.id (the theater
    # guid) as the "f" URL param instead of the film_session_id (a different guid,
    # captured from list_showtimes' own bookingSeat(...) parsing).
    from cinema_booking.providers.beta import BetaProvider, SEAT_MAP_URL
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
    showtime = showtimes[0]

    fake_page = _FakePage(beta_seat_map_html)
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    seat_map = provider.get_seat_map(showtime)

    expected_url = f"{SEAT_MAP_URL}?f=842328a0-c4e3-4e1f-8597-20d35312d126&s={showtime.id}"
    assert fake_page.goto_calls == [expected_url]
    assert cinema.id not in fake_page.goto_calls[0]
    assert seat_map.rows == ["A", "C", "L"]  # sanity: parse_seat_map really ran


def test_get_seat_map_raises_value_error_when_film_session_id_unknown():
    # A Showtime that never went through this provider instance's list_showtimes() has
    # no known film_session_id -- get_seat_map must fail loudly rather than silently
    # building a URL with the wrong guid.
    from cinema_booking.providers.beta import BetaProvider
    from cinema_booking.types import Cinema, Showtime

    provider = BetaProvider()
    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    showtime = Showtime(id="unknown-show-id", movie="Some Movie", cinema=cinema,
                         start_time="08:10", date="2026-08-11")

    with pytest.raises(ValueError):
        provider.get_seat_map(showtime)
