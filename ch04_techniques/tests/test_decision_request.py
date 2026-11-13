import numpy as np
from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.decision_request import (
    DEFAULT_HISTORY_COUNT,
    LATEST_LABEL,
    ROLE_DESCRIPTION,
    DecisionRequest,
    JpegCache,
    LabeledImage,
    build_decision_request,
    build_instructions,
    format_operation_history,
    format_previous_operation,
    label_latest_frame,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.game_notes import format_game_notes
from switch2_agent.operation_log import NeutralRecord, OperationRecord
from switch2_agent.questions import Choice, Question
from switch2_agent.safety import SAFETY_RULES

NOTES = {"controls": "A で決定", "tips": "", "strategy": "右へ進む", "advice": ""}
QUESTIONS = [
    Question(
        "button_A",
        "今、A ボタンをどうするか。",
        "button",
        "A",
        "choice",
        (
            Choice("none", "押さない"),
            Choice("hold", "押した状態にする。押した状態は次の判断まで続く"),
            Choice("tap", "1 回だけ短く押す"),
        ),
    ),
    Question(
        "advisor",
        "今、advisor に戦略の更新を求めるべきか。",
        "advisor",
        "",
        "probability",
        (),
    ),
]
# 要求の時刻と、要求に載せるフレーム (フレームの履歴の最新のフレーム) の時刻
NOW = 100.0
LATEST_TIME = 99.79
# NOW と異なる、経過秒の起点
ORIGIN = 40.0
HISTORY_HEADING = "## 直近の応答と送った操作 (古い順、最大 5 回)"
EXPECTED_ROLE_DESCRIPTION_LINES = [
    "あなたは Nintendo Switch 2 のゲームを操作するコントローラーの判断をする。",
    "画像は、ゲームの画面のフレームの履歴のうち、最新の 1 枚である。画像の前のラベルに、実行を始めてからの経過秒 (t) と、何秒前の画像かを書いてある。",
    "ラベルの「前回の操作の前」「前回の操作の後」は、前回の操作 (操作の履歴の最後の行。前回の判断で決めた操作か、その後にコントローラーを中立に戻したこと) の前の画像か、後の画像かを表す。",
    "操作が画面に出るまでには遅れがあるため、「前回の操作の後」の画像でも、操作の前の画面のことがある。",
    "直近の応答と送った操作の履歴に、最近の判断で送った操作と、コントローラーを中立に戻したことを、その時刻とともに書いてある。",
    "問いには、選択肢の中から 1 つを選ぶ問い (選択肢の問い) と、真である確率で答える問いがある。",
    "スティックの強さの問い (傾きを何 % 以上にするべきか) は、そのスティックをその軸 (左右か上下) に倒す場合の傾きについての問いである。",
    "選択肢の問いで迷ったら、none (押さない、倒さない) の選択肢を選ぶ。",
    "ゲームの情報 (操作方法・攻略情報・目標と進行状況と戦略・問題の解決の助言) を参考にし、各問いには、ほかの問いの答えと関係なく独立に答える。",
    "問題の解決の助言は、助言に書かれている場面に今の画面が当てはまるときだけ参考にする。",
    "操作方法の「(一般)」の行は、Switch 2 のゲームに多い一般的なボタンの対応で、このゲームではまだ確かめていない。",
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


def latest_frame() -> TimedFrame:
    """要求に載せるフレーム (NOW の 0.21 秒前の、フレームの履歴の最新のフレーム) を返す。"""
    return frame(LATEST_TIME)


def operation(received_at: float, taps: set[str] | None = None) -> OperationRecord:
    """received_at に応答を受け取り、taps のボタンを短押しした (押下は無い) 記録を返す。"""
    return OperationRecord(
        sent_at=received_at - 0.3,
        received_at=received_at,
        action=PadAction(
            held=PadState(frozenset(), {}),
            taps=frozenset(taps or ()),
            advisor_probabilities={},
            advisor_requested=frozenset(),
        ),
    )


def neutral(
    neutralized_at: float, reason: str = "Decision Model の失敗"
) -> NeutralRecord:
    """neutralized_at に、reason の理由でコントローラーを中立に戻した記録を返す。"""
    return NeutralRecord(neutralized_at=neutralized_at, reason=reason)


# --- label_latest_frame ---


def test_latest_label_value():
    assert LATEST_LABEL == "最新"


def test_label_latest_frame_without_previous_operation():
    # 前回の操作が無い (最初の要求) ときは、ラベルに前回の操作の前後を書かない
    latest = latest_frame()
    assert label_latest_frame(latest, NOW, ORIGIN, None) == (
        "最新 (t=59.79 秒、0.21 秒前)",
        latest,
        False,
    )


def test_label_latest_frame_after_previous_operation():
    latest = latest_frame()
    assert label_latest_frame(latest, NOW, ORIGIN, 99.5) == (
        "最新 (t=59.79 秒、0.21 秒前、前回の操作の後)",
        latest,
        True,
    )


def test_label_latest_frame_at_previous_operation_is_after():
    # 前回の操作の時刻ちょうどのフレームは「後」
    latest = latest_frame()
    assert label_latest_frame(latest, NOW, ORIGIN, LATEST_TIME) == (
        "最新 (t=59.79 秒、0.21 秒前、前回の操作の後)",
        latest,
        True,
    )


def test_label_latest_frame_before_previous_operation():
    latest = latest_frame()
    assert label_latest_frame(latest, NOW, ORIGIN, 99.9) == (
        "最新 (t=59.79 秒、0.21 秒前、前回の操作の前)",
        latest,
        False,
    )


def test_label_latest_frame_writes_elapsed_and_seconds_ago_from_origin_and_now():
    # 起点と今の時刻が変わると、t と秒前が変わる
    latest = frame(129.89)
    assert label_latest_frame(latest, 130.1, 2.0, 129.0) == (
        "最新 (t=127.89 秒、0.21 秒前、前回の操作の後)",
        latest,
        True,
    )


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


def test_format_operation_history_marks_neutral_record_as_previous_operation():
    records = [operation(228.1, {"UP"}), neutral(230.3)]
    assert format_operation_history(records, 230.85, 100.0) == (
        f"{HISTORY_HEADING}\n"
        "- t=128.10 秒 (2.75 秒前): 押下 none / 短押し UP\n"
        "- t=130.30 秒 (0.55 秒前): 中立に戻した (Decision Model の失敗) (前回の操作)"
    )


def test_format_operation_history_writes_neutral_record_between_operations():
    records = [
        operation(228.1, {"UP"}),
        neutral(229.0, "コントローラーの接続の復旧待ち"),
        operation(230.3, {"HOME"}),
    ]
    assert format_operation_history(records, 230.85, 100.0) == (
        f"{HISTORY_HEADING}\n"
        "- t=128.10 秒 (2.75 秒前): 押下 none / 短押し UP\n"
        "- t=129.00 秒 (1.85 秒前): 中立に戻した (コントローラーの接続の復旧待ち)\n"
        "- t=130.30 秒 (0.55 秒前): 押下 none / 短押し HOME (前回の操作)"
    )


def test_format_previous_operation_writes_time_of_neutral_record():
    assert format_previous_operation(neutral(230.3), 230.85, 100.0) == (
        "── t=130.30 秒 (0.55 秒前) に、中立に戻した (Decision Model の失敗) ──"
    )


# --- build_instructions / build_decision_request ---


def test_role_description_explains_latest_image_history_and_questions():
    assert ROLE_DESCRIPTION.splitlines() == EXPECTED_ROLE_DESCRIPTION_LINES


def test_role_description_does_not_explain_past_scenes_or_recent_images():
    # 要求の画像は最新の 1 枚だけなので、advisor に渡す画像のラベルの語は説明しない
    assert "「過去の場面」" not in ROLE_DESCRIPTION
    assert "「直近」" not in ROLE_DESCRIPTION


def test_instructions_contain_safety_rules():
    assert SAFETY_RULES in build_instructions()


def test_instructions_start_with_role_description():
    assert build_instructions().startswith(ROLE_DESCRIPTION)


def test_build_decision_request_uses_instructions_and_notes():
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, []
    )
    assert isinstance(request, DecisionRequest)
    assert request.instructions == build_instructions()
    assert SAFETY_RULES in request.instructions
    assert request.state_text == format_game_notes(NOTES)


def test_build_decision_request_state_text_contains_four_kinds():
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, []
    )
    for heading in [
        "## ゲームの操作方法",
        "## 攻略情報・Tips",
        "## 目標・進行状況・戦略",
        "## 問題の解決の助言",
    ]:
        assert heading in request.state_text
    assert "右へ進む" in request.state_text


def test_build_decision_request_has_only_given_frame_as_image():
    records = [operation(97.0), operation(99.5)]
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert request.images == [
        LabeledImage(
            label="最新 (t=59.79 秒、0.21 秒前、前回の操作の後)",
            jpeg=fake_encode(latest_frame()),
            frame_time=LATEST_TIME,
            after_previous_operation=True,
        )
    ]


def test_build_decision_request_encodes_frame_once():
    encoder = CountingEncoder()
    build_decision_request(latest_frame(), NOW, NOTES, QUESTIONS, encoder, ORIGIN, [])
    assert encoder.calls == [LATEST_TIME]


def test_build_decision_request_uses_latest_record_as_previous_operation():
    # 前回の操作の時刻は、最後 (最新) の記録の received_at (99.85)。最初の記録 (99.7) なら「後」になる
    records = [operation(99.7), operation(99.85)]
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=59.79 秒、0.21 秒前、前回の操作の前)", False)]


def test_build_decision_request_uses_neutral_record_as_previous_operation():
    # 操作を送った (99.0) 後に中立に戻した (99.95) なら、前回の操作の時刻は中立に戻した時刻
    records = [operation(99.0, {"UP"}), neutral(99.95)]
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=59.79 秒、0.21 秒前、前回の操作の前)", False)]
    assert request.history_text.endswith(
        "\n- t=59.95 秒 (0.05 秒前): 中立に戻した (Decision Model の失敗) (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=59.95 秒 (0.05 秒前) に、中立に戻した (Decision Model の失敗) ──"
    )


def test_build_decision_request_has_history_and_previous_operation_text():
    records = [operation(97.0, {"UP"}), operation(99.9, {"HOME"})]
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, records
    )
    assert request.history_text == format_operation_history(records, NOW, ORIGIN)
    assert request.history_text == (
        f"{HISTORY_HEADING}\n"
        "- t=57.00 秒 (3.00 秒前): 押下 none / 短押し UP\n"
        "- t=59.90 秒 (0.10 秒前): 押下 none / 短押し HOME (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=59.90 秒 (0.10 秒前) に、前回の応答の操作を送った ──"
    )


def test_build_decision_request_without_operations():
    # 最初の要求: 履歴は「まだ操作を送っていない」、区切りの文は無く、画像は「後」ではない
    request = build_decision_request(
        latest_frame(), NOW, NOTES, QUESTIONS, fake_encode, ORIGIN, []
    )
    assert request.history_text == f"{HISTORY_HEADING}\n(まだ操作を送っていない)"
    assert request.previous_operation_text is None
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=59.79 秒、0.21 秒前)", False)]


def test_build_decision_request_keeps_questions():
    request = build_decision_request(
        latest_frame(), NOW, NOTES, tuple(QUESTIONS), fake_encode, ORIGIN, ()
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
