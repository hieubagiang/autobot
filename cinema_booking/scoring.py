from cinema_booking.types import Seat, SeatStatus, SeatMap, SeatZone

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


def leaves_isolated_gap(row_seats: list[Seat], start_idx: int, length: int) -> bool:
    def is_available(idx: int) -> bool:
        return 0 <= idx < len(row_seats) and row_seats[idx].status == SeatStatus.AVAILABLE

    left = start_idx - 1
    if is_available(left) and not is_available(left - 1):
        return True
    right = start_idx + length
    if is_available(right) and not is_available(right + 1):
        return True
    return False


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
