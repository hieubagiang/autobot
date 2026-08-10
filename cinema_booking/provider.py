from abc import ABC, abstractmethod

from cinema_booking.types import Cinema, LockResult, Seat, SeatMap, Showtime


class CinemaProvider(ABC):
    @abstractmethod
    def is_logged_in(self) -> bool: ...

    @abstractmethod
    def list_cinemas(self) -> list[Cinema]: ...

    @abstractmethod
    def list_showtimes(self, cinema: Cinema, movie_query: str,
                        date_range: tuple[str, str]) -> list[Showtime]: ...

    @abstractmethod
    def get_seat_map(self, showtime: Showtime) -> SeatMap: ...

    @abstractmethod
    def lock_seats(self, showtime: Showtime, seats: list[Seat]) -> LockResult: ...


class FakeProvider(CinemaProvider):
    """Test double — never touches a network or browser. Scripted return values."""

    def __init__(self, cinemas=None, showtimes=None, seat_maps=None,
                 lock_result=None, logged_in=True):
        self.cinemas = cinemas or []
        self.showtimes = showtimes or []
        self._seat_map_queue = list(seat_maps or [])
        self._last_seat_map = None
        self.lock_result = lock_result or LockResult(success=False, error="not configured")
        self.logged_in = logged_in
        self.get_seat_map_calls: list[Showtime] = []
        self.lock_seats_calls: list[tuple] = []

    def is_logged_in(self) -> bool:
        return self.logged_in

    def list_cinemas(self) -> list[Cinema]:
        return self.cinemas

    def list_showtimes(self, cinema: Cinema, movie_query: str,
                        date_range: tuple[str, str]) -> list[Showtime]:
        start, end = date_range
        return [
            s for s in self.showtimes
            if s.cinema.id == cinema.id and start <= s.date <= end and movie_query in s.movie
        ]

    def get_seat_map(self, showtime: Showtime) -> SeatMap:
        self.get_seat_map_calls.append(showtime)
        if self._seat_map_queue:
            self._last_seat_map = self._seat_map_queue.pop(0)
        return self._last_seat_map

    def lock_seats(self, showtime: Showtime, seats: list[Seat]) -> LockResult:
        self.lock_seats_calls.append((showtime, seats))
        return self.lock_result
