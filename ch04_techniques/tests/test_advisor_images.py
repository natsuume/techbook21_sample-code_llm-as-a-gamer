import numpy as np

from switch2_agent.advisor_images import build_advisor_images, label_advisor_frames
from switch2_agent.decision_request import LabeledImage
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.advisor_frames import AdvisorFrames

NOW = 100.0
# NOW と異なる、経過秒の起点
ORIGIN = 40.0
# sample_frames の並び (意味のあるフレーム → フレームの履歴から選んだフレーム) のラベル
EXPECTED_LABELS = [
    "過去の場面 1/2 (t=47.66 秒、12.34 秒前)",
    "過去の場面 2/2 (t=55.00 秒、5.00 秒前)",
    "直近 1/3 (t=59.79 秒、0.21 秒前)",
    "直近 2/3 (t=59.90 秒、0.10 秒前)",
    "直近 3/3 (t=60.00 秒、0.00 秒前)",
]
EXPECTED_TIMES = [87.66, 95.0, 99.79, 99.9, 100.0]


def frame(time: float) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((2, 2), np.uint8),
        sharpness=0.0,
    )


def fake_encode(frame: TimedFrame) -> bytes:
    """フレームの時刻を書いたバイト列を JPEG の代わりに返す。"""
    return f"jpeg@{frame.time}".encode()


class CountingEncoder:
    """encode の呼び出しを、受け取ったフレームの時刻で記録する偽の符号化の関数。"""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, frame: TimedFrame) -> bytes:
        self.calls.append(frame.time)
        return fake_encode(frame)


def sample_frames() -> AdvisorFrames:
    # NOW = 100.0 として、意味のあるフレーム 2 枚と、フレームの履歴から選んだフレーム 3 枚
    return AdvisorFrames(
        recent=[frame(99.79), frame(99.9), frame(100.0)],
        meaningful=[frame(87.66), frame(95.0)],
    )


# --- label_advisor_frames ---


def test_label_advisor_frames_orders_meaningful_then_recent():
    frames = sample_frames()
    labeled = label_advisor_frames(frames, NOW, ORIGIN)
    assert [f for _, f in labeled] == [*frames.meaningful, *frames.recent]


def test_label_advisor_frames_labels_with_kind_number_elapsed_and_seconds_ago():
    labeled = label_advisor_frames(sample_frames(), NOW, ORIGIN)
    assert [label for label, _ in labeled] == EXPECTED_LABELS


def test_label_advisor_frames_without_meaningful_frames():
    frames = AdvisorFrames(recent=[frame(9.5)], meaningful=[])
    assert label_advisor_frames(frames, 10.0, 2.0) == [
        ("直近 1/1 (t=7.50 秒、0.50 秒前)", frames.recent[0])
    ]


def test_label_advisor_frames_without_recent_frames():
    frames = AdvisorFrames(recent=[], meaningful=[frame(4.0)])
    assert label_advisor_frames(frames, 10.0, 2.0) == [
        ("過去の場面 1/1 (t=2.00 秒、6.00 秒前)", frames.meaningful[0])
    ]


def test_label_advisor_frames_without_frames():
    assert (
        label_advisor_frames(AdvisorFrames(recent=[], meaningful=[]), 10.0, 2.0) == []
    )


# --- build_advisor_images ---


def test_build_advisor_images_encodes_frames_in_label_order():
    images = build_advisor_images(sample_frames(), NOW, ORIGIN, fake_encode)
    assert images == [
        LabeledImage(
            label=label,
            jpeg=f"jpeg@{time}".encode(),
            frame_time=time,
            after_previous_operation=False,
        )
        for label, time in zip(EXPECTED_LABELS, EXPECTED_TIMES, strict=True)
    ]


def test_build_advisor_images_encodes_each_frame_once_in_order():
    encoder = CountingEncoder()
    build_advisor_images(sample_frames(), NOW, ORIGIN, encoder)
    assert encoder.calls == EXPECTED_TIMES


def test_build_advisor_images_without_frames():
    encoder = CountingEncoder()
    frames = AdvisorFrames(recent=[], meaningful=[])
    assert build_advisor_images(frames, NOW, ORIGIN, encoder) == []
    assert encoder.calls == []
