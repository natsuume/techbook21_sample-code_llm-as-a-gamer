import numpy as np
import pytest

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import (
    MeanAbsDiffSimilarity,
    MeaningfulFrameStore,
    SharpnessValleyDetector,
)


def frame(time: float, sharpness: float = 0.0, gray: int = 0) -> TimedFrame:
    """時刻 time・鮮明度 sharpness の TimedFrame を返す。gray_small は全画素が gray の 180x320 の画像。"""
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.full((180, 320), gray, np.uint8),
        sharpness=sharpness,
    )


def sharpness_series(values: list[float], gray: int = 0) -> list[TimedFrame]:
    """鮮明度の列 values を、時刻 0.0, 0.1, … のフレームにする。"""
    return [frame(i / 10, value, gray) for i, value in enumerate(values)]


def feed_all(
    detector: SharpnessValleyDetector, frames: list[TimedFrame]
) -> list[TimedFrame | None]:
    """frames を順に feed し、各回の戻り値を返す。"""
    return [detector.feed(f) for f in frames]


def kept(results: list[TimedFrame | None]) -> list[TimedFrame]:
    return [result for result in results if result is not None]


class RecordingSimilarity:
    """is_similar の呼び出しを記録し、決まった答えを返す偽の FrameSimilarity。"""

    def __init__(self, answer: bool):
        self.answer = answer
        self.calls: list[tuple[TimedFrame, TimedFrame]] = []

    def is_similar(self, a: TimedFrame, b: TimedFrame) -> bool:
        self.calls.append((a, b))
        return self.answer


# --- MeanAbsDiffSimilarity ---


def test_similarity_default_threshold_is_poc_dup_th():
    assert MeanAbsDiffSimilarity().threshold == 0.15


def test_similarity_is_true_for_same_image():
    assert MeanAbsDiffSimilarity().is_similar(frame(0.0, gray=50), frame(1.0, gray=50))


def test_similarity_is_false_when_diff_reaches_threshold():
    # 差分 1.0 は既定のしきい値 0.15 以上なので似ていない
    assert not MeanAbsDiffSimilarity().is_similar(
        frame(0.0, gray=50), frame(1.0, gray=51)
    )


def test_similarity_uses_given_threshold():
    similarity = MeanAbsDiffSimilarity(threshold=2.0)
    assert similarity.is_similar(frame(0.0, gray=50), frame(1.0, gray=51))
    assert not similarity.is_similar(frame(0.0, gray=50), frame(1.0, gray=52))


# --- SharpnessValleyDetector ---


@pytest.mark.parametrize("valley_ratio", [0.0, 1.0, -0.1, 1.5])
def test_detector_rejects_valley_ratio_out_of_open_interval(valley_ratio):
    with pytest.raises(ValueError):
        SharpnessValleyDetector(valley_ratio=valley_ratio)


def test_detector_returns_previous_peak_after_valley():
    frames = sharpness_series([100, 200, 90, 50, 120])
    results = feed_all(SharpnessValleyDetector(), frames)
    # 50 の谷から 120 に回復したところで、谷の前の山 (200) が確定する
    assert results[:4] == [None, None, None, None]
    assert results[4] is frames[1]


def test_detector_ignores_shallow_fluctuation():
    # どの下がり方も、山の鮮明度 * 0.5 以下まで下がらないので谷にならない
    frames = sharpness_series([100, 80, 120, 70, 130, 90, 140])
    assert kept(feed_all(SharpnessValleyDetector(), frames)) == []


def test_detector_returns_peak_from_start_to_first_valley():
    # 最初のフレームが区間の山のまま、最初の谷が来る
    frames = sharpness_series([200, 150, 80, 200])
    results = feed_all(SharpnessValleyDetector(), frames)
    assert results[3] is frames[0]


def test_detector_waits_until_recovery_from_valley():
    frames = sharpness_series([200, 90, 60, 40, 79, 85])
    results = feed_all(SharpnessValleyDetector(), frames)
    # 79 * 0.5 = 39.5 は谷の 40 に届かないので、まだ確定しない。85 * 0.5 = 42.5 で確定する
    assert results[4] is None
    assert results[5] is frames[0]


def test_detector_does_not_return_peak_of_current_segment():
    frames = sharpness_series([100, 200, 50, 120, 300, 400])
    detector = SharpnessValleyDetector()
    results = feed_all(detector, frames)
    # 谷の後に始まった区間の山 (400) は、次の谷が来るまで返さない
    assert kept(results) == [frames[1]]
    assert detector.phase == "rising"
    assert detector.peak is frames[5]


def test_detector_does_not_return_candidate_similar_to_last_kept():
    first = sharpness_series([100, 200, 50, 150], gray=10)
    # 2 回目の谷で確定する山 (300) は、1 回目に残した山と同じ画像なので返さない
    second = [frame(1.0, 300, 10), frame(1.1, 100, 10), frame(1.2, 250, 10)]
    # 3 回目の谷で確定する山 (400) は、違う画像なので返す
    third = [frame(2.0, 400, 200), frame(2.1, 100, 200), frame(2.2, 300, 200)]
    detector = SharpnessValleyDetector()
    results = feed_all(detector, first + second + third)
    assert kept(results) == [first[1], third[0]]


def test_detector_compares_candidate_with_last_kept_frame():
    similarity = RecordingSimilarity(answer=False)
    frames = sharpness_series([100, 200, 50, 150, 300, 100, 250])
    detector = SharpnessValleyDetector(similarity=similarity)
    results = feed_all(detector, frames)
    # 1 回目の候補は比べる相手が無いので判定を使わない。2 回目は (候補, 直前に残したフレーム) で判定する
    assert kept(results) == [frames[1], frames[4]]
    assert similarity.calls == [(frames[4], frames[1])]
    assert detector.last_kept is frames[4]


def test_detector_keeps_last_kept_when_candidate_is_similar():
    similarity = RecordingSimilarity(answer=True)
    frames = sharpness_series([100, 200, 50, 150, 300, 100, 250])
    detector = SharpnessValleyDetector(similarity=similarity)
    assert kept(feed_all(detector, frames)) == [frames[1]]
    assert detector.last_kept is frames[1]


# --- MeaningfulFrameStore ---


def times(frames: list[TimedFrame]) -> list[float]:
    return [f.time for f in frames]


def test_store_latest_before_returns_newest_frames_oldest_first():
    store = MeaningfulFrameStore()
    for t in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]:
        store.add(frame(t))
    assert times(store.latest_before(5.0, 3)) == [2.0, 3.0, 4.0]


def test_store_latest_before_excludes_frame_at_given_time():
    store = MeaningfulFrameStore()
    for t in [1.0, 2.0, 3.0]:
        store.add(frame(t))
    assert times(store.latest_before(3.0, 5)) == [1.0, 2.0]


def test_store_latest_before_returns_what_it_has():
    store = MeaningfulFrameStore()
    for t in [1.0, 2.0]:
        store.add(frame(t))
    assert times(store.latest_before(10.0, 5)) == [1.0, 2.0]
    assert store.latest_before(1.0, 5) == []


def test_store_drops_oldest_frame_over_capacity():
    store = MeaningfulFrameStore(capacity=3)
    for t in [1.0, 2.0, 3.0, 4.0, 5.0]:
        store.add(frame(t))
    assert times(store.latest_before(10.0, 10)) == [3.0, 4.0, 5.0]


def test_store_default_capacity_is_50():
    assert MeaningfulFrameStore().capacity == 50
