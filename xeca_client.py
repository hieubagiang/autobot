"""Shared client for api-pro.xeca.vn (Văn Minh bus booking) + Telegram notifications.

Reverse-engineered endpoints are documented in docs/xeca_booking_mechanism.md.
"""

import os
import uuid

import requests

BASE_URL = "https://api-pro.xeca.vn/v1"
ORIGIN = "https://vanminh.xeca.vn"
DEFAULT_AGENCY_ID = "1"
DEFAULT_SOURCE_CHANNEL = 11

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

    def create_order(self, depart_date: int, bus_time_id, bus_hop_id, seat_id: int,
                      cust_name: str, cust_mobile: str, pickup_name: str,
                      home_pickup_zone_id, dropoff_name: str, dropoff_point_id,
                      cust_email: str = "") -> dict:
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
            "details": [{
                "seatId": seat_id,
                "custPickupAddr": pickup_name,
                "homePickupZoneId": home_pickup_zone_id,
                "custBoardingPointId": None,
                "notes": "",
                "paymentType": PAYMENT_TYPE_ONLINE,
                "arriveAddrDetail": dropoff_name,
                "custMobileDetail": cust_mobile,
                "custNameDetail": cust_name,
                "custArriveZone": None,
                "custArrivePointId": dropoff_point_id,
                "pickupType": 1,
                "custArriveType": 3,
                "isShip": 0,
                "custEmailDetail": cust_email,
            }],
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


REGULAR_BUS_TYPE_NAME = "Xe giường nằm"


def select_preferred_bus_time(bus_times: list[dict]) -> dict | None:
    """Pick the bus_time the user actually wants:

    - Prefer the regular bus ("Xe giường nằm", ~340k) over Limousine VIP (~530k) —
      Limousine is a last resort only.
    - Within the preferred type, pick the LATEST departure of the day ("càng cuối ngày
      càng tốt"), among times that still have seats.
    - Falls back to Limousine (still latest-first) only if no regular bus has seats left.
    """
    if not bus_times:
        return None

    def latest_with_seats(candidates: list[dict]) -> dict | None:
        with_seats = [b for b in candidates if int(b.get("empty_seat", 0) or 0) > 0]
        pool = with_seats or candidates
        if not pool:
            return None
        return max(pool, key=lambda b: b.get("start_time", ""))

    regular = [b for b in bus_times if b.get("bus_type_name") == REGULAR_BUS_TYPE_NAME]
    return latest_with_seats(regular) or latest_with_seats(bus_times)


SEAT_FLOOR_LETTER_PRIORITY = [
    ("Tầng 2", "E"),
    ("Tầng 2", "A"),
    ("Tầng 1", "F"),
    ("Tầng 1", "B"),
]
SEAT_NUMBER_PRIORITY = [3, 2, 4, 1, 5, 6]

DEFAULT_PICKUP_NAME = "493 Nguyễn Trãi"
DEFAULT_DROPOFF_NAME = "VP THẠCH HÀ - HT"
COASTAL_DROPOFF_NAME = "XANH ĐỎ THẠCH LONG - HT"
COASTAL_ROUTE_KEYWORD = "Ven biển"

PAYMENT_METHOD_ONLINE = 3
PAYMENT_TYPE_ONLINE = 8


def select_seat(seat_map: dict) -> dict | None:
    """Pick the best seat from a detail-bus-time `seatMap` per the user's stated preference:
    floor 2 before floor 1, letter E > A (floor 2) / F > B (floor 1), then seat number
    3 > 2 > 4 > 1 > 5 > 6. Skips auxiliary "P-" seats (type 4) and non-empty seats."""
    seats_by_area_letter_number = {}
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
                seats_by_area_letter_number[(area_name, letter, number)] = seat

    for area_name, letter in SEAT_FLOOR_LETTER_PRIORITY:
        for number in SEAT_NUMBER_PRIORITY:
            seat = seats_by_area_letter_number.get((area_name, letter, number))
            if seat:
                return seat
    return None


def is_coastal_route(bus_time: dict) -> bool:
    text = f"{bus_time.get('bus_stage_name', '')} {bus_time.get('hop_name', '')}"
    return COASTAL_ROUTE_KEYWORD.lower() in text.lower()


def find_boarding_point(points: list[dict], name: str) -> dict | None:
    for p in points:
        if p.get("home_pickup_zone_name") == name or p.get("boarding_point_name") == name:
            return p
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


def send_telegram_message(token: str, chat_id: str, text: str) -> dict:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
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
