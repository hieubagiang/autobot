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
    # Wire id of this seat's OTHER half, for a physical two-person seat that a provider
    # merges into a single Seat (e.g. Beta's "sweetheart" pair). None for every ordinary
    # seat. Providers that have no such concept never set this.
    partner_id: str | None = None


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
