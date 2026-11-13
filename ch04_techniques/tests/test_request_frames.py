from collections.abc import Sequence

import numpy as np

from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.frame_selection.request_frames import (
    RequestFrames,
    select_request_frames,
)


def frame(time: float) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((2, 2), np.uint8),
        sharpness=0.0,
    )


def times(frames: Sequence[TimedFrame]) -> list[float]:
    return [f.time for f in frames]


def history_with(*frame_times: float) -> FrameHistory:
    history = FrameHistory(60.0)
    for t in frame_times:
        history.add(frame(t))
    return history


def store_with(*frame_times: float) -> MeaningfulFrameStore:
    store = MeaningfulFrameStore()
    for t in frame_times:
        store.add(frame(t))
    return store


class RecordingSelector:
    """select の引数を記録し、受け取ったフレームをそのまま返す偽の RecentFrameSelector。"""

    def __init__(self):
        self.calls: list[tuple[list[float], int]] = []

    def select(self, frames: Sequence[TimedFrame], max_count: int) -> list[TimedFrame]:
        self.calls.append((times(frames), max_count))
        return list(frames)


def test_first_request_uses_window_before_now():
    history = history_with(9.0, 9.4, 9.6, 9.8, 10.0)
    frames = select_request_frames(
        history, store_with(), EvenIntervalSelector(), None, 10.0
    )
    # 最初の要求の直近の区間は now - 0.5 = 9.5 から now まで
    assert times(frames.recent) == [9.6, 9.8, 10.0]


def test_first_request_uses_given_first_window():
    history = history_with(9.0, 9.4, 9.6, 9.8, 10.0)
    frames = select_request_frames(
        history, store_with(), EvenIntervalSelector(), None, 10.0, first_window_s=0.7
    )
    assert times(frames.recent) == [9.4, 9.6, 9.8, 10.0]


def test_later_request_window_starts_at_previous_send():
    history = history_with(9.0, 9.4, 9.6, 9.8, 10.0)
    frames = select_request_frames(
        history, store_with(), EvenIntervalSelector(), 9.7, 10.0
    )
    assert times(frames.recent) == [9.8, 10.0]


def test_recent_frames_are_selected_by_selector_with_recent_max():
    history = history_with(9.6, 9.7, 9.8, 9.9, 10.0)
    selector = RecordingSelector()
    select_request_frames(history, store_with(), selector, 9.65, 10.0, recent_max=3)
    assert selector.calls == [([9.7, 9.8, 9.9, 10.0], 3)]


def test_recent_frames_are_limited_to_recent_max():
    frame_times = [10.0 + i / 10 for i in range(7)]
    history = history_with(*frame_times)
    frames = select_request_frames(
        history, store_with(), EvenIntervalSelector(), 10.0, 11.0, recent_max=3
    )
    # 7 枚から最初・真ん中・最後の 3 枚を選ぶ
    assert times(frames.recent) == [frame_times[0], frame_times[3], frame_times[6]]


def test_meaningful_frames_are_before_previous_send():
    store = store_with(8.0, 9.0, 9.65, 9.9)
    frames = select_request_frames(
        history_with(9.8, 10.0), store, EvenIntervalSelector(), 9.7, 10.0
    )
    # 前回の送信 (9.7) より後の 9.9 は、意味のあるフレームとして載せない
    assert times(frames.meaningful) == [8.0, 9.0, 9.65]


def test_meaningful_frames_of_first_request_are_before_first_window():
    store = store_with(9.0, 9.6)
    frames = select_request_frames(
        history_with(9.8, 10.0), store, EvenIntervalSelector(), None, 10.0
    )
    assert times(frames.meaningful) == [9.0]


def test_window_starts_at_previous_operation_before_first_window():
    # まだ要求が成功していない (previous_sent_at が None) うちに、最初の区間の始まり (now - 0.5 = 10.5) より前の
    # 9.8 に中立に戻した: 9.8 からを直近の区間にし、意味のあるフレームは 9.8 より前に限る
    history = history_with(9.5, 9.9, 10.2, 10.8)
    store = store_with(9.0, 9.7, 10.0)
    frames = select_request_frames(
        history, store, EvenIntervalSelector(), None, 11.0, previous_operation_at=9.8
    )
    assert times(frames.recent) == [9.9, 10.2, 10.8]
    assert times(frames.meaningful) == [9.0, 9.7]


def test_window_does_not_move_when_previous_operation_is_after_start():
    # 前回の操作 (9.9) が前回の送信 (9.7) より後なら、直近の区間の始まりは前回の送信のまま
    history = history_with(9.6, 9.8, 10.0)
    store = store_with(9.65, 9.85)
    frames = select_request_frames(
        history, store, EvenIntervalSelector(), 9.7, 10.0, previous_operation_at=9.9
    )
    assert times(frames.recent) == [9.8, 10.0]
    assert times(frames.meaningful) == [9.65]


def test_first_window_does_not_move_when_previous_operation_is_inside_it():
    # 前回の操作 (9.7) が最初の区間 (9.5 から 10.0) の中なら、始まりは now - first_window_s のまま
    history = history_with(9.4, 9.6, 9.8, 10.0)
    frames = select_request_frames(
        history,
        store_with(9.3),
        EvenIntervalSelector(),
        None,
        10.0,
        previous_operation_at=9.7,
    )
    assert times(frames.recent) == [9.6, 9.8, 10.0]
    assert times(frames.meaningful) == [9.3]


def test_meaningful_frames_are_limited_to_newest_meaningful_max():
    store = store_with(1.0, 2.0, 3.0, 4.0)
    frames = select_request_frames(
        history_with(10.0),
        store,
        EvenIntervalSelector(),
        9.0,
        10.0,
        meaningful_max=2,
    )
    assert times(frames.meaningful) == [3.0, 4.0]


def test_request_frames_holds_recent_and_meaningful():
    recent = [frame(1.0)]
    meaningful = [frame(0.5)]
    frames = RequestFrames(recent=recent, meaningful=meaningful)
    assert frames.recent is recent
    assert frames.meaningful is meaningful
