import numpy as np

from switch2_agent.frame_history import TimedFrame
from switch2_agent.timeline.screen_change import (
    ScreenChangeTracker,
    format_screen_change,
)


def frame(time: float, value: int) -> TimedFrame:
    """すべての画素が value の縮小グレー画像を持つフレームを返す。"""
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.full((4, 4), value, np.uint8),
        sharpness=0.0,
    )


def test_static_since_is_none_before_first_frame():
    assert ScreenChangeTracker().static_since() is None


def test_first_frame_becomes_reference():
    tracker = ScreenChangeTracker(threshold=2.0)
    tracker.feed(frame(1.0, 100))
    assert tracker.static_since() == 1.0


def test_small_differences_keep_the_change_time():
    tracker = ScreenChangeTracker(threshold=2.0)
    tracker.feed(frame(1.0, 100))
    tracker.feed(frame(1.1, 101))
    tracker.feed(frame(1.2, 99))
    assert tracker.static_since() == 1.0


def test_difference_at_or_above_threshold_moves_the_change_time():
    tracker = ScreenChangeTracker(threshold=2.0)
    tracker.feed(frame(1.0, 100))
    tracker.feed(frame(1.1, 102))
    assert tracker.static_since() == 1.1
    # 新しい基準 (102) と比べる
    tracker.feed(frame(1.2, 101))
    assert tracker.static_since() == 1.1
    tracker.feed(frame(1.3, 110))
    assert tracker.static_since() == 1.3


def test_format_screen_change_is_empty_without_frames():
    assert format_screen_change(None, now=10.0, origin=0.0) == ""


def test_format_screen_change_reports_recent_change():
    assert format_screen_change(9.0, now=10.5, origin=0.0) == (
        "## 画面の変化\n- 最新の画面は、直前まで変わっていた (1.5 秒前までの画面と違う)"
    )


def test_format_screen_change_reports_static_screen():
    assert format_screen_change(210.4, now=310.2, origin=10.0) == (
        "## 画面の変化\n"
        "- 最新の画面は、t=200.4 秒 (99.8 秒前) から、ほぼ変わっていない "
        "(縮小した画像どうしの差がほとんど無い)"
    )
