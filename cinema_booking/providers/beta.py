import json
import re
import threading
from datetime import datetime, timedelta
from typing import Callable

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from cinema_booking.provider import CinemaProvider
from cinema_booking.types import Cinema, LockResult, Seat, SeatMap, SeatStatus, SeatZone, Showtime

HOME_URL = "https://betacinemas.vn/home.htm"
SHOWTIMES_URL = "https://betacinemas.vn/Ajax.aspx/LoadShowtimesByFilm"
SEAT_MAP_URL = "https://betacinemas.vn/chon-ghe.htm"
SELECT_SEAT_PATH = "/Ajax.aspx/SelectSeat"
RETURN_SEAT_PATH = "/Ajax.aspx/ReturnSeat"

# Beta's confirmed hold mechanism (design spec addendum 2026-08-10, Task 12 live spike) is
# a single 10-minute PAGE-LEVEL countdown for the whole chon-ghe.htm page, re-armed on
# every page load -- NOT a per-seat expiry field in SelectSeat's response (unlike CGV).
# The page's own JS has a localStorage-based persisted-deadline path, but it is dead,
# commented-out code, so there is no reliable source for a true server-side deadline.
# This is therefore a documented ESTIMATE for LockResult.hold_expiry, not a
# server-confirmed guarantee.
HOLD_MINUTES = 10

# home.htm cache TTL (finding N2). I4's original cache had no expiry at all, so a film
# that appears on the homepage after the bot process started was never discovered for
# the rest of that process's life. 5 minutes balances the original goal (don't hit a
# Cloudflare-fronted page on every single poll) against staleness.
HOME_HTML_CACHE_TTL = timedelta(minutes=5)

# A long-running camp loop keeps calling page.goto() on the SAME page for hours; Chrome's
# per-navigation memory (JS heap, DOM, cached resources) accumulates over that time and
# was observed to OOM-kill the whole bot process after ~3 hours of continuous polling.
# Recycling the page/context periodically caps how much a single Chrome session can
# accumulate before it's replaced with a fresh one (finding N9).
PAGE_MAX_AGE = timedelta(minutes=30)

# Shared by SelectSeat (lock) and ReturnSeat (release) -- identical payload shape
# ({"aData": [seatIndex, showId, customerId]}, three strings) and identical response
# shape ({"d": "<nested JSON string>"}), just opposite meaning of IsYourSeat on success.
_SEAT_ENDPOINT_SCRIPT = """async ([path, seatIndex, showId, customerId]) => {
    const resp = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json; charset=UTF-8'},
        body: JSON.stringify({aData: [seatIndex, showId, customerId]}),
    });
    return await resp.json();
}"""

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
        if "seat-double-hidden" in classes:
            # First half of a Beta "sweetheart"/couple-seat pair (two seat-cells linked
            # via SeatIndexRelation, e.g. L1/L2). The site's own JS hides this cell and
            # merges the visual label onto the SECOND cell -- skip it here so
            # parse_seat_map emits exactly ONE Seat per physical couple-seat (using the
            # second cell's own data-seat-index/name/price/status below), instead of two
            # independent Seats that pick_best_block could mismatch across two different
            # couple-seats. Known simplification (finding I6): the resulting Seat.label
            # is just the second half's plain name (e.g. "L2"), not the merged "L1 - L2".
            continue

        name = (cell.get("data-seat-name") or "").strip()
        seat_index = int(cell.get("data-seat-index") or 0)
        row_number = int(cell.get("data-seat-row") or 0)
        price = int(cell.get("data-seat-price") or 0)
        # Present only on the kept (second) half of a seat-double couple-seat pair
        # (finding N4) -- the wire id of its partner cell, needed so lock_seats can lock
        # BOTH halves of the one physical two-person seat, not just this cell's own id.
        relation_index = cell.get("data-relation-seat-index")

        status = next((v for k, v in STATUS_BY_CLASS.items() if k in classes), DEFAULT_STATUS)
        zone = next((v for k, v in ZONE_BY_CLASS.items() if k in classes), DEFAULT_ZONE)

        row_letter_match = _ROW_LETTER_RE.match(name)
        row_letter = row_letter_match.group(0) if row_letter_match else name
        seat_number_match = _SEAT_NUMBER_RE.search(name)
        col = int(seat_number_match.group(1)) if seat_number_match else 0

        seat = Seat(id=str(seat_index), label=name, row=row_letter, col=col,
                    zone=zone, price=price, status=status, partner_id=relation_index)

        row_letters.setdefault(row_number, row_letter)
        seats_by_row_number.setdefault(row_number, []).append(seat)

    rows = [row_letters[n] for n in sorted(seats_by_row_number)]
    seats_by_row = {row_letters[n]: seats_by_row_number[n] for n in seats_by_row_number}
    return SeatMap(rows=rows, seats_by_row=seats_by_row)


def _parse_seat_payload(data: dict) -> dict:
    """Double-parse a Beta Ajax.aspx SelectSeat/ReturnSeat response.

    ASP.NET wraps the real payload as a JSON STRING nested inside the outer JSON's "d"
    field, e.g. {"d": "{\\"SeatIndex\\":5,\\"SeatStatus\\":1,\\"IsYourSeat\\":true}"} --
    confirmed live in Task 12's spike and captured verbatim in
    cinema_booking/tests/fixtures/beta_lock_response.json. `data` must therefore be
    parsed twice: once by the caller (resp.json() / page.evaluate's own JSON.parse) to
    get this outer dict, and once here to get at the inner object.
    """
    return json.loads(data["d"])


def parse_lock_response(data: dict) -> tuple[bool, str | None]:
    """Parse a SelectSeat response into (success, error).

    Success means IsYourSeat is True -- Beta's own signal that the lock landed on OUR
    account. There is no human-readable error-message field anywhere in the real
    response (the plan's original "success"/"message" placeholder does not match Beta's
    actual API), so on failure the error is synthesized from whatever fields the
    response did contain.
    """
    inner = _parse_seat_payload(data)
    if inner.get("IsYourSeat") is True:
        return True, None
    return False, (
        f"seat lock not confirmed (IsYourSeat={inner.get('IsYourSeat')!r}, "
        f"SeatStatus={inner.get('SeatStatus')!r})"
    )


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
        self._page_created_at: datetime | None = None

        # Cache of home.htm's raw HTML (finding I4, TTL added for finding N2). Without a
        # TTL, list_cinemas()/_find_film_id() would only ever fetch home.htm once per
        # process lifetime -- a film that appears on the homepage after the bot started
        # would never be discovered. See HOME_HTML_CACHE_TTL.
        self._home_html_cache: str | None = None
        self._home_html_cache_time: datetime | None = None

        # Guards every method that drives self._page() (finding N3). get_provider()
        # caches one BetaProvider instance per process (finding I5), so two concurrent
        # /instant camp-loop threads for the same provider now share this one instance
        # -- and Playwright's sync API is bound to the thread that created it, so
        # unsynchronized cross-thread use of the same page can raise or interleave
        # navigation/state (thread A's goto landing mid-way through thread B reading
        # page.content()). Serializing access trades concurrency for correctness, which
        # is the only option with a single underlying browser page.
        self._lock = threading.Lock()

    def _page(self):
        if self._context is None:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                self.profile_dir, headless=False,
                # Playwright disables extensions by default. Allowing them lets a human
                # use a normal browser extension (e.g. a cookie-import tool) during the
                # one-time manual Facebook login this provider's session-reuse design
                # requires -- without this, an extension installed under one launch
                # becomes inaccessible (ERR_FILE_NOT_FOUND) the next time the profile is
                # opened with the default extensions-disabled flags.
                ignore_default_args=["--disable-extensions"],
                # Facebook's OAuth consent screen showed inconsistent behavior during
                # the manual login flow (identical click sometimes completed the
                # handshake, sometimes silently didn't) -- possibly Meta's own
                # bot-detection reacting to Chromium's default automation fingerprint
                # (navigator.webdriver, etc). This is the standard Selenium/Puppeteer
                # mitigation flag; cheap to add, no behavior change if it isn't the
                # actual cause.
                args=["--disable-blink-features=AutomationControlled"],
            )

        if self._page_obj is not None and self._page_created_at is not None:
            if datetime.now() - self._page_created_at > PAGE_MAX_AGE:
                self._recycle_page()

        if self._page_obj is None:
            self._page_obj = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page_created_at = datetime.now()
        return self._page_obj

    def _recycle_page(self) -> None:
        """Close and replace just the PAGE/tab (finding N9), never the CONTEXT.

        A page/tab's renderer process is what accumulates memory over hours of
        continuous goto()-ing; closing and reopening it caps that. The context (and the
        Chrome persistent profile behind it) must stay alive across recycles: Beta's own
        login state lives in a session-only cookie (no persistent auth cookie exists --
        confirmed by inspecting the profile's cookie store), so closing the CONTEXT (as
        an earlier version of this method did) discards that cookie and silently logs
        the bot out every PAGE_MAX_AGE, exactly the "chưa đăng nhập" loop this was
        supposed to prevent, not cause. Best-effort: a failure while closing the stale
        page must not prevent opening a new one.
        """
        try:
            self._page_obj.close()
        except Exception as exc:
            self.notify(f"[beta] failed to close stale browser page during recycle "
                        f"(continuing to relaunch anyway): {exc}")
        self._page_obj = None
        self._page_created_at = None

    def is_logged_in(self) -> bool:
        with self._lock:
            page = self._page()
            page.goto(HOME_URL)
            return _page_shows_logged_in(page.content())

    def login_via_facebook(self) -> bool:
        with self._lock:
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
        with self._lock:
            page = self._page()
            page.goto(f"{SEAT_MAP_URL}?f={film_session_id}&s={showtime.id}")
            seat_map = parse_seat_map(page.content())

        total_seats = sum(len(seats) for seats in seat_map.seats_by_row.values())
        if total_seats == 0:
            # An all-empty SeatMap here is NOT a normal "no seats free right now" poll
            # result -- parse_seat_map only ever returns zero seats when the page isn't
            # actually a seat-selection page (login redirect, error page, Cloudflare
            # interstitial). Don't raise (the camp loop should keep polling, same as its
            # existing "no seats found this poll" behavior for pick_best_block(None)) --
            # just make the situation observable instead of silently polling forever with
            # no signal (finding I7).
            self.notify(
                f"[beta] Seat map cho showtime {showtime.id} trả về rỗng (0 ghế) -- "
                "có thể do session hết hạn, bị redirect về trang đăng nhập, hoặc gặp "
                "Cloudflare challenge. Vẫn tiếp tục poll."
            )
        return seat_map

    def lock_seats(self, showtime: Showtime, seats: list[Seat]) -> LockResult:
        """Lock a block of seats via Beta's real SelectSeat endpoint.

        Beta has no bulk-lock API -- confirmed live in Task 12's spike that selecting 2
        seats fired 2 separate SelectSeat requests -- so this calls SelectSeat once per
        WIRE INDEX, sequentially, in order. A couple-seat (Seat.partner_id set, finding
        N4) is one physical two-person seat but needs 2 SelectSeat calls -- one for
        seat.id, one for seat.partner_id -- since the site tracks each half's wire index
        independently; both must succeed for that Seat to count as locked. If any
        individual call fails (network error, or a response with IsYourSeat:false meaning
        someone else grabbed that index in the meantime), every index already locked in
        this same attempt -- including any already-locked half of the CURRENT seat -- is
        released via ReturnSeat before returning failure, to avoid leaving an orphaned
        partial hold. Only returns LockResult(success=True, ...) if every seat in the
        block fully locked (both halves, for a couple-seat).
        """
        film_session_id = self._film_session_ids.get(showtime.id)
        if film_session_id is None:
            raise ValueError(
                f"no film_session_id known for showtime {showtime.id!r} -- "
                "lock_seats requires a Showtime this same BetaProvider instance "
                "already returned from list_showtimes()"
            )
        with self._lock:
            page = self._page()
            page.goto(f"{SEAT_MAP_URL}?f={film_session_id}&s={showtime.id}")
            # customerId is the logged-in customer's own GUID -- NOT per-seat and not
            # derivable from anything already in our types. It exists only as a JS global
            # (`var customerId = '...';`) on this page, so it must be read live.
            customer_id = page.evaluate("customerId")

            locked_indexes: list[tuple[str, str]] = []  # (seat_index, display label)
            for seat in seats:
                indexes = [(seat.id, seat.label)]
                if seat.partner_id:
                    indexes.append((seat.partner_id, f"{seat.label} (nửa ghế đôi)"))

                newly_locked: list[tuple[str, str]] = []
                failure: str | None = None
                for seat_index, label in indexes:
                    try:
                        response = self._call_seat_endpoint(page, SELECT_SEAT_PATH, seat_index,
                                                             showtime.id, customer_id)
                        success, error = parse_lock_response(response)
                    except Exception as exc:
                        success, error = False, f"SelectSeat request failed: {exc}"
                    if not success:
                        failure = error
                        break
                    newly_locked.append((seat_index, label))

                if failure is not None:
                    self._release_indexes(page, newly_locked + locked_indexes,
                                           showtime.id, customer_id)
                    return LockResult(success=False,
                                       error=f"failed to lock seat {seat.label}: {failure}")
                locked_indexes.extend(newly_locked)

        hold_expiry = (datetime.now() + timedelta(minutes=HOLD_MINUTES)).isoformat()
        # payment_url: the seat-selection page URL itself, since that's the page the
        # hold actually lives on (Beta has no separate "review your hold" page) --
        # without this, the success Telegram message read "Link: None" (finding I2).
        payment_url = f"{SEAT_MAP_URL}?f={film_session_id}&s={showtime.id}"
        return LockResult(success=True, hold_expiry=hold_expiry, payment_url=payment_url)

    def _call_seat_endpoint(self, page, path: str, seat_index: str, show_id: str,
                             customer_id: str) -> dict:
        return page.evaluate(_SEAT_ENDPOINT_SCRIPT, [path, seat_index, show_id, customer_id])

    def _release_indexes(self, page, indexes: list[tuple[str, str]], show_id: str,
                          customer_id: str) -> None:
        """Best-effort rollback: call ReturnSeat for every already-locked wire index.

        This is the cleanup path for a partial-lock failure, so it never raises -- a
        release that fails or comes back unconfirmed is surfaced via notify() (there is
        nothing more automatic that can be done about it) rather than compounding the
        original failure with a new exception.
        """
        for seat_index, label in indexes:
            try:
                response = self._call_seat_endpoint(page, RETURN_SEAT_PATH, seat_index,
                                                     show_id, customer_id)
                inner = _parse_seat_payload(response)
                if inner.get("IsYourSeat") is not False:
                    self.notify(
                        f"[beta] ReturnSeat for seat {label} during lock rollback did "
                        f"not confirm release (response={inner!r}) -- may be an orphaned "
                        "hold, check manually."
                    )
            except Exception as exc:
                self.notify(
                    f"[beta] ReturnSeat request failed for seat {label} during lock "
                    f"rollback -- may be an orphaned hold, check manually: {exc}"
                )

    def _get_home_html(self) -> str:
        now = datetime.now()
        is_stale = (
            self._home_html_cache is None
            or self._home_html_cache_time is None
            or now - self._home_html_cache_time > HOME_HTML_CACHE_TTL
        )
        if is_stale:
            resp = requests.get(HOME_URL, timeout=15)
            resp.raise_for_status()
            self._home_html_cache = resp.text
            self._home_html_cache_time = now
        return self._home_html_cache

    def list_cinemas(self) -> list[Cinema]:
        html = self._get_home_html()
        return [
            Cinema(id=cinema_id, name=name, city="", provider="beta")
            for cinema_id, name in CHOOSE_CINEMA_RE.findall(html)
        ]

    def _find_film_id(self, movie_query: str) -> tuple[str, str] | None:
        html = self._get_home_html()
        for _cinema_id, film_id, film_name, _cinema_name in VIEWS_SHOWTIMES_RE.findall(html):
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
