from cinema_booking.scoring import vertical_score, is_center_half, seat_sort_key, leaves_isolated_gap
from cinema_booking.types import Seat, SeatStatus, SeatZone


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
    # [sold, LONE EMPTY, taken, taken] -> block at indices 2,3 leaves index 1 isolated.
    row = [seat(SeatStatus.SOLD), seat(SeatStatus.AVAILABLE),
           seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE)]
    assert leaves_isolated_gap(row, start_idx=2, length=2) is True


def test_two_empty_seats_on_the_left_is_not_isolated():
    # [empty, empty, taken, taken] -> the two empties on the left aren't a lone gap.
    row = [seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE),
           seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE)]
    assert leaves_isolated_gap(row, start_idx=2, length=2) is False


def test_no_gap_when_block_is_at_row_end():
    # [sold, taken, taken] -> block at row end, no isolated gap on right (row edge).
    row = [seat(SeatStatus.SOLD), seat(SeatStatus.AVAILABLE), seat(SeatStatus.AVAILABLE)]
    assert leaves_isolated_gap(row, start_idx=1, length=2) is False


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
