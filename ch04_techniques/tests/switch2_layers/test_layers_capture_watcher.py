import pytest

from switch2_layers.hardware.capture_watcher import (
    FIRST_FRAME_TIMEOUT_S,
    REASON_CAPTURE_ENDED,
    REASON_NO_FIRST_FRAME,
    REASON_STALE_FRAME,
    STALE_FRAME_TIMEOUT_S,
    CaptureWatcher,
    capture_stop_reason,
)
from switch2_layers.run_clock import RunClock


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_timeouts_follow_the_issue():
    assert STALE_FRAME_TIMEOUT_S == 2.0
    assert FIRST_FRAME_TIMEOUT_S == 10.0


@pytest.mark.parametrize(
    "ended, latest_frame_t, now_t, expected",
    [
        (True, 5.0, 5.1, REASON_CAPTURE_ENDED),
        (True, None, 0.0, REASON_CAPTURE_ENDED),
        # 起動直後の最初のフレームは 10 秒まで待つ
        (False, None, 0.0, None),
        (False, None, 9.999, None),
        (False, None, 10.0, REASON_NO_FIRST_FRAME),
        # 最新のフレームが 2 秒以上古ければ止まったとみなす
        (False, 5.0, 6.999, None),
        (False, 5.0, 7.0, REASON_STALE_FRAME),
        (False, 5.0, 30.0, REASON_STALE_FRAME),
    ],
)
def test_capture_stop_reason(ended, latest_frame_t, now_t, expected):
    assert capture_stop_reason(ended, latest_frame_t, now_t) == expected


def test_watcher_remembers_the_first_reason():
    monotonic = FakeMonotonic(100.0)
    clock = RunClock(monotonic)
    state = {"ended": False, "latest": 0.5}
    watcher = CaptureWatcher(
        is_ended=lambda: state["ended"],
        latest_frame_t=lambda: state["latest"],
        clock=clock,
    )
    monotonic.now = 101.0
    assert watcher.check_once() is None
    monotonic.now = 102.5
    assert watcher.check_once() == REASON_STALE_FRAME
    # 新しいフレームが届いても、判定は取り消さない
    state["latest"] = 2.5
    assert watcher.check_once() == REASON_STALE_FRAME
    state["ended"] = True
    assert watcher.check_once() == REASON_STALE_FRAME


def test_watcher_waits_for_first_frame():
    monotonic = FakeMonotonic(100.0)
    watcher = CaptureWatcher(
        is_ended=lambda: False,
        latest_frame_t=lambda: None,
        clock=RunClock(monotonic),
    )
    monotonic.now = 109.0
    assert watcher.check_once() is None
    monotonic.now = 110.0
    assert watcher.check_once() == REASON_NO_FIRST_FRAME
