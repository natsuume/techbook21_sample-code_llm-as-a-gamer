import threading
from collections.abc import Callable

import numpy as np
import pytest
from switch2_remote.bridge_supervisor import BridgeReconnectingError
from switch2_remote.error_message import describe_error

from switch2_agent.action_decision import PadAction, Thresholds
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.decision_models.base import Decision, DecisionModelError
from switch2_agent.decision_request import DecisionRequest, LabeledImage
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.operation_log import NeutralRecord, OperationLog, format_action
from switch2_agent.pad_output import OutputStoppedError
from switch2_agent.pipeline import (
    CaptureStoppedError,
    OutputSendError,
    Pipeline,
    PipelineParts,
)
from switch2_agent.questions import NONE_CHOICE, build_questions
from switch2_agent.request_log import RequestLogEntry

NOTES = {"controls": "A で決定", "tips": "", "strategy": "右へ進む", "advice": ""}
HISTORY_HEADING = "## 直近の応答と送った操作 (古い順、最大 5 回)"
DECISION_LOG_KEYS = {
    "number",
    "interval_wait_s",
    "select_s",
    "encode_s",
    "decide_s",
    "apply_s",
    "images",
    "payload_bytes",
    "action",
    "advisor_probabilities",
    "advisor_requested",
    "advisor_accepted",
    "refusals",
}


def frame(time: float) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((2, 2), np.uint8),
        sharpness=0.0,
    )


def fake_encode(frame: TimedFrame) -> bytes:
    return f"jpeg@{frame.time:.2f}".encode()


def decision(
    refusals: int = 0, chosen: dict[str, str] | None = None, **overrides: float
) -> Decision:
    """すべての問いに答えた Decision を返す。

    選択肢の問いは none の確率を 1.0 (ほかの選択肢を 0.0)、確率の問いは 0.0 にし、chosen (選択肢の問いの name →
    選ぶ選択肢の name。その選択肢を 1.0、ほかを 0.0 にする) と overrides (確率の問いの name → 確率) の問いだけを
    置き換える。置き換える問いか選択肢が問いの一覧に無ければ、テストを失敗させる。
    """
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, float]] = {}
    for question in build_questions():
        if question.answer_type == "choice":
            choices[question.name] = {
                choice.name: 1.0 if choice.name == NONE_CHOICE else 0.0
                for choice in question.choices
            }
        else:
            probabilities[question.name] = 0.0
    for name, chosen_name in (chosen or {}).items():
        assert name in choices, f"unknown choice question: {name}"
        assert chosen_name in choices[name], f"unknown choice of {name}: {chosen_name}"
        choices[name] = {
            choice_name: 1.0 if choice_name == chosen_name else 0.0
            for choice_name in choices[name]
        }
    for name, value in overrides.items():
        assert name in probabilities, f"unknown probability question: {name}"
        probabilities[name] = value
    return Decision(probabilities=probabilities, choices=choices, refusals=refusals)


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeModel:
    """偽の DecisionModel。要求を記録し、clock を decide_s 進めてから outcomes の先頭を返す (例外なら送出する)。

    outcomes が空なら、すべての確率が 0.0 の Decision を返す。on_decide があれば、clock を進めた後に呼ぶ
    (キャプチャーが新しいフレームを足したことにするのに使う)。
    """

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.decide_s = 0.2
        self.requests: list[DecisionRequest] = []
        self.outcomes: list[Decision | Exception] = []
        self.on_decide = None

    def decide(self, request: DecisionRequest) -> Decision:
        self.requests.append(request)
        self.clock.now += self.decide_s
        if self.on_decide is not None:
            self.on_decide()
        outcome = self.outcomes.pop(0) if self.outcomes else decision()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeOutput:
    """偽の PadOutput。apply と neutral の呼び出しを記録する。apply_errors の先頭を apply で送出する。"""

    def __init__(self):
        self.events: list[str] = []
        self.applied: list[PadAction] = []
        self.apply_errors: list[Exception] = []

    def apply(self, action: PadAction) -> None:
        if self.apply_errors:
            raise self.apply_errors.pop(0)
        self.applied.append(action)
        self.events.append("apply")

    def neutral(self) -> None:
        self.events.append("neutral")


class FakeAdvisor:
    """偽の Advisor (役割 name の advisor)。受け取った文脈を記録し、answers の先頭 (空なら True) を返す。

    is_busy は busy を返す。request_update を呼ばれたら、役割の間で共有する calls に `request <name>` を足す
    (役割どうしの呼ばれた順を確かめるため)。
    """

    def __init__(self, name: str, calls: list[str]):
        self.name = name
        self.calls = calls
        self.contexts: list[AdvisorContext] = []
        self.answers: list[bool] = []
        self.busy = False

    def request_update(self, context: AdvisorContext) -> bool:
        self.calls.append(f"request {self.name}")
        self.contexts.append(context)
        return self.answers.pop(0) if self.answers else True

    def is_busy(self) -> bool:
        return self.busy

    def close(self) -> None:
        pass


class FakeRequestLog:
    """偽の RequestLog。write で受け取った記録を entries に足す。errors の先頭があれば、足さずに送出する。"""

    def __init__(self):
        self.entries: list[RequestLogEntry] = []
        self.errors: list[Exception] = []

    def write(self, entry: RequestLogEntry) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.entries.append(entry)


class Env:
    """Pipeline に渡した偽の部品と、記録を持つ。"""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.history = FrameHistory(60.0)
        self.store = MeaningfulFrameStore()
        self.model = FakeModel(clock)
        self.output = FakeOutput()
        # 役割の name → その役割の偽の advisor。advisor_calls は役割の間で共有する呼び出しの記録
        self.advisor_calls: list[str] = []
        self.advisors = {
            role.name: FakeAdvisor(role.name, self.advisor_calls)
            for role in ADVISOR_ROLES
        }
        self.operation_log = OperationLog()
        self.logs: list[dict] = []
        self.sleeps: list[float] = []
        self.capture_ended = False
        self.bridge_failed = False
        self.bridge_recovering = False
        self.capture_lost: str | None = None

    def add_frames(self, *times: float) -> None:
        for t in times:
            self.history.add(frame(t))

    def events(self, name: str) -> list[dict]:
        return [event for event in self.logs if event.get("event") == name]

    def contexts(self) -> dict[str, list[AdvisorContext]]:
        """役割の name → その役割の advisor が受け取った文脈を返す。"""
        return {name: advisor.contexts for name, advisor in self.advisors.items()}

    def no_advisor_was_called(self) -> bool:
        return self.advisor_calls == []


def make_pipeline(now: float = 9.0, **kwargs) -> tuple[Pipeline, Env]:
    """時刻 now で作った Pipeline と、その偽の部品を返す。kwargs は Pipeline に渡す。"""
    env = Env(FakeClock(now))
    parts = PipelineParts(
        history=env.history,
        store=env.store,
        selector=EvenIntervalSelector(),
        model=env.model,
        output=env.output,
        advisors=env.advisors,
        read_notes=lambda: dict(NOTES),
        questions=build_questions(),
        thresholds=Thresholds(),
        encode=fake_encode,
        operation_log=env.operation_log,
        log=env.logs.append,
        clock=env.clock,
        sleep=env.sleeps.append,
        game="zelda",
        capture_ended=lambda: env.capture_ended,
        bridge_failed=lambda: env.bridge_failed,
        bridge_recovering=lambda: env.bridge_recovering,
        capture_lost=lambda: env.capture_lost,
    )
    return Pipeline(parts, **kwargs), env


def make_pipeline_with_request_log(
    now: float = 9.0, **kwargs
) -> tuple[Pipeline, Env, FakeRequestLog]:
    """make_pipeline の Pipeline に、偽の RequestLog を渡したものを返す。"""
    pipeline, env = make_pipeline(now, **kwargs)
    request_log = FakeRequestLog()
    pipeline.parts.request_log = request_log
    return pipeline, env, request_log


def frame_times(request: DecisionRequest) -> list[float]:
    return [image.frame_time for image in request.images]


# --- 要求に載せるフレーム ---


def test_request_has_only_latest_frame_of_history():
    pipeline, env = make_pipeline()
    env.add_frames(9.0, 9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert frame_times(env.model.requests[0]) == [9.9]
    assert pipeline.last_sent_frame_time == 9.9


def test_first_request_sends_latest_frame_even_if_it_is_not_just_before_now():
    # 最新のフレーム (9.0) が今 (10.0) より 1 秒前でも、まだ送っていないフレームなので送る
    pipeline, env = make_pipeline()
    env.add_frames(8.0, 9.0)
    env.clock.now = 10.0
    pipeline.run_once()
    assert len(env.model.requests) == 1
    assert frame_times(env.model.requests[0]) == [9.0]


def test_second_request_has_latest_frame_after_first_request():
    pipeline, env = make_pipeline()
    env.add_frames(9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.add_frames(10.1, 10.3)
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.model.requests) == 2
    assert frame_times(env.model.requests[1]) == [10.3]
    assert pipeline.last_sent_frame_time == 10.3


def test_late_frame_newer_than_last_sent_frame_is_sent():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の送信時刻 (10.0) より前の時刻で遅れて届いたフレームでも、前回の要求のフレーム (9.9) より新しいので送る
    env.add_frames(9.95)
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.model.requests) == 2
    assert frame_times(env.model.requests[1]) == [9.95]


def test_no_request_when_latest_frame_was_already_sent():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 最新のフレームの time (9.9) が last_sent_frame_time 以下なので、新しいフレームが無いときの扱いをする
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.model.requests) == 1
    assert env.sleeps == [0.05]


def test_request_does_not_include_meaningful_frames():
    pipeline, env = make_pipeline()
    env.store.add(frame(5.0))
    env.add_frames(9.6, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    request = env.model.requests[0]
    assert frame_times(request) == [9.9]
    assert request.images[0].label.startswith("最新 ")


def test_request_contains_latest_notes_and_questions():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    request = env.model.requests[0]
    assert "右へ進む" in request.state_text
    assert [q.name for q in request.questions] == [q.name for q in build_questions()]


# --- 画像の時系列と操作の履歴 ---


def test_first_request_has_elapsed_labels_and_no_previous_operation():
    # パイプラインの開始 (9.0) を経過秒の起点にする
    pipeline, env = make_pipeline(now=9.0)
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    request = env.model.requests[0]
    assert [image.label for image in request.images] == ["最新 (t=0.90 秒、0.10 秒前)"]
    assert [image.after_previous_operation for image in request.images] == [False]
    assert request.history_text == f"{HISTORY_HEADING}\n(まだ操作を送っていない)"
    assert request.previous_operation_text is None


def test_second_request_marks_latest_frame_before_previous_operation():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [decision()]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の応答は 10.2 に受け取り、その直後に操作を送った。最新のフレーム (10.1) はそれより前
    env.add_frames(10.1)
    env.clock.now = 10.5
    pipeline.run_once()
    request = env.model.requests[1]
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=1.10 秒、0.40 秒前、前回の操作の前)", False)]


def test_second_request_marks_latest_frame_after_previous_operation():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [decision()]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の応答は 10.2 に受け取り、その直後に操作を送った
    env.add_frames(10.1, 10.3)
    env.clock.now = 10.5
    pipeline.run_once()
    request = env.model.requests[1]
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=1.30 秒、0.20 秒前、前回の操作の後)", True)]
    assert request.history_text == (
        f"{HISTORY_HEADING}\n- t=1.20 秒 (0.30 秒前): 押下 none / 短押し none (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=1.20 秒 (0.30 秒前) に、前回の応答の操作を送った ──"
    )


def test_request_history_has_at_most_five_recent_operations():
    pipeline, env = make_pipeline(now=9.0)
    # 時刻 10.0, 10.5, …, 13.0 に 7 回要求する (応答はそれぞれ 0.2 秒後)
    for index in range(7):
        now = 10.0 + 0.5 * index
        env.add_frames(now - 0.1)
        env.clock.now = now
        pipeline.run_once()
    lines = env.model.requests[6].history_text.splitlines()
    # 見出しと、それまでの 6 回の操作のうち新しい 5 回 (10.7〜12.7 に受け取った応答)
    assert lines[0] == HISTORY_HEADING
    assert len(lines) == 6
    assert lines[1].startswith("- t=1.70 秒 ")
    assert lines[5].startswith("- t=3.70 秒 ")
    assert lines[5].endswith(" (前回の操作)")


# --- 操作 ---


def test_applies_decided_action_and_records_operation():
    pipeline, env = make_pipeline()
    env.model.outcomes = [
        decision(chosen={"button_A": "hold", "button_B": "tap", "stick_LS_x": "left"})
    ]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert len(env.output.applied) == 1
    applied = env.output.applied[0]
    # 選択肢の問いの答え (Decision.choices) から操作を決める。強さの問いは 0 なので、傾きは最小の 0.25
    assert applied.held.buttons == frozenset({"A"})
    assert applied.held.sticks == {"LS": (-0.25, 0.0)}
    assert applied.taps == frozenset({"B"})
    records = env.operation_log.since(0.0)
    assert len(records) == 1
    assert records[0].sent_at == 10.0
    assert records[0].received_at == pytest.approx(10.2)
    assert records[0].action == applied


# --- advisor ---


def test_advisor_of_requested_role_is_called_with_its_context():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor_strategy=0.9, advisor_tips=0.2)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # しきい値を超えた strategy の advisor だけを呼ぶ
    assert [len(contexts) for contexts in env.contexts().values()] == [0, 1, 0, 0]
    context = env.advisors["strategy"].contexts[0]
    assert context.game == "zelda"
    assert context.current_strategy == "右へ進む"
    assert context.trigger_probability == 0.9
    assert context.trigger_time_text
    # パイプラインの開始 (9.0) からの応答 1 件が、その役割の確率とともに操作の記録に入る
    assert context.operation_log_text == (
        "0.00 秒: 押下 none / 短押し none / advisor 0.90"
    )
    log = env.events("decision")[0]
    assert log["advisor_requested"] == ["strategy"]
    assert log["advisor_accepted"] == ["strategy"]


def test_each_role_gets_probability_of_its_own_question():
    pipeline, env = make_pipeline()
    env.model.outcomes = [
        decision(
            advisor_controls=0.6,
            advisor_strategy=0.7,
            advisor_advice=0.8,
            advisor_tips=0.9,
        )
    ]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    for name, probability in [
        ("controls", 0.6),
        ("strategy", 0.7),
        ("advice", 0.8),
        ("tips", 0.9),
    ]:
        context = env.advisors[name].contexts[0]
        assert context.trigger_probability == probability
        assert context.operation_log_text.endswith(f" / advisor {probability:.2f}")


def test_advisors_are_called_in_role_order():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor_advice=0.9, advisor_strategy=0.9)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # ADVISOR_ROLES の順 (strategy が advice より先。名前の順ではない)
    assert env.advisor_calls == ["request strategy", "request advice"]
    log = env.events("decision")[0]
    assert log["advisor_requested"] == ["strategy", "advice"]
    assert log["advisor_accepted"] == ["strategy", "advice"]


def test_busy_role_is_skipped_and_other_requested_role_is_called():
    pipeline, env = make_pipeline()
    env.advisors["strategy"].busy = True
    env.model.outcomes = [decision(advisor_strategy=0.9, advisor_advice=0.9)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.advisor_calls == ["request advice"]
    log = env.events("decision")[0]
    assert log["advisor_requested"] == ["strategy", "advice"]
    assert log["advisor_accepted"] == ["advice"]
    # 呼ばなかった役割の操作の記録の起点は、パイプラインの開始のまま
    assert pipeline.last_advisor_at == {
        "controls": 9.0,
        "strategy": 9.0,
        "advice": pytest.approx(10.2),
        "tips": 9.0,
    }


def test_last_advisor_at_starts_at_pipeline_start_for_every_role():
    pipeline, _ = make_pipeline(now=9.0)
    assert pipeline.last_advisor_at == {role.name: 9.0 for role in ADVISOR_ROLES}


def test_advisor_context_has_images_selected_for_advisor():
    # 経過秒の起点は 4.0。意味のあるフレームは、フレームの履歴から選んだ最初のフレーム (9.1) より前の 5.0 だけ
    pipeline, env = make_pipeline(now=4.0)
    env.model.outcomes = [decision(advisor_strategy=0.9)]
    env.store.add(frame(5.0))
    env.store.add(frame(9.2))
    env.add_frames(9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 要求には最新のフレームだけを載せる
    assert frame_times(env.model.requests[0]) == [9.9]
    # advisor の画像は、応答を受け取った後の 10.2 を今の時刻として選ぶ。フレームの履歴 (9 枚) からは等間隔に 5 枚
    expected = [
        ("過去の場面 1/1 (t=1.00 秒、5.20 秒前)", 5.0),
        ("直近 1/5 (t=5.10 秒、1.10 秒前)", 9.1),
        ("直近 2/5 (t=5.30 秒、0.90 秒前)", 9.3),
        ("直近 3/5 (t=5.50 秒、0.70 秒前)", 9.5),
        ("直近 4/5 (t=5.70 秒、0.50 秒前)", 9.7),
        ("直近 5/5 (t=5.90 秒、0.30 秒前)", 9.9),
    ]
    assert env.advisors["strategy"].contexts[0].images == [
        LabeledImage(
            label=label,
            jpeg=fake_encode(frame(time)),
            frame_time=time,
            after_previous_operation=False,
        )
        for label, time in expected
    ]


def recording_encode(encoded: list[float]) -> Callable[[TimedFrame], bytes]:
    """符号化したフレームの time を encoded に足してから、fake_encode の結果を返す encode を返す。"""

    def encode(frame: TimedFrame) -> bytes:
        encoded.append(frame.time)
        return fake_encode(frame)

    return encode


def test_advisor_images_are_selected_once_for_all_requested_roles():
    pipeline, env = make_pipeline()
    encoded: list[float] = []
    pipeline.parts.encode = recording_encode(encoded)
    env.model.outcomes = [decision(advisor_strategy=0.9, advisor_tips=0.9)]
    env.store.add(frame(5.0))
    env.add_frames(9.5, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 要求の画像 (9.9) と、advisor の画像 (5.0, 9.5, 9.9) を 1 回ずつだけ符号化し、2 つの役割で使い回す
    assert encoded == [9.9, 5.0, 9.5, 9.9]
    strategy_images = env.advisors["strategy"].contexts[0].images
    assert [image.frame_time for image in strategy_images] == [5.0, 9.5, 9.9]
    assert env.advisors["tips"].contexts[0].images == strategy_images


def test_no_advisor_images_are_selected_when_all_requested_roles_are_busy():
    pipeline, env = make_pipeline()
    encoded: list[float] = []
    pipeline.parts.encode = recording_encode(encoded)
    env.advisors["strategy"].busy = True
    env.advisors["tips"].busy = True
    env.model.outcomes = [decision(advisor_strategy=0.9, advisor_tips=0.9)]
    env.store.add(frame(5.0))
    env.add_frames(9.5, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 実行中の役割には request_update を呼ばず、advisor の画像も符号化しない (要求の画像だけを符号化する)
    assert env.no_advisor_was_called()
    assert encoded == [9.9]
    log = env.events("decision")[0]
    assert log["advisor_requested"] == ["strategy", "tips"]
    assert log["advisor_accepted"] == []


def test_advisor_is_not_requested_at_threshold():
    pipeline, env = make_pipeline()
    encoded: list[float] = []
    pipeline.parts.encode = recording_encode(encoded)
    env.model.outcomes = [
        decision(
            advisor_controls=0.5,
            advisor_strategy=0.5,
            advisor_advice=0.5,
            advisor_tips=0.5,
        )
    ]
    env.store.add(frame(5.0))
    env.add_frames(9.5, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.no_advisor_was_called()
    # advisor を呼ばない要求では、advisor の画像を選ばず、要求の画像だけを符号化する
    assert encoded == [9.9]
    log = env.events("decision")[0]
    assert log["advisor_requested"] == []
    assert log["advisor_accepted"] == []


def run_three_requests(env: Env, pipeline: Pipeline) -> None:
    """時刻 10.0・10.5・11.0 に、新しいフレームを足して 1 回ずつ要求する (応答は 0.2 秒後)。"""
    for now, new_frame in [(10.0, 9.9), (10.5, 10.4), (11.0, 10.9)]:
        env.add_frames(new_frame)
        env.clock.now = now
        pipeline.run_once()


def test_operation_log_of_each_role_starts_after_its_last_accepted_request():
    pipeline, env = make_pipeline()
    # 応答は 10.2・10.7・11.2 に受け取る。strategy は 1 回目と 3 回目、tips は毎回、advisor を呼ぶ
    env.model.outcomes = [
        decision(advisor_strategy=0.9, advisor_tips=0.9),
        decision(advisor_strategy=0.1, advisor_tips=0.9),
        decision(advisor_strategy=0.9, advisor_tips=0.9),
    ]
    run_three_requests(env, pipeline)
    strategy = env.advisors["strategy"].contexts
    tips = env.advisors["tips"].contexts
    assert (len(strategy), len(tips)) == (2, 3)
    # strategy は 1 回目 (10.2) の後の 2 回目と 3 回目、tips は 2 回目 (10.7) の後の 3 回目だけが入る
    assert len(strategy[1].operation_log_text.splitlines()) == 2
    assert len(tips[2].operation_log_text.splitlines()) == 1
    assert pipeline.last_advisor_at == {
        "controls": 9.0,
        "strategy": pytest.approx(11.2),
        "advice": 9.0,
        "tips": pytest.approx(11.2),
    }


def test_rejected_advisor_request_keeps_operation_log_start_of_that_role():
    pipeline, env = make_pipeline()
    env.model.outcomes = [
        decision(advisor_strategy=0.9),
        decision(advisor_strategy=0.1),
        decision(advisor_strategy=0.9),
    ]
    env.advisors["strategy"].answers = [False, True]
    run_three_requests(env, pipeline)
    contexts = env.advisors["strategy"].contexts
    assert len(contexts) == 2
    # 1 回目は受け付けられなかったので、起点はパイプラインの開始のまま
    assert len(contexts[1].operation_log_text.splitlines()) == 3
    logs = env.events("decision")
    assert logs[0]["advisor_accepted"] == []
    assert logs[2]["advisor_accepted"] == ["strategy"]
    assert pipeline.last_advisor_at["strategy"] == pytest.approx(11.2)


# --- 新しいフレームが無いとき ---


def test_no_request_without_frames():
    pipeline, env = make_pipeline()
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.model.requests == []
    assert env.output.events == []
    assert env.sleeps == [0.05]


def test_first_frame_is_waited_longer_than_stale_timeout():
    pipeline, env = make_pipeline()
    env.clock.now = 10.0
    pipeline.run_once()
    # まだ要求を送っていない間は、stale_frame_timeout_s (2 秒) を過ぎても止まらない
    env.clock.now = 13.0
    pipeline.run_once()
    env.clock.now = 19.9
    pipeline.run_once()
    assert env.output.events == []
    assert env.sleeps == [0.05, 0.05, 0.05]
    # 最初のフレームが first_frame_timeout_s (10 秒) 届かなければ止まる
    env.clock.now = 20.0
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()
    assert env.output.events == ["neutral"]
    assert env.model.requests == []


def test_first_frame_timeout_can_be_given():
    pipeline, env = make_pipeline(first_frame_timeout_s=3.0)
    env.clock.now = 10.0
    pipeline.run_once()
    env.clock.now = 13.0
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()


def test_first_request_is_sent_when_first_frame_arrives():
    pipeline, env = make_pipeline()
    env.clock.now = 10.0
    pipeline.run_once()
    env.add_frames(15.0)
    env.clock.now = 15.2
    pipeline.run_once()
    assert len(env.model.requests) == 1
    # 最初の要求を送った後は、stale_frame_timeout_s で判定する
    env.model.decide_s = 0.0
    env.clock.now = 15.5
    pipeline.run_once()
    env.clock.now = 17.5
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()


def test_no_request_until_new_frame_arrives_and_stops_when_stale():
    pipeline, env = make_pipeline()
    env.model.decide_s = 0.0
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.clock.now = 10.1
    pipeline.run_once()
    assert len(env.model.requests) == 1
    assert env.sleeps == [0.05]
    env.clock.now = 11.5
    pipeline.run_once()
    assert len(env.model.requests) == 1
    assert "neutral" not in env.output.events
    env.clock.now = 12.2
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()
    assert env.output.events == ["apply", "neutral"]
    assert len(env.model.requests) == 1


def test_stale_time_is_measured_again_after_new_frame():
    pipeline, env = make_pipeline()
    env.model.decide_s = 0.0
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.clock.now = 10.1
    pipeline.run_once()
    env.add_frames(11.0)
    env.clock.now = 11.5
    pipeline.run_once()
    assert len(env.model.requests) == 2
    env.clock.now = 12.0
    pipeline.run_once()
    # 新しいフレームが無いと最初に分かった 12.0 から 1.0 秒しかたっていないので、まだ止まらない
    env.clock.now = 13.0
    pipeline.run_once()
    assert len(env.model.requests) == 2
    assert "neutral" not in env.output.events


def test_capture_ended_neutralizes_and_raises():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.capture_ended = True
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()
    assert env.output.events == ["neutral"]
    assert env.model.requests == []


# --- ログ ---


def test_decision_log_has_measurement_items():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(refusals=2)]
    env.add_frames(9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    logs = env.events("decision")
    assert len(logs) == 1
    log = logs[0]
    assert DECISION_LOG_KEYS <= set(log)
    request = env.model.requests[0]
    # 時計が進むのは model.decide の中だけなので、ほかの時間は 0。最初の要求は間隔を空けずに送る
    assert log["decide_s"] == pytest.approx(0.2)
    for key in ("select_s", "encode_s", "apply_s"):
        assert log[key] == pytest.approx(0.0)
    assert log["interval_wait_s"] == 0.0
    assert log["images"] == 1
    assert log["payload_bytes"] == sum(len(image.jpeg) for image in request.images)
    assert log["action"] == format_action(env.output.applied[0])
    assert log["refusals"] == 2
    assert log["number"] == 1


def test_decision_log_has_advisor_items_of_each_role():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor_controls=0.25, advisor_advice=0.75)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    log = env.events("decision")[0]
    assert log["advisor_probabilities"] == {
        "controls": 0.25,
        "strategy": 0.0,
        "advice": 0.75,
        "tips": 0.0,
    }
    # 役割の name のリスト (JSON に書けるよう、集合ではなくリスト)
    assert log["advisor_requested"] == ["advice"]
    assert log["advisor_accepted"] == ["advice"]
    assert "advisor_probability" not in log


# --- 直前の応答からの間隔 ---


def test_min_request_interval_defaults_to_zero():
    pipeline, _ = make_pipeline()
    assert pipeline.min_request_interval_s == 0.0
    assert pipeline.last_response_at is None


def test_last_response_at_is_time_when_decide_returns():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert pipeline.last_response_at == pytest.approx(10.2)


def test_last_response_at_is_time_when_decide_fails():
    pipeline, env = make_pipeline()
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    assert pipeline.last_response_at == pytest.approx(10.2)


def test_waits_rest_of_interval_after_response_before_choosing_frame():
    pipeline, env = make_pipeline(now=9.0, min_request_interval_s=0.2)
    # 待つ間に clock を進め、待ち終わりの時刻のフレームを足す
    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の応答は 10.2 に受け取った。10.3 の時点では、間隔 (0.2 秒) の残りは 0.1 秒
    env.add_frames(10.25)
    env.clock.now = 10.3
    pipeline.run_once()
    assert env.sleeps == [pytest.approx(0.1)]
    # 待った後にフレームを選ぶので、待つ前の最新のフレーム (10.25) ではなく、待つ間に届いたフレーム (10.4) を載せる。
    # 要求の時刻 (秒前の起点) も、待った後の時刻になる
    request = env.model.requests[1]
    assert frame_times(request) == pytest.approx([10.4])
    assert [image.label for image in request.images] == [
        "最新 (t=1.40 秒、0.00 秒前、前回の操作の後)"
    ]
    assert [log["interval_wait_s"] for log in env.events("decision")] == [
        0.0,
        pytest.approx(0.1),
    ]


def test_waits_rest_of_interval_after_decision_error():
    pipeline, env = make_pipeline(min_request_interval_s=0.2)
    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    # 失敗の応答も 10.2 に返った。run の失敗の後の待ちを挟まずに、10.25 に次の要求を始める
    env.clock.now = 10.25
    pipeline.run_once()
    assert env.sleeps == [pytest.approx(0.15)]
    assert frame_times(env.model.requests[1]) == pytest.approx([10.4])
    assert env.events("decision")[0]["interval_wait_s"] == pytest.approx(0.15)


def test_does_not_wait_when_interval_has_passed():
    pipeline, env = make_pipeline(min_request_interval_s=0.2)
    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の応答 (10.2) から 0.3 秒たっているので待たない
    env.add_frames(10.5)
    env.clock.now = 10.5
    pipeline.run_once()
    assert env.sleeps == []
    assert frame_times(env.model.requests[1]) == [10.5]
    assert [log["interval_wait_s"] for log in env.events("decision")] == [0.0, 0.0]


def test_does_not_wait_when_interval_has_just_passed():
    # 応答 (10.25) から間隔 (0.25 秒) ちょうどの 10.5 では、残りが 0 なので待たない
    pipeline, env = make_pipeline(min_request_interval_s=0.25)
    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.model.decide_s = 0.25
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.add_frames(10.4)
    env.clock.now = 10.5
    pipeline.run_once()
    assert env.sleeps == []
    assert len(env.model.requests) == 2
    assert env.events("decision")[1]["interval_wait_s"] == 0.0


def test_capture_end_during_interval_wait_stops_without_request():
    pipeline, env = make_pipeline(min_request_interval_s=0.2)

    def end_capture(count: int) -> None:
        env.capture_ended = True

    pipeline.parts.sleep = sleep_with_new_frames(env, end_capture)
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.add_frames(10.25)
    env.clock.now = 10.3
    # 待った後にキャプチャーの状態を確かめるので、待つ間に終わったら要求を送らずに止まる
    with pytest.raises(CaptureStoppedError, match="capture ended"):
        pipeline.run_once()
    assert env.sleeps == [pytest.approx(0.1)]
    assert len(env.model.requests) == 1
    assert env.output.events == ["apply", "neutral"]


def test_run_waits_interval_before_each_request_after_first():
    pipeline, env = make_pipeline(min_request_interval_s=0.2)
    stop = threading.Event()
    env.model.decide_s = 0.1

    def stop_after_three_requests():
        if len(env.model.requests) == 3:
            stop.set()

    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.model.on_decide = stop_after_three_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 応答 (10.1、10.4) のたびに 0.2 秒待ち、待つ間に届いたフレーム (10.3、10.6) で次の要求を送る
    assert len(env.model.requests) == 3
    assert env.sleeps == [pytest.approx(0.2), pytest.approx(0.2)]
    assert [frame_times(request) for request in env.model.requests] == [
        [9.9],
        [pytest.approx(10.3)],
        [pytest.approx(10.6)],
    ]


def test_run_once_sends_no_request_when_bridge_starts_recovering_during_interval_wait():
    pipeline, env = make_pipeline(min_request_interval_s=0.2)

    def start_recovery(count: int) -> None:
        env.bridge_recovering = True

    pipeline.parts.sleep = sleep_with_new_frames(env, start_recovery)
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    env.clock.now = 10.3
    pipeline.run_once()
    # 待つ間に復旧が始まったので、新しいフレーム (10.4) があっても要求を送らずに戻る。
    # 中立に戻すことと記録は、run の復旧の待ちに任せる
    assert env.sleeps == [pytest.approx(0.1)]
    assert len(env.model.requests) == 1
    assert pipeline.request_count == 1
    assert env.output.events == ["apply"]
    assert len(env.events("decision")) == 1
    assert neutral_records(env) == []


def test_run_once_sends_no_request_while_bridge_is_recovering_without_waiting():
    # 間隔を待たなかった回 (最初の要求) でも、ブリッジが復旧中なら要求を送らない
    pipeline, env = make_pipeline()
    env.bridge_recovering = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.model.requests == []
    assert pipeline.request_count == 0
    assert env.output.events == []
    assert env.sleeps == []
    assert env.logs == []


def test_run_once_stops_when_capture_is_lost_while_bridge_is_recovering():
    # キャプチャーが止まっていれば、ブリッジが復旧中かを確かめる前に止まる
    pipeline, env = make_pipeline()
    env.bridge_recovering = True
    env.capture_lost = "no new frame for 2.0 seconds"
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run_once()
    assert env.model.requests == []
    assert env.output.events == ["neutral"]


def test_run_waits_for_bridge_recovery_when_it_starts_during_interval_wait():
    pipeline, env = make_pipeline(now=9.0, min_request_interval_s=0.2)
    stop = threading.Event()
    env.model.decide_s = 0.1

    def control_bridge(count: int) -> None:
        # 1 回目の待ち (間隔の待ち、10.1 から 10.3) の間に復旧が始まり、復旧の待ちの 2 回目 (10.5) で終わる
        if count == 1:
            env.bridge_recovering = True
        if count == 3:
            env.bridge_recovering = False

    def stop_after_two_requests():
        if len(env.model.requests) == 2:
            stop.set()

    pipeline.parts.sleep = sleep_with_new_frames(env, control_bridge)
    env.model.on_decide = stop_after_two_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 間隔の待ちの後は要求を送らず、復旧の待ち (中立に戻して記録する) に入り、復旧が終わってから 2 回目を送る
    assert len(env.model.requests) == 2
    assert env.sleeps == [pytest.approx(0.2), pytest.approx(0.1), pytest.approx(0.1)]
    assert [event["event"] for event in env.logs] == [
        "decision",
        "bridge_wait",
        "bridge_wait_end",
        "decision",
    ]
    assert env.output.events == ["apply", "neutral", "apply"]
    records = neutral_records(env)
    assert [record.reason for record in records] == ["コントローラーの接続の復旧待ち"]
    assert records[0].neutralized_at == pytest.approx(10.3)
    assert frame_times(env.model.requests[1]) == pytest.approx([10.5])


# --- 要求のログ ---


@pytest.mark.parametrize("accepted", [True, False])
def test_request_log_records_successful_request(accepted):
    pipeline, env, request_log = make_pipeline_with_request_log(now=9.0)
    sent_decision = decision(advisor_strategy=0.9)
    env.model.outcomes = [sent_decision]
    env.advisors["strategy"].answers = [accepted]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert len(request_log.entries) == 1
    entry = request_log.entries[0]
    assert entry.number == 1
    assert entry.origin == 9.0
    assert entry.sent_at == 10.0
    assert entry.received_at == pytest.approx(10.2)
    assert entry.request is env.model.requests[0]
    assert entry.decision is sent_decision
    assert entry.action == env.output.applied[0]
    assert entry.advisor_accepted == (
        frozenset({"strategy"}) if accepted else frozenset()
    )
    assert entry.result == "ok"
    assert entry.error is None


def test_request_log_entry_has_time_just_before_decide():
    pipeline, env, request_log = make_pipeline_with_request_log(now=9.0)

    def read_notes_taking_time() -> dict[str, str]:
        # 要求を組み立てる間に 0.1 秒かかったことにする
        env.clock.now += 0.1
        return dict(NOTES)

    pipeline.parts.read_notes = read_notes_taking_time
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    entry = request_log.entries[0]
    # sent_at は要求の時刻 (フレームを選ぶ前)、encoded_at は model.decide を呼ぶ直前の時刻
    assert entry.sent_at == 10.0
    assert entry.encoded_at == pytest.approx(10.1)
    assert entry.received_at == pytest.approx(10.3)
    # run.jsonl の decide_s と同じく、encoded_at から received_at までが model.decide の時間
    assert env.events("decision")[0]["decide_s"] == pytest.approx(
        entry.received_at - entry.encoded_at
    )


def test_request_numbers_count_every_sent_request():
    pipeline, env, request_log = make_pipeline_with_request_log()
    env.model.outcomes = [DecisionModelError("HTTP 500"), decision()]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    env.add_frames(10.3)
    env.clock.now = 10.5
    pipeline.run_once()
    # 失敗した要求も通し番号を進める
    assert pipeline.request_count == 2
    assert [entry.number for entry in request_log.entries] == [1, 2]
    assert [entry.result for entry in request_log.entries] == ["error", "ok"]
    assert [event["number"] for event in env.events("decision")] == [2]


def test_request_count_starts_at_zero():
    pipeline, _ = make_pipeline()
    assert pipeline.request_count == 0


def test_no_request_log_entry_without_new_frame():
    # 新しいフレームが無く要求を送らない回は、通し番号を進めず、要求のログにも書かない
    pipeline, env, request_log = make_pipeline_with_request_log()
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.model.requests == []
    assert pipeline.request_count == 0
    assert request_log.entries == []


def test_request_log_records_decision_error_and_raises():
    pipeline, env, request_log = make_pipeline_with_request_log()
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(DecisionModelError, match="HTTP 500"):
        pipeline.run_once()
    assert len(request_log.entries) == 1
    entry = request_log.entries[0]
    assert entry.result == "error"
    assert entry.error == "HTTP 500"
    assert entry.request is env.model.requests[0]
    assert entry.sent_at == 10.0
    assert entry.encoded_at == 10.0
    assert entry.received_at is None
    assert entry.decision is None
    assert entry.action is None
    assert entry.advisor_accepted == frozenset()


def test_request_log_records_discarded_decision_when_capture_is_lost():
    pipeline, env, request_log = make_pipeline_with_request_log()
    sent_decision = decision()
    env.model.outcomes = [sent_decision]

    def lose_capture():
        env.capture_lost = "no new frame for 2.0 seconds"

    env.model.on_decide = lose_capture
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run_once()
    assert len(request_log.entries) == 1
    entry = request_log.entries[0]
    assert entry.result == "discarded"
    assert entry.error == "no new frame for 2.0 seconds"
    assert entry.received_at == pytest.approx(10.2)
    assert entry.decision is sent_decision
    # 操作を決める前に応答を捨てた
    assert entry.action is None
    assert entry.advisor_accepted == frozenset()


def test_request_log_records_discarded_decision_when_capture_ends():
    pipeline, env, request_log = make_pipeline_with_request_log()

    def end_capture():
        env.capture_ended = True

    env.model.on_decide = end_capture
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="capture ended"):
        pipeline.run_once()
    assert [(e.result, e.error) for e in request_log.entries] == [
        ("discarded", "capture ended")
    ]


def mark_capture_lost(env: Env) -> None:
    env.capture_lost = "no new frame for 2.0 seconds"


def mark_capture_ended(env: Env) -> None:
    env.capture_ended = True


@pytest.mark.parametrize("stop_capture", [mark_capture_lost, mark_capture_ended])
def test_neutralizes_before_writing_request_log_when_capture_stops_while_waiting(
    stop_capture,
):
    pipeline, env = make_pipeline()
    # 偽の output の neutral / apply と、偽の RequestLog の write を、呼ばれた順に 1 つのリストに記録する
    calls: list[str] = []
    env.output.events = calls

    class OrderRecordingRequestLog(FakeRequestLog):
        def write(self, entry: RequestLogEntry) -> None:
            calls.append(f"write {entry.result}")
            super().write(entry)

    pipeline.parts.request_log = OrderRecordingRequestLog()
    env.model.on_decide = lambda: stop_capture(env)
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()
    # 要求のログの書き込みで中立に戻すのを遅らせないよう、先に中立に戻してからログを書く
    assert calls == ["neutral", "write discarded"]


def test_neutralizes_before_writing_request_log_when_decide_fails():
    pipeline, env = make_pipeline()
    # 偽の output の neutral / apply と、偽の RequestLog の write を、呼ばれた順に 1 つのリストに記録する
    calls: list[str] = []
    env.output.events = calls

    class OrderRecordingRequestLog(FakeRequestLog):
        def write(self, entry: RequestLogEntry) -> None:
            calls.append(f"write {entry.result}")
            super().write(entry)

    pipeline.parts.request_log = OrderRecordingRequestLog()
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(DecisionModelError, match="HTTP 500"):
        pipeline.run_once()
    # 要求のログの書き込みで中立に戻すのを遅らせないよう、先に中立に戻してからログを書く
    assert calls == ["neutral", "write error"]


def test_request_log_records_discarded_decision_when_output_is_stopped():
    pipeline, env, request_log = make_pipeline_with_request_log()
    sent_decision = decision(chosen={"button_A": "hold"})
    env.model.outcomes = [sent_decision]
    env.output.apply_errors = [OutputStoppedError("pad output was stopped")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="pad output was stopped"):
        pipeline.run_once()
    assert len(request_log.entries) == 1
    entry = request_log.entries[0]
    assert entry.result == "discarded"
    assert entry.error == "pad output was stopped"
    assert entry.decision is sent_decision
    # 操作は決めたが、送られていない
    assert entry.action is not None
    assert entry.action.held.buttons == frozenset({"A"})
    assert entry.advisor_accepted == frozenset()


def test_request_log_uses_reason_from_watcher_when_output_is_stopped():
    pipeline, env, request_log = make_pipeline_with_request_log()

    class StoppingOutput(FakeOutput):
        def apply(self, action):
            env.capture_lost = "no new frame for 2.0 seconds"
            raise OutputStoppedError("pad output was stopped")

    pipeline.parts.output = StoppingOutput()
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError):
        pipeline.run_once()
    assert [(e.result, e.error) for e in request_log.entries] == [
        ("discarded", "no new frame for 2.0 seconds")
    ]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("bridge did not acknowledge the report"),
        OSError("pipe closed"),
    ],
)
def test_request_log_records_send_error(error):
    pipeline, env, request_log = make_pipeline_with_request_log()
    sent_decision = decision(chosen={"button_A": "hold"})
    env.model.outcomes = [sent_decision]
    env.output.apply_errors = [error]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(OutputSendError):
        pipeline.run_once()
    assert len(request_log.entries) == 1
    entry = request_log.entries[0]
    assert entry.result == "error"
    assert entry.error == describe_error(error)
    assert entry.received_at == pytest.approx(10.2)
    assert entry.decision is sent_decision
    # 応答から決めた操作を記録する
    assert entry.action is not None
    assert entry.action.held.buttons == frozenset({"A"})
    assert entry.advisor_accepted == frozenset()


def test_request_log_write_error_does_not_stop_requests():
    pipeline, env, request_log = make_pipeline_with_request_log()
    stop = threading.Event()
    env.model.decide_s = 0.1
    errors = [OSError("disk full"), OSError("disk full")]
    request_log.errors = list(errors)

    def add_frame_and_stop_after_two_requests():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 2:
            stop.set()

    env.model.on_decide = add_frame_and_stop_after_two_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 書けなくても判断のループは止まらず、操作を送って run.jsonl に残す
    assert len(env.model.requests) == 2
    assert len(env.output.applied) == 2
    assert len(env.events("decision")) == 2
    assert len(env.operation_log.since(0.0)) == 2
    assert env.events("request_log_error") == [
        {"event": "request_log_error", "error": describe_error(error)}
        for error in errors
    ]
    assert request_log.errors == []


@pytest.mark.parametrize(
    "error",
    [
        UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
        ValueError("Circular reference detected"),
    ],
)
def test_request_log_value_error_does_not_stop_run_once(error):
    pipeline, env, request_log = make_pipeline_with_request_log()
    request_log.errors = [error]
    env.model.outcomes = [decision()]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 書けなくても例外を出さず、操作を送って記録し、run.jsonl に残す
    assert len(env.output.applied) == 1
    assert len(env.operation_log.since(0.0)) == 1
    assert len(env.events("decision")) == 1
    assert env.events("request_log_error") == [
        {"event": "request_log_error", "error": describe_error(error)}
    ]


def test_request_log_write_error_keeps_decision_model_error():
    pipeline, env, request_log = make_pipeline_with_request_log()
    error = OSError("disk full")
    request_log.errors = [error]
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    # 要求のログが書けなくても、Decision Model の失敗はそのまま出す
    with pytest.raises(DecisionModelError, match="HTTP 500"):
        pipeline.run_once()
    assert env.events("request_log_error") == [
        {"event": "request_log_error", "error": describe_error(error)}
    ]


def test_request_log_writes_one_entry_per_request():
    pipeline, env, request_log = make_pipeline_with_request_log()
    run_three_requests(env, pipeline)
    assert [entry.number for entry in request_log.entries] == [1, 2, 3]
    assert [entry.request for entry in request_log.entries] == env.model.requests
    assert [event["number"] for event in env.events("decision")] == [1, 2, 3]


# --- run ---


def test_run_neutralizes_waits_and_stops_after_consecutive_decision_errors():
    pipeline, env = make_pipeline(max_consecutive_errors=3, error_backoff_s=1.0)
    env.model.decide_s = 0.0
    env.model.outcomes = [DecisionModelError("HTTP 500") for _ in range(10)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(RuntimeError) as excinfo:
        pipeline.run(threading.Event())
    assert type(excinfo.value) is RuntimeError
    assert len(env.model.requests) == 3
    # 失敗のたびに、run_once が要求のログを書く前に 1 回、run の扱いでもう 1 回、中立に戻す
    assert env.output.events == ["neutral", "neutral"] * 3
    assert len(env.events("decision_error")) == 3
    # 失敗のたびに待つ (止まる直前の待ちの有無は問わない)
    assert env.sleeps[:2] == [1.0, 1.0]
    assert set(env.sleeps) == {1.0}


def test_run_resets_error_count_after_success():
    pipeline, env = make_pipeline(max_consecutive_errors=2)
    stop = threading.Event()
    env.model.decide_s = 0.1
    error = DecisionModelError("HTTP 500")
    env.model.outcomes = [error, decision(), error, decision(), error, decision()]

    def add_frame_and_stop_after_six_requests():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 6:
            stop.set()

    env.model.on_decide = add_frame_and_stop_after_six_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 6
    # 3 回の失敗のそれぞれで、run_once と run の扱いが 1 回ずつ中立に戻す
    assert env.output.events.count("neutral") == 6
    assert env.output.events.count("apply") == 3


def test_run_resends_latest_frame_after_first_request_fails():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    # 応答の時間 (0.6 秒) と失敗の後の待ち (1.0 秒) の間 (中立に戻した 13.6 から 14.6 まで) は、
    # 2 秒間隔のフレームが届かない。失敗した要求では last_sent_frame_time を進めないため、
    # 次のフレーム (15.0) を待たずに、同じフレーム (13.0) で 2 回目の要求を送る
    env.model.decide_s = 0.6
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    # 起動 (10.0) から 3 秒 (stale_frame_timeout_s より長く first_frame_timeout_s より短い) フレームが無く、
    # その後 13.0 から 2 秒間隔でフレームが届く
    next_frame_time = 13.0

    def sleep_and_receive_frames(seconds: float) -> None:
        nonlocal next_frame_time
        env.clock.now += seconds
        if env.clock.now > 30.0:
            pytest.fail("pipeline did not stop by 30.0")
        while next_frame_time <= env.clock.now:
            env.add_frames(next_frame_time)
            next_frame_time += 2.0

    def stop_after_two_requests():
        if len(env.model.requests) == 2:
            stop.set()

    pipeline.parts.sleep = sleep_and_receive_frames
    env.model.on_decide = stop_after_two_requests
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 2
    assert [frame_times(request) for request in env.model.requests] == [
        [13.0],
        [13.0],
    ]
    assert env.events("capture_stopped") == []
    assert len(env.events("decision_error")) == 1
    assert len(env.events("decision")) == 1


def test_run_continues_after_bridge_reconnecting():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    env.model.decide_s = 0.1
    env.output.apply_errors = [
        BridgeReconnectingError("controller bridge is reconnecting")
    ]

    def add_frame_and_stop_after_two_requests():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 2:
            stop.set()

    env.model.on_decide = add_frame_and_stop_after_two_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 2
    assert len(env.output.applied) == 1
    assert len(env.events("bridge_reconnecting")) == 1


# --- ブリッジの復旧を待つ ---


def sleep_with_new_frames(
    env: Env, after_sleep: Callable[[int], None]
) -> Callable[[float], None]:
    """clock を進めてその時刻の新しいフレームを足し、after_sleep(それまでの sleep の回数) を呼ぶ sleep を返す。

    秒数は env.sleeps に記録する。新しいフレームを足し続けるので、新しいフレームが無い状態にはならない
    (要求を送らないのは、フレームが無いからではないことを確かめるため)。clock が 30.0 を超えたらテストを失敗させる。
    """

    def sleep(seconds: float) -> None:
        env.sleeps.append(seconds)
        env.clock.now += seconds
        if env.clock.now > 30.0:
            pytest.fail("pipeline did not stop by 30.0")
        env.add_frames(env.clock.now)
        after_sleep(len(env.sleeps))

    return sleep


def test_run_sends_no_request_while_bridge_is_recovering():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    recovering_at_decide: list[bool] = []

    def end_recovery_after_three_waits(count: int) -> None:
        if count == 3:
            env.bridge_recovering = False

    def record_and_stop():
        recovering_at_decide.append(env.bridge_recovering)
        stop.set()

    pipeline.parts.sleep = sleep_with_new_frames(env, end_recovery_after_three_waits)
    env.model.on_decide = record_and_stop
    env.bridge_recovering = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 復旧中は要求を送らず、復旧が終わってから要求を再開する
    assert recovering_at_decide == [False]
    assert env.output.events == ["neutral", "apply"]
    assert env.sleeps == [pytest.approx(0.1)] * 3
    assert [event["event"] for event in env.logs] == [
        "bridge_wait",
        "bridge_wait_end",
        "decision",
    ]
    assert env.events("bridge_wait") == [{"event": "bridge_wait"}]
    assert env.events("bridge_wait_end")[0]["waited_s"] == pytest.approx(0.3)


def test_run_sends_no_request_while_recovering_after_send_fails():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    env.model.decide_s = 0.1
    env.output.apply_errors = [
        BridgeReconnectingError("controller bridge is reconnecting")
    ]
    recovering_at_decide: list[bool] = []

    def record_decide():
        recovering_at_decide.append(env.bridge_recovering)
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 1:
            # 1 回目の応答を待つ間にブリッジが壊れて復旧が始まり、操作の送信は BridgeReconnectingError になる
            env.bridge_recovering = True
        if len(env.model.requests) == 2:
            stop.set()

    def end_recovery_after_five_waits(count: int) -> None:
        if count == 5:
            env.bridge_recovering = False

    pipeline.parts.sleep = sleep_with_new_frames(env, end_recovery_after_five_waits)
    env.model.on_decide = record_decide
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 復旧中は要求の数が増えず、復旧が終わってから 2 回目の要求を送る
    assert recovering_at_decide == [False, False]
    assert len(env.events("bridge_reconnecting")) == 1
    assert len(env.events("bridge_wait")) == 1
    assert len(env.output.applied) == 1


def test_run_stops_when_bridge_fails_while_waiting_for_recovery():
    pipeline, env = make_pipeline()

    def fail_bridge_after_three_waits(count: int) -> None:
        if count == 3:
            env.bridge_recovering = False
            env.bridge_failed = True

    pipeline.parts.sleep = sleep_with_new_frames(env, fail_bridge_after_three_waits)
    env.bridge_recovering = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    message = "controller bridge failed while waiting for recovery"
    with pytest.raises(RuntimeError, match=message) as excinfo:
        pipeline.run(threading.Event())
    assert type(excinfo.value) is RuntimeError
    assert env.model.requests == []
    assert len(env.events("bridge_wait_end")) == 1
    assert env.events("pipeline_error") == [
        {"event": "pipeline_error", "error": message}
    ]


def test_run_ends_when_stop_is_set_while_waiting_for_recovery():
    pipeline, env = make_pipeline()
    stop = threading.Event()

    def stop_after_three_waits(count: int) -> None:
        if count == 3:
            stop.set()

    pipeline.parts.sleep = sleep_with_new_frames(env, stop_after_three_waits)
    env.bridge_recovering = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    # 新しいフレームがあっても、stop が立ったら要求を送らずに終わる
    assert env.model.requests == []
    assert env.sleeps == [pytest.approx(0.1)] * 3
    assert len(env.events("bridge_wait_end")) == 1


# --- 見張りが判定したキャプチャーの停止 ---


def sleep_failing_the_test(env: Env) -> Callable[[float], None]:
    """呼ばれたらテストを失敗させる sleep を返す (止まるべき Pipeline が、止まらずに待とうとしたとき)。"""

    def sleep(seconds: float) -> None:
        pytest.fail(
            f"pipeline slept {seconds} seconds instead of stopping "
            f"(applied {len(env.output.applied)} actions)"
        )

    return sleep


def test_run_discards_decision_when_capture_is_lost_while_waiting_for_it():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor_strategy=0.9)]

    def lose_capture():
        env.capture_lost = "no new frame for 2.0 seconds"

    env.model.on_decide = lose_capture
    pipeline.parts.sleep = sleep_failing_the_test(env)
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run(threading.Event())
    # 応答は捨て、操作を送らず、記録にも残さずに、中立に戻して止まる
    assert len(env.model.requests) == 1
    assert env.output.applied == []
    assert env.output.events == ["neutral"]
    assert env.operation_log.since(0.0) == []
    assert env.no_advisor_was_called()
    assert env.events("decision") == []
    assert env.events("capture_stopped") == [
        {"event": "capture_stopped", "error": "no new frame for 2.0 seconds"}
    ]


def test_run_discards_decision_when_capture_ends_while_waiting_for_it():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision()]

    def end_capture():
        env.capture_ended = True

    env.model.on_decide = end_capture
    pipeline.parts.sleep = sleep_failing_the_test(env)
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="capture ended"):
        pipeline.run(threading.Event())
    assert len(env.model.requests) == 1
    assert env.output.applied == []
    assert env.output.events == ["neutral"]
    assert env.operation_log.since(0.0) == []
    assert env.events("decision") == []
    assert env.events("capture_stopped") == [
        {"event": "capture_stopped", "error": "capture ended"}
    ]


def test_run_discards_decision_when_output_is_stopped_before_apply():
    # 応答の後の確認では止まっていなかったが、apply までの間に見張りが出力を止めた場合
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor_strategy=0.9)]
    env.output.apply_errors = [OutputStoppedError("pad output was stopped")]
    pipeline.parts.sleep = sleep_failing_the_test(env)
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="pad output was stopped"):
        pipeline.run(threading.Event())
    # 操作は送られていないので、記録にも残さず、advisor も呼ばない。送信の失敗としても扱わない
    assert len(env.model.requests) == 1
    assert env.output.applied == []
    assert env.operation_log.since(0.0) == []
    assert env.no_advisor_was_called()
    assert env.events("decision") == []
    assert env.events("send_error") == []
    assert env.events("capture_stopped") == [
        {"event": "capture_stopped", "error": "pad output was stopped"}
    ]


def test_output_stopped_uses_reason_from_watcher_when_available():
    pipeline, env = make_pipeline()

    class StoppingOutput(FakeOutput):
        def apply(self, action):
            # apply のロックを取る直前に、見張りが判定して出力を止めた
            env.capture_lost = "no new frame for 2.0 seconds"
            raise OutputStoppedError("pad output was stopped")

    pipeline.parts.output = StoppingOutput()
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run_once()
    assert env.operation_log.since(0.0) == []


def test_run_once_stops_without_request_when_capture_is_lost():
    pipeline, env = make_pipeline()
    env.capture_lost = "no new frame for 2.0 seconds"
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run_once()
    assert env.model.requests == []
    assert env.output.events == ["neutral"]


def test_run_stops_when_capture_is_lost_while_waiting_for_bridge_recovery():
    pipeline, env = make_pipeline()

    def lose_capture_after_three_waits(count: int) -> None:
        if count == 3:
            env.capture_lost = "no new frame for 2.0 seconds"

    def log_with_limit(event: dict) -> None:
        env.logs.append(event)
        if len(env.logs) > 50:
            pytest.fail("pipeline kept logging without stopping")

    pipeline.parts.sleep = sleep_with_new_frames(env, lose_capture_after_three_waits)
    pipeline.parts.log = log_with_limit
    # ブリッジは最後まで復旧中のまま
    env.bridge_recovering = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(CaptureStoppedError, match="no new frame for 2.0 seconds"):
        pipeline.run(threading.Event())
    assert env.model.requests == []
    assert env.sleeps == [pytest.approx(0.1)] * 3
    assert len(env.events("bridge_wait")) == 1
    assert len(env.events("bridge_wait_end")) == 1
    assert len(env.events("capture_stopped")) == 1


def test_run_logs_and_raises_capture_stopped():
    pipeline, env = make_pipeline()
    env.capture_ended = True
    with pytest.raises(CaptureStoppedError):
        pipeline.run(threading.Event())
    assert len(env.events("capture_stopped")) == 1


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("controller bridge could not be reconnected"),
        OSError("pipe closed"),
    ],
)
def test_run_logs_and_raises_send_errors_when_bridge_failed(error):
    pipeline, env = make_pipeline()
    env.output.apply_errors = [error]
    env.bridge_failed = True
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(type(error)) as excinfo:
        pipeline.run(threading.Event())
    assert excinfo.value is error
    assert len(env.events("pipeline_error")) == 1


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("bridge did not acknowledge the report"),
        OSError("pipe closed"),
    ],
)
def test_run_logs_and_continues_send_errors_while_bridge_is_not_failed(error):
    pipeline, env = make_pipeline()
    stop = threading.Event()
    env.model.decide_s = 0.1
    env.output.apply_errors = [error]

    def add_frame_and_stop_after_two_requests():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 2:
            stop.set()

    env.model.on_decide = add_frame_and_stop_after_two_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 2
    assert len(env.output.applied) == 1
    assert len(env.events("send_error")) == 1
    assert env.events("pipeline_error") == []


def test_run_once_wraps_send_error_from_apply():
    pipeline, env = make_pipeline()
    error = RuntimeError("bridge did not acknowledge the report")
    env.output.apply_errors = [error]
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(OutputSendError) as excinfo:
        pipeline.run_once()
    assert excinfo.value.error is error


def test_run_does_not_treat_other_runtime_errors_as_send_errors():
    pipeline, env = make_pipeline()

    def failing_read_notes() -> dict[str, str]:
        raise RuntimeError("notes broken")

    pipeline.parts.read_notes = failing_read_notes
    env.add_frames(9.9)
    env.clock.now = 10.0
    with pytest.raises(RuntimeError, match="notes broken"):
        pipeline.run(threading.Event())
    assert env.events("send_error") == []


def test_run_does_nothing_when_stop_is_already_set():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    stop.set()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert env.model.requests == []


# --- 判断のループを続けたまま中立に戻したことの記録 ---


def neutral_records(env: Env) -> list[NeutralRecord]:
    """env の operation_log にある、中立に戻した記録を古い順に返す。"""
    return [
        record
        for record in env.operation_log.since(0.0)
        if isinstance(record, NeutralRecord)
    ]


def test_request_after_decision_error_shows_neutral_as_previous_operation():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [decision(), DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の応答は 10.2 に受け取り、RIGHT を押した。2 回目の要求は 10.7 に失敗し、中立に戻した
    env.add_frames(10.4)
    env.clock.now = 10.5
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    env.add_frames(10.6, 10.8)
    env.clock.now = 11.0
    pipeline.run_once()
    request = env.model.requests[2]
    assert request.history_text == (
        f"{HISTORY_HEADING}\n"
        "- t=1.20 秒 (0.80 秒前): 押下 none / 短押し none\n"
        "- t=1.70 秒 (0.30 秒前): 中立に戻した (Decision Model の失敗) (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=1.70 秒 (0.30 秒前) に、中立に戻した (Decision Model の失敗) ──"
    )
    # 中立に戻した時刻 (10.7) が、前回の操作の前と後の境目になる。最新のフレーム (10.8) はその後
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=1.80 秒、0.20 秒前、前回の操作の後)", True)]


def test_request_after_first_request_fails_marks_latest_frame_after_neutral():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    # 最初の要求は 10.2 に失敗し、中立に戻した (成功した要求はまだ無い)
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    # 中立に戻す前 (9.6) と後 (10.5) に意味のあるフレームを撮り、失敗の後の待ちの後の 11.2 に次の要求を送る
    env.store.add(frame(9.6))
    env.store.add(frame(10.5))
    env.add_frames(10.5, 11.0)
    env.clock.now = 11.2
    pipeline.run_once()
    request = env.model.requests[1]
    # 意味のあるフレームは載せず、中立に戻した後の最新のフレーム (11.0) を「前回の操作の後」の画像として載せる
    assert [
        (image.frame_time, image.label, image.after_previous_operation)
        for image in request.images
    ] == [(11.0, "最新 (t=2.00 秒、0.20 秒前、前回の操作の後)", True)]


def test_request_after_decision_error_resends_latest_frame_without_new_frame():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    env.add_frames(9.9)
    env.clock.now = 10.0
    # 最初の要求は 10.2 に失敗し、中立に戻した
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    # 失敗した要求では last_sent_frame_time を進めないため、新しいフレームが届いていなくても、
    # 同じフレーム (9.9) を「前回の操作 (中立に戻したこと) の前」の画像として次の要求に載せる
    assert pipeline.last_sent_frame_time is None
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.model.requests) == 2
    assert [
        (image.frame_time, image.label, image.after_previous_operation)
        for image in env.model.requests[1].images
    ] == [(9.9, "最新 (t=0.90 秒、0.60 秒前、前回の操作の前)", False)]


def test_run_records_neutral_once_for_each_decision_error():
    pipeline, env = make_pipeline(now=9.0, error_backoff_s=1.0)
    stop = threading.Event()
    env.model.outcomes = [decision(), DecisionModelError("HTTP 500")]

    def add_frame_and_stop_after_three_requests():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 3:
            stop.set()

    # 失敗の後の待ち (1.0 秒) で clock を進め、待ち終わりの時刻のフレームを足す
    pipeline.parts.sleep = sleep_with_new_frames(env, lambda count: None)
    env.model.on_decide = add_frame_and_stop_after_three_requests
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 3
    # run_once と run の扱いで 2 回中立に戻すが、記録は 1 件だけ (2 回目の要求が失敗した 10.4 の記録)
    assert env.output.events == ["apply", "neutral", "neutral", "apply"]
    records = neutral_records(env)
    assert len(records) == 1
    assert records[0].neutralized_at == pytest.approx(10.4)
    assert records[0].reason == "Decision Model の失敗"
    assert len(env.operation_log.since(0.0)) == 3
    assert env.model.requests[2].history_text.splitlines()[-1] == (
        "- t=1.40 秒 (1.00 秒前): 中立に戻した (Decision Model の失敗) (前回の操作)"
    )


def test_request_after_bridge_wait_shows_neutral_as_previous_operation():
    pipeline, env = make_pipeline(now=9.0)
    stop = threading.Event()
    env.model.decide_s = 0.1
    env.model.outcomes = [decision()]

    def add_frame_and_break_bridge_on_second_request():
        env.add_frames(env.clock.now)
        if len(env.model.requests) == 2:
            # 2 回目の応答を待つ間にブリッジが壊れて復旧が始まり、操作の送信は BridgeReconnectingError になる
            env.bridge_recovering = True
            env.output.apply_errors = [
                BridgeReconnectingError("controller bridge is reconnecting")
            ]
        if len(env.model.requests) == 3:
            stop.set()

    def end_recovery_after_two_waits(count: int) -> None:
        if count == 2:
            env.bridge_recovering = False

    pipeline.parts.sleep = sleep_with_new_frames(env, end_recovery_after_two_waits)
    env.model.on_decide = add_frame_and_break_bridge_on_second_request
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 3
    # 1 回目の操作 (10.1 に RIGHT を押した) の後、10.2 に復旧を待ち始めて中立に戻し、10.4 に 3 回目の要求を送った
    assert env.output.events == ["apply", "neutral", "apply"]
    records = neutral_records(env)
    assert len(records) == 1
    assert records[0].neutralized_at == pytest.approx(10.2)
    assert records[0].reason == "コントローラーの接続の復旧待ち"
    request = env.model.requests[2]
    assert request.history_text == (
        f"{HISTORY_HEADING}\n"
        "- t=1.10 秒 (0.30 秒前): 押下 none / 短押し none\n"
        "- t=1.20 秒 (0.20 秒前): 中立に戻した (コントローラーの接続の復旧待ち) (前回の操作)"
    )
    assert request.previous_operation_text == (
        "── t=1.20 秒 (0.20 秒前) に、中立に戻した (コントローラーの接続の復旧待ち) ──"
    )
    # 最新のフレーム (10.4) は、中立に戻した後 (復旧を待つ間) に撮ったフレーム
    assert frame_times(request) == pytest.approx([10.4])
    assert [
        (image.label, image.after_previous_operation) for image in request.images
    ] == [("最新 (t=1.40 秒、0.00 秒前、前回の操作の後)", True)]


def test_advisor_operation_log_includes_neutral_after_decision_error():
    pipeline, env = make_pipeline(now=9.0)
    env.model.outcomes = [
        DecisionModelError("HTTP 500"),
        decision(advisor_advice=0.9),
    ]
    env.add_frames(9.9)
    env.clock.now = 10.0
    # 1 回目の要求は 10.2 に失敗し、中立に戻した
    with pytest.raises(DecisionModelError):
        pipeline.run_once()
    env.add_frames(10.4)
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.advisors["advice"].contexts) == 1
    # advisor に渡す記録にも、中立に戻したことの行が入る (中立に戻した行には advisor の確率は書かない)
    assert env.advisors["advice"].contexts[0].operation_log_text == (
        "-0.50 秒: 中立に戻した (Decision Model の失敗)\n"
        "0.00 秒: 押下 none / 短押し none / advisor 0.90"
    )
