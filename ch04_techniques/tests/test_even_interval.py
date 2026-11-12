import numpy as np
import pytest

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector


def frames(count: int) -> list[TimedFrame]:
    """時刻 0.0, 1.0, … の count 枚の小さな TimedFrame を返す (時刻は添字と同じ値)。"""
    return [
        TimedFrame(
            time=float(i),
            nv12=np.zeros((3, 2), np.uint8),
            gray_small=np.zeros((2, 2), np.uint8),
            sharpness=0.0,
        )
        for i in range(count)
    ]


def indexes(selected: list[TimedFrame]) -> list[int]:
    return [int(f.time) for f in selected]


@pytest.mark.parametrize("count, max_count", [(3, 5), (5, 5), (1, 1), (0, 5)])
def test_select_returns_all_frames_when_not_more_than_max(count, max_count):
    assert indexes(EvenIntervalSelector().select(frames(count), max_count)) == list(
        range(count)
    )


@pytest.mark.parametrize(
    "count, max_count, expected",
    [
        (9, 5, [0, 2, 4, 6, 8]),
        (10, 4, [0, 3, 6, 9]),
        (13, 5, [0, 3, 6, 9, 12]),
    ],
)
def test_select_picks_evenly_spaced_frames_including_first_and_last(
    count, max_count, expected
):
    selected = indexes(EvenIntervalSelector().select(frames(count), max_count))
    assert selected == expected
    assert selected[0] == 0
    assert selected[-1] == count - 1


def test_select_uses_rounded_index():
    # 添字は round(i * (n - 1) / (max_count - 1))。n = 6, max_count = 4 では 0, 1.67, 3.33, 5 → 0, 2, 3, 5
    assert indexes(EvenIntervalSelector().select(frames(6), 4)) == [0, 2, 3, 5]


def test_select_with_max_count_one_returns_last_frame():
    assert indexes(EvenIntervalSelector().select(frames(4), 1)) == [3]


@pytest.mark.parametrize("max_count", [0, -1])
def test_select_rejects_max_count_not_positive(max_count):
    with pytest.raises(ValueError):
        EvenIntervalSelector().select(frames(3), max_count)
