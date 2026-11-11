import numpy as np

from switch2_agent.decision_request import (
    DecisionRequest,
    JpegCache,
    LabeledImage,
    build_decision_request,
    build_instructions,
    label_frames,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.request_frames import RequestFrames
from switch2_agent.game_notes import format_game_notes
from switch2_agent.questions import Question
from switch2_agent.safety import SAFETY_RULES

NOTES = {"controls": "A で決定", "tips": "", "progress": "1 面", "strategy": "右へ進む"}
QUESTIONS = [
    Question("press_A", "今、A ボタン を押した状態にするべきか。", "press", "A"),
    Question("advisor", "今、advisor に戦略の更新を求めるべきか。", "advisor", ""),
]


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
    """encode の呼び出しを数える偽の符号化の関数。"""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, frame: TimedFrame) -> bytes:
        self.calls.append(frame.time)
        return f"jpeg@{frame.time}#{len(self.calls)}".encode()


def sample_frames() -> RequestFrames:
    # now = 100.0 として、意味のあるフレーム 2 枚と直近の区間のフレーム 3 枚
    return RequestFrames(
        recent=[frame(99.79), frame(99.9), frame(100.0)],
        meaningful=[frame(87.66), frame(95.0)],
    )


# --- label_frames ---


def test_label_frames_orders_meaningful_then_recent():
    frames = sample_frames()
    labeled = label_frames(frames, 100.0)
    assert [f for _, f in labeled] == [*frames.meaningful, *frames.recent]


def test_label_frames_labels_with_number_count_and_seconds_ago():
    labels = [label for label, _ in label_frames(sample_frames(), 100.0)]
    assert labels == [
        "過去の場面 1/2 (12.34 秒前)",
        "過去の場面 2/2 (5.00 秒前)",
        "直近 1/3 (0.21 秒前)",
        "直近 2/3 (0.10 秒前)",
        "直近 3/3 (0.00 秒前)",
    ]


def test_label_frames_without_meaningful_frames():
    frames = RequestFrames(recent=[frame(9.5)], meaningful=[])
    assert label_frames(frames, 10.0) == [("直近 1/1 (0.50 秒前)", frames.recent[0])]


# --- build_instructions / build_decision_request ---


def test_instructions_contain_safety_rules():
    assert SAFETY_RULES in build_instructions()


def test_build_decision_request_uses_instructions_and_notes():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode
    )
    assert isinstance(request, DecisionRequest)
    assert request.instructions == build_instructions()
    assert SAFETY_RULES in request.instructions
    assert request.state_text == format_game_notes(NOTES)


def test_build_decision_request_state_text_contains_four_kinds():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode
    )
    for heading in [
        "## ゲームの操作方法",
        "## 攻略情報・Tips",
        "## 進行状況",
        "## 戦略",
    ]:
        assert heading in request.state_text
    assert "右へ進む" in request.state_text


def test_build_decision_request_encodes_images_in_label_order():
    frames = sample_frames()
    request = build_decision_request(frames, 100.0, NOTES, QUESTIONS, fake_encode)
    expected = [
        LabeledImage(label=label, jpeg=fake_encode(f), frame_time=f.time)
        for label, f in label_frames(frames, 100.0)
    ]
    assert request.images == expected


def test_build_decision_request_keeps_questions():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, tuple(QUESTIONS), fake_encode
    )
    assert request.questions == QUESTIONS


# --- JpegCache ---


def test_jpeg_cache_encodes_frame_of_same_time_once():
    encoder = CountingEncoder()
    cache = JpegCache(encoder)
    first = cache(frame(1.0))
    # 同じ time のフレームは、別のオブジェクトでも符号化し直さない
    second = cache(frame(1.0))
    assert encoder.calls == [1.0]
    assert first == second


def test_jpeg_cache_encodes_frames_of_different_times():
    encoder = CountingEncoder()
    cache = JpegCache(encoder)
    cache(frame(1.0))
    cache(frame(2.0))
    assert encoder.calls == [1.0, 2.0]


def test_jpeg_cache_drops_oldest_key_over_capacity():
    encoder = CountingEncoder()
    cache = JpegCache(encoder, capacity=2)
    for t in [1.0, 2.0, 3.0]:
        cache(frame(t))
    cache(frame(3.0))
    assert encoder.calls == [1.0, 2.0, 3.0]
    # 容量 2 を超えたので、最も古い 1.0 は捨てられて符号化し直す
    cache(frame(1.0))
    assert encoder.calls == [1.0, 2.0, 3.0, 1.0]


def test_jpeg_cache_default_capacity_is_64():
    assert JpegCache(fake_encode).capacity == 64
