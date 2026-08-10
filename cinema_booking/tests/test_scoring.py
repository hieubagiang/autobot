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
