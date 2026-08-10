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


@pytest.fixture
def beta_lock_response_json():
    return json.loads((FIXTURES / "beta_lock_response.json").read_text(encoding="utf-8"))


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


def test_is_logged_in_reads_greeting_marker():
    from cinema_booking.providers.beta import _page_shows_logged_in

    assert _page_shows_logged_in("...Xin chào: Phạm Doãn Hiếu ...") is True
    assert _page_shows_logged_in("...ĐĂNG NHẬP ĐĂNG KÝ...") is False


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


# ---------------------------------------------------------------------------
# Task 15: seat locking
# ---------------------------------------------------------------------------


def test_parse_lock_response_success_reads_real_fixture(beta_lock_response_json):
    # beta_lock_response.json is the REAL captured SelectSeat response from Task 12's
    # live spike: {"d": "{\"SeatIndex\":5,\"SeatStatus\":1,\"IsYourSeat\":true}"} -- a
    # JSON string nested inside the outer JSON's "d" field. parse_lock_response must
    # parse that nested string, not treat "d" as already-structured data.
    from cinema_booking.providers.beta import parse_lock_response

    success, error = parse_lock_response(beta_lock_response_json)
    assert success is True
    assert error is None


def test_parse_lock_response_failure_when_is_your_seat_false():
    # Hand-constructed failure variant in the SAME real shape (nested "d" JSON string),
    # with IsYourSeat:false -- e.g. someone else grabbed the seat first. There is no
    # "message" field in Beta's real response (that was the plan's placeholder, not the
    # real shape), so the error must be synthesized from the fields that DO exist.
    from cinema_booking.providers.beta import parse_lock_response

    raw = {"d": '{"SeatIndex":5,"SeatStatus":1,"IsYourSeat":false}'}
    success, error = parse_lock_response(raw)
    assert success is False
    assert error is not None
    assert "False" in error or "false" in error.lower()


def test_parse_lock_response_double_parses_nested_json_string():
    # Explicit regression guard: if someone "simplifies" parse_lock_response to read
    # data["d"] directly as a dict (skipping the second json.loads), this must fail loudly
    # rather than silently -- data["d"] is a STRING, not a dict, in the real API.
    from cinema_booking.providers.beta import parse_lock_response

    raw = {"d": '{"SeatIndex":7,"SeatStatus":1,"IsYourSeat":true}'}
    assert isinstance(raw["d"], str)  # sanity: the fixture shape really is string-nested
    success, error = parse_lock_response(raw)
    assert success is True
    assert error is None


class _FakeLockPage:
    """Minimal stand-in for a Playwright Page, for lock_seats tests.

    Real BetaProvider.lock_seats calls page.evaluate("customerId") (no arg) once to read
    the live customerId JS global, then page.evaluate(<fetch script>, [path, seatIndex,
    showId, customerId]) once per SelectSeat/ReturnSeat call. This fake tells those two
    apart the same way the real evaluate() call sites do: no second argument vs. one.
    """

    def __init__(self, customer_id: str, responses: list[dict]):
        self.customer_id = customer_id
        self._responses = list(responses)
        self.evaluate_calls: list[tuple[str, list]] = []  # (script, [path, seat, show, cust])
        self.goto_calls: list[str] = []

    def goto(self, url: str):
        self.goto_calls.append(url)

    def evaluate(self, script, arg=None):
        if arg is None:
            return self.customer_id
        self.evaluate_calls.append((script, arg))
        return self._responses.pop(0)


def _select_seat_response(seat_index: int, is_your_seat: bool) -> dict:
    inner = json.dumps({"SeatIndex": seat_index, "SeatStatus": 1, "IsYourSeat": is_your_seat})
    return {"d": inner}


def _beta_lock_fixtures():
    from cinema_booking.types import Cinema, Seat, SeatStatus, SeatZone, Showtime

    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    showtime = Showtime(id="show-1", movie="Người Nhện: Khởi Đầu Mới", cinema=cinema,
                         start_time="08:10", date="2026-08-13")
    seat_a1 = Seat(id="5", label="A1", row="A", col=1, zone=SeatZone.STANDARD,
                   price=50000, status=SeatStatus.AVAILABLE)
    seat_a2 = Seat(id="6", label="A2", row="A", col=2, zone=SeatZone.STANDARD,
                   price=50000, status=SeatStatus.AVAILABLE)
    return showtime, seat_a1, seat_a2


def test_lock_seats_calls_select_seat_once_per_seat_using_seat_id_in_order(monkeypatch):
    from cinema_booking.providers.beta import BetaProvider, SELECT_SEAT_PATH
    showtime, seat_a1, seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(
        customer_id="cust-guid-1",
        responses=[_select_seat_response(5, True), _select_seat_response(6, True)],
    )
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    result = provider.lock_seats(showtime, [seat_a1, seat_a2])

    assert result.success is True
    assert result.error is None
    calls = [arg for _script, arg in fake_page.evaluate_calls]
    # seat.id ("5"/"6"), not seat.label ("A1"/"A2"), is what goes into aData -- and the
    # customerId read live from the page threads through to every call.
    assert calls == [
        [SELECT_SEAT_PATH, "5", "show-1", "cust-guid-1"],
        [SELECT_SEAT_PATH, "6", "show-1", "cust-guid-1"],
    ]


def test_lock_seats_navigates_to_seat_page_with_film_session_id(monkeypatch):
    from cinema_booking.providers.beta import BetaProvider, SEAT_MAP_URL
    showtime, seat_a1, _seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(customer_id="cust-guid-1", responses=[_select_seat_response(5, True)])
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    provider.lock_seats(showtime, [seat_a1])

    assert fake_page.goto_calls == [f"{SEAT_MAP_URL}?f=fsid-1&s=show-1"]


def test_lock_seats_computes_hold_expiry_as_roughly_ten_minutes_from_now(monkeypatch):
    from datetime import datetime, timedelta

    from cinema_booking.providers.beta import BetaProvider
    showtime, seat_a1, _seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(customer_id="cust-guid-1", responses=[_select_seat_response(5, True)])
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    before = datetime.now()
    result = provider.lock_seats(showtime, [seat_a1])
    after = datetime.now()

    expiry = datetime.fromisoformat(result.hold_expiry)
    # Beta's confirmed hold mechanism is a 10-minute ESTIMATE (see design spec addendum),
    # not a server-guaranteed per-seat deadline -- assert it's roughly 10 minutes out,
    # with slack for test execution time.
    assert before + timedelta(minutes=9, seconds=55) <= expiry <= after + timedelta(minutes=10, seconds=5)


def test_lock_seats_rolls_back_already_locked_seats_on_partial_failure(monkeypatch):
    # Locking a block of seats means one SelectSeat call per seat (confirmed live: 2
    # seats -> 2 separate requests). If seat A2 fails after A1 already locked, A1 must be
    # released via ReturnSeat before returning failure, to avoid an orphaned partial hold.
    from cinema_booking.providers.beta import BetaProvider, RETURN_SEAT_PATH, SELECT_SEAT_PATH
    showtime, seat_a1, seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(
        customer_id="cust-guid-1",
        responses=[
            _select_seat_response(5, True),    # A1 locks fine
            _select_seat_response(6, False),   # A2 fails -- someone else grabbed it
            _select_seat_response(5, False),   # ReturnSeat rollback of A1 confirms release
        ],
    )
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    result = provider.lock_seats(showtime, [seat_a1, seat_a2])

    assert result.success is False
    assert result.hold_expiry is None
    assert result.error is not None

    calls = [arg for _script, arg in fake_page.evaluate_calls]
    assert calls == [
        [SELECT_SEAT_PATH, "5", "show-1", "cust-guid-1"],
        [SELECT_SEAT_PATH, "6", "show-1", "cust-guid-1"],
        [RETURN_SEAT_PATH, "5", "show-1", "cust-guid-1"],  # only A1 -- A2 never locked
    ]


def test_lock_seats_does_not_call_return_seat_when_first_seat_fails(monkeypatch):
    # If the very first seat fails, nothing was locked yet in this attempt, so there is
    # nothing to roll back -- no ReturnSeat call should be made.
    from cinema_booking.providers.beta import BetaProvider, RETURN_SEAT_PATH
    showtime, seat_a1, _seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(customer_id="cust-guid-1", responses=[_select_seat_response(5, False)])
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    result = provider.lock_seats(showtime, [seat_a1])

    assert result.success is False
    assert not any(arg[0] == RETURN_SEAT_PATH for _script, arg in fake_page.evaluate_calls)


def test_lock_seats_notifies_when_rollback_release_not_confirmed(monkeypatch):
    # If ReturnSeat's own response doesn't confirm the release (IsYourSeat still true),
    # that's a possible orphaned hold -- lock_seats must surface it via notify() rather
    # than silently swallowing it, since there's nothing more it can do automatically.
    from cinema_booking.providers.beta import BetaProvider
    showtime, seat_a1, seat_a2 = _beta_lock_fixtures()

    notifications = []
    provider = BetaProvider(notify=notifications.append)
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPage(
        customer_id="cust-guid-1",
        responses=[
            _select_seat_response(5, True),   # A1 locks fine
            _select_seat_response(6, False),  # A2 fails
            _select_seat_response(5, True),   # ReturnSeat rollback of A1 -- NOT confirmed released
        ],
    )
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    result = provider.lock_seats(showtime, [seat_a1, seat_a2])

    assert result.success is False
    assert len(notifications) == 1
    assert "A1" in notifications[0]


class _FakeLockPageWithFailure(_FakeLockPage):
    """Like _FakeLockPage, but raises on a chosen 1-based call number instead of
    returning a scripted response -- simulates a network/JS error mid-fetch, not just an
    IsYourSeat:false response."""

    def __init__(self, customer_id: str, responses: list[dict], fail_on_call: int):
        super().__init__(customer_id, responses)
        self._fail_on_call = fail_on_call
        self._call_count = 0

    def evaluate(self, script, arg=None):
        if arg is None:
            return self.customer_id
        self._call_count += 1
        if self._call_count == self._fail_on_call:
            raise RuntimeError("simulated network error")
        return super().evaluate(script, arg)


def test_lock_seats_rolls_back_on_network_error_during_select_seat(monkeypatch):
    # A network/JS error while locking the SECOND seat (not just an IsYourSeat:false
    # response) must still trigger rollback of the already-locked FIRST seat -- the
    # brief's failure modes explicitly include "network error", not only a false response.
    from cinema_booking.providers.beta import BetaProvider, RETURN_SEAT_PATH, SELECT_SEAT_PATH
    showtime, seat_a1, seat_a2 = _beta_lock_fixtures()

    provider = BetaProvider()
    provider._film_session_ids[showtime.id] = "fsid-1"
    fake_page = _FakeLockPageWithFailure(
        customer_id="cust-guid-1",
        responses=[
            _select_seat_response(5, True),   # A1 locks fine (call #1)
            # call #2 (A2's SelectSeat) raises instead of returning a response
            _select_seat_response(5, False),  # call #3: ReturnSeat rollback of A1
        ],
        fail_on_call=2,
    )
    monkeypatch.setattr(provider, "_page", lambda: fake_page, raising=False)

    result = provider.lock_seats(showtime, [seat_a1, seat_a2])

    assert result.success is False
    assert result.error is not None
    calls = [arg for _script, arg in fake_page.evaluate_calls]
    assert calls == [
        [SELECT_SEAT_PATH, "5", "show-1", "cust-guid-1"],
        [RETURN_SEAT_PATH, "5", "show-1", "cust-guid-1"],  # rollback still ran
    ]


def test_lock_seats_raises_value_error_when_film_session_id_unknown():
    # Same contract as get_seat_map: a Showtime that never went through this provider
    # instance's list_showtimes() has no known film_session_id -- lock_seats must fail
    # loudly rather than silently building a URL with the wrong guid.
    from cinema_booking.providers.beta import BetaProvider
    from cinema_booking.types import Cinema, Showtime

    provider = BetaProvider()
    cinema = Cinema(id="381f745f-c110-4d0c-9117-3a79f36ba9c4", name="Beta Tây Sơn",
                     city="", provider="beta")
    showtime = Showtime(id="unknown-show-id", movie="Some Movie", cinema=cinema,
                         start_time="08:10", date="2026-08-11")

    with pytest.raises(ValueError):
        provider.lock_seats(showtime, [])
