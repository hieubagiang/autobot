"""Shared client for api-pro.xeca.vn (Văn Minh bus booking) + Telegram notifications.

Reverse-engineered endpoints are documented in docs/xeca_booking_mechanism.md.
"""

import datetime
import os
import random
import time
import uuid

import requests

BASE_URL = "https://api-pro.xeca.vn/v1"
ORIGIN = "https://vanminh.xeca.vn"
DEFAULT_AGENCY_ID = "1"
DEFAULT_SOURCE_CHANNEL = 11

TIGHT_POLL_SECONDS = 0.4  # poll cadence inside the tight window around a known target time
TIGHT_WINDOW_BEFORE_SECONDS = 60  # start hammering this long before target
TIGHT_WINDOW_AFTER_SECONDS = 120  # give up hammering this long after target if still not
# open (the announced time might be off) and fall back to the normal cadence — same
# scheme as tqtt_client.py's, duplicated here rather than imported since the Xeca and TQTT
# modules are kept independently deployable.


def parse_target_time(value: str) -> float:
    """Parses 'HH:MM' or 'HH:MM:SS' (server-local time) as the next occurrence of that
    time from now, returned as a Unix timestamp. Only safe to call once, right when the
    user sets the schedule — see resolve_target_time()/parse_stored_target_time() for why
    this must NOT be re-called later against the same stored string."""
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    now = datetime.datetime.now()
    target = now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target.timestamp()


STORED_TARGET_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def resolve_target_time(value: str, yyyymmdd: int | None = None) -> str:
    """Resolves a user-typed 'HH:MM'/'HH:MM:SS' into a CONCRETE absolute
    'YYYY-MM-DD HH:MM:SS' string, to be persisted (e.g. item["target_time"]) instead of
    the bare input.

    If `yyyymmdd` is given (e.g. 20260813), builds the target for that EXACT date
    directly — no "next occurrence from now" involved at all, so this is safe to set any
    number of days in advance (the gap flagged right after the incident below: setting a
    bare "08:00" more than ~24h ahead of the real date would resolve to the wrong day).
    Without it, falls back to "the next time the clock reads this value from right now"
    (parse_target_time) — only safe when set close to the same day, which is the common
    case (turning instant on shortly before a same-day announced opening).

    This must happen exactly once, at set-time — incident 2026-08-10:
    instant_lock_loop used to call parse_target_time() fresh on every loop iteration
    against the stored bare "HH:MM", relying on in-memory caching (only re-parse when the
    string changes) to keep the resolved timestamp stable. That cache is thread-local and
    does NOT survive an xeca-bot.service restart — resume_instant_items() spins up a brand
    new thread with no memory of the earlier resolution, so it re-parses "08:00" fresh. If
    the restart happens after 08:00 has already passed today (exactly what happened: a
    deploy restarted the service at 08:14, after several items' seat holds had already been
    created), "next occurrence of 08:00" silently rolls to TOMORROW — instant mode then
    treats an active event as "scheduled a day out" and stops trying entirely, abandoning
    real, already-expired seat holds for however long the mistaken 24h wait would have
    lasted (until someone notices and manually intervenes, as happened here). Storing the
    already-resolved absolute datetime instead makes re-parsing on any subsequent restart,
    at any later wall-clock time, always return the exact same original instant — for good
    or bad (see parse_stored_target_time's docstring on why letting `target_time` itself be
    a stale absolute instant is fine and even correct)."""
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    if yyyymmdd is not None:
        year, month, day = yyyymmdd // 10000, (yyyymmdd // 100) % 100, yyyymmdd % 100
        target = datetime.datetime(year, month, day, parts[0], parts[1], parts[2])
        return target.strftime(STORED_TARGET_TIME_FORMAT)
    ts = parse_target_time(value)
    return datetime.datetime.fromtimestamp(ts).strftime(STORED_TARGET_TIME_FORMAT)


def parse_stored_target_time(value: str) -> float:
    """Parses a persisted item["target_time"] value back into a Unix timestamp. Expects
    the absolute format written by resolve_target_time(); falls back to treating `value`
    as a bare HH:MM (the old, pre-2026-08-10-fix format, reintroducing the exact
    "silently rolls to tomorrow" ambiguity this function exists to avoid) only for
    resilience against already-persisted old-format state, never for new writes.
    A resolved value that's already hours or days in the past is NOT re-rolled forward —
    it just means the tight window has long since passed, so is_in_tight_window() and
    next_poll_interval() correctly fall through to normal-cadence camping instead of the
    old failure mode of quietly deferring for a full extra day."""
    try:
        return datetime.datetime.strptime(value, STORED_TARGET_TIME_FORMAT).timestamp()
    except ValueError:
        return parse_target_time(value)


def is_in_tight_window(target_ts: float | None) -> bool:
    if target_ts is None:
        return False
    now = time.time()
    return target_ts - TIGHT_WINDOW_BEFORE_SECONDS <= now <= target_ts + TIGHT_WINDOW_AFTER_SECONDS


def next_poll_interval(interval: float, jitter: float, target_ts: float | None) -> float:
    """Normal cadence (`interval` + random jitter) far from `target_ts`; switches to
    near-continuous polling (`TIGHT_POLL_SECONDS`) inside the window around it — matters
    when the exact opening instant matters more than steady-state politeness."""
    if is_in_tight_window(target_ts):
        return TIGHT_POLL_SECONDS
    return interval + random.uniform(0, max(jitter, 0))

COMMON_HEADERS = {
    "accept": "*/*",
    "origin": ORIGIN,
    "referer": ORIGIN + "/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def load_env_file(path: str = ".env"):
    """Minimal .env loader (KEY=VALUE per line), no external dependency."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class XecaClient:
    """Holds one requests.Session + a stable _client_id for the whole run
    (seat holds / order flows are usually tied to this identifier)."""

    def __init__(self, agency_id: str = DEFAULT_AGENCY_ID, client_id: str | None = None):
        self.session = requests.Session()
        self.session.headers.update(COMMON_HEADERS)
        self.session.headers["x-bus-agency-id"] = agency_id
        self.client_id = client_id or str(uuid.uuid4())

    def _params(self, extra: dict) -> dict:
        params = {"_source": "wb", "_client_id": self.client_id}
        params.update(extra)
        return params

    def get_bus_times(self, depart_date: int, from_province_id: int, to_province_id: int,
                       source_channel: int = DEFAULT_SOURCE_CHANNEL) -> list[dict]:
        resp = self.session.get(
            f"{BASE_URL}/bus-times",
            params=self._params({
                "departDate": depart_date,
                "fromProvinceId": from_province_id,
                "toProvinceId": to_province_id,
                "sourceChannel": source_channel,
            }),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("busTimes", [])

    def get_detail_bus_time(self, depart_date: int, bus_time_id, bus_hop_id, bus_stage_id,
                             from_province_id: int, to_province_id: int) -> dict:
        resp = self.session.get(
            f"{BASE_URL}/bus-time-exts/detail-bus-time",
            params=self._params({
                "depart_date": depart_date,
                "bus_time_id": bus_time_id,
                "bus_hop_id": bus_hop_id,
                "bus_stage_id": bus_stage_id,
                "from_province_id": from_province_id,
                "to_province_id": to_province_id,
            }),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_boarding_points(self, bus_time_id, depart_date: int, point_type: int,
                             seats: str = "", from_web: bool = True) -> list[dict]:
        resp = self.session.get(
            f"{BASE_URL}/boarding-points/pickup-drop-points",
            params=self._params({
                "busTimeId": bus_time_id,
                "type": point_type,
                "seats": seats,
                "departDate": depart_date,
                "fromWeb": str(from_web).lower(),
            }),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_book_expired_time(self, bus_time_id, depart_date: int, start_time: str,
                               number_of_tickets: int = 1) -> dict:
        resp = self.session.get(
            f"{BASE_URL}/orders/get-book-expired-time",
            params=self._params({
                "busTimeId": bus_time_id,
                "departDate": depart_date,
                "numberOfTickets": number_of_tickets,
                "startTime": start_time,
            }),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def toggle_seat_lock(self, action: str, bus_hop_id, bus_time_id, depart_date: int,
                          seat_ids: list[int], pre_status: str) -> dict:
        resp = self.session.post(
            f"{BASE_URL}/tickets/toggleSeatLock",
            params=self._params({}),
            json={
                "action": action,
                "busHopId": bus_hop_id,
                "busTimeId": bus_time_id,
                "departDate": depart_date,
                "seatIds": seat_ids,
                "preStatus": pre_status,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def create_order(self, depart_date: int, bus_time_id, bus_hop_id, seat_ids: list[int],
                      cust_name: str, cust_mobile: str, pickup_name: str, pickup_point: dict,
                      dropoff_name: str, dropoff_point: dict, cust_email: str = "") -> dict:
        detail = {
            "custPickupAddr": pickup_name,
            **pickup_fields(pickup_point),
            "notes": "",
            "paymentType": PAYMENT_TYPE_ONLINE,
            "arriveAddrDetail": dropoff_name,
            "custMobileDetail": cust_mobile,
            "custNameDetail": cust_name,
            **dropoff_fields(dropoff_point),
            "isShip": 0,
            "custEmailDetail": cust_email,
        }
        payload = {
            "departDate": depart_date,
            "busTimeId": str(bus_time_id),
            "busHopsId": str(bus_hop_id),
            "couponUuid": 0,
            "discountId": 0,
            "paymentMethod": PAYMENT_METHOD_ONLINE,
            "custId": 0,
            "custMobileNo": cust_mobile,
            "custName": cust_name,
            "custArriveAddr": dropoff_name,
            "srcChannel": DEFAULT_SOURCE_CHANNEL,
            "sendSms": False,
            "pickupType": None,
            "details": [{"seatId": seat_id, **detail} for seat_id in seat_ids],
            "buyInsurance": False,
        }
        resp = self.session.post(
            f"{BASE_URL.replace('/v1', '')}/brand-service/v1/orders/book/web",
            params=self._params({}),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def initiate_payment(self, order_id, provider: str = "vnpay") -> dict:
        return_url = f"{ORIGIN}/booking/booking/complete?ticket={order_id}&type=1"
        resp = self.session.post(
            f"{BASE_URL.replace('/v1', '')}/payment-service/v1/payment",
            params=self._params({}),
            json={"orderId": order_id, "provider": provider, "returnUrl": return_url},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_ticket_detail(self, order_id) -> dict:
        """`GET /brand-service/v1/ticket/detail-ticket/{order_id}` — the call the `complete`
        page (`?ticket=<id>&type=1`, see initiate_payment's returnUrl) fires to render order
        details. Confirmed via live capture (Chrome DevTools MCP, read-only GET, no side
        effect) against order 14013599 ~24h after its ~30min hold had lapsed unpaid:
        `{"status": 3, "payment": {"paymentStatus": 1, ...}, ...}`. See
        `PAYMENT_STATUS_UNPAID` for why only `paymentStatus` (not `status`) is treated as a
        signal — no confirmed example of a successfully PAID order's shape exists yet."""
        resp = self.session.get(
            f"{BASE_URL.replace('/v1', '')}/brand-service/v1/ticket/detail-ticket/{order_id}",
            params=self._params({}),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


REGULAR_BUS_TYPE_NAME = "Xe giường nằm"

# Time-of-day bands, most preferred first: tối (evening, >=20:00) > chiều (afternoon,
# 12:00-20:00) > sáng (morning, <12:00 — last resort, only if nothing else is available).
EVENING_START = "20:00"
AFTERNOON_START = "12:00"


def time_band_rank(start_time: str) -> int:
    """0=tối (best), 1=chiều, 2=sáng (worst)."""
    st = start_time or "00:00"
    if st >= EVENING_START:
        return 0
    if st >= AFTERNOON_START:
        return 1
    return 2


def rank_bus_times(bus_times: list[dict]) -> list[dict]:
    """Order ALL bus_times of a day by the user's full preference, most-preferred first:
    1. Regular bus ("Xe giường nằm") over Limousine VIP — Limousine is a last resort.
    2. Time band: tối (evening, >=20:00) > chiều (afternoon) > sáng (morning, last resort).
    3. Latest start_time first within the same band.

    Used for "camping" a sold-out date: since we don't know in advance which specific
    departure will free up a seat, every retry cycle should try ALL candidates in this
    order (not just a single "best" pick) and grab the first one with a matching seat.
    """
    by_latest = sorted(bus_times, key=lambda b: b.get("start_time", ""), reverse=True)
    by_band = sorted(by_latest, key=lambda b: time_band_rank(b.get("start_time")))
    by_type = sorted(by_band, key=lambda b: 0 if b.get("bus_type_name") == REGULAR_BUS_TYPE_NAME else 1)
    return by_type


def filter_bus_times(bus_times: list[dict], depart_from: str | None = None, depart_to: str | None = None,
                      allow_limousine: bool = True) -> list[dict]:
    """Restricts candidates to the user's desired DEPARTURE time-of-day window
    (`depart_from`/`depart_to`, 'HH:MM' strings, inclusive) and/or bus type, applied BEFORE
    ranking — a bus outside the window or of an excluded type is never picked even if it's
    the only one with seats. `depart_from > depart_to` is treated as a window that wraps
    past midnight (e.g. "22:00".."02:00" keeps departures >=22:00 OR <=02:00). Either bound
    omitted/None means "no constraint" on that side; both omitted means no time filtering
    at all. `allow_limousine=False` drops every non-`REGULAR_BUS_TYPE_NAME` candidate."""
    result = []
    for bt in bus_times:
        if not allow_limousine and bt.get("bus_type_name") != REGULAR_BUS_TYPE_NAME:
            continue
        start_time = bt.get("start_time") or ""
        if depart_from and depart_to and start_time:
            in_window = (
                depart_from <= start_time <= depart_to
                if depart_from <= depart_to
                else (start_time >= depart_from or start_time <= depart_to)
            )
            if not in_window:
                continue
        result.append(bt)
    return result


def select_preferred_bus_time(bus_times: list[dict]) -> dict | None:
    """Single best pick for simple status reporting (Phase 1 notifications) — the top of
    `rank_bus_times()`, preferring a candidate that still has seats. Real booking/camping
    logic should use `rank_bus_times()` directly and try every candidate, not just this one."""
    if not bus_times:
        return None
    ranked = rank_bus_times(bus_times)
    with_seats = [b for b in ranked if int(b.get("empty_seat", 0) or 0) > 0]
    return (with_seats or ranked)[0]


SEAT_FLOOR_LETTER_PRIORITY = [
    ("Tầng 2", "E"),
    ("Tầng 2", "A"),
    ("Tầng 1", "F"),
    ("Tầng 1", "B"),
]
# Middle-lane seats — only acceptable when actively camping a sold-out date (instant-book
# mode), never for a regular one-shot /book. Still floor-2-first when both are considered.
SEAT_FLOOR_LETTER_PRIORITY_MIDDLE = [
    ("Tầng 2", "C"),
    ("Tầng 1", "D"),
]
SEAT_NUMBER_PRIORITY = [3, 2, 4, 1, 5, 6]
SEAT_NUMBER_RANK = {n: i for i, n in enumerate(SEAT_NUMBER_PRIORITY)}

COASTAL_ROUTE_KEYWORD = "Ven biển"

PAYMENT_METHOD_ONLINE = 3
PAYMENT_TYPE_ONLINE = 8

# Per-direction defaults. The "Ven biển HT - Quốc lộ 1 NA" coastal route variant swaps
# out whichever endpoint sits in Hà Tĩnh — the dropoff when Hà Tĩnh is the destination
# (HN-HT), or the pickup when Hà Tĩnh is the origin (HT-HN). `coastal_pickup_name` /
# `coastal_dropoff_name` are null on whichever end doesn't vary.
DIRECTIONS = {
    "HN-HT": {
        "label": "Hà Nội → Hà Tĩnh",
        "from_province_id": 2,
        "to_province_id": 3,
        "default_pickup_name": ["493 Nguyễn Trãi"],
        "default_dropoff_name": ["VP THẠCH HÀ - HT"],
        "coastal_pickup_name": None,
        "coastal_dropoff_name": ["XANH ĐỎ THẠCH LONG - HT"],
    },
    "HT-HN": {
        "label": "Hà Tĩnh → Hà Nội",
        "from_province_id": 3,
        "to_province_id": 2,
        "default_pickup_name": ["VP THẠCH HÀ - HT"],
        # "BX YÊN NGHĨA" fallback added 2026-08-10: some buses on this route/date have no
        # "Số 275 Nguyễn Trãi" at all (a different operator/bus_stage config) — confirmed
        # live via a real not-found error listing every point actually available on
        # bus_time_id 18251, which included BX YÊN NGHĨA but not Nguyễn Trãi.
        "default_dropoff_name": ["Số 275 Nguyễn Trãi", "BX YÊN NGHĨA"],
        "coastal_pickup_name": ["XANH ĐỎ THẠCH LONG - HT"],
        "coastal_dropoff_name": None,
    },
}

# Kept for backward compatibility with existing callers on the default direction.
DEFAULT_PICKUP_NAME = DIRECTIONS["HN-HT"]["default_pickup_name"]
DEFAULT_DROPOFF_NAME = DIRECTIONS["HN-HT"]["default_dropoff_name"]
COASTAL_DROPOFF_NAME = DIRECTIONS["HN-HT"]["coastal_dropoff_name"]


def get_direction(code: str) -> dict:
    direction = DIRECTIONS.get(code.upper())
    if not direction:
        raise ValueError(f"Chiều '{code}' không hợp lệ. Chọn: {', '.join(DIRECTIONS)}")
    return direction


def _iter_bookable_seats(seat_map: dict):
    for area in seat_map.get("objArea", []):
        area_name = area.get("areaName")
        for row in area.get("objRow", []):
            for seat in row.get("objSeat", []):
                name = seat.get("seatDisplayName", "")
                if seat.get("type") == 4 or name.startswith("P-"):
                    continue
                if seat.get("seatStatus") != "empty":
                    continue
                if not name or not name[-1].isdigit():
                    continue
                letter = name[0]
                try:
                    number = int(name[1:])
                except ValueError:
                    continue
                yield area_name, letter, number, seat


def select_seats(seat_map: dict, quantity: int = 1, allow_middle: bool = False) -> list[dict]:
    """Pick `quantity` seats per the user's stated preference:
    floor 2 before floor 1, letter E > A (floor 2) / F > B (floor 1), then seat number
    3 > 2 > 4 > 1 > 5 > 6. Skips auxiliary "P-" seats (type 4) and non-empty seats.

    `allow_middle=True` additionally accepts the middle-lane C (floor 2) / D (floor 1) as a
    last resort if no outer-lane seat is free — only meant for camping a sold-out date
    (instant-book mode), never for a regular one-shot booking.

    For quantity > 1, prefers a contiguous run of seat numbers within a single
    (floor, letter) column (adjacent seats) over independently-ranked scattered seats —
    only falls back to scattered seats if no column has enough of a contiguous block.
    """
    floor_letter_priority = SEAT_FLOOR_LETTER_PRIORITY + (SEAT_FLOOR_LETTER_PRIORITY_MIDDLE if allow_middle else [])

    by_area_letter = {}
    by_key = {}
    for area_name, letter, number, seat in _iter_bookable_seats(seat_map):
        by_area_letter.setdefault((area_name, letter), {})[number] = seat
        by_key[(area_name, letter, number)] = seat

    if quantity <= 1:
        for area_name, letter in floor_letter_priority:
            for number in SEAT_NUMBER_PRIORITY:
                seat = by_key.get((area_name, letter, number))
                if seat:
                    return [seat]
        return []

    # Try each column, in floor/letter priority order, for a contiguous run of `quantity`.
    for area_name, letter in floor_letter_priority:
        numbers = by_area_letter.get((area_name, letter), {})
        if len(numbers) < quantity:
            continue
        best_run = None
        best_score = None
        sorted_numbers = sorted(numbers)
        min_n, max_n = sorted_numbers[0], sorted_numbers[-1]
        for start in range(min_n, max_n - quantity + 2):
            run = list(range(start, start + quantity))
            if not all(n in numbers for n in run):
                continue
            score = sum(SEAT_NUMBER_RANK.get(n, len(SEAT_NUMBER_PRIORITY)) for n in run)
            if best_score is None or score < best_score:
                best_score, best_run = score, run
        if best_run:
            return [numbers[n] for n in best_run]

    # No column has a contiguous block big enough — fall back to the best individually
    # ranked seats across all columns (still respects floor/letter/number priority).
    ranked = []
    for area_name, letter in floor_letter_priority:
        for number in SEAT_NUMBER_PRIORITY:
            seat = by_key.get((area_name, letter, number))
            if seat:
                ranked.append(seat)
    return ranked[:quantity]


def is_coastal_route(bus_time: dict) -> bool:
    text = f"{bus_time.get('bus_stage_name', '')} {bus_time.get('hop_name', '')}"
    return COASTAL_ROUTE_KEYWORD.lower() in text.lower()


def find_boarding_point(points: list[dict], name: str | list[str]) -> dict | None:
    """Matches by exact name. `name` may be a single string, or an ORDERED list of
    preferred names to try in priority order — returns the first one that's actually
    available on this specific bus, not just list()[0] regardless of availability.
    Some route variants (a different operator/bus_stage config) drop the usual default
    point entirely — e.g. some Hà Tĩnh→Hà Nội buses have no "Số 275 Nguyễn Trãi" at all,
    only "BX YÊN NGHĨA" — so a single hardcoded preference can get permanently stuck
    retrying the exact same doomed candidate every camp cycle. A priority list lets the
    next-best preference take over on those buses instead of failing outright."""
    names = [name] if isinstance(name, str) else name
    for n in names:
        for p in points:
            if p.get("home_pickup_zone_name") == n or p.get("boarding_point_name") == n:
                return p
    return None


def pickup_fields(point: dict) -> dict:
    """A boarding point is either a home-pickup zone (door-to-door, needs
    homePickupZoneId + pickupType=1) or a fixed stage point (a bus office/station, needs
    custBoardingPointId + pickupType=3). Confirmed via live create-order captures on both
    directions: Hà Nội→Hà Tĩnh (pickup = home zone "493 Nguyễn Trãi") and
    Hà Tĩnh→Hà Nội (pickup = fixed point "VP THẠCH HÀ - HT", pickupType=3).
    `pickupType` is NOT a fixed constant — it must mirror which kind of point this is,
    same as the `type` field already distinguishes home-zone (1) vs fixed-point (3)
    entries in the boarding-points API response."""
    if point.get("home_pickup_zone_id") is not None:
        return {"homePickupZoneId": point["home_pickup_zone_id"], "custBoardingPointId": None, "pickupType": 1}
    return {"homePickupZoneId": None, "custBoardingPointId": point.get("boarding_point_id"), "pickupType": 3}


def dropoff_fields(point: dict) -> dict:
    """Mirror of pickup_fields for the drop-off side (`custArriveType` follows the same
    1=home-zone / 3=fixed-point convention). Both branches are confirmed via live capture:
    `custArrivePointId`/`custArriveType=3` on Hà Nội→Hà Tĩnh (drop-off = fixed point
    "VP THẠCH HÀ - HT"), `custArriveZone`/`custArriveType=1` on Hà Tĩnh→Hà Nội (drop-off =
    home zone "Số 275 Nguyễn Trãi", custArriveZone=1110)."""
    if point.get("home_pickup_zone_id") is not None:
        return {"custArrivePointId": None, "custArriveZone": point["home_pickup_zone_id"], "custArriveType": 1}
    return {"custArrivePointId": point.get("boarding_point_id"), "custArriveZone": None, "custArriveType": 3}


PAYMENT_STATUS_UNPAID = 1  # confirmed via live capture on two independent expired, never-
# paid orders (14013599 and 14013565): payment.paymentStatus == 1 both times. No confirmed
# example of a PAID order's value exists — would require completing a real payment to
# observe, which is out of scope for reverse-engineering. So this is only ever used to
# detect a *change* away from this known baseline (worth a human's attention), never to
# assert a specific value means "paid".


def payment_status_changed(ticket_detail: dict) -> int | None:
    """Returns the new `payment.paymentStatus` value if it differs from the confirmed-unpaid
    baseline (worth notifying a human to check/confirm via /paid), or None if it still looks
    unpaid / the field is missing. Deliberately does not look at the top-level `status` field
    or claim to know what "paid" looks like — see PAYMENT_STATUS_UNPAID."""
    payment_status = (ticket_detail.get("payment") or {}).get("paymentStatus")
    if payment_status is not None and payment_status != PAYMENT_STATUS_UNPAID:
        return payment_status
    return None


def is_sale_open(special_rules: list[dict], depart_date: int, bus_stage_id) -> tuple[bool, str]:
    """Check busStageSpecialRules from detail-bus-time to see if depart_date is blocked.

    Returns (open, reason). A rule blocks the date when its [from_date, to_date] window
    contains depart_date, it applies to this bus_stage_id (or globally via bus_stage_id=-1),
    and it marks not_allow_book or not_allow_sell.
    """
    try:
        bus_stage_id = int(bus_stage_id)
    except (TypeError, ValueError):
        bus_stage_id = None

    for rule in special_rules or []:
        rule_stage_id = rule.get("bus_stage_id")
        applies = rule_stage_id in (-1, None) or rule_stage_id == bus_stage_id
        if not applies:
            continue

        from_date = rule.get("from_date")
        to_date = rule.get("to_date")
        if from_date is None or to_date is None:
            continue
        if not (from_date <= depart_date <= to_date):
            continue

        if rule.get("not_allow_book") or rule.get("not_allow_sell"):
            msg = rule.get("book_msg") or rule.get("sell_msg") or rule.get("warning_msg") or "Chưa mở bán vé."
            return False, msg

    return True, "Đã mở bán."


def send_telegram_message(token: str, chat_id: str, text: str, parse_mode: str | None = None) -> dict:
    """`parse_mode` defaults to None (plain text) so literal `<`/`>`/`&` in ordinary
    messages (e.g. "<HN-HT|HT-HN>" in help text) can't be misread as HTML and reject the
    whole message. Only pass parse_mode="HTML" for text you've built with proper tags and
    html.escape()'d any interpolated content."""
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def send_telegram_photo(token: str, chat_id: str, photo, caption: str = "") -> dict:
    """`photo` can be raw bytes, a file-like object, or a public URL string."""
    if isinstance(photo, str):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "photo": photo, "caption": caption},
            timeout=30,
        )
    else:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("qr.png", photo)},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()
