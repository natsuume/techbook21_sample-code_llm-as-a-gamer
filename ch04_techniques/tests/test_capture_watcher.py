import time

import pytest

from switch2_agent.capture_watcher import (
    DEFAULT_WATCH_INTERVAL_S,
    CaptureWatcher,
    capture_lost_reason,
)

WAIT_TIMEOUT = 2.0  # テストがスレッドの進みを待つ上限 (秒)
POLL_INTERVAL = 0.01


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeCapture:
    """見張る対象の偽物。ended と最新のフレームの時刻を持ち、on_lost の呼び出しを数える。"""

    def __init__(self):
        self.ended = False
        self.latest_time: float | None = 9.5
        self.lost_calls = 0
        self.lost_error: Exception | None = None

    def on_lost(self) -> None:
        self.lost_calls += 1
        if self.lost_error is not None:
            raise self.lost_error


def make_watcher() -> tuple[CaptureWatcher, FakeCapture, FakeClock]:
    """スレッドを起動しない CaptureWatcher (stale_timeout_s は 2.0、時刻は 10.0) と、偽のキャプチャー・時計を返す。"""
    capture = FakeCapture()
    clock = FakeClock(10.0)
    watcher = CaptureWatcher(
        capture_ended=lambda: capture.ended,
        latest_frame_time=lambda: capture.latest_time,
        on_lost=capture.on_lost,
        stale_timeout_s=2.0,
        clock=clock,
        start_thread=False,
    )
    return watcher, capture, clock


def test_default_watch_interval():
    assert DEFAULT_WATCH_INTERVAL_S == 0.1


# --- capture_lost_reason ---


@pytest.mark.parametrize(
    "ended, latest_frame_time, expected",
    [
        (True, 9.9, "capture ended"),
        (True, None, "capture ended"),
        (False, 7.0, "no new frame for 2.0 seconds"),
        # ちょうど stale_timeout_s だけ古いフレームも、止まったとみなす
        (False, 8.0, "no new frame for 2.0 seconds"),
        (False, 9.5, None),
        # 最初のフレームが届く前は、古さで判定しない
        (False, None, None),
    ],
)
def test_capture_lost_reason(ended, latest_frame_time, expected):
    assert capture_lost_reason(ended, latest_frame_time, 10.0, 2.0) == expected


# --- check_once ---


def test_check_once_returns_none_while_capture_is_alive():
    watcher, capture, _ = make_watcher()
    assert watcher.check_once() is None
    assert watcher.lost() is None
    assert capture.lost_calls == 0


def test_check_once_records_reason_and_calls_on_lost_once():
    watcher, capture, clock = make_watcher()
    clock.now = 11.5
    assert watcher.check_once() == "no new frame for 2.0 seconds"
    assert watcher.lost() == "no new frame for 2.0 seconds"
    assert capture.lost_calls == 1
    assert watcher.check_once() == "no new frame for 2.0 seconds"
    assert capture.lost_calls == 1


def test_check_once_records_capture_ended():
    watcher, capture, _ = make_watcher()
    capture.ended = True
    assert watcher.check_once() == "capture ended"
    assert watcher.lost() == "capture ended"
    assert capture.lost_calls == 1


def test_lost_reason_is_not_cleared_by_new_frames():
    watcher, capture, clock = make_watcher()
    clock.now = 11.5
    watcher.check_once()
    # その後に新しいフレームが届いても、判定は取り消さない
    capture.latest_time = clock.now
    assert watcher.check_once() == "no new frame for 2.0 seconds"
    assert watcher.lost() == "no new frame for 2.0 seconds"
    assert capture.lost_calls == 1


def test_check_once_stops_output_before_recording_reason():
    watcher, capture, _ = make_watcher()
    capture.ended = True
    reasons_seen_by_on_lost: list[str | None] = []
    # on_lost の中からは lost() を呼べない (ロックを持っている) ため、属性を直接読む
    watcher.on_lost = lambda: reasons_seen_by_on_lost.append(watcher.lost_reason)
    assert watcher.check_once() == "capture ended"
    # 出力を止めた時点では、まだ判定を記録していない (lost() で読めた時点で、出力は止まっている)
    assert reasons_seen_by_on_lost == [None]
    assert watcher.lost() == "capture ended"


def test_check_once_does_not_raise_on_lost_error(caplog):
    watcher, capture, _ = make_watcher()
    capture.ended = True
    capture.lost_error = RuntimeError("bridge failed")
    assert watcher.check_once() == "capture ended"
    assert watcher.lost() == "capture ended"
    assert "on_lost failed" in caplog.text
    assert "bridge failed" in caplog.text


# --- スレッド ---


def test_thread_checks_capture_and_stops_on_close():
    capture = FakeCapture()
    capture.ended = True
    watcher = CaptureWatcher(
        capture_ended=lambda: capture.ended,
        latest_frame_time=lambda: capture.latest_time,
        on_lost=capture.on_lost,
        stale_timeout_s=2.0,
        interval_s=POLL_INTERVAL,
    )
    thread = watcher.watch_thread
    try:
        deadline = time.monotonic() + WAIT_TIMEOUT
        while watcher.lost() is None:
            if time.monotonic() >= deadline:
                pytest.fail(f"watcher did not check within {WAIT_TIMEOUT} seconds")
            time.sleep(POLL_INTERVAL)
        assert watcher.lost() == "capture ended"
        assert capture.lost_calls == 1
    finally:
        watcher.close()
    assert thread is not None
    assert not thread.is_alive()
    assert watcher.watch_thread is None


def test_close_without_thread_does_nothing():
    watcher, _, _ = make_watcher()
    watcher.close()
    assert watcher.watch_thread is None
