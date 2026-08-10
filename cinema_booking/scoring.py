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
