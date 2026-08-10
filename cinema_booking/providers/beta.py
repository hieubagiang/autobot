import re
from datetime import datetime
from typing import Callable

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from cinema_booking.provider import CinemaProvider
from cinema_booking.types import Cinema, Seat, SeatMap, SeatStatus, SeatZone, Showtime

HOME_URL = "https://betacinemas.vn/home.htm"
SHOWTIMES_URL = "https://betacinemas.vn/Ajax.aspx/LoadShowtimesByFilm"
SEAT_MAP_URL = "https://betacinemas.vn/chon-ghe.htm"

CHOOSE_CINEMA_RE = re.compile(r"ChooseCinema\('([^']+)',\s*'([^']+)'\)")
VIEWS_SHOWTIMES_RE = re.compile(
    r"viewsShowtimes\('([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'\)"
)
BOOKING_SEAT_RE = re.compile(
    r"bookingSeat\('([^']*)',\s*'([^']+)',\s*'([^']+)',\s*'(\d{2}:\d{2})',\s*'(\d{2}/\d{2}/\d{4})',"
)

# Non-seat placeholder cells: "seat-for-way" is a walkway/gap between real seats, and
# "seat-broken" marks non-bookable cells like "Lối vào" (entrance). Neither carries the
# "seat-used" class that every real seat cell has.
EXCLUDED_STATUS_CLASSES = {"seat-for-way", "seat-broken"}

STATUS_BY_CLASS = {
    "seat-empty": SeatStatus.AVAILABLE,
    # seat-select means someone -- possibly the logged-in user themselves, mid-click --
    # is actively choosing this seat right now via the site's realtime (SignalR) state.
    # It is not free to grab, so treat it as HELD rather than AVAILABLE. There is no
    # SELECTING value in the shared SeatStatus enum, and HELD is the defensively-correct
    # choice: it keeps the scorer from ever racing to claim a seat someone else is
    # actively taking.
    "seat-select": SeatStatus.HELD,
    "seat-hold": SeatStatus.HELD,
    "seat-sold": SeatStatus.SOLD,
}
# Any real (seat-used) cell whose status class isn't one of the above (e.g. an
# unconfirmed "reserved" class, or a future/unknown class) fails safe to RESERVED --
# never mistaken for AVAILABLE.
DEFAULT_STATUS = SeatStatus.RESERVED

ZONE_BY_CLASS = {
    "seat-normal": SeatZone.STANDARD,
    "seat-vip": SeatZone.VIP,
    "seat-double": SeatZone.SWEETBOX,  # Beta's "sweetheart"/couple seat.
}
DEFAULT_ZONE = SeatZone.STANDARD

_ROW_LETTER_RE = re.compile(r"^\D+")
_SEAT_NUMBER_RE = re.compile(r"(\d+)$")


def _page_shows_logged_in(html: str) -> bool:
    return "Xin chào:" in html


def parse_seat_map(html: str) -> SeatMap:
    """Parse a Beta chon-ghe.htm seat grid fragment into the shared SeatMap type.

    Row ordering (SeatMap.rows) follows the numeric data-seat-row attribute (lower =
    closer to the screen), since the row *letters* are not contiguous.

    Within a row, seats are kept in DOM order (the order soup.select(...) naturally
    yields), NOT sorted by data-seat-index: the page's authors lay seat-cell divs out in
    visual left-to-right order, which is the actual structural guarantee we have. Sorting
    by data-seat-index would be unjustified -- it is not documented to be a monotonic
    position signal, and in fact isn't one even within this fixture: row C's DOM order is
    C1 (index 38), C2 (index 39), then the walkway cell C15 (index 35) -- C15 comes AFTER
    C1/C2 in the DOM despite having a LOWER index than both, so index does not
    monotonically track DOM/visual position once non-seat cells are considered. DOM order
    has no such counter-example and needs no assumption about what the index field means.
    """
    soup = BeautifulSoup(html, "html.parser")

    row_letters: dict[int, str] = {}
    seats_by_row_number: dict[int, list[Seat]] = {}

    for cell in soup.select(".seat-cell"):
        classes = set(cell.get("class", []))
        if classes & EXCLUDED_STATUS_CLASSES:
            continue
        if "seat-used" not in classes:
            continue  # not a real, bookable seat cell

        name = (cell.get("data-seat-name") or "").strip()
        seat_index = int(cell.get("data-seat-index") or 0)
        row_number = int(cell.get("data-seat-row") or 0)
        price = int(cell.get("data-seat-price") or 0)

        status = next((v for k, v in STATUS_BY_CLASS.items() if k in classes), DEFAULT_STATUS)
        zone = next((v for k, v in ZONE_BY_CLASS.items() if k in classes), DEFAULT_ZONE)

        row_letter_match = _ROW_LETTER_RE.match(name)
        row_letter = row_letter_match.group(0) if row_letter_match else name
        seat_number_match = _SEAT_NUMBER_RE.search(name)
        col = int(seat_number_match.group(1)) if seat_number_match else 0

        seat = Seat(id=str(seat_index), label=name, row=row_letter, col=col,
                    zone=zone, price=price, status=status)

        row_letters.setdefault(row_number, row_letter)
        seats_by_row_number.setdefault(row_number, []).append(seat)

    rows = [row_letters[n] for n in sorted(seats_by_row_number)]
    seats_by_row = {row_letters[n]: seats_by_row_number[n] for n in seats_by_row_number}
    return SeatMap(rows=rows, seats_by_row=seats_by_row)


class BetaProvider(CinemaProvider):
    """Beta Cinemas provider.

    Only public, no-login showtime search is implemented here
    (list_cinemas / list_showtimes). Login-gated operations are
    implemented in later tasks.
    """

    def __init__(self, profile_dir: str = ".chrome_profiles/beta",
                 notify: Callable[[str], None] | None = None):
        # showtime.id (the "s" URL param) -> film_session_id (the "f" URL param), a
        # DIFFERENT guid than either showtime.id or showtime.cinema.id. Populated as a
        # side effect of list_showtimes, since that's the only place this value is
        # available (parsed from bookingSeat(...) and otherwise discarded).
        self._film_session_ids: dict[str, str] = {}

        # Playwright setup (Task 14). The persistent context/page are created lazily,
        # on first call to _page() -- not here -- so unit tests of list_cinemas /
        # list_showtimes / _find_film_id stay free of any browser dependency.
        self.profile_dir = profile_dir
        self.notify = notify or (lambda message: None)
        self._playwright = None
        self._context = None
        self._page_obj = None

    def _page(self):
        if self._page_obj is None:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                self.profile_dir, headless=False,
            )
            self._page_obj = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self._page_obj

    def is_logged_in(self) -> bool:
        page = self._page()
        page.goto(HOME_URL)
        return _page_shows_logged_in(page.content())

    def login_via_facebook(self) -> bool:
        page = self._page()
        page.goto("https://betacinemas.vn/login.htm")
        page.evaluate("loginByFacebook()")
        popup = page.wait_for_event("popup")
        try:
            continue_button = popup.get_by_role(
                "button", name=re.compile(r"^Tiếp tục dưới tên")
            )
            continue_button.wait_for(timeout=15000)
        except Exception:
            self.notify(
                "[beta] Facebook không hiện màn hình 'Tiếp tục' như mong đợi — "
                "có thể cần đăng nhập lại tay hoặc xác minh thêm."
            )
            return False
        continue_button.click()
        return True

    def get_seat_map(self, showtime: Showtime) -> SeatMap:
        # self._page() is provided by Task 14 (persistent authenticated Playwright
        # context) -- this method only works once that lands.
        film_session_id = self._film_session_ids.get(showtime.id)
        if film_session_id is None:
            raise ValueError(
                f"no film_session_id known for showtime {showtime.id!r} -- "
                "get_seat_map requires a Showtime this same BetaProvider instance "
                "already returned from list_showtimes()"
            )
        page = self._page()
        page.goto(f"{SEAT_MAP_URL}?f={film_session_id}&s={showtime.id}")
        return parse_seat_map(page.content())

    def lock_seats(self, showtime, seats):
        raise NotImplementedError  # Task 15

    def list_cinemas(self) -> list[Cinema]:
        resp = requests.get(HOME_URL, timeout=15)
        resp.raise_for_status()
        return [
            Cinema(id=cinema_id, name=name, city="", provider="beta")
            for cinema_id, name in CHOOSE_CINEMA_RE.findall(resp.text)
        ]

    def _find_film_id(self, movie_query: str) -> tuple[str, str] | None:
        resp = requests.get(HOME_URL, timeout=15)
        resp.raise_for_status()
        for _cinema_id, film_id, film_name, _cinema_name in VIEWS_SHOWTIMES_RE.findall(resp.text):
            if movie_query.lower() in film_name.lower():
                return film_id, film_name
        return None

    def list_showtimes(self, cinema: Cinema, movie_query: str,
                        date_range: tuple[str, str]) -> list[Showtime]:
        found = self._find_film_id(movie_query)
        if found is None:
            return []
        film_id, film_name = found

        resp = requests.post(
            SHOWTIMES_URL,
            json={"aData": [cinema.id, film_id, film_name]},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.json()["d"]

        start, end = date_range
        showtimes = []
        for _cinema_name, film_session_id, show_id, hhmm, ddmmyyyy in BOOKING_SEAT_RE.findall(html):
            self._film_session_ids[show_id] = film_session_id
            iso_date = datetime.strptime(ddmmyyyy, "%d/%m/%Y").date().isoformat()
            if start <= iso_date <= end:
                showtimes.append(Showtime(id=show_id, movie=film_name, cinema=cinema,
                                           start_time=hhmm, date=iso_date))
        return showtimes
