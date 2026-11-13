from collections.abc import Sequence

import numpy as np

from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.advisor_frames import (
    DEFAULT_MEANINGFUL_MAX,
    DEFAULT_RECENT_MAX,
    AdvisorFrames,
    select_advisor_frames,
)
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore


def frame(time: float) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((2, 2), np.uint8),
        sharpness=0.0,
    )


def times(frames: Sequence[TimedFrame]) -> list[float]:
    return [f.time for f in frames]


def history_with(max_age_s: float, *frame_times: float) -> FrameHistory:
    history = FrameHistory(max_age_s)
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


def test_default_max_counts_are_5():
    assert DEFAULT_RECENT_MAX == 5
    assert DEFAULT_MEANINGFUL_MAX == 5


def test_recent_frames_are_chosen_from_history_of_max_age_before_now():
    # 履歴 (2 秒) はフレームの時刻で古いものを捨てるため 8.0 と 8.5 も残っているが、
    # 選ぶ区間は now (10.6) から max_age_s (2.0) 前の 8.6 から
    history = history_with(2.0, 8.0, 8.5, 9.0, 9.5, 10.0)
    selector = RecordingSelector()
    frames = select_advisor_frames(history, store_with(), selector, 10.6)
    assert selector.calls == [([9.0, 9.5, 10.0], DEFAULT_RECENT_MAX)]
    assert times(frames.recent) == [9.0, 9.5, 10.0]


def test_recent_frames_are_selected_by_selector_with_recent_max():
    history = history_with(60.0, 9.6, 9.7, 9.8, 9.9, 10.0)
    selector = RecordingSelector()
    select_advisor_frames(history, store_with(), selector, 10.0, recent_max=3)
    assert selector.calls == [([9.6, 9.7, 9.8, 9.9, 10.0], 3)]


def test_recent_frames_are_evenly_spaced_over_history():
    frame_times = [1.0 + i for i in range(9)]
    history = history_with(60.0, *frame_times)
    frames = select_advisor_frames(history, store_with(), EvenIntervalSelector(), 9.5)
    # 9 枚から最初と最後を含めて等間隔に 5 枚を選ぶ
    assert times(frames.recent) == [1.0, 3.0, 5.0, 7.0, 9.0]


def test_meaningful_frames_are_before_first_recent_frame():
    history = history_with(2.0, 9.0, 9.5, 10.0)
    store = store_with(7.0, 8.9, 9.0, 9.2)
    frames = select_advisor_frames(history, store, EvenIntervalSelector(), 10.0)
    # 直近の最初のフレーム (9.0) ちょうどと、それより後 (9.2) の意味のあるフレームは選ばない
    assert times(frames.meaningful) == [7.0, 8.9]


def test_meaningful_frames_are_limited_to_newest_meaningful_max():
    store = store_with(1.0, 2.0, 3.0, 4.0)
    frames = select_advisor_frames(
        history_with(60.0, 10.0),
        store,
        EvenIntervalSelector(),
        10.0,
        meaningful_max=2,
    )
    assert times(frames.meaningful) == [3.0, 4.0]


def test_meaningful_frames_are_limited_to_default_meaningful_max():
    store = store_with(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    frames = select_advisor_frames(
        history_with(60.0, 10.0), store, EvenIntervalSelector(), 10.0
    )
    assert times(frames.meaningful) == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_meaningful_frames_are_before_now_when_history_is_empty():
    store = store_with(5.0, 9.0, 10.0)
    frames = select_advisor_frames(
        history_with(2.0), store, EvenIntervalSelector(), 10.0
    )
    assert frames.recent == []
    # 直近のフレームが無ければ、now (10.0) より前の意味のあるフレームを選ぶ
    assert times(frames.meaningful) == [5.0, 9.0]


def test_meaningful_frames_are_before_now_when_history_has_no_frame_in_window():
    # 履歴のフレーム (6.0, 7.0) は、選ぶ区間 (8.0 から 10.0) より古い
    history = history_with(2.0, 6.0, 7.0)
    store = store_with(5.0, 6.5, 9.5)
    frames = select_advisor_frames(history, store, EvenIntervalSelector(), 10.0)
    assert frames.recent == []
    assert times(frames.meaningful) == [5.0, 6.5, 9.5]


def test_advisor_frames_holds_recent_and_meaningful():
    recent = [frame(1.0)]
    meaningful = [frame(0.5)]
    frames = AdvisorFrames(recent=recent, meaningful=meaningful)
    assert frames.recent is recent
    assert frames.meaningful is meaningful
