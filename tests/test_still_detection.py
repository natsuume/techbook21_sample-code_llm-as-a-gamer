import numpy as np

from switch2_remote.still_detection import STILL_TH, motion_between, next_still_count


def test_motion_between_returns_max_without_previous_frame():
    small = np.zeros((180, 320), np.uint8)
    assert motion_between(None, small) == 255.0


def test_motion_between_is_zero_for_identical_frames():
    small = np.full((180, 320), 128, np.uint8)
    assert motion_between(small.copy(), small) == 0.0


def test_motion_between_is_mean_absolute_difference():
    prev_small = np.zeros((2, 2), np.uint8)
    # 4 画素のうち 1 画素だけ 100 変わると、平均絶対差は 25
    small = np.array([[100, 0], [0, 0]], np.uint8)
    assert motion_between(prev_small, small) == 25.0


def test_motion_between_does_not_wrap_around_on_decrease():
    # uint8 の引き算で桁あふれせず、明るさが下がった場合も差の絶対値になる
    prev_small = np.full((2, 2), 200, np.uint8)
    small = np.full((2, 2), 50, np.uint8)
    assert motion_between(prev_small, small) == 150.0


def test_next_still_count_increments_when_motion_is_below_threshold():
    assert next_still_count(3, STILL_TH - 0.1) == 4


def test_next_still_count_resets_when_motion_reaches_threshold():
    assert next_still_count(3, STILL_TH) == 0
    assert next_still_count(3, 255.0) == 0


def test_next_still_count_uses_given_threshold():
    assert next_still_count(0, 1.0, still_th=2.0) == 1
    assert next_still_count(5, 1.0, still_th=0.5) == 0
