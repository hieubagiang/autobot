# Cinema Ticket-Hold Bot (Beta Cinemas first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `cinema_booking/`, a provider-abstracted bot that camps a movie showtime (by movie + cinema-priority list + date range), auto-selects and holds the best-scoring seat block the instant one frees up, and hands off to the user via Telegram to pay within the hold window — first provider is Beta Cinemas.

**Architecture:** A pure-logic core (`types.py`, `provider.py` interface, `scoring.py`, `state.py`, `control.py`) that never imports a specific cinema chain, tested entirely with a `FakeProvider` test double. `providers/beta.py` is the one concrete implementation: public showtime search via plain HTTP (confirmed to work without a browser), everything requiring a session (login, seat map, seat lock) via Playwright driving a persistent, already-authenticated browser profile. `telegram_bot.py` wires it to Telegram commands, mirroring the existing `xeca_telegram_bot.py` pattern.

**Tech Stack:** Python 3.11+, `pytest`, `requests` (public API calls), `playwright` (new dependency — session-backed browser automation), `python-telegram` via raw `requests` calls to the Bot API (same as `xeca_telegram_bot.py`, no SDK).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-cinema-booking-bot-design.md` — every task below implements a piece of it; re-read it if a task's rationale is unclear.
- **Never automate solving a CAPTCHA or any human-verification challenge** (image CAPTCHA, 2FA, unrecognized WAF/security-checkpoint page). Any provider code that detects one must stop and call its `notify` callback, never attempt to pass it.
- Login is always a manual, one-time action by the human in a persistent browser profile; providers only ever *reuse* that session, never type a password into a third-party auth form (Facebook, Google, etc.) on the human's behalf.
- All new code lives under `cinema_booking/` (package + `providers/` + `tests/`) — nothing spills into the flat repo root the way `xeca_*.py` does today.
- Bot never auto-pays. `lock_seats()` succeeding is the terminal automated action; payment is always a manual step the human does via the link the bot sends.
- Python: dataclasses + `Enum` for all shared types (no bare dicts/strings crossing module boundaries for `Cinema`/`Showtime`/`Seat`/`SeatMap`/`LockResult`).

---

## Task 1: Shared types (`types.py`)

**Files:**
- Create: `cinema_booking/__init__.py` (empty)
- Create: `cinema_booking/types.py`
- Test: `cinema_booking/tests/__init__.py` (empty)
- Test: `cinema_booking/tests/test_types.py`

**Interfaces:**
- Produces: `SeatZone` (Enum: `STANDARD`, `VIP`, `SWEETBOX`), `SeatStatus` (Enum: `AVAILABLE`, `HELD`, `SOLD`, `RESERVED`), `Cinema(id, name, city, provider)`, `Showtime(id, movie, cinema, start_time, date)`, `Seat(id, label, row, col, zone, price, status)`, `SeatMap(rows, seats_by_row)` where `rows: list[str]` is front-to-back row-label order and `seats_by_row: dict[str, list[Seat]]` lists each row's seats in physical left-to-right order (this list order is the source of truth for position — NOT the printed seat number, since real cinemas (confirmed on CGV) sometimes number seats right-to-left within a row), `LockResult(success, hold_expiry=None, payment_url=None, error=None)`.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_types.py
from cinema_booking.types import (
    Cinema, LockResult, Seat, SeatMap, SeatStatus, SeatZone, Showtime,
)


def test_seat_map_row_order_is_left_to_right_not_printed_number():
    # Mirrors a real CGV row: seat numbers descend left-to-right (A18..A1).
    seats = [
        Seat(id="loc-18", label="A18", row="A", col=18, zone=SeatZone.STANDARD,
             price=105000, status=SeatStatus.AVAILABLE),
        Seat(id="loc-17", label="A17", row="A", col=17, zone=SeatZone.STANDARD,
             price=105000, status=SeatStatus.AVAILABLE),
    ]
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": seats})
    assert seat_map.seats_by_row["A"][0].label == "A18"
    assert seat_map.seats_by_row["A"][1].label == "A17"


def test_showtime_holds_its_cinema():
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema,
                         start_time="09:00", date="2026-08-12")
    assert showtime.cinema.name == "Beta Tây Sơn"


def test_lock_result_defaults_to_no_extras():
    result = LockResult(success=False, error="sold out")
    assert result.hold_expiry is None
    assert result.payment_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cinema_booking'` (or `types` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/types.py
from dataclasses import dataclass
from enum import Enum


class SeatZone(Enum):
    STANDARD = "standard"
    VIP = "vip"
    SWEETBOX = "sweetbox"


class SeatStatus(Enum):
    AVAILABLE = "available"
    HELD = "held"
    SOLD = "sold"
    RESERVED = "reserved"


@dataclass(frozen=True)
class Cinema:
    id: str
    name: str
    city: str
    provider: str


@dataclass(frozen=True)
class Showtime:
    id: str
    movie: str
    cinema: Cinema
    start_time: str  # "HH:MM"
    date: str  # "YYYY-MM-DD"


@dataclass(frozen=True)
class Seat:
    id: str  # opaque provider-specific identifier needed to actually lock this seat
    label: str  # display name, e.g. "A18"
    row: str
    col: int  # printed seat number — informational only, NOT the physical position
    zone: SeatZone
    price: int
    status: SeatStatus


@dataclass(frozen=True)
class SeatMap:
    rows: list[str]
    seats_by_row: dict[str, list[Seat]]


@dataclass(frozen=True)
class LockResult:
    success: bool
    hold_expiry: str | None = None
    payment_url: str | None = None
    error: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_types.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/__init__.py cinema_booking/types.py cinema_booking/tests/__init__.py cinema_booking/tests/test_types.py
git commit -m "feat(cinema_booking): add provider-agnostic shared types"
```

---

## Task 2: Provider interface + `FakeProvider` test double (`provider.py`)

**Files:**
- Create: `cinema_booking/provider.py`
- Test: `cinema_booking/tests/test_provider.py`

**Interfaces:**
- Consumes: `Cinema`, `Showtime`, `SeatMap`, `Seat`, `LockResult` from Task 1.
- Produces: abstract `CinemaProvider` with methods `is_logged_in() -> bool`, `list_cinemas() -> list[Cinema]`, `list_showtimes(cinema: Cinema, movie_query: str, date_range: tuple[str, str]) -> list[Showtime]`, `get_seat_map(showtime: Showtime) -> SeatMap`, `lock_seats(showtime: Showtime, seats: list[Seat]) -> LockResult`. Also `FakeProvider(cinemas=None, showtimes=None, seat_maps=None, lock_result=None, logged_in=True)` — a concrete `CinemaProvider` for tests. `get_seat_map` pops one entry off an internal queue per call (repeats the last one once the queue is empty) and records every call in `self.get_seat_map_calls: list[Showtime]`; `lock_seats` returns the configured `lock_result` and records `(showtime, seats)` tuples in `self.lock_seats_calls`.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_provider.py
from cinema_booking.provider import CinemaProvider, FakeProvider
from cinema_booking.types import Cinema, LockResult, SeatMap, Showtime


def make_cinema():
    return Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")


def test_fake_provider_is_a_real_cinema_provider():
    assert isinstance(FakeProvider(), CinemaProvider)


def test_fake_provider_reports_configured_login_state():
    assert FakeProvider(logged_in=False).is_logged_in() is False
    assert FakeProvider(logged_in=True).is_logged_in() is True


def test_fake_provider_get_seat_map_pops_queue_then_repeats_last():
    cinema = make_cinema()
    showtime = Showtime(id="s1", movie="M", cinema=cinema, start_time="09:00", date="2026-08-12")
    empty_map = SeatMap(rows=[], seats_by_row={})
    full_map = SeatMap(rows=["A"], seats_by_row={"A": []})
    provider = FakeProvider(seat_maps=[empty_map, full_map])

    assert provider.get_seat_map(showtime) is empty_map
    assert provider.get_seat_map(showtime) is full_map
    assert provider.get_seat_map(showtime) is full_map  # queue exhausted, repeats last
    assert provider.get_seat_map_calls == [showtime, showtime, showtime]


def test_fake_provider_lock_seats_returns_configured_result_and_records_call():
    cinema = make_cinema()
    showtime = Showtime(id="s1", movie="M", cinema=cinema, start_time="09:00", date="2026-08-12")
    expected = LockResult(success=True, hold_expiry="2026-08-12T09:05:00")
    provider = FakeProvider(lock_result=expected)

    result = provider.lock_seats(showtime, [])
    assert result is expected
    assert provider.lock_seats_calls == [(showtime, [])]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cinema_booking.provider'`

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/provider.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_provider.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/provider.py cinema_booking/tests/test_provider.py
git commit -m "feat(cinema_booking): add CinemaProvider interface and FakeProvider test double"
```

---

## Task 3: Vertical seat score (`scoring.py`)

**Files:**
- Create: `cinema_booking/scoring.py`
- Test: `cinema_booking/tests/test_scoring.py`

**Interfaces:**
- Produces: `vertical_score(row_index: int, total_rows: int) -> float`. `row_index` and `total_rows` are 1-indexed (row 1 = closest to the screen). Peak preference is at `peak_row = round(2 * total_rows / 3)`. Rows past the peak (further from the screen) lose `BACK_PENALTY_PER_ROW = 0.4` per row (normalized over the rows behind the peak); rows short of the peak lose `FRONT_PENALTY_PER_ROW = 1.0` per row (normalized over the rows in front of the peak) — this asymmetry (back cheaper than front) is the exact rule confirmed with the user during brainstorming.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_scoring.py
from cinema_booking.scoring import vertical_score


def test_peak_row_scores_highest():
    # 12 rows -> peak_row = round(2*12/3) = 8
    assert vertical_score(8, 12) == 1.0


def test_row_behind_peak_scores_higher_than_equal_distance_row_in_front():
    # peak=8 on a 12-row map: row 9 is 1 behind peak, row 7 is 1 in front.
    behind = vertical_score(9, 12)
    front = vertical_score(7, 12)
    assert behind > front
    assert behind < 1.0
    assert front < 1.0


def test_single_row_always_scores_perfectly():
    assert vertical_score(1, 1) == 1.0


def test_front_row_scores_lower_than_back_row():
    total_rows = 12
    assert vertical_score(1, total_rows) < vertical_score(total_rows, total_rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cinema_booking.scoring'`

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/scoring.py
FRONT_PENALTY_PER_ROW = 1.0
BACK_PENALTY_PER_ROW = 0.4


def vertical_score(row_index: int, total_rows: int) -> float:
    if total_rows <= 1:
        return 1.0
    peak_row = max(1, min(total_rows, round(2 * total_rows / 3)))
    if row_index >= peak_row:
        span = max(total_rows - peak_row, 1)
        return 1.0 - BACK_PENALTY_PER_ROW * (row_index - peak_row) / span
    span = max(peak_row - 1, 1)
    return 1.0 - FRONT_PENALTY_PER_ROW * (peak_row - row_index) / span
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/scoring.py cinema_booking/tests/test_scoring.py
git commit -m "feat(cinema_booking): add asymmetric vertical seat scoring"
```

---

## Task 4: Horizontal center-band + combined sort key (`scoring.py`)

**Files:**
- Modify: `cinema_booking/scoring.py`
- Modify: `cinema_booking/tests/test_scoring.py`

**Interfaces:**
- Consumes: `vertical_score` from Task 3.
- Produces: `is_center_half(col_index: int, total_cols: int) -> bool` (`col_index` 1-indexed physical left-to-right position; true when `total_cols/4 <= col_index <= 3*total_cols/4`). `seat_sort_key(row_index, total_rows, col_index, total_cols) -> tuple[int, float, float]` returning `(1 if in center-half else 0, vertical_score(...), -abs(col_index - center_col))` — sorting a list of these descending gives center-half seats first, then best vertical score, then closest to center, exactly reproducing "prefer center-half, else fall back to vertical-then-spread-outward" with no special-case branching.

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_scoring.py
from cinema_booking.scoring import is_center_half, seat_sort_key


def test_is_center_half_boundaries_on_20_cols():
    # total_cols=20 -> center half is cols 5..15 inclusive
    assert is_center_half(5, 20) is True
    assert is_center_half(15, 20) is True
    assert is_center_half(4, 20) is False
    assert is_center_half(16, 20) is False


def test_sort_key_prefers_center_half_over_better_vertical_score():
    total_rows, total_cols = 12, 20
    center_seat_ok_vertical = seat_sort_key(row_index=6, total_rows=total_rows,
                                             col_index=10, total_cols=total_cols)
    edge_seat_best_vertical = seat_sort_key(row_index=8, total_rows=total_rows,
                                             col_index=1, total_cols=total_cols)
    assert center_seat_ok_vertical > edge_seat_best_vertical


def test_sort_key_falls_back_to_vertical_then_center_distance_outside_band():
    total_rows, total_cols = 12, 20
    # Both outside the center-half band (cols 5..15); col 16 is closer to center than col 20.
    closer_to_center = seat_sort_key(8, total_rows, 16, total_cols)
    farther_from_center = seat_sort_key(8, total_rows, 20, total_cols)
    assert closer_to_center > farther_from_center
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: FAIL — `is_center_half`/`seat_sort_key` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to cinema_booking/scoring.py

def is_center_half(col_index: int, total_cols: int) -> bool:
    lower = total_cols / 4
    upper = 3 * total_cols / 4
    return lower <= col_index <= upper


def seat_sort_key(row_index: int, total_rows: int, col_index: int,
                   total_cols: int) -> tuple[int, float, float]:
    center_col = (total_cols + 1) / 2
    return (
        1 if is_center_half(col_index, total_cols) else 0,
        vertical_score(row_index, total_rows),
        -abs(col_index - center_col),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/scoring.py cinema_booking/tests/test_scoring.py
git commit -m "feat(cinema_booking): add horizontal center-band scoring and combined sort key"
```

---

## Task 5: Isolated-single-gap rule (`scoring.py`)

**Files:**
- Modify: `cinema_booking/scoring.py`
- Modify: `cinema_booking/tests/test_scoring.py`

**Interfaces:**
- Consumes: `Seat`, `SeatStatus` from Task 1.
- Produces: `leaves_isolated_gap(row_seats: list[Seat], start_idx: int, length: int) -> bool` — true if taking the block `row_seats[start_idx:start_idx+length]` would leave exactly one isolated empty seat immediately to its left or right (CGV's own `checkleftright()` rule: a lone empty seat has an available neighbor on the block side but a non-available — sold/held/off-the-row-edge — neighbor beyond that).

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_scoring.py
from cinema_booking.types import Seat, SeatStatus, SeatZone
from cinema_booking.scoring import leaves_isolated_gap


def seat(status):
    return Seat(id="x", label="X", row="A", col=1, zone=SeatZone.STANDARD,
                price=100000, status=status)


def test_no_gap_when_block_is_at_row_start():
    row = [seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE), seat(SeatStatus.SOLD)]
    assert leaves_isolated_gap(row, start_idx=0, length=2) is False


def test_isolated_single_seat_on_the_right_is_rejected():
    # [taken, taken, LONE EMPTY, sold] -> index 2 would be an isolated single gap.
    row = [seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE),
           seat(SeatStatus.AVAILABLE), seat(SeatStatus.SOLD)]
    assert leaves_isolated_gap(row, start_idx=0, length=2) is True


def test_two_empty_seats_on_the_right_is_not_isolated():
    # [taken, taken, empty, empty] -> the two empties aren't a lone gap.
    row = [seat(SeatStatus.AVAILABLE)] * 4
    assert leaves_isolated_gap(row, start_idx=0, length=2) is False


def test_isolated_single_seat_on_the_left_is_rejected():
    # [sold, LONE EMPTY, taken, taken] -> index 1 is isolated.
    row = [seat(SeatStatus.SOLD), seat(SeatStatus.AVAILABLE),
           seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE)]
    assert leaves_isolated_gap(row, start_idx=1, length=2) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: FAIL — `leaves_isolated_gap` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to cinema_booking/scoring.py
from cinema_booking.types import Seat, SeatStatus


def leaves_isolated_gap(row_seats: list, start_idx: int, length: int) -> bool:
    def is_available(idx: int) -> bool:
        return 0 <= idx < len(row_seats) and row_seats[idx].status == SeatStatus.AVAILABLE

    left = start_idx - 1
    if is_available(left) and not is_available(left - 1):
        return True
    right = start_idx + length
    if is_available(right) and not is_available(right + 1):
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/scoring.py cinema_booking/tests/test_scoring.py
git commit -m "feat(cinema_booking): reject seat blocks that leave an isolated single gap"
```

---

## Task 6: Candidate blocks + sweetbox fallback (`scoring.py`)

**Files:**
- Modify: `cinema_booking/scoring.py`
- Modify: `cinema_booking/tests/test_scoring.py`

**Interfaces:**
- Consumes: `SeatMap`, `Seat`, `SeatZone`, `SeatStatus` (Task 1); `seat_sort_key`, `leaves_isolated_gap` (Tasks 4–5).
- Produces: `find_candidate_blocks(seat_map: SeatMap, quantity: int, zone: SeatZone | None = None) -> list[dict]` — each dict is `{"seats": list[Seat], "key": tuple}`, one per legal contiguous same-zone run of `quantity` available seats (illegal = leaves an isolated gap, or spans more than one zone, or `zone` given and doesn't match). `pick_best_block(seat_map: SeatMap, quantity: int, prefer_sweetbox: bool = False) -> list[Seat] | None` — tries `SWEETBOX`-only candidates first when `prefer_sweetbox`, falls back to unrestricted candidates if none qualify (or if `prefer_sweetbox` is false), returns the max-`key` block's seats, or `None` if there are no legal candidates at all.

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_scoring.py
from cinema_booking.scoring import find_candidate_blocks, pick_best_block
from cinema_booking.types import SeatMap


def make_row(statuses, zone=SeatZone.STANDARD, row="A"):
    return [
        Seat(id=f"{row}{i}", label=f"{row}{i}", row=row, col=i, zone=zone,
             price=100000, status=status)
        for i, status in enumerate(statuses, start=1)
    ]


def test_find_candidate_blocks_skips_sold_seats_and_mixed_zones():
    row_a = make_row([SeatStatus.AVAILABLE, SeatStatus.SOLD, SeatStatus.AVAILABLE, SeatStatus.AVAILABLE])
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": row_a})
    candidates = find_candidate_blocks(seat_map, quantity=2)
    # Only seats 3-4 (index 2-3) form a legal pair; seats 1-2 are blocked by the sold seat.
    assert len(candidates) == 1
    assert [s.label for s in candidates[0]["seats"]] == ["A3", "A4"]


def test_pick_best_block_returns_none_when_sold_out():
    row_a = make_row([SeatStatus.SOLD, SeatStatus.SOLD])
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": row_a})
    assert pick_best_block(seat_map, quantity=2) is None


def test_pick_best_block_prefers_sweetbox_when_flagged():
    row_a = make_row([SeatStatus.AVAILABLE] * 4, zone=SeatZone.STANDARD, row="A")
    row_b = make_row([SeatStatus.AVAILABLE] * 4, zone=SeatZone.SWEETBOX, row="B")
    seat_map = SeatMap(rows=["A", "B"], seats_by_row={"A": row_a, "B": row_b})
    block = pick_best_block(seat_map, quantity=2, prefer_sweetbox=True)
    assert all(s.zone == SeatZone.SWEETBOX for s in block)


def test_pick_best_block_falls_back_to_standard_when_no_sweetbox_available():
    row_a = make_row([SeatStatus.AVAILABLE] * 4, zone=SeatZone.STANDARD, row="A")
    row_b = make_row([SeatStatus.SOLD] * 4, zone=SeatZone.SWEETBOX, row="B")
    seat_map = SeatMap(rows=["A", "B"], seats_by_row={"A": row_a, "B": row_b})
    block = pick_best_block(seat_map, quantity=2, prefer_sweetbox=True)
    assert block is not None
    assert all(s.zone == SeatZone.STANDARD for s in block)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: FAIL — `find_candidate_blocks`/`pick_best_block` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to cinema_booking/scoring.py
from cinema_booking.types import SeatMap, SeatZone


def find_candidate_blocks(seat_map: SeatMap, quantity: int, zone: SeatZone | None = None) -> list[dict]:
    candidates = []
    total_rows = len(seat_map.rows)
    for row_index, row_label in enumerate(seat_map.rows, start=1):
        row_seats = seat_map.seats_by_row[row_label]
        total_cols = len(row_seats)
        for start in range(0, total_cols - quantity + 1):
            block = row_seats[start:start + quantity]
            if any(s.status != SeatStatus.AVAILABLE for s in block):
                continue
            if len({s.zone for s in block}) > 1:
                continue
            if zone is not None and block[0].zone != zone:
                continue
            if leaves_isolated_gap(row_seats, start, quantity):
                continue
            keys = [
                seat_sort_key(row_index, total_rows, start + i + 1, total_cols)
                for i in range(quantity)
            ]
            candidates.append({"seats": block, "key": min(keys)})
    return candidates


def pick_best_block(seat_map: SeatMap, quantity: int, prefer_sweetbox: bool = False) -> list | None:
    if prefer_sweetbox:
        sweetbox_candidates = find_candidate_blocks(seat_map, quantity, zone=SeatZone.SWEETBOX)
        if sweetbox_candidates:
            return max(sweetbox_candidates, key=lambda c: c["key"])["seats"]
    candidates = find_candidate_blocks(seat_map, quantity)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["key"])["seats"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_scoring.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/scoring.py cinema_booking/tests/test_scoring.py
git commit -m "feat(cinema_booking): add candidate-block search with sweetbox-first fallback"
```

---

## Task 7: Watchlist persistence (`state.py`)

**Files:**
- Create: `cinema_booking/state.py`
- Test: `cinema_booking/tests/test_state.py`

**Interfaces:**
- Produces: `DEFAULT_STATE_FILE = "cinema_booking_state.json"`. `add_ticket_request(provider: str, movie_query: str, date_range: list[str], quantity: int = 2, prefer_sweetbox: bool = False, cinema_priority: list[str] | None = None, state_file: str = DEFAULT_STATE_FILE) -> dict` (returns the created item, including a generated `"id"` and `"status": "pending"`). `list_ticket_requests(state_file: str = DEFAULT_STATE_FILE) -> list[dict]`. `get_item(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> dict | None`. `update_item(item_id: str, path: str = DEFAULT_STATE_FILE, **fields) -> dict | None` (merges `fields` into the stored item). `remove_ticket_request(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_state.py
import json

from cinema_booking.state import (
    add_ticket_request, get_item, list_ticket_requests,
    remove_ticket_request, update_item,
)


def test_add_then_list_round_trips(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(
        provider="beta", movie_query="Người Nhện",
        date_range=["2026-08-12", "2026-08-12"],
        cinema_priority=["Beta Tây Sơn"], state_file=state_file,
    )
    assert item["status"] == "pending"
    assert item["quantity"] == 2
    assert item["prefer_sweetbox"] is False

    items = list_ticket_requests(state_file)
    assert len(items) == 1
    assert items[0]["id"] == item["id"]


def test_get_item_returns_none_when_missing(tmp_path):
    state_file = str(tmp_path / "state.json")
    add_ticket_request(provider="beta", movie_query="X", date_range=["2026-08-12", "2026-08-12"],
                        state_file=state_file)
    assert get_item("does-not-exist", state_file) is None


def test_update_item_merges_fields(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    updated = update_item(item["id"], path=state_file, status="pending_payment", hold_expiry="soon")
    assert updated["status"] == "pending_payment"
    assert updated["hold_expiry"] == "soon"
    # Persisted, not just returned:
    reloaded = get_item(item["id"], state_file)
    assert reloaded["status"] == "pending_payment"


def test_remove_ticket_request(tmp_path):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    assert remove_ticket_request(item["id"], state_file) is True
    assert list_ticket_requests(state_file) == []
    assert remove_ticket_request(item["id"], state_file) is False


def test_state_file_is_plain_json(tmp_path):
    state_file = str(tmp_path / "state.json")
    add_ticket_request(provider="beta", movie_query="X",
                        date_range=["2026-08-12", "2026-08-12"], state_file=state_file)
    with open(state_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "items" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cinema_booking.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/state.py
import json
import os
import uuid
from datetime import datetime, timezone

DEFAULT_STATE_FILE = "cinema_booking_state.json"


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"items": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_ticket_request(provider: str, movie_query: str, date_range: list[str],
                        quantity: int = 2, prefer_sweetbox: bool = False,
                        cinema_priority: list[str] | None = None,
                        state_file: str = DEFAULT_STATE_FILE) -> dict:
    data = _load(state_file)
    item = {
        "id": uuid.uuid4().hex[:8],
        "provider": provider,
        "movie_query": movie_query,
        "date_range": list(date_range),
        "quantity": quantity,
        "prefer_sweetbox": prefer_sweetbox,
        "cinema_priority": cinema_priority or [],
        "status": "pending",
        "instant": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["items"].append(item)
    _save(data, state_file)
    return item


def list_ticket_requests(state_file: str = DEFAULT_STATE_FILE) -> list[dict]:
    return _load(state_file)["items"]


def get_item(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> dict | None:
    for item in list_ticket_requests(state_file):
        if item["id"] == item_id:
            return item
    return None


def update_item(item_id: str, path: str = DEFAULT_STATE_FILE, **fields) -> dict | None:
    data = _load(path)
    for item in data["items"]:
        if item["id"] == item_id:
            item.update(fields)
            _save(data, path)
            return item
    return None


def remove_ticket_request(item_id: str, state_file: str = DEFAULT_STATE_FILE) -> bool:
    data = _load(state_file)
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    _save(data, state_file)
    return len(data["items"]) < before
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/state.py cinema_booking/tests/test_state.py
git commit -m "feat(cinema_booking): add JSON watchlist persistence"
```

---

## Task 8: Date ranking — Monday/Wednesday price preference (`control.py`)

**Files:**
- Create: `cinema_booking/control.py`
- Test: `cinema_booking/tests/test_control.py`

**Interfaces:**
- Produces: `rank_dates(date_range: list[str]) -> list[str]` — `date_range` is `["YYYY-MM-DD", "YYYY-MM-DD"]` (may be the same date twice for a single-date request). Returns every date in the inclusive range, Monday/Wednesday dates first (in ascending date order among themselves), then all other dates (ascending date order).

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_control.py
from cinema_booking.control import rank_dates


def test_rank_dates_single_date_returns_just_that_date():
    assert rank_dates(["2026-08-12", "2026-08-12"]) == ["2026-08-12"]


def test_rank_dates_prefers_monday_and_wednesday():
    # 2026-08-10 is Mon, 11 Tue, 12 Wed, 13 Thu.
    ranked = rank_dates(["2026-08-10", "2026-08-13"])
    assert ranked == ["2026-08-10", "2026-08-12", "2026-08-11", "2026-08-13"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cinema_booking.control'`

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/control.py
from datetime import date, timedelta

MON_WED = {0, 2}  # date.weekday(): Monday=0 .. Sunday=6


def _daterange(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def rank_dates(date_range: list[str]) -> list[str]:
    start = date.fromisoformat(date_range[0])
    end = date.fromisoformat(date_range[1])
    days = list(_daterange(start, end))
    ranked = sorted(days, key=lambda d: (0 if d.weekday() in MON_WED else 1, d))
    return [d.isoformat() for d in ranked]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/control.py cinema_booking/tests/test_control.py
git commit -m "feat(cinema_booking): rank candidate dates by Mon/Wed price preference"
```

---

## Task 9: Provider registry + showtime-candidate ranking (`control.py`)

**Files:**
- Modify: `cinema_booking/control.py`
- Modify: `cinema_booking/tests/test_control.py`

**Interfaces:**
- Consumes: `CinemaProvider`, `FakeProvider` (Task 2); `Cinema`, `Showtime` (Task 1); `rank_dates` (Task 8).
- Produces: `get_provider(name: str) -> CinemaProvider` (raises `ValueError` for unknown names; imports `cinema_booking.providers.beta.BetaProvider` lazily so importing `control.py` never requires Playwright to be installed). `DEFAULT_CINEMA_PRIORITY: dict[str, list[str]]` — per the spec, `{"beta": ["Beta Tây Sơn"]}` for now (the only default confirmed with the user so far); `telegram_bot.cmd_add` (Task 16) applies this when a new item doesn't get an explicit cinema priority, since an item with an empty `cinema_priority` would make `rank_showtime_candidates` always return zero candidates — silently doing nothing forever is worse than a reasonable default the user can override with `/setcinemapriority`. `rank_showtime_candidates(provider: CinemaProvider, cinema_priority: list[str], movie_query: str, date_range: list[str]) -> list[Showtime]` — cinema priority is the primary sort key (all of the first-priority cinema's candidates come before any of the second-priority cinema's, full stop), Mon/Wed-ranked date is secondary within one cinema. Cinema names not found via `provider.list_cinemas()` are silently skipped (not an error — the priority list may include cinemas from a different city/provider by mistake).

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_control.py
from cinema_booking.control import get_provider, rank_showtime_candidates
from cinema_booking.provider import FakeProvider
from cinema_booking.types import Cinema, Showtime
import pytest


def test_get_provider_unknown_name_raises():
    with pytest.raises(ValueError):
        get_provider("not-a-real-provider")


def test_default_cinema_priority_has_beta_tay_son_for_beta():
    from cinema_booking.control import DEFAULT_CINEMA_PRIORITY

    assert DEFAULT_CINEMA_PRIORITY["beta"] == ["Beta Tây Sơn"]


def make_showtime(cinema, date, sid):
    return Showtime(id=sid, movie="Người Nhện", cinema=cinema, start_time="09:00", date=date)


def test_rank_showtime_candidates_orders_by_cinema_priority_first():
    cinema_a = Cinema(id="a", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    cinema_b = Cinema(id="b", name="Beta Mỹ Đình", city="Hà Nội", provider="beta")
    # cinema_b's Monday showtime would out-rank cinema_a's Tuesday one on date alone,
    # but cinema_a is higher cinema-priority so ALL of its candidates must come first.
    showtimes = [
        make_showtime(cinema_a, "2026-08-11", "a-tue"),   # Tuesday
        make_showtime(cinema_b, "2026-08-10", "b-mon"),   # Monday
    ]
    provider = FakeProvider(cinemas=[cinema_a, cinema_b], showtimes=showtimes)

    candidates = rank_showtime_candidates(
        provider, cinema_priority=["Beta Tây Sơn", "Beta Mỹ Đình"],
        movie_query="Người Nhện", date_range=["2026-08-10", "2026-08-11"],
    )
    assert [s.id for s in candidates] == ["a-tue", "b-mon"]


def test_rank_showtime_candidates_skips_unknown_cinema_names():
    cinema_a = Cinema(id="a", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtimes = [make_showtime(cinema_a, "2026-08-10", "a-mon")]
    provider = FakeProvider(cinemas=[cinema_a], showtimes=showtimes)

    candidates = rank_showtime_candidates(
        provider, cinema_priority=["Rạp Không Tồn Tại", "Beta Tây Sơn"],
        movie_query="Người Nhện", date_range=["2026-08-10", "2026-08-10"],
    )
    assert [s.id for s in candidates] == ["a-mon"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: FAIL — `get_provider`/`rank_showtime_candidates` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to cinema_booking/control.py
from cinema_booking.provider import CinemaProvider
from cinema_booking.types import Showtime


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/control.py cinema_booking/tests/test_control.py
git commit -m "feat(cinema_booking): add provider registry and cinema-then-date showtime ranking"
```

---

## Task 10: Camp loop (`control.py`)

**Files:**
- Modify: `cinema_booking/control.py`
- Modify: `cinema_booking/tests/test_control.py`

**Interfaces:**
- Consumes: `pick_best_block` (Task 6); `get_item`, `update_item` (Task 7); `rank_showtime_candidates` (Task 9); `get_provider` (Task 9).
- Produces: `instant_camp_loop(item_id: str, stop_event: threading.Event, notify: Callable[[str], None], state_file: str = DEFAULT_STATE_FILE, poll_interval_seconds: float = 5.0) -> None`. Loop body per outer iteration: if not logged in, `notify(...)` and wait; otherwise rank candidates, and for the first candidate showtime whose seat map yields a `pick_best_block` result, call `lock_seats`; on success, `update_item(..., status="pending_payment", hold_expiry=..., payment_url=..., seat_labels=[...])`, `notify(...)`, and return. Otherwise wait `poll_interval_seconds` (via `stop_event.wait`, so a caller can interrupt it) and loop again. Exits immediately, without polling anything, if `stop_event` is already set when the loop starts or becomes set between outer iterations.

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_control.py
import threading

from cinema_booking.control import instant_camp_loop
from cinema_booking.state import add_ticket_request, get_item
from cinema_booking.types import LockResult, Seat, SeatMap, SeatStatus, SeatZone


def make_seat(label, status=SeatStatus.AVAILABLE, zone=SeatZone.STANDARD, col=1):
    return Seat(id=label, label=label, row="A", col=col, zone=zone, price=100000, status=status)


def test_camp_loop_stops_immediately_if_already_stopped(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-10", "2026-08-10"], state_file=state_file)
    provider = FakeProvider(logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    stop_event.set()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file)

    assert notifications == []
    assert provider.get_seat_map_calls == []


def test_camp_loop_notifies_and_waits_when_not_logged_in(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="X",
                               date_range=["2026-08-10", "2026-08-10"], state_file=state_file)
    provider = FakeProvider(logged_in=False)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()

    def notify_then_stop(message):
        notifications.append(message)
        stop_event.set()  # simulate the operator being told and stopping the camp

    notifications = []
    instant_camp_loop(item["id"], stop_event, notify_then_stop, state_file=state_file,
                       poll_interval_seconds=0)

    assert len(notifications) == 1
    assert "chưa đăng nhập" in notifications[0]


def test_camp_loop_locks_best_block_and_updates_state_on_success(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    seat_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="2026-08-10T09:05:00", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime], seat_maps=[seat_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file)

    assert len(provider.lock_seats_calls) == 1
    assert len(notifications) == 1
    assert "A1" in notifications[0] and "A2" in notifications[0]
    updated = get_item(item["id"], state_file)
    assert updated["status"] == "pending_payment"
    assert updated["hold_expiry"] == "2026-08-10T09:05:00"
    assert updated["seat_labels"] == ["A1", "A2"]


def test_camp_loop_keeps_polling_until_a_seat_frees_up(tmp_path, monkeypatch):
    state_file = str(tmp_path / "state.json")
    item = add_ticket_request(provider="beta", movie_query="Người Nhện",
                               date_range=["2026-08-10", "2026-08-10"],
                               cinema_priority=["Beta Tây Sơn"], state_file=state_file)
    cinema = Cinema(id="c1", name="Beta Tây Sơn", city="Hà Nội", provider="beta")
    showtime = Showtime(id="s1", movie="Người Nhện", cinema=cinema, start_time="09:00", date="2026-08-10")
    sold_out_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1", status=SeatStatus.SOLD),
                                                            make_seat("A2", status=SeatStatus.SOLD)]})
    free_map = SeatMap(rows=["A"], seats_by_row={"A": [make_seat("A1"), make_seat("A2")]})
    lock_result = LockResult(success=True, hold_expiry="soon", payment_url="http://pay")
    provider = FakeProvider(cinemas=[cinema], showtimes=[showtime],
                             seat_maps=[sold_out_map, free_map],
                             lock_result=lock_result, logged_in=True)
    monkeypatch.setattr("cinema_booking.control.get_provider", lambda name: provider)

    stop_event = threading.Event()
    notifications = []
    instant_camp_loop(item["id"], stop_event, notifications.append, state_file=state_file,
                       poll_interval_seconds=0)

    assert len(provider.get_seat_map_calls) == 2
    assert len(notifications) == 1
```

Add the missing imports at the top of `test_control.py`: `from cinema_booking.provider import FakeProvider` and `from cinema_booking.types import Cinema, LockResult, Seat, SeatMap, SeatStatus, SeatZone, Showtime` (merge with the existing `Cinema, Showtime` import from Task 9).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: FAIL — `instant_camp_loop` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to cinema_booking/control.py
from cinema_booking.scoring import pick_best_block
from cinema_booking.state import DEFAULT_STATE_FILE, get_item, update_item


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_control.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/control.py cinema_booking/tests/test_control.py
git commit -m "feat(cinema_booking): add instant_camp_loop that locks the first qualifying seat block"
```

---

## Task 11: Beta Cinemas public showtime search (`providers/beta.py`)

**Files:**
- Create: `cinema_booking/providers/__init__.py` (empty)
- Create: `cinema_booking/providers/beta.py`
- Create: `cinema_booking/tests/fixtures/beta_home.html` (see Step 0)
- Create: `cinema_booking/tests/fixtures/beta_showtimes_response.json` (see Step 0)
- Test: `cinema_booking/tests/test_providers_beta.py`

**Interfaces:**
- Consumes: `Cinema`, `Showtime` (Task 1); `CinemaProvider` (Task 2).
- Produces: `BetaProvider()` — a `CinemaProvider`. This task implements only `list_cinemas()` and `list_showtimes()` (the two methods confirmed today to work via plain HTTP, no login, no browser); `is_logged_in`, `get_seat_map`, `lock_seats` are implemented in Tasks 13–15 and should raise `NotImplementedError` for now so the class is still instantiable. `list_cinemas()` fetches `GET https://betacinemas.vn/home.htm` and regex-parses every `ChooseCinema('<guid>', '<name>')` call into a `Cinema(id=guid, name=name, city="", provider="beta")` (city is unknown from this page alone — leave `""`, it's not used by any scoring/ranking logic, only `name` is matched against `cinema_priority`). `list_showtimes(cinema, movie_query, date_range)` first re-fetches `home.htm` to find a `viewsShowtimes('<cinemaId>', '<filmId>', '<filmName>', '<cinemaName>')` call whose `filmName` contains `movie_query` (case-insensitive substring match) — this is how a movie's GUID is discovered, there is no separate search-by-name endpoint — then calls `POST https://betacinemas.vn/Ajax.aspx/LoadShowtimesByFilm` with body `{"aData": [cinema.id, filmId, filmName]}`, and regex-parses every `bookingSeat('<cinemaName>', '<filmSessionId>', '<showId>', '<HH:MM>', '<dd/MM/yyyy>', '<filmName>', ...)` call out of the returned HTML-in-JSON into a `Showtime(id=showId, movie=filmName, cinema=cinema, start_time=<HH:MM>, date=<yyyy-MM-dd>)`, keeping only showtimes whose date falls inside `date_range`.

- [ ] **Step 0: Capture real fixtures (one-time, live research)**

Using the `chrome-devtools-mcp` skill against `https://betacinemas.vn/home.htm` (no login needed for this part):
1. `navigate_page` to `https://betacinemas.vn/home.htm`, `evaluate_script` to dump `document.documentElement.outerHTML`, save it verbatim to `cinema_booking/tests/fixtures/beta_home.html`.
2. From that page, extract one real `viewsShowtimes(...)` call's `cinemaId`/`filmId`/`filmName` (or reuse `381f745f-c110-4d0c-9117-3a79f36ba9c4` / `4d206616-6753-49e1-a21a-a95729e7e5fb` / `Người Nhện: Khởi Đầu Mới` captured on 2026-08-10, if that film is still showing — otherwise pick whatever is currently on `home.htm`).
3. Call `POST https://betacinemas.vn/Ajax.aspx/LoadShowtimesByFilm` with that triple as `aData` (via `evaluate_script`'s `fetch`, same as done during brainstorming), save the raw JSON response body verbatim to `cinema_booking/tests/fixtures/beta_showtimes_response.json`.

These fixtures make Steps 1–4 below fully offline and deterministic — no live network calls in the test suite.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_providers_beta.py
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
    assert len(cinemas) > 0
    assert any(c.name == "Beta Tây Sơn" for c in cinemas)
    assert all(c.provider == "beta" for c in cinemas)


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
    assert len(showtimes) > 0
    assert all(s.date == "2026-08-11" for s in showtimes)
    assert all(s.cinema is cinema for s in showtimes)
    assert any(s.start_time == "09:00" for s in showtimes)
```

Adjust the `"09:00"` assertion (and the date `"2026-08-11"`) to match whatever the actual captured fixture contains if the movie/date differ from what was captured on 2026-08-10 during brainstorming.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: FAIL — `cinema_booking.providers.beta` module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# cinema_booking/providers/beta.py
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
        for cinema_id, film_id, film_name, _cinema_name in VIEWS_SHOWTIMES_RE.findall(resp.text):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: PASS (2 tests). If the date/time assertions don't match your captured fixture, fix the assertions (not the regex) — the regex must match the real site's markup exactly as captured in Step 0.

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/providers/__init__.py cinema_booking/providers/beta.py cinema_booking/tests/test_providers_beta.py cinema_booking/tests/fixtures/beta_home.html cinema_booking/tests/fixtures/beta_showtimes_response.json
git commit -m "feat(cinema_booking): add Beta Cinemas public showtime search (no browser needed)"
```

---

## Task 12: Research seat map + lock endpoint (live spike, no shipped code)

**Files:**
- Create: `cinema_booking/tests/fixtures/beta_seat_map.html`
- Create: `cinema_booking/tests/fixtures/beta_lock_response.json`
- Modify: `docs/superpowers/specs/2026-08-10-cinema-booking-bot-design.md` (append findings)

This task produces no production code — it resolves the open items flagged in the spec's Beta risk section (seat DOM structure, lock endpoint, hold duration, isolated-gap-style rules) so Tasks 13 and 15 can be written for real instead of guessed.

- [ ] **Step 1: Log in and capture the seat map**

Using `chrome-devtools-mcp`: log in to `https://betacinemas.vn` via the Facebook-continue flow (manual — same as during brainstorming), navigate to a `/chon-ghe.htm?f=...&s=...` URL for a real, currently-on-sale showtime picked from Task 11's fixture. `evaluate_script` to dump `document.documentElement.outerHTML` and save it to `cinema_booking/tests/fixtures/beta_seat_map.html`. Inspect the seat elements' classes/attributes (mirror the approach used for CGV: `document.querySelectorAll` filtered to seat-like elements, dump `className` + `attributes` for a sample of each of the 5 legend states) and note the exact CSS classes/attribute names for: seat id, zone, price, and each of the 5 statuses (`Ghế trống`/`Ghế đang chọn`/`Ghế đang giữ`/`Ghế đã bán`/`Ghế đặt trước`).

- [ ] **Step 2: Capture the lock call**

Select two adjacent available standard seats (pick a low-demand showtime — late-night or far-future — same care as taken with CGV: never lock a seat on a showtime real customers are actively contending for). Trigger whatever "confirm seats" action the page exposes. Use `list_network_requests`/`get_network_request` to capture the exact lock request (URL, method, request body shape, request headers) and its response body. Save the response body to `cinema_booking/tests/fixtures/beta_lock_response.json`. Immediately look for a same-session cancel/release endpoint (mirror CGV's `ajaxdelete`) and call it right away to release the hold — do not let it sit for its full duration.

- [ ] **Step 3: Determine the hold duration**

Look for a countdown timer or expiry timestamp on the post-lock page (mirror CGV's `Countdown Clock` discovery: search the page's inline `<script>` text for a countdown initializer). If found before the hold naturally expires, note the exact duration. If a release endpoint exists (Step 2), you don't need to wait out a real expiry to confirm the mechanism exists — only to learn the number.

- [ ] **Step 4: Determine seat-adjacency rules**

Check whether Beta enforces anything like CGV's "no isolated single-seat gap" rule: attempt to select a single seat with empty seats on both sides where taking it would isolate a neighbor, and see whether the page blocks it (alert/validation) before you'd even reach the lock call. Note the finding (rule exists and its exact shape, or no such rule) — either way, `scoring.leaves_isolated_gap` (Task 5) already covers the "rule exists" case generically; if Beta has no such rule, no code change is needed, just remove `leaves_isolated_gap` from consideration if it turns out to be strictly wrong for Beta (unlikely — a rule that's *stricter* than reality only loses a few candidate seats, it never causes an illegal lock attempt).

- [ ] **Step 5: Append findings to the spec and commit**

Add a dated addendum under the spec's "Kết quả research cơ chế đặt vé Beta Cinemas" section with: the confirmed DOM structure/attribute names, the lock endpoint's method/URL/payload/response shape, the confirmed hold duration (or "still using the CGV-style 5-minute assumption, could not confirm — reason: ___" if genuinely blocked), and the adjacency-rule finding.

```bash
git add cinema_booking/tests/fixtures/beta_seat_map.html cinema_booking/tests/fixtures/beta_lock_response.json docs/superpowers/specs/2026-08-10-cinema-booking-bot-design.md
git commit -m "docs(cinema_booking): capture Beta Cinemas seat-map DOM and lock endpoint via live research"
```

---

## Task 13: Seat map parsing (`providers/beta.py`)

**Files:**
- Modify: `cinema_booking/providers/beta.py`
- Modify: `cinema_booking/tests/test_providers_beta.py`

**Interfaces:**
- Consumes: `beta_seat_map.html` fixture and its documented DOM structure (Task 12); `SeatMap`, `Seat`, `SeatZone`, `SeatStatus` (Task 1).
- Produces: a module-level pure function `parse_seat_map(html: str) -> SeatMap` (parses a `chon-ghe.htm` page's HTML into the shared `SeatMap` type — kept as a free function, not a method, specifically so it can be unit-tested against the fixture without a browser), and `BetaProvider.get_seat_map(showtime: Showtime) -> SeatMap` which drives Playwright to the showtime's `chon-ghe.htm` URL and calls `parse_seat_map` on the resulting page HTML.

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_providers_beta.py
@pytest.fixture
def beta_seat_map_html():
    return (FIXTURES / "beta_seat_map.html").read_text(encoding="utf-8")


def test_parse_seat_map_finds_all_five_statuses(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map
    from cinema_booking.types import SeatStatus

    seat_map = parse_seat_map(beta_seat_map_html)
    all_seats = [s for row in seat_map.seats_by_row.values() for s in row]
    assert len(all_seats) > 0
    statuses_seen = {s.status for s in all_seats}
    # At minimum, a real showing has both available and sold seats.
    assert SeatStatus.AVAILABLE in statuses_seen
    assert SeatStatus.SOLD in statuses_seen


def test_parse_seat_map_rows_are_in_screen_order(beta_seat_map_html):
    from cinema_booking.providers.beta import parse_seat_map

    seat_map = parse_seat_map(beta_seat_map_html)
    assert seat_map.rows == sorted(seat_map.rows)  # row labels are already front-to-back alphabetical
```

Replace the exact assertions above with whatever Task 12's research actually found once the fixture exists — these are the minimum properties any correct parse must satisfy regardless of Beta's exact markup, but add more specific assertions (e.g. a known seat's `id`/`zone`/`price`) once you have the real fixture in hand.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: FAIL — `parse_seat_map` not defined.

- [ ] **Step 3: Write minimal implementation**

This step's exact selectors depend on Task 12's findings. Starting point (adjust class names/attributes to match what Task 12 documented):

```python
# append to cinema_booking/providers/beta.py
from bs4 import BeautifulSoup

from cinema_booking.types import Seat, SeatMap, SeatStatus, SeatZone

STATUS_BY_CLASS = {
    "seat-empty": SeatStatus.AVAILABLE,
    "seat-holding": SeatStatus.HELD,
    "seat-sold": SeatStatus.SOLD,
    "seat-reserved": SeatStatus.RESERVED,
}
ZONE_BY_CLASS = {
    "seat-standard": SeatZone.STANDARD,
    "seat-vip": SeatZone.VIP,
    "seat-sweetbox": SeatZone.SWEETBOX,
}


def parse_seat_map(html: str) -> SeatMap:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[str] = []
    seats_by_row: dict[str, list[Seat]] = {}

    for row_el in soup.select(".seat-row"):  # placeholder selector — fix per Task 12 findings
        row_label = row_el.get("data-row", "").strip()
        if not row_label:
            continue
        rows.append(row_label)
        row_seats = []
        for seat_el in row_el.select(".seat"):
            classes = seat_el.get("class", [])
            status = next((v for k, v in STATUS_BY_CLASS.items() if k in classes), SeatStatus.SOLD)
            zone = next((v for k, v in ZONE_BY_CLASS.items() if k in classes), SeatZone.STANDARD)
            seat_id = seat_el.get("data-seat-id", "")
            label = seat_el.get_text(strip=True)
            col = int(seat_el.get("data-col", "0") or 0)
            price = int(seat_el.get("data-price", "0") or 0)
            row_seats.append(Seat(id=seat_id, label=label, row=row_label, col=col,
                                   zone=zone, price=price, status=status))
        seats_by_row[row_label] = row_seats

    return SeatMap(rows=rows, seats_by_row=seats_by_row)
```

Add `beautifulsoup4` to `requirements.txt` before running tests: append a line `beautifulsoup4` to the repo-root `requirements.txt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: PASS. If it fails, the selectors in Step 3 don't match the real fixture — open `beta_seat_map.html` and fix `STATUS_BY_CLASS`/`ZONE_BY_CLASS`/the CSS selectors to match what's actually there (this is expected — Step 3's code is a starting point, not a guess to leave unfixed).

- [ ] **Step 5: Implement `get_seat_map` and commit**

```python
# add to BetaProvider in cinema_booking/providers/beta.py, replacing the placeholder
def get_seat_map(self, showtime: Showtime) -> SeatMap:
    page = self._page()  # Playwright Page from the persistent authenticated context, set up in Task 14
    page.goto(f"https://betacinemas.vn/chon-ghe.htm?f={showtime.cinema.id}&s={showtime.id}")
    return parse_seat_map(page.content())
```

```bash
git add cinema_booking/providers/beta.py cinema_booking/tests/test_providers_beta.py requirements.txt
git commit -m "feat(cinema_booking): parse Beta Cinemas seat map from real DOM structure"
```

---

## Task 14: Login/session handling via Playwright (`providers/beta.py`)

**Files:**
- Modify: `cinema_booking/providers/beta.py`
- Modify: `cinema_booking/tests/test_providers_beta.py`

**Interfaces:**
- Produces: `BetaProvider.__init__(self, profile_dir: str = ".chrome_profiles/beta", notify: Callable[[str], None] | None = None)` launches (lazily, on first use — not in `__init__` itself, to keep unit tests of `list_cinemas`/`list_showtimes` free of any browser dependency) a Playwright `launch_persistent_context` at `profile_dir`. `BetaProvider._page()` returns the single reused `Page` (creating it on first call). `BetaProvider.is_logged_in() -> bool` navigates to `https://betacinemas.vn/home.htm` and returns whether the page shows a logged-in indicator (e.g. contains `"Xin chào:"` — confirm the exact marker against Task 12's captured HTML) rather than the guest "Đăng nhập / Đăng ký" header. `BetaProvider.login_via_facebook() -> bool` clicks the Facebook-login button, waits for either (a) the "Tiếp tục dưới tên ..." consent screen — clicks it and returns `True`, or (b) anything else (2FA, password re-entry, checkpoint) — calls `self.notify(...)` describing what it saw and returns `False` without clicking anything further. This method is for the manual smoke test in Step 3, never called automatically from `get_seat_map`/`lock_seats` (those must fail loudly via `is_logged_in()` returning `False` and the caller, `instant_camp_loop`, handling that — see Task 10 — not by silently trying to log in).

- [ ] **Step 1: Write the failing test (pure logic only — no real Playwright)**

```python
# append to cinema_booking/tests/test_providers_beta.py
def test_is_logged_in_reads_greeting_marker():
    from cinema_booking.providers.beta import _page_shows_logged_in

    assert _page_shows_logged_in("...Xin chào: Phạm Doãn Hiếu ...") is True
    assert _page_shows_logged_in("...ĐĂNG NHẬP ĐĂNG KÝ...") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: FAIL — `_page_shows_logged_in` not defined.

- [ ] **Step 3: Write the implementation**

This step **edits the existing `BetaProvider` class from Task 11 in place** — it does not redeclare the
class. A second top-level `class BetaProvider(...):` block appended to the file would silently rebind the
name and delete Task 11's `list_cinemas`/`list_showtimes`/`_find_film_id` and Task 13's `get_seat_map`
methods, since Python executes class statements top-to-bottom and the last one wins. Concretely:

1. Add this free function near the top of `cinema_booking/providers/beta.py` (module level, not inside the class), alongside the other imports:

```python
from playwright.sync_api import sync_playwright


def _page_shows_logged_in(html: str) -> bool:
    return "Xin chào:" in html
```

2. Inside the existing `BetaProvider` class body, add an `__init__` (it doesn't have one yet — Task 11's class only had method stubs) and a `_page()` helper:

```python
    def __init__(self, profile_dir: str = ".chrome_profiles/beta", notify=None):
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
```

3. **Replace** the Task-11 placeholder method body (`def is_logged_in(self): raise NotImplementedError`) in place with:

```python
    def is_logged_in(self) -> bool:
        page = self._page()
        page.goto("https://betacinemas.vn/home.htm")
        return _page_shows_logged_in(page.content())
```

4. Add a new method to the same class (there is no placeholder for this one — it's new):

```python
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
```

After this step, `BetaProvider` has exactly one `__init__`, one `_page`, and real (non-stub) `list_cinemas`,
`list_showtimes`, `_find_film_id` (Task 11), `get_seat_map` (Task 13), `is_logged_in`, `login_via_facebook`
(this task), and still-stubbed `lock_seats` (Task 15) — verify this by reading the whole file after editing,
not just the diff.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: PASS. This test never opens a real browser — it only checks the pure string-matching helper.

- [ ] **Step 5: Manual smoke test (not part of the automated suite)**

Run once by hand, with a real, already-Facebook-authenticated browser profile:

```python
from cinema_booking.providers.beta import BetaProvider
provider = BetaProvider(profile_dir=".chrome_profiles/beta")
print("logged in before:", provider.is_logged_in())
if not provider.is_logged_in():
    print("login result:", provider.login_via_facebook())
print("logged in after:", provider.is_logged_in())
```

Confirm it prints `True` after login, and that no password was typed anywhere.

- [ ] **Step 6: Commit**

```bash
git add cinema_booking/providers/beta.py cinema_booking/tests/test_providers_beta.py
git commit -m "feat(cinema_booking): add Beta Cinemas login/session handling via Playwright"
```

---

## Task 15: Seat locking (`providers/beta.py`)

**Files:**
- Modify: `cinema_booking/providers/beta.py`
- Modify: `cinema_booking/tests/test_providers_beta.py`

**Interfaces:**
- Consumes: `beta_lock_response.json` fixture and its documented shape (Task 12); `LockResult` (Task 1); `_page()` (Task 14).
- Produces: a pure function `parse_lock_response(data: dict, hold_minutes: int) -> LockResult` (maps the raw response JSON — whatever shape Task 12 found — into `LockResult`, computing `hold_expiry` as an ISO timestamp `hold_minutes` from now on success, `None` on failure) and `BetaProvider.lock_seats(showtime, seats) -> LockResult` which drives the real click-through-to-lock flow on the seat page and calls `parse_lock_response` on the result.

- [ ] **Step 1: Write the failing test**

```python
# append to cinema_booking/tests/test_providers_beta.py
def test_parse_lock_response_success(beta_showtimes_json):
    from cinema_booking.providers.beta import parse_lock_response

    # Replace this literal with the real shape captured in Task 12's beta_lock_response.json.
    raw = {"success": True, "message": "OK"}
    result = parse_lock_response(raw, hold_minutes=5)
    assert result.success is True
    assert result.hold_expiry is not None
    assert result.error is None


def test_parse_lock_response_failure(beta_showtimes_json):
    from cinema_booking.providers.beta import parse_lock_response

    raw = {"success": False, "message": "Ghế đã được giữ bởi người khác"}
    result = parse_lock_response(raw, hold_minutes=5)
    assert result.success is False
    assert result.hold_expiry is None
    assert result.error == "Ghế đã được giữ bởi người khác"
```

Replace the literal `raw` dicts in both tests with the actual JSON loaded from `cinema_booking/tests/fixtures/beta_lock_response.json` (success case) plus a hand-constructed failure-shaped variant, once Task 12's real fixture exists — the field names above (`success`/`message`) are placeholders to be corrected to match Beta's real response shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: FAIL — `parse_lock_response` not defined.

- [ ] **Step 3: Write the implementation**

```python
# append to cinema_booking/providers/beta.py
from datetime import datetime, timedelta


def parse_lock_response(data: dict, hold_minutes: int) -> LockResult:
    if data.get("success"):
        expiry = (datetime.now() + timedelta(minutes=hold_minutes)).isoformat()
        return LockResult(success=True, hold_expiry=expiry)
    return LockResult(success=False, error=data.get("message"))
```

(Adjust field names — `data.get("success")`/`data.get("message")` — to match whatever Task 12's real response actually contains; the structure of "success flag + optional error message" is the invariant, the exact keys are not.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_providers_beta.py -v`
Expected: PASS.

- [ ] **Step 5: Implement `lock_seats` for real**

```python
# add to BetaProvider in cinema_booking/providers/beta.py, replacing the placeholder
def lock_seats(self, showtime: Showtime, seats: list) -> LockResult:
    page = self._page()
    page.goto(f"https://betacinemas.vn/chon-ghe.htm?f={showtime.cinema.id}&s={showtime.id}")
    for seat in seats:
        page.click(f"[data-seat-id='{seat.id}']")  # adjust selector per Task 12 findings
    response = page.evaluate(
        """async () => {
            const resp = await fetch('/Ajax.aspx/LockSeats', {  // adjust URL per Task 12 findings
                method: 'POST',
                headers: {'Content-Type': 'application/json; charset=UTF-8'},
                body: JSON.stringify({})  // adjust payload shape per Task 12 findings
            });
            return await resp.json();
        }"""
    )
    hold_minutes = 5  # replace with the value confirmed in Task 12, once known
    return parse_lock_response(response, hold_minutes)
```

This step's selector/URL/payload placeholders MUST be replaced with Task 12's real findings before this method is trusted — until then, treat `lock_seats` as unverified and keep exercising it only through the manual smoke test below, never through the automated Telegram bot.

- [ ] **Step 6: Manual smoke test (not part of the automated suite)**

Run once by hand against a real, low-demand showtime (same care as CGV: never a showtime real customers are actively booking), and immediately release the hold afterward using whatever release endpoint Task 12 found:

```python
from cinema_booking.providers.beta import BetaProvider
from cinema_booking.types import Showtime, Cinema

provider = BetaProvider()
cinema = Cinema(id="...", name="...", city="", provider="beta")
showtime = Showtime(id="...", movie="...", cinema=cinema, start_time="...", date="...")
seat_map = provider.get_seat_map(showtime)
from cinema_booking.scoring import pick_best_block
block = pick_best_block(seat_map, quantity=2)
result = provider.lock_seats(showtime, block)
print(result)
# then immediately release — call whatever endpoint Task 12 found for this
```

- [ ] **Step 7: Commit**

```bash
git add cinema_booking/providers/beta.py cinema_booking/tests/test_providers_beta.py
git commit -m "feat(cinema_booking): implement Beta Cinemas seat locking"
```

---

## Task 16: Telegram bot commands (`telegram_bot.py`)

**Files:**
- Create: `cinema_booking/telegram_bot.py`
- Test: `cinema_booking/tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `add_ticket_request`, `list_ticket_requests`, `remove_ticket_request`, `update_item`, `get_item` (Task 7); `instant_camp_loop` (Task 10); `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from `.env` (already present, same variables `xeca_telegram_bot.py` uses).
- Produces: a `Bot` class with the same shape as `xeca_telegram_bot.py`'s (`send`, `dispatch`, `handle_message`, `run`), plus command handlers `cmd_add`, `cmd_list`, `cmd_remove`, `cmd_setcinemapriority`, `cmd_setquantity`, `cmd_setsweetbox`, `cmd_listcinemas`, `cmd_instant`, `cmd_paid`, `cmd_status`, `cmd_help` (covers every command the spec's "Watchlist & lệnh Telegram" section lists except `/logs`, which reads systemd-service logs via `xeca_control.systemctl_is_active`-style plumbing that doesn't exist for this bot yet — no systemd service has been set up for `cinema_booking`, so there's nothing for `/logs` to read; add it later alongside that deployment work, not here). This task's test coverage is `dispatch()`'s command→handler routing and each handler's argument parsing/validation — it monkeypatches `cinema_booking.state`/`cinema_booking.control` functions and never calls the real Telegram API or a real provider. Also produces `main()`, a 5-line entry point (`python -m cinema_booking.telegram_bot`) that reads `.env`, constructs a `Bot`, and calls `.run()` — copied in shape from `xeca_telegram_bot.py`'s `main()`.

- [ ] **Step 1: Write the failing test**

```python
# cinema_booking/tests/test_telegram_bot.py
from cinema_booking.telegram_bot import Bot


def make_bot(tmp_path):
    return Bot(token="fake-token", chat_id="123", state_file=str(tmp_path / "state.json"))


def test_cmd_add_requires_provider_movie_and_date(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/add")
    assert "Cú pháp" in reply


def test_cmd_add_creates_watchlist_item(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    reply = bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    assert "Đã thêm" in reply

    items = bot.dispatch("/list")
    assert "Người Nhện" in items or reply  # /list sends via bot.send in the real bot; see Step 3 note


def test_cmd_remove_unknown_id_reports_not_found(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/remove does-not-exist")
    assert "Không tìm thấy" in reply


def test_dispatch_unknown_command_shows_help(tmp_path):
    bot = make_bot(tmp_path)
    reply = bot.dispatch("/not-a-real-command")
    assert "Lệnh" in reply or "cú pháp" in reply.lower()


def test_cmd_add_applies_default_cinema_priority_for_beta(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    assert get_item(item_id, bot.state_file)["cinema_priority"] == ["Beta Tây Sơn"]


def test_cmd_setquantity_updates_item(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/setquantity {item_id} 4")
    assert "4" in reply
    assert get_item(item_id, bot.state_file)["quantity"] == 4


def test_cmd_setquantity_rejects_non_integer(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    reply = bot.dispatch(f"/setquantity {item_id} hai")
    assert "Cú pháp" in reply


def test_cmd_setsweetbox_toggles_flag(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    bot.dispatch(f"/setsweetbox {item_id} on")
    assert get_item(item_id, bot.state_file)["prefer_sweetbox"] is True
    bot.dispatch(f"/setsweetbox {item_id} off")
    assert get_item(item_id, bot.state_file)["prefer_sweetbox"] is False


def test_cmd_paid_marks_status_and_stops_instant(tmp_path):
    bot = make_bot(tmp_path)
    bot.dispatch('/add beta "Người Nhện" 12/08/2026')
    item_id = list_ticket_requests(bot.state_file)[0]["id"]
    update_item(item_id, path=bot.state_file, status="pending_payment")
    reply = bot.dispatch(f"/paid {item_id}")
    assert "thanh toán" in reply.lower()
    assert get_item(item_id, bot.state_file)["status"] == "paid"


def test_cmd_listcinemas_reports_provider_error_without_crashing(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)

    def broken_provider(name):
        raise ValueError(f"Unknown provider: {name}")

    monkeypatch.setattr("cinema_booking.telegram_bot.get_provider", broken_provider)
    reply = bot.dispatch("/listcinemas not-a-real-provider")
    assert "❌" in reply
```

Add `from cinema_booking.state import get_item, list_ticket_requests, update_item` to the top of
`test_telegram_bot.py` alongside the existing `Bot` import.

Note on the `/list` test: like `xeca_telegram_bot.py`'s `cmd_list`, this command sends one Telegram message per item via `self.send(...)` and returns `""` rather than returning the listing directly — adjust `test_cmd_add_creates_watchlist_item` once `cmd_list` is written (Step 3) to instead monkeypatch `bot.send` and assert on what it was called with, matching the real return-empty-string behavior.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cinema_booking/tests/test_telegram_bot.py -v`
Expected: FAIL — `cinema_booking.telegram_bot` module not found.

- [ ] **Step 3: Write the implementation**

```python
# cinema_booking/telegram_bot.py
import re
import threading

import requests

from cinema_booking.control import DEFAULT_CINEMA_PRIORITY, get_provider, instant_camp_loop
from cinema_booking.state import (
    DEFAULT_STATE_FILE, add_ticket_request, get_item, list_ticket_requests,
    remove_ticket_request, update_item,
)

DATE_SINGLE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
DATE_RANGE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})-(\d{2}/\d{2}/\d{4})$")


def _to_iso(ddmmyyyy: str) -> str:
    d, m, y = ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


def parse_date_arg(text: str) -> list[str] | None:
    if DATE_RANGE_RE.match(text):
        start, end = text.split("-")
        return [_to_iso(start), _to_iso(end)]
    if DATE_SINGLE_RE.match(text):
        iso = _to_iso(text)
        return [iso, iso]
    return None


class Bot:
    def __init__(self, token: str, chat_id: str, state_file: str = DEFAULT_STATE_FILE):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
        self.api = f"https://api.telegram.org/bot{token}"
        self.instant_threads: dict[str, dict] = {}

    def send(self, text: str) -> None:
        requests.post(f"{self.api}/sendMessage", json={"chat_id": self.chat_id, "text": text}, timeout=20)

    def dispatch(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        handlers = {
            "/add": self.cmd_add,
            "/list": self.cmd_list,
            "/remove": self.cmd_remove,
            "/setcinemapriority": self.cmd_setcinemapriority,
            "/setquantity": self.cmd_setquantity,
            "/setsweetbox": self.cmd_setsweetbox,
            "/listcinemas": self.cmd_listcinemas,
            "/instant": self.cmd_instant,
            "/paid": self.cmd_paid,
            "/status": self.cmd_status,
            "/help": lambda r: self.cmd_help(),
        }
        handler = handlers.get(cmd)
        if not handler:
            return self.cmd_help()
        return handler(rest)

    def cmd_help(self) -> str:
        return (
            "Lệnh:\n"
            '/add <provider> "<tên phim>" <ngày dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>\n'
            "/list\n"
            "/remove <id>\n"
            "/setcinemapriority <id> <rạp 1>, <rạp 2>, ...\n"
            "/setquantity <id> <n>\n"
            "/setsweetbox <id> on|off\n"
            "/listcinemas <provider>\n"
            "/instant <id> on|off\n"
            "/paid <id>\n"
            "/status"
        )

    def cmd_setquantity(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or not parts[1].isdigit():
            return "❌ Cú pháp: /setquantity <id> <n> (n là số nguyên)"
        item_id, quantity_text = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        quantity = int(quantity_text)
        update_item(item_id, path=self.state_file, quantity=quantity)
        return f"✅ Đã đặt số ghế cho [{item_id}]: {quantity}"

    def cmd_setsweetbox(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            return "❌ Cú pháp: /setsweetbox <id> on|off"
        item_id, action = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        update_item(item_id, path=self.state_file, prefer_sweetbox=action.lower() == "on")
        return f"✅ Đã đặt sweetbox={action.lower()} cho [{item_id}]"

    def cmd_listcinemas(self, rest: str) -> str:
        provider_name = rest.strip()
        if not provider_name:
            return "Cú pháp: /listcinemas <provider>"
        try:
            provider = get_provider(provider_name)
            cinemas = provider.list_cinemas()
        except Exception as e:
            return f"❌ Lỗi khi lấy danh sách rạp: {e}"
        return "\n".join(f"- {c.name}" for c in cinemas) or "(không có rạp nào)"

    def cmd_paid(self, rest: str) -> str:
        item_id = rest.strip()
        if not item_id:
            return "Cú pháp: /paid <id>"
        item = get_item(item_id, self.state_file)
        if item is None:
            return f"Không tìm thấy id={item_id}"
        update_item(item_id, path=self.state_file, status="paid", instant=False)
        entry = self.instant_threads.pop(item_id, None)
        if entry:
            entry["stop_event"].set()
        return f"✅ Đã đánh dấu [{item_id}] là đã thanh toán."

    def cmd_add(self, rest: str) -> str:
        match = re.match(r'^(\S+)\s+"([^"]+)"\s+(\S+)$', rest.strip())
        if not match:
            return '❌ Cú pháp: /add <provider> "<tên phim>" <dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>'
        provider, movie_query, date_text = match.groups()
        date_range = parse_date_arg(date_text)
        if date_range is None:
            return "❌ Ngày không hợp lệ, dùng dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy."
        cinema_priority = DEFAULT_CINEMA_PRIORITY.get(provider, [])
        item = add_ticket_request(provider, movie_query, date_range,
                                   cinema_priority=cinema_priority, state_file=self.state_file)
        extra = "" if cinema_priority else (
            "\n⚠️ Chưa có rạp ưu tiên mặc định cho provider này — dùng /setcinemapriority trước khi /instant."
        )
        return f"✅ Đã thêm watchlist [{item['id']}]: {movie_query} ({date_text}){extra}"

    def cmd_list(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng."
        for item in items:
            self.send(f"[{item['id']}] {item['movie_query']} — {item['status']}")
        return ""

    def cmd_remove(self, rest: str) -> str:
        item_id = rest.strip()
        if not item_id:
            return "Cú pháp: /remove <id>"
        entry = self.instant_threads.pop(item_id, None)
        if entry:
            entry["stop_event"].set()
        return f"✅ Đã xoá {item_id}" if remove_ticket_request(item_id, self.state_file) else f"Không tìm thấy id={item_id}"

    def cmd_setcinemapriority(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Cú pháp: /setcinemapriority <id> <rạp 1>, <rạp 2>, ..."
        item_id, names_str = parts
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        update_item(item_id, path=self.state_file, cinema_priority=names)
        return f"✅ Đã đặt ưu tiên rạp cho [{item_id}]: {' > '.join(names)}"

    def cmd_status(self, rest: str) -> str:
        items = list_ticket_requests(self.state_file)
        if not items:
            return "Watchlist rỗng."
        return "\n".join(f"[{i['id']}] {i['movie_query']} — {i['status']}" for i in items)

    def cmd_instant(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            return "Cú pháp: /instant <id> on|off"
        item_id, action = parts
        if action.lower() == "off":
            entry = self.instant_threads.pop(item_id, None)
            if entry:
                entry["stop_event"].set()
            update_item(item_id, path=self.state_file, instant=False)
            return f"🛑 Đã tắt instant cho [{item_id}]."
        if get_item(item_id, self.state_file) is None:
            return f"Không tìm thấy id={item_id}"
        if item_id in self.instant_threads:
            return f"[{item_id}] đã đang bật rồi."
        update_item(item_id, path=self.state_file, instant=True)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=instant_camp_loop, args=(item_id, stop_event, self.send, self.state_file), daemon=True,
        )
        self.instant_threads[item_id] = {"stop_event": stop_event, "thread": thread}
        thread.start()
        return f"⚡ Đã bật instant cho [{item_id}]."


def _load_env_file(path: str) -> None:
    """Minimal .env loader — sets os.environ from KEY=VALUE lines, skipping blanks/comments.
    Deliberately not importing xeca_client.load_env_file: cinema_booking stays independent of
    the unrelated bus-ticket modules rather than reaching across domains for a 5-line helper."""
    import os

    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main():
    import os

    from cinema_booking.state import DEFAULT_STATE_FILE as _DEFAULT
    _load_env_file(".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[ERROR] Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID trong .env")
        return
    Bot(token, chat_id, state_file=_DEFAULT).run()


if __name__ == "__main__":
    main()
```

`Bot.run()`/`get_updates()`/`handle_message()` (the long-polling loop itself) are not written out here —
copy them verbatim from `xeca_telegram_bot.py`'s `Bot.run`/`get_updates`/`handle_message`, adjusting only
the import at the top of `handle_message` if it references anything xeca-specific (it doesn't — that
method just checks `from_id`/`chat_id` and calls `self.dispatch(text)`, which is provider-agnostic already).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cinema_booking/tests/test_telegram_bot.py -v`
Expected: PASS (10 tests, after fixing `test_cmd_add_creates_watchlist_item` per the Step 1 note about `/list`'s send-per-item behavior).

- [ ] **Step 5: Commit**

```bash
git add cinema_booking/telegram_bot.py cinema_booking/tests/test_telegram_bot.py
git commit -m "feat(cinema_booking): add Telegram bot commands for the watchlist and camp loop"
```

---

## Task 17: Manual end-to-end checklist (not automated)

**Files:** none (verification only).

This task has no code changes — it's the final manual gate before trusting the bot with a real booking attempt, matching the spec's "kiểm tra thủ công đầu-cuối" requirement for Phase 4.

- [ ] **Step 1:** Confirm `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set in `.env` (already present from the xeca setup), then run `python -m cinema_booking.telegram_bot` (the `main()` entry point from Task 16).
- [ ] **Step 2:** From Telegram, run `/add beta "<a movie currently showing>" <a real near-term date>` with a real `movie_query` that matches something on `betacinemas.vn/home.htm` right now.
- [ ] **Step 3:** Run `/setcinemapriority <id> Beta Tây Sơn`.
- [ ] **Step 4:** Run `/instant <id> on`, confirm the bot logs `[BOT]`-style activity and, once a real seat is found, sends a Telegram message with seat labels + hold expiry + payment link.
- [ ] **Step 5:** Manually verify on `betacinemas.vn` (logged in as yourself, same account) that the reported seats are genuinely held under your account, then either complete payment or let the hold expire — confirm the seats free up again afterward if left unpaid.
- [ ] **Step 6:** Run `/instant <id> off`, confirm the background thread stops (no further Telegram messages after a few poll intervals).

No commit for this task — it's a verification pass. If it surfaces a bug, fix it as a new small task (write a failing test first, per every other task in this plan) rather than patching silently.
