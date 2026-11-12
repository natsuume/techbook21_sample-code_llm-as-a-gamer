import threading
from collections.abc import Callable

import numpy as np
import pytest
from switch2_remote.bridge_supervisor import BridgeReconnectingError

from switch2_agent.action_decision import PadAction, Thresholds
from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.decision_models.base import Decision, DecisionModelError
from switch2_agent.decision_request import DecisionRequest
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.even_interval import EvenIntervalSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.operation_log import OperationLog, format_action
from switch2_agent.pad_output import OutputStoppedError
from switch2_agent.pipeline import (
    CaptureStoppedError,
    OutputSendError,
    Pipeline,
    PipelineParts,
)
from switch2_agent.questions import build_questions

NOTES = {"controls": "A で決定", "tips": "", "progress": "1 面", "strategy": "右へ進む"}
DECISION_LOG_KEYS = {
    "select_s",
    "encode_s",
    "decide_s",
    "apply_s",
    "images",
    "payload_bytes",
    "action",
    "advisor_probability",
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


def decision(refusals: int = 0, **overrides: float) -> Decision:
    """すべての問いの確率を 0.0 にし、overrides の問いだけを置き換えた Decision を返す。"""
    probabilities = {question.name: 0.0 for question in build_questions()}
    probabilities.update(overrides)
    return Decision(probabilities=probabilities, refusals=refusals)


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
    """偽の Advisor。受け取った文脈を記録し、answers の先頭 (空なら True) を返す。"""

    def __init__(self):
        self.contexts: list[AdvisorContext] = []
        self.answers: list[bool] = []

    def request_update(self, context: AdvisorContext) -> bool:
        self.contexts.append(context)
        return self.answers.pop(0) if self.answers else True

    def is_busy(self) -> bool:
        return False

    def close(self) -> None:
        pass


class Env:
    """Pipeline に渡した偽の部品と、記録を持つ。"""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.history = FrameHistory(60.0)
        self.store = MeaningfulFrameStore()
        self.model = FakeModel(clock)
        self.output = FakeOutput()
        self.advisor = FakeAdvisor()
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


def make_pipeline(now: float = 9.0, **kwargs) -> tuple[Pipeline, Env]:
    """時刻 now で作った Pipeline と、その偽の部品を返す。kwargs は Pipeline に渡す。"""
    env = Env(FakeClock(now))
    parts = PipelineParts(
        history=env.history,
        store=env.store,
        selector=EvenIntervalSelector(),
        model=env.model,
        output=env.output,
        advisor=env.advisor,
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


def frame_times(request: DecisionRequest) -> list[float]:
    return [image.frame_time for image in request.images]


# --- 要求に載せるフレーム ---


def test_first_request_uses_frames_just_before_now():
    pipeline, env = make_pipeline()
    env.add_frames(9.0, 9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert frame_times(env.model.requests[0]) == [9.6, 9.8, 9.9]


def test_second_request_window_starts_at_first_send_time():
    pipeline, env = make_pipeline()
    env.add_frames(9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    # 1 回目の送信時刻 (10.0) より前の時刻で遅れて届いたフレームは、2 回目の直近の区間に入らない
    env.add_frames(9.95, 10.1, 10.3)
    env.clock.now = 10.5
    pipeline.run_once()
    assert len(env.model.requests) == 2
    assert frame_times(env.model.requests[1]) == [10.1, 10.3]


def test_request_puts_meaningful_frames_before_recent_frames():
    pipeline, env = make_pipeline()
    env.store.add(frame(5.0))
    env.add_frames(9.6, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    request = env.model.requests[0]
    assert frame_times(request) == [5.0, 9.6, 9.9]
    assert request.images[0].label.startswith("過去の場面")
    assert request.images[1].label.startswith("直近")


def test_request_contains_latest_notes_and_questions():
    pipeline, env = make_pipeline()
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    request = env.model.requests[0]
    assert "右へ進む" in request.state_text
    assert [q.name for q in request.questions] == [q.name for q in build_questions()]


# --- 操作 ---


def test_applies_decided_action_and_records_operation():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(press_A=0.9, tap_B=0.8)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert len(env.output.applied) == 1
    applied = env.output.applied[0]
    assert applied.held.buttons == frozenset({"A"})
    assert applied.taps == frozenset({"B"})
    records = env.operation_log.since(0.0)
    assert len(records) == 1
    assert records[0].sent_at == 10.0
    assert records[0].received_at == pytest.approx(10.2)
    assert records[0].action == applied


# --- advisor ---


def test_advisor_is_requested_when_probability_exceeds_threshold():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor=0.9)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert len(env.advisor.contexts) == 1
    context = env.advisor.contexts[0]
    request = env.model.requests[0]
    assert context.game == "zelda"
    assert context.images == request.images
    assert context.current_strategy == "右へ進む"
    assert context.trigger_probability == 0.9
    assert context.trigger_time_text
    # パイプラインの開始 (9.0) からの応答 1 件が操作の記録に入る
    assert len(context.operation_log_text.splitlines()) == 1
    assert "advisor 0.90" in context.operation_log_text
    log = env.events("decision")[0]
    assert log["advisor_requested"] is True
    assert log["advisor_accepted"] is True


def test_advisor_is_not_requested_at_threshold():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(advisor=0.5)]
    env.add_frames(9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    assert env.advisor.contexts == []
    log = env.events("decision")[0]
    assert log["advisor_requested"] is False
    assert log["advisor_accepted"] is False


def run_three_requests(env: Env, pipeline: Pipeline) -> None:
    """時刻 10.0・10.5・11.0 に、新しいフレームを足して 1 回ずつ要求する (応答は 0.2 秒後)。"""
    for now, new_frame in [(10.0, 9.9), (10.5, 10.4), (11.0, 10.9)]:
        env.add_frames(new_frame)
        env.clock.now = now
        pipeline.run_once()


def test_advisor_operation_log_starts_after_last_accepted_request():
    pipeline, env = make_pipeline()
    env.model.outcomes = [
        decision(advisor=0.9),
        decision(advisor=0.1),
        decision(advisor=0.9),
    ]
    run_three_requests(env, pipeline)
    assert len(env.advisor.contexts) == 2
    # 受け付けられた 1 回目の応答の後の、2 回目と 3 回目の応答だけが入る
    assert len(env.advisor.contexts[1].operation_log_text.splitlines()) == 2


def test_rejected_advisor_request_keeps_operation_log_start():
    pipeline, env = make_pipeline()
    env.model.outcomes = [
        decision(advisor=0.9),
        decision(advisor=0.1),
        decision(advisor=0.9),
    ]
    env.advisor.answers = [False, True]
    run_three_requests(env, pipeline)
    assert len(env.advisor.contexts) == 2
    # 1 回目は受け付けられなかったので、起点はパイプラインの開始のまま
    assert len(env.advisor.contexts[1].operation_log_text.splitlines()) == 3
    assert env.events("decision")[0]["advisor_accepted"] is False


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
    env.model.outcomes = [decision(refusals=2, press_A=0.9)]
    env.add_frames(9.6, 9.8, 9.9)
    env.clock.now = 10.0
    pipeline.run_once()
    logs = env.events("decision")
    assert len(logs) == 1
    log = logs[0]
    assert DECISION_LOG_KEYS <= set(log)
    request = env.model.requests[0]
    # 時計が進むのは model.decide の中だけなので、ほかの時間は 0
    assert log["decide_s"] == pytest.approx(0.2)
    for key in ("select_s", "encode_s", "apply_s"):
        assert log[key] == pytest.approx(0.0)
    assert log["images"] == 3
    assert log["payload_bytes"] == sum(len(image.jpeg) for image in request.images)
    assert log["action"] == format_action(env.output.applied[0])
    assert log["advisor_probability"] == 0.0
    assert log["refusals"] == 2


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
    assert env.output.events == ["neutral"] * 3
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
    assert env.output.events.count("neutral") == 3
    assert env.output.events.count("apply") == 3


def test_run_waits_for_next_frame_after_first_request_fails():
    pipeline, env = make_pipeline()
    stop = threading.Event()
    # 応答の時間 (0.6 秒) と失敗の後の待ち (1.0 秒) の後は、1 秒間隔のフレームが最初の区間 (0.5 秒) に入らない
    env.model.decide_s = 0.6
    env.model.outcomes = [DecisionModelError("HTTP 500")]
    # 起動 (10.0) から 3 秒 (stale_frame_timeout_s より長く first_frame_timeout_s より短い) フレームが無く、
    # その後 13.0 から 1 秒間隔でフレームが届く
    next_frame_time = 13.0

    def sleep_and_receive_frames(seconds: float) -> None:
        nonlocal next_frame_time
        env.clock.now += seconds
        if env.clock.now > 30.0:
            pytest.fail("pipeline did not stop by 30.0")
        while next_frame_time <= env.clock.now:
            env.add_frames(next_frame_time)
            next_frame_time += 1.0

    def stop_after_two_requests():
        if len(env.model.requests) == 2:
            stop.set()

    pipeline.parts.sleep = sleep_and_receive_frames
    env.model.on_decide = stop_after_two_requests
    env.clock.now = 10.0
    pipeline.run(stop)
    assert len(env.model.requests) == 2
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

    秒数は env.sleeps に記録する。新しいフレームを足し続けるので、直近の区間は空にならない
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
    # 直近の区間に新しいフレームがあっても、stop が立ったら要求を送らずに終わる
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
    env.model.outcomes = [decision(press_A=0.9, advisor=0.9)]

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
    assert env.advisor.contexts == []
    assert env.events("decision") == []
    assert env.events("capture_stopped") == [
        {"event": "capture_stopped", "error": "no new frame for 2.0 seconds"}
    ]


def test_run_discards_decision_when_capture_ends_while_waiting_for_it():
    pipeline, env = make_pipeline()
    env.model.outcomes = [decision(press_A=0.9)]

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
    env.model.outcomes = [decision(press_A=0.9, advisor=0.9)]
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
    assert env.advisor.contexts == []
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
