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
