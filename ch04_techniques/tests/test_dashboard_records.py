import numpy as np
import pytest
from switch2_remote.controller_state import PadState

from switch2_agent.dashboard_records import (
    FRAMES_RECORD_FILE_NAME,
    PAD_RECORD_FILE_NAME,
    THUMB_HEIGHT,
    THUMB_JPEG_QUALITY,
    THUMB_WIDTH,
    THUMBS_DIR_NAME,
    DetectorSnapshot,
    FrameStep,
    PadReport,
    build_frame_record,
    build_pad_record,
    build_record_end_event,
    build_record_error_event,
    detector_event,
    reference_sharpness,
    snapshot_detector,
    thumb_file_name,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.meaningful_frames import SharpnessValleyDetector


def frame(time: float, sharpness: float, gray: int = 0) -> TimedFrame:
    """時刻 time・鮮明度 sharpness の TimedFrame を返す。gray_small は全画素が gray の 180x320 の画像。"""
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.full((180, 320), gray, np.uint8),
        sharpness=sharpness,
    )


def feed(detector: SharpnessValleyDetector, frame: TimedFrame) -> FrameStep:
    """frame を detector に入れ、その前後の状態を FrameStep にして返す (dashboard_recorder.feed_detector と同じ順)。"""
    before = snapshot_detector(detector)
    kept = detector.feed(frame)
    return FrameStep(
        frame=frame,
        before=before,
        after=snapshot_detector(detector),
        kept=kept,
        valley_ratio=detector.valley_ratio,
        after_drop=False,
    )


def feed_all(
    detector: SharpnessValleyDetector, frames: list[TimedFrame]
) -> list[FrameStep]:
    return [feed(detector, f) for f in frames]


# --- 定数とファイル名 ---


def test_constants():
    assert FRAMES_RECORD_FILE_NAME == "frames.jsonl"
    assert PAD_RECORD_FILE_NAME == "pad.jsonl"
    assert THUMBS_DIR_NAME == "thumbs"
    assert (THUMB_WIDTH, THUMB_HEIGHT) == (320, 180)
    assert THUMB_JPEG_QUALITY == 70


@pytest.mark.parametrize(
    "mono, expected",
    [
        (12345.678901, "thumbs/12345678901.jpg"),
        (1.5, "thumbs/1500000.jpg"),
        # 0 埋めしない
        (0.0, "thumbs/0.jpg"),
        # マイクロ秒は切り捨てではなく丸める
        (0.0000016, "thumbs/2.jpg"),
        # 1 ミリ秒以内の 2 枚も別の名前になる
        (1.5004, "thumbs/1500400.jpg"),
    ],
)
def test_thumb_file_name_is_microseconds_under_thumbs(mono, expected):
    assert thumb_file_name(mono) == expected


# --- snapshot_detector ---


def test_snapshot_of_new_detector_has_no_frames():
    assert snapshot_detector(SharpnessValleyDetector()) == DetectorSnapshot(
        phase="rising", peak=None, valley=None, last_kept=None
    )


def test_snapshot_refers_to_detector_frames_without_copying():
    detector = SharpnessValleyDetector()
    frames = [frame(0.0, 100), frame(0.1, 200), frame(0.2, 50), frame(0.3, 150)]
    for f in frames[:3]:
        detector.feed(f)
    snapshot = snapshot_detector(detector)
    assert snapshot.phase == "falling"
    assert snapshot.peak is frames[1]
    assert snapshot.valley is frames[2]
    assert snapshot.last_kept is None
    # 後で判定を進めても、取った状態は変わらない
    detector.feed(frames[3])
    assert snapshot.phase == "falling"
    assert snapshot.peak is frames[1]
    assert snapshot.valley is frames[2]
    assert snapshot.last_kept is None
    assert snapshot_detector(detector).last_kept is frames[1]


# --- detector_event ---


def test_event_is_none_for_first_frame():
    step = feed(SharpnessValleyDetector(), frame(0.0, 100))
    assert detector_event(step) is None


def test_event_is_none_when_peak_is_updated():
    steps = feed_all(SharpnessValleyDetector(), [frame(0.0, 100), frame(0.1, 200)])
    assert detector_event(steps[1]) is None


def test_event_is_none_for_shallow_fluctuation():
    # 80 は山 (100) * 0.5 より大きいので、谷に向かい始めない
    steps = feed_all(SharpnessValleyDetector(), [frame(0.0, 100), frame(0.1, 80)])
    assert steps[1].after.phase == "rising"
    assert detector_event(steps[1]) is None


def test_event_is_fall_when_sharpness_drops_to_valley_ratio_of_peak():
    steps = feed_all(
        SharpnessValleyDetector(), [frame(0.0, 100), frame(0.1, 200), frame(0.2, 90)]
    )
    assert detector_event(steps[2]) == {"kind": "fall"}


def test_event_is_none_when_valley_is_updated():
    steps = feed_all(
        SharpnessValleyDetector(),
        [frame(0.0, 200), frame(0.1, 90), frame(0.2, 50)],
    )
    assert steps[2].after.valley is steps[2].frame
    assert detector_event(steps[2]) is None


def test_event_is_none_until_recovery_from_valley():
    # 70 * 0.5 = 35 は谷の 50 に届かないので、まだ谷を確定しない
    steps = feed_all(
        SharpnessValleyDetector(),
        [frame(0.0, 200), frame(0.1, 50), frame(0.2, 70)],
    )
    assert steps[2].after.phase == "falling"
    assert detector_event(steps[2]) is None


def test_event_is_close_with_kept_candidate_and_no_last_kept():
    frames = [frame(0.0, 100), frame(0.1, 200), frame(0.2, 50), frame(0.3, 150)]
    steps = feed_all(SharpnessValleyDetector(), frames)
    assert steps[3].kept is frames[1]
    assert detector_event(steps[3]) == {
        "kind": "close",
        "candidate_mono": 0.1,
        "candidate_sharpness": 200.0,
        "kept": True,
        # 直前に残したフレームが無いので、差分を求めない
        "similar_diff": None,
    }


def test_event_is_close_with_kept_candidate_and_diff_from_last_kept():
    first = [frame(0.0, 100, 10), frame(0.1, 200, 10), frame(0.2, 50), frame(0.3, 150)]
    # 2 回目の谷で確定する山 (300) は、1 回目に残した山 (gray 10) と違う画像 (gray 200)
    second = [frame(1.0, 300, 200), frame(1.1, 100), frame(1.2, 250)]
    steps = feed_all(SharpnessValleyDetector(), first + second)
    close = steps[-1]
    assert close.kept is second[0]
    assert detector_event(close) == {
        "kind": "close",
        "candidate_mono": 1.0,
        "candidate_sharpness": 300.0,
        "kept": True,
        "similar_diff": 190.0,
    }


def test_event_is_close_with_similar_candidate_not_kept():
    first = [frame(0.0, 100, 10), frame(0.1, 200, 10), frame(0.2, 50), frame(0.3, 150)]
    # 2 回目の谷で確定する山 (300) は、1 回目に残した山と同じ画像なので残さない
    second = [frame(1.0, 300, 10), frame(1.1, 100), frame(1.2, 250)]
    steps = feed_all(SharpnessValleyDetector(), first + second)
    close = steps[-1]
    assert close.kept is None
    assert detector_event(close) == {
        "kind": "close",
        "candidate_mono": 1.0,
        "candidate_sharpness": 300.0,
        "kept": False,
        "similar_diff": 0.0,
    }


# --- reference_sharpness ---


def test_reference_while_rising_is_peak_times_valley_ratio():
    detector = SharpnessValleyDetector(valley_ratio=0.25)
    steps = feed_all(detector, [frame(0.0, 300), frame(0.1, 400)])
    assert steps[1].after.phase == "rising"
    assert reference_sharpness(steps[1].after, 0.25) == pytest.approx(100.0)


def test_reference_while_falling_is_valley_divided_by_valley_ratio():
    detector = SharpnessValleyDetector(valley_ratio=0.25)
    steps = feed_all(detector, [frame(0.0, 400), frame(0.1, 80)])
    assert steps[1].after.phase == "falling"
    assert reference_sharpness(steps[1].after, 0.25) == pytest.approx(320.0)


# --- build_frame_record ---


def test_frame_record_of_first_frame():
    step = feed(SharpnessValleyDetector(), frame(12.0, 100.0))
    record = build_frame_record(step, None)
    assert list(record) == [
        "mono",
        "sharpness",
        "diff",
        "phase",
        "peak_mono",
        "peak_sharpness",
        "ref",
        "event",
        "thumb",
    ]
    assert record == {
        "mono": 12.0,
        "sharpness": 100.0,
        "diff": None,
        "phase": "rising",
        "peak_mono": 12.0,
        "peak_sharpness": 100.0,
        "ref": 50.0,
        "event": None,
        "thumb": "thumbs/12000000.jpg",
    }


def test_frame_record_of_fall_has_after_state_and_given_diff():
    steps = feed_all(
        SharpnessValleyDetector(),
        [frame(12.0, 100.0), frame(12.1, 200.0), frame(12.2, 90.0)],
    )
    record = build_frame_record(steps[2], 0.09)
    assert record == {
        "mono": 12.2,
        "sharpness": 90.0,
        "diff": 0.09,
        "phase": "falling",
        "peak_mono": 12.1,
        "peak_sharpness": 200.0,
        # falling なので 谷 (90) / valley_ratio (0.5)
        "ref": pytest.approx(180.0),
        "event": {"kind": "fall"},
        "thumb": "thumbs/12200000.jpg",
    }


def test_frame_record_of_close_has_new_segment_peak():
    frames = [frame(1.0, 100), frame(1.1, 200), frame(1.2, 50), frame(1.3, 150)]
    steps = feed_all(SharpnessValleyDetector(), frames)
    record = build_frame_record(steps[3], 3.5)
    # 谷を確定した後は、回復したフレームから新しい区間が始まる
    assert record["phase"] == "rising"
    assert record["peak_mono"] == 1.3
    assert record["peak_sharpness"] == 150.0
    assert record["ref"] == pytest.approx(75.0)
    assert record["diff"] == 3.5
    assert record["event"] == {
        "kind": "close",
        "candidate_mono": 1.1,
        "candidate_sharpness": 200.0,
        "kept": True,
        "similar_diff": None,
    }


# --- build_pad_record ---


def test_pad_record_formats_state_of_sent_report():
    report = PadReport(
        mono=12345.678901,
        source="apply",
        state=PadState(frozenset({"A"}), {"LS": (0.8, 0.0)}),
        ok=True,
        error=None,
    )
    record = build_pad_record(report)
    assert list(record) == ["mono", "source", "state", "ok", "error"]
    assert record == {
        "mono": 12345.678901,
        "source": "apply",
        "state": "A+LS:0.8,0",
        "ok": True,
        "error": None,
    }


def test_pad_record_of_failed_neutral_report():
    report = PadReport(
        mono=2.5, source="neutral", state=PadState(), ok=False, error="pipe closed"
    )
    assert build_pad_record(report) == {
        "mono": 2.5,
        "source": "neutral",
        "state": "none",
        "ok": False,
        "error": "pipe closed",
    }


# --- run.jsonl の行 ---


def test_record_error_event():
    assert build_record_error_event("frames", "No space left on device") == {
        "event": "record_error",
        "kind": "frames",
        "error": "No space left on device",
    }


def test_record_end_event():
    assert build_record_end_event({"frames": 3, "pad": 5}, {"frames": 1, "pad": 0}) == {
        "event": "record_end",
        "written": {"frames": 3, "pad": 5},
        "dropped": {"frames": 1, "pad": 0},
    }
