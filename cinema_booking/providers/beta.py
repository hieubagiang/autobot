import re
from datetime import datetime

import requests

from cinema_booking.provider import CinemaProvider
from cinema_booking.types import Cinema, Showtime

HOME_URL = "https://betacinemas.vn/home.htm"
SHOWTIMES_URL = "https://betacinemas.vn/Ajax.aspx/LoadShowtimesByFilm"

CHOOSE_CINEMA_RE = re.compile(r"ChooseCinema\('([^']+)',\s*'([^']+)'\)")
VIEWS_SHOWTIMES_RE = re.compile(
    r"viewsShowtimes\('([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'\)"
)
BOOKING_SEAT_RE = re.compile(
    r"bookingSeat\('([^']*)',\s*'([^']+)',\s*'([^']+)',\s*'(\d{2}:\d{2})',\s*'(\d{2}/\d{2}/\d{4})',"
)


class BetaProvider(CinemaProvider):
    """Beta Cinemas provider.

    Only public, no-login showtime search is implemented here
    (list_cinemas / list_showtimes). Login-gated operations are
    implemented in later tasks.
    """

    def is_logged_in(self) -> bool:
        raise NotImplementedError  # Task 14

    def get_seat_map(self, showtime):
        raise NotImplementedError  # Task 13

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
        for _cinema_name, _film_session_id, show_id, hhmm, ddmmyyyy in BOOKING_SEAT_RE.findall(html):
            iso_date = datetime.strptime(ddmmyyyy, "%d/%m/%Y").date().isoformat()
            if start <= iso_date <= end:
                showtimes.append(Showtime(id=show_id, movie=film_name, cinema=cinema,
                                           start_time=hhmm, date=iso_date))
        return showtimes
