import math

import numpy as np

from switch2_agent.frame_history import FrameHistory, TimedFrame


def frame(time: float) -> TimedFrame:
    """時刻 time の小さな TimedFrame を返す (画像の中身はこのテストでは使わない)。"""
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((2, 2), np.uint8),
        sharpness=0.0,
    )


def times(frames: list[TimedFrame]) -> list[float]:
    return [f.time for f in frames]


def all_frames(history: FrameHistory) -> list[TimedFrame]:
    return history.between(-math.inf, math.inf)


def test_latest_is_none_without_frames():
    assert FrameHistory(5.0).latest() is None


def test_latest_returns_newest_frame():
    history = FrameHistory(5.0)
    history.add(frame(1.0))
    history.add(frame(2.0))
    assert history.latest().time == 2.0


def test_add_drops_frames_older_than_max_age():
    history = FrameHistory(1.0)
    for t in [0.0, 0.5, 1.0, 1.6]:
        history.add(frame(t))
    # 1.6 - 1.0 = 0.6 より古い 0.0 と 0.5 を捨てる
    assert times(all_frames(history)) == [1.0, 1.6]


def test_add_keeps_frame_exactly_max_age_old():
    history = FrameHistory(1.0)
    history.add(frame(0.0))
    history.add(frame(1.0))
    assert times(all_frames(history)) == [0.0, 1.0]


def test_between_includes_both_ends():
    history = FrameHistory(10.0)
    for t in [1.0, 2.0, 3.0, 4.0]:
        history.add(frame(t))
    assert times(history.between(2.0, 3.0)) == [2.0, 3.0]


def test_between_returns_frames_oldest_first():
    history = FrameHistory(10.0)
    for t in [1.0, 2.0, 3.0]:
        history.add(frame(t))
    assert times(history.between(0.0, 10.0)) == [1.0, 2.0, 3.0]


def test_between_returns_empty_list_without_matching_frames():
    history = FrameHistory(10.0)
    for t in [1.0, 2.0]:
        history.add(frame(t))
    assert history.between(1.2, 1.8) == []
