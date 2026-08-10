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

# Per-name provider instance cache (finding I5). Repeated get_provider("beta") calls
# must return the SAME BetaProvider instance -- otherwise two concurrent camp loops for
# the same provider would each spin up their own Playwright persistent-context profile
# on the same profile_dir, which collides (a Chromium persistent profile can only be
# owned by one running process at a time).
_provider_instances: dict[str, CinemaProvider] = {}


def get_provider(name: str) -> CinemaProvider:
    if name in _provider_instances:
        return _provider_instances[name]
    if name == "beta":
        from cinema_booking.providers.beta import BetaProvider
        provider = BetaProvider()
        _provider_instances[name] = provider
        return provider
    raise ValueError(f"Unknown provider: {name}")


def rank_showtime_candidates(provider: CinemaProvider, cinema_priority: list[str],
                              movie_query: str, date_range: list[str]) -> list[Showtime]:
    cinemas_by_name = {c.name: c for c in provider.list_cinemas()}
    ranked_dates = rank_dates(date_range)
    date_rank = {d: i for i, d in enumerate(ranked_dates)}
    candidates: list[Showtime] = []
    for cinema_name in cinema_priority:
        cinema = cinemas_by_name.get(cinema_name)
        if cinema is None:
            continue
        # One call for the whole date range, not one per ranked day: Beta's
        # LoadShowtimesByFilm response already contains every date-tab in a single
        # response (finding I4), and other providers' list_showtimes are expected to
        # respect date_range the same way. We recover the Mon/Wed-first ordering
        # ourselves afterward, by sorting this cinema's showtimes on date_rank.
        showtimes = provider.list_showtimes(cinema, movie_query, (date_range[0], date_range[-1]))
        showtimes = sorted(showtimes, key=lambda s: date_rank.get(s.date, len(ranked_dates)))
        candidates.extend(showtimes)
    return candidates


def instant_camp_loop(item_id: str, stop_event, notify, state_file: str = DEFAULT_STATE_FILE,
                       poll_interval_seconds: float = 5.0) -> None:
    item = get_item(item_id, state_file)
    if item is None:
        return

    while not stop_event.is_set():
        try:
            provider = get_provider(item["provider"])

            if not provider.is_logged_in():
                notify(f"[{item_id}] Provider {item['provider']} chưa đăng nhập — vui lòng đăng nhập lại.")
                stop_event.wait(poll_interval_seconds)
                continue

            candidates = rank_showtime_candidates(
                provider, item["cinema_priority"], item["movie_query"], item["date_range"]
            )
            locked = False
            for showtime in candidates:
                seat_map = provider.get_seat_map(showtime)
                block = pick_best_block(seat_map, item["quantity"], item["prefer_sweetbox"])
                if block is None:
                    continue
                result = provider.lock_seats(showtime, block)
                if result.success:
                    seat_labels = [s.label for s in block]
                    # instant=False: once a lock succeeds, this item must NOT be
                    # re-camped on the next bot restart (finding I3) -- otherwise
                    # resume_instant_items would spin up a second camp loop that could
                    # go on to lock (and double-book) a second, unrelated seat block for
                    # an item the user is already deciding whether to pay for.
                    update_item(item_id, path=state_file, status="pending_payment",
                                hold_expiry=result.hold_expiry, payment_url=result.payment_url,
                                seat_labels=seat_labels, instant=False)
                    # locked=True is set (and the state above is persisted) BEFORE the
                    # notify call below, and that call's own exception is swallowed here
                    # rather than left to the outer except (finding N1) -- otherwise a
                    # transient notify failure (e.g. a Telegram API hiccup) right after a
                    # real successful lock would fall into the outer `except Exception`
                    # handler and let the `while` loop go around again, attempting a
                    # SECOND lock on an item that already holds real seats.
                    locked = True
                    try:
                        notify(f"[{item_id}] Đã giữ ghế: {', '.join(seat_labels)} — "
                               f"hạn giữ chỗ: {result.hold_expiry}. Link: {result.payment_url}")
                    except Exception as notify_error:
                        print(f"[{item_id}] notify() failed after a successful lock "
                              f"(seats are held regardless): {notify_error}")
                    break
            if locked:
                return
        except Exception as e:
            # Any transient error (unknown provider name, HTTP error inside the real
            # BetaProvider's list_cinemas/list_showtimes, a Playwright error, ...) must
            # NOT be allowed to propagate and kill this daemon thread silently (finding
            # C1) -- the caller (telegram_bot.Bot) still believes the camp is running
            # (instant_threads[item_id] stays populated, /status still shows "instant")
            # unless we notify and keep looping instead of dying.
            notify(f"[{item_id}] Lỗi camp loop: {e}")

        stop_event.wait(poll_interval_seconds)
