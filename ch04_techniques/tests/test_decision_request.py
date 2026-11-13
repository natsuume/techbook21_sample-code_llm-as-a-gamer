import numpy as np
from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.decision_request import (
    DEFAULT_HISTORY_COUNT,
    ROLE_DESCRIPTION,
    DecisionRequest,
    JpegCache,
    LabeledImage,
    build_decision_request,
    build_instructions,
    format_operation_history,
    format_previous_operation,
    label_frames,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.request_frames import RequestFrames
from switch2_agent.game_notes import format_game_notes
from switch2_agent.operation_log import OperationRecord
from switch2_agent.questions import Question
from switch2_agent.safety import SAFETY_RULES

NOTES = {"controls": "A で決定", "tips": "", "progress": "1 面", "strategy": "右へ進む"}
QUESTIONS = [
    Question("press_A", "今、A ボタン を押した状態にするべきか。", "press", "A"),
    Question("advisor", "今、advisor に戦略の更新を求めるべきか。", "advisor", ""),
]
# sample_frames の now (100.0) と異なる、経過秒の起点
ORIGIN = 40.0
HISTORY_HEADING = "## 直近の応答と送った操作 (古い順、最大 5 回)"
EXPECTED_ROLE_DESCRIPTION_LINES = [
    "あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。",
    "画像は時刻の古い順に並んでいる。各画像の前のラベルに、実行を始めてからの経過秒 (t) と、何秒前の画像かを書いてある。",
    "「過去の場面」は今より前の場面の代表の画像、「直近」は前回の判断から今までの画像である。",
    "「直近」のラベルの「前回の操作の前」「前回の操作の後」は、前回の判断で決めた操作を送る前の画像か、送った後の画像かを表す。",
    "操作が画面に出るまでには遅れがあるため、「前回の操作の後」の画像でも、操作の前の画面のことがある。",
    "直近の応答と送った操作の履歴に、最近の判断で送った操作とその時刻を書いてある。",
    "ゲームの情報 (操作方法・攻略情報・進行状況・戦略) を参考にし、各問いには、ほかの問いの答えと関係なく独立に答える。",
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


def operation(received_at: float, taps: set[str] | None = None) -> OperationRecord:
    """received_at に応答を受け取り、taps のボタンを短押しした (押下は無い) 記録を返す。"""
    return OperationRecord(
        sent_at=received_at - 0.3,
        received_at=received_at,
        action=PadAction(
            held=PadState(frozenset(), {}),
            taps=frozenset(taps or ()),
            advisor_requested=False,
            advisor_probability=0.0,
        ),
    )


# --- label_frames ---


def test_label_frames_orders_meaningful_then_recent():
    frames = sample_frames()
    labeled = label_frames(frames, 100.0, ORIGIN, 99.9)
    assert [f for _, f, _ in labeled] == [*frames.meaningful, *frames.recent]


def test_label_frames_labels_first_request_with_elapsed_and_seconds_ago():
    # 前回の操作が無い (最初の要求) ときは、直近のラベルに前回の操作の前後を書かない
    labeled = label_frames(sample_frames(), 100.0, ORIGIN, None)
    assert [(label, after) for label, _, after in labeled] == [
        ("過去の場面 1/2 (t=47.66 秒、12.34 秒前)", False),
        ("過去の場面 2/2 (t=55.00 秒、5.00 秒前)", False),
        ("直近 1/3 (t=59.79 秒、0.21 秒前)", False),
        ("直近 2/3 (t=59.90 秒、0.10 秒前)", False),
        ("直近 3/3 (t=60.00 秒、0.00 秒前)", False),
    ]


def test_label_frames_marks_recent_frames_before_and_after_previous_operation():
    # 前回の操作の時刻 (99.9) ちょうどのフレームは「後」
    labeled = label_frames(sample_frames(), 100.0, ORIGIN, 99.9)
    assert [(label, after) for label, _, after in labeled] == [
        ("過去の場面 1/2 (t=47.66 秒、12.34 秒前)", False),
        ("過去の場面 2/2 (t=55.00 秒、5.00 秒前)", False),
        ("直近 1/3 (t=59.79 秒、0.21 秒前、前回の操作の前)", False),
        ("直近 2/3 (t=59.90 秒、0.10 秒前、前回の操作の後)", True),
        ("直近 3/3 (t=60.00 秒、0.00 秒前、前回の操作の後)", True),
    ]


def test_label_frames_never_marks_meaningful_frames_after_previous_operation():
    # 前回の操作の時刻がすべてのフレームより前でも、過去の場面のラベルは変わらず、3 つ目の値は False
    labeled = label_frames(sample_frames(), 100.0, ORIGIN, 80.0)
    assert [(label, after) for label, _, after in labeled[:2]] == [
        ("過去の場面 1/2 (t=47.66 秒、12.34 秒前)", False),
        ("過去の場面 2/2 (t=55.00 秒、5.00 秒前)", False),
    ]
    assert [after for _, _, after in labeled[2:]] == [True, True, True]


def test_label_frames_without_meaningful_frames():
    frames = RequestFrames(recent=[frame(9.5)], meaningful=[])
    assert label_frames(frames, 10.0, 2.0, None) == [
        ("直近 1/1 (t=7.50 秒、0.50 秒前)", frames.recent[0], False)
    ]


# --- format_operation_history / format_previous_operation ---


def test_default_history_count_is_5():
    assert DEFAULT_HISTORY_COUNT == 5


def test_format_operation_history_writes_records_oldest_first():
    records = [operation(228.1, {"UP"}), operation(230.3, {"HOME"})]
    assert format_operation_history(records, 230.85, 100.0) == (
        "## 直近の応答と送った操作 (古い順、最大 5 回)\n"
        "- t=128.10 秒 (2.75 秒前): 押下 none / 短押し UP\n"
        "- t=130.30 秒 (0.55 秒前): 押下 none / 短押し HOME (前回の操作)"
    )


def test_format_operation_history_marks_single_record_as_previous_operation():
    assert format_operation_history([operation(12.0, {"A"})], 13.5, 2.0) == (
        f"{HISTORY_HEADING}\n- t=10.00 秒 (1.50 秒前): 押下 none / 短押し A (前回の操作)"
    )


def test_format_operation_history_without_records():
    assert format_operation_history([], 13.5, 2.0) == (
        f"{HISTORY_HEADING}\n(まだ操作を送っていない)"
    )


def test_format_previous_operation_writes_time_of_previous_operation():
    assert format_previous_operation(operation(230.3), 230.85, 100.0) == (
        "── t=130.30 秒 (0.55 秒前) に、前回の応答の操作を送った ──"
    )


def test_format_previous_operation_is_none_without_record():
    assert format_previous_operation(None, 230.85, 100.0) is None


# --- build_instructions / build_decision_request ---


def test_role_description_explains_timeline_and_history():
    assert ROLE_DESCRIPTION.splitlines() == EXPECTED_ROLE_DESCRIPTION_LINES


def test_instructions_contain_safety_rules():
    assert SAFETY_RULES in build_instructions()


def test_instructions_start_with_role_description():
    assert build_instructions().startswith(ROLE_DESCRIPTION)


def test_build_decision_request_uses_instructions_and_notes():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, []
    )
    assert isinstance(request, DecisionRequest)
    assert request.instructions == build_instructions()
    assert SAFETY_RULES in request.instructions
    assert request.state_text == format_game_notes(NOTES)


def test_build_decision_request_state_text_contains_four_kinds():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, []
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
    records = [operation(97.0), operation(99.9)]
    request = build_decision_request(
        frames, 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    expected = [
        LabeledImage(
            label=label,
            jpeg=fake_encode(f),
            frame_time=f.time,
            after_previous_operation=after,
        )
        for label, f, after in label_frames(frames, 100.0, ORIGIN, 99.9)
    ]
    assert request.images == expected


def test_build_decision_request_uses_latest_record_as_previous_operation():
    # 前回の操作の時刻は、最後 (最新) の記録の received_at (99.95)。最初の記録 (99.85) ではない
    records = [operation(99.85), operation(99.95)]
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert [image.after_previous_operation for image in request.images] == [
        False,
        False,
        False,
        False,
        True,
    ]
    assert request.images[2].label.endswith("、前回の操作の前)")
    assert request.images[4].label.endswith("、前回の操作の後)")


def test_build_decision_request_has_history_and_previous_operation_text():
    records = [operation(97.0, {"UP"}), operation(99.9, {"HOME"})]
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert request.history_text == format_operation_history(records, 100.0, ORIGIN)
    assert request.history_text == (
        f"{HISTORY_HEADING}\n"
        "- t=57.00 秒 (3.00 秒前): 押下 none / 短押し UP\n"
        "- t=59.90 秒 (0.10 秒前): 押下 none / 短押し HOME (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=59.90 秒 (0.10 秒前) に、前回の応答の操作を送った ──"
    )


def test_build_decision_request_without_operations():
    # 最初の要求: 履歴は「まだ操作を送っていない」、区切りの文は無く、どの画像も「後」ではない
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, QUESTIONS, fake_encode, ORIGIN, []
    )
    assert request.history_text == f"{HISTORY_HEADING}\n(まだ操作を送っていない)"
    assert request.previous_operation_text is None
    assert [image.after_previous_operation for image in request.images] == [False] * 5
    assert [image.label for image in request.images][2:] == [
        "直近 1/3 (t=59.79 秒、0.21 秒前)",
        "直近 2/3 (t=59.90 秒、0.10 秒前)",
        "直近 3/3 (t=60.00 秒、0.00 秒前)",
    ]


def test_build_decision_request_keeps_questions():
    request = build_decision_request(
        sample_frames(), 100.0, NOTES, tuple(QUESTIONS), fake_encode, ORIGIN, ()
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
