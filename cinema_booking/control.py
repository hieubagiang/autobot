from datetime import date, timedelta

from cinema_booking.provider import CinemaProvider
from cinema_booking.scoring import pick_best_block
from cinema_booking.state import DEFAULT_STATE_FILE, get_item, update_item
from cinema_booking.types import Showtime

MON_WED = {0, 2}  # date.weekday(): Monday=0 .. Sunday=6


def _daterange(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def rank_dates(date_range: list[str]) -> list[str]:
    """Rank candidate dates by preference for Monday and Wednesday (typically lower prices).

    Args:
        date_range: List of two ISO-format date strings [start, end].
                    Precondition: start <= end (inclusive range).

    Returns:
        List of all dates in range, with Mon/Wed dates first (ascending),
        then all other dates (ascending).

    Raises:
        ValueError: If start > end (reversed date range).
    """
    start = date.fromisoformat(date_range[0])
    end = date.fromisoformat(date_range[1])

    if start > end:
        raise ValueError(
            f"Invalid date range: start date {start.isoformat()} "
            f"must be <= end date {end.isoformat()}"
        )

    days = list(_daterange(start, end))
    ranked = sorted(days, key=lambda d: (0 if d.weekday() in MON_WED else 1, d))
    return [d.isoformat() for d in ranked]


DEFAULT_CINEMA_PRIORITY: dict[str, list[str]] = {
    "beta": ["Beta Tây Sơn"],
}


def get_provider(name: str) -> CinemaProvider:
    if name == "beta":
        from cinema_booking.providers.beta import BetaProvider
        return BetaProvider()
    raise ValueError(f"Unknown provider: {name}")


def rank_showtime_candidates(provider: CinemaProvider, cinema_priority: list[str],
                              movie_query: str, date_range: list[str]) -> list[Showtime]:
    cinemas_by_name = {c.name: c for c in provider.list_cinemas()}
    ranked_dates = rank_dates(date_range)
    candidates: list[Showtime] = []
    for cinema_name in cinema_priority:
        cinema = cinemas_by_name.get(cinema_name)
        if cinema is None:
            continue
        for day in ranked_dates:
            candidates.extend(provider.list_showtimes(cinema, movie_query, (day, day)))
    return candidates


def instant_camp_loop(item_id: str, stop_event, notify, state_file: str = DEFAULT_STATE_FILE,
                       poll_interval_seconds: float = 5.0) -> None:
    item = get_item(item_id, state_file)
    if item is None:
        return
    provider = get_provider(item["provider"])

    while not stop_event.is_set():
        if not provider.is_logged_in():
            notify(f"[{item_id}] Provider {item['provider']} chưa đăng nhập — vui lòng đăng nhập lại.")
            stop_event.wait(poll_interval_seconds)
            continue

        candidates = rank_showtime_candidates(
            provider, item["cinema_priority"], item["movie_query"], item["date_range"]
        )
        for showtime in candidates:
            seat_map = provider.get_seat_map(showtime)
            block = pick_best_block(seat_map, item["quantity"], item["prefer_sweetbox"])
            if block is None:
                continue
            result = provider.lock_seats(showtime, block)
            if result.success:
                seat_labels = [s.label for s in block]
                update_item(item_id, path=state_file, status="pending_payment",
                            hold_expiry=result.hold_expiry, payment_url=result.payment_url,
                            seat_labels=seat_labels)
                notify(f"[{item_id}] Đã giữ ghế: {', '.join(seat_labels)} — "
                       f"hạn giữ chỗ: {result.hold_expiry}. Link: {result.payment_url}")
                return

        stop_event.wait(poll_interval_seconds)
