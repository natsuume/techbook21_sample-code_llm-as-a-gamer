import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from switch2_remote.controller_state import NEUTRAL_REPORT

from switch2_layers.buttons import ALL_BUTTONS
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.middle.middle_loop import middle_failure_reason
from switch2_layers.middle.system_screen_middle import SystemScreenMiddleError
from switch2_layers.middle.system_screen_types import (
    SystemCutReason,
    SystemGoal,
    SystemOperation,
    SystemRunResult,
    SystemScreenInput,
    SystemScreenOutput,
)
from switch2_layers.records.operation_log import OperationEvent
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.system_screen.run_phase import RunPhase
from switch2_layers.system_screen.system_operation_runner import (
    SETTLE_S,
    SystemOutputError,
)
from switch2_layers.system_screen.system_screen_loop import (
    PHASE_GOALS,
    SYSTEM_LOOP_POLL_S,
    SystemScreenLoop,
    SystemScreenLoopParts,
    SystemScreenLoopSettings,
    SystemScreenStepOutcome,
    SystemScreenStepResult,
)

ORIGIN = 1000.0
START_T = 10.0  # 段階に入った時刻
SOFTWARE = "テストのソフト"
USER = "テストのユーザー"
MIDDLE_ELAPSED_S = 1.0  # middle の decide の間に進める偽の時計の秒数
RUNNER_ELAPSED_S = 2.0  # 操作の列を送る間に進める偽の時計の秒数


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass(frozen=True)
class FakeJudgment:
    """照合の結果 (JudgmentResult の形)。"""

    frame_t: float
    screen: str | None


class FakeJudgments:
    """JudgmentSource の偽物。足した結果のうち frame_t がいちばん新しいものを持ち、待たずに返す。"""

    def __init__(self):
        self.results: list[FakeJudgment] = []
        self.after_values: list[float] = []

    def add(self, frame_t: float, screen: str | None) -> None:
        self.results.append(FakeJudgment(frame_t, screen))

    def latest(self) -> FakeJudgment | None:
        return max(self.results, key=lambda result: result.frame_t, default=None)

    def wait_newer(self, after_frame_t: float, timeout_s: float) -> FakeJudgment | None:
        self.after_values.append(after_frame_t)
        latest = self.latest()
        if latest is not None and latest.frame_t > after_frame_t:
            return latest
        return None


class FakePhases:
    """SystemScreenPhases の偽物。段階はテストが書き換え、入力の呼ばれ方を名前の列で記録する。

    on_stop が呼ばれたことも、calls に "stop" として記録する (stop_requested)。clean_up_on_stop が True なら、
    そのときに clean_up に移す (app の request_stop が、片付けを行う理由で片付けを始めるのと同じ)。
    """

    def __init__(self, phase: RunPhase = RunPhase.PREPARE, started_t: float = START_T):
        self.phase = phase
        self.phase_started_t = started_t
        self.wants_system_middle = True
        self.calls: list[str] = []
        self.outputs_done: list[bool] = []
        # on_system_middle_failed を受け取ったときの段階
        self.failed_phases: list[RunPhase] = []
        self.clean_up_on_stop = False

    def stop_requested(self, t: float) -> None:
        self.calls.append("stop")
        if self.clean_up_on_stop:
            self.phase = RunPhase.CLEAN_UP
            self.phase_started_t = t

    def on_system_output(self, t: float, done: bool) -> None:
        self.calls.append("output")
        self.outputs_done.append(done)

    def on_operations_started(self, t: float) -> None:
        self.calls.append("started")

    def on_operations_finished(self, t: float) -> None:
        self.calls.append("finished")

    def on_system_middle_failed(self, t: float) -> None:
        self.calls.append("failed")
        self.failed_phases.append(self.phase)


class ScriptedMiddle:
    """SystemScreenMiddle の偽物。results を順に返す (例外なら出す。最後の 1 つは繰り返す)。

    decide の間に偽の時計を MIDDLE_ELAPSED_S 進め、on_decide があれば呼ぶ。受け取った入力を記録する。
    """

    def __init__(self, monotonic: FakeMonotonic, results: list):
        self.monotonic = monotonic
        self.results = list(results)
        self.inputs: list[SystemScreenInput] = []
        self.on_decide: Callable[[], None] | None = None

    def decide(self, screen_input: SystemScreenInput) -> SystemScreenOutput:
        self.inputs.append(screen_input)
        self.monotonic.now += MIDDLE_ELAPSED_S
        if self.on_decide is not None:
            self.on_decide()
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class FakeRunner:
    """OperationsRunner の偽物。results を順に返す (例外なら出す。最後の 1 つは繰り返す)。

    送る間に偽の時計を RUNNER_ELAPSED_S 進め、渡された (出力, 照合の結果の画面) を記録する。
    """

    def __init__(self, monotonic: FakeMonotonic, results: list):
        self.monotonic = monotonic
        self.results = list(results)
        self.calls: list[tuple[SystemScreenOutput, str | None]] = []

    def run(
        self,
        output: SystemScreenOutput,
        judged_screen: str | None,
        stop: threading.Event,
    ) -> SystemRunResult:
        self.calls.append((output, judged_screen))
        self.monotonic.now += RUNNER_ELAPSED_S
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class FakeSender:
    def __init__(self):
        self.reports: list[bytes] = []

    def set_report(self, report: bytes) -> str:
        self.reports.append(bytes(report))
        return "ack"


@dataclass
class Harness:
    monotonic: FakeMonotonic
    clock: RunClock
    paths: RunPaths
    judgments: FakeJudgments
    phases: FakePhases
    middle: ScriptedMiddle
    runner: FakeRunner
    sender: FakeSender
    loop: SystemScreenLoop
    bridge: dict[str, str] = field(default_factory=lambda: {"state": "ok"})
    frame: dict[str, float | None] = field(default_factory=lambda: {"t": None})
    events: list[OperationEvent] = field(default_factory=list)
    stops: list[str] = field(default_factory=list)


NV12 = np.full((360 * 3 // 2, 640), 128, np.uint8)


def make_harness(
    tmp_path: Path,
    middle_results: list,
    runner_results: list | None = None,
    phase: RunPhase = RunPhase.PREPARE,
    max_errors: int = 5,
) -> Harness:
    """偽の部品で SystemScreenLoop を組み立てる。時計は段階に入った時刻 START_T から始める。"""
    root = tmp_path / "run"
    (root / "middle").mkdir(parents=True)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + START_T)
    clock = RunClock(monotonic, origin=ORIGIN)
    judgments = FakeJudgments()
    phases = FakePhases(phase)
    middle = ScriptedMiddle(monotonic, middle_results)
    runner = FakeRunner(monotonic, runner_results or [sent_all(0)])
    sender = FakeSender()
    events: list[OperationEvent] = []
    pad_output = PadOutput(
        sender,
        ALL_BUTTONS,
        clock=monotonic,
        on_operation=events.append,
        start_timer=False,
    )
    bridge = {"state": "ok"}
    frame: dict[str, float | None] = {"t": None}
    stops: list[str] = []

    def request_stop(reason: str) -> None:
        stops.append(reason)
        phases.stop_requested(clock.now())

    def latest_frame() -> CapturedFrame | None:
        t = frame["t"]
        return None if t is None else CapturedFrame(t=t, nv12=NV12)

    loop = SystemScreenLoop(
        SystemScreenLoopParts(
            paths=paths,
            middle=middle,
            runner=runner,
            judgments=judgments,
            latest_frame=latest_frame,
            pad_output=pad_output,
            phases=phases,
            clock=clock,
            bridge_state=lambda: bridge["state"],
            on_stop=request_stop,
        ),
        SystemScreenLoopSettings(
            software_name=SOFTWARE, user_name=USER, max_errors=max_errors
        ),
    )
    return Harness(
        monotonic=monotonic,
        clock=clock,
        paths=paths,
        judgments=judgments,
        phases=phases,
        middle=middle,
        runner=runner,
        sender=sender,
        loop=loop,
        bridge=bridge,
        frame=frame,
        events=events,
        stops=stops,
    )


def show(harness: Harness, screen: str | None = "home_menu") -> float:
    """照合の結果を 1 件足し、その frame_t を返す。

    偽の時計を SETTLE_S より長く進め、進めた後の時刻の 0.1 秒前のフレームの結果にする。最新のフレームは進めた後の時刻。
    """
    harness.monotonic.now += SETTLE_S + 0.2
    now = harness.clock.now()
    frame_t = round(now - 0.1, 3)
    harness.judgments.add(frame_t, screen)
    harness.frame["t"] = now
    return frame_t


def press(screen: str, *buttons: str) -> SystemOperation:
    return SystemOperation(screen=screen, kind="press", buttons=buttons, seconds=None)


def output_of(*operations: SystemOperation, done: bool = False) -> SystemScreenOutput:
    return SystemScreenOutput(
        observation="画面の様子", operations=operations, done=done
    )


def sent_all(count: int) -> SystemRunResult:
    return SystemRunResult(
        sent_count=count, cut_index=None, cut_reason=None, blocked_buttons=frozenset()
    )


def send_failed(index: int) -> SystemRunResult:
    return SystemRunResult(
        sent_count=index + 1,
        cut_index=index,
        cut_reason=SystemCutReason.SEND_FAILED,
        blocked_buttons=frozenset(),
    )


def step(harness: Harness) -> SystemScreenStepResult:
    return harness.loop.step(threading.Event())


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def outcome(kind: SystemScreenStepOutcome) -> SystemScreenStepResult:
    return SystemScreenStepResult(kind, 0.0)


# --- 定数 ---


def test_goals_follow_the_phases():
    assert PHASE_GOALS == {
        RunPhase.PREPARE: SystemGoal.PREPARE,
        RunPhase.RECOVER: SystemGoal.RECOVER,
        RunPhase.CLEAN_UP: SystemGoal.CLEAN_UP,
    }
    assert SYSTEM_LOOP_POLL_S == 0.05


# --- 1 回分の流れ ---


def test_prepare_calls_the_middle_once_and_sends_its_operations(tmp_path):
    output = output_of(press("home_menu", "DOWN"))
    harness = make_harness(tmp_path, [output], [sent_all(1)])
    judged_t = show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    [screen_input] = harness.middle.inputs
    assert screen_input.goal is SystemGoal.PREPARE
    assert screen_input.software_name == SOFTWARE
    assert screen_input.user_name == USER
    assert screen_input.judged_screen == "home_menu"
    assert screen_input.judged_frame_t == judged_t
    assert screen_input.frame_t == harness.frame["t"]
    assert screen_input.frame_jpeg[:2] == b"\xff\xd8"
    assert screen_input.history == ()
    # 照合の結果の画面と比べながら、出力の列を送る
    assert harness.runner.calls == [(output, "home_menu")]
    assert harness.phases.calls == ["output", "started", "finished"]
    assert harness.phases.outputs_done == [False]
    rows = read_rows(harness.paths.system_calls)
    assert [row["kind"] for row in rows] == ["call", "run"]
    assert rows[0]["goal"] == "prepare"
    assert rows[0]["judged_screen"] == "home_menu"
    assert rows[0]["error"] is None
    assert rows[1]["sent_count"] == 1
    assert rows[1]["cut_reason"] is None


def test_idle_while_the_phase_does_not_want_the_middle(tmp_path):
    harness = make_harness(tmp_path, [output_of()])
    harness.phases.wants_system_middle = False
    show(harness)
    assert step(harness) == SystemScreenStepResult(
        SystemScreenStepOutcome.IDLE, SYSTEM_LOOP_POLL_S
    )
    assert harness.middle.inputs == []
    assert read_rows(harness.paths.system_calls) == []


def test_first_call_waits_for_a_judgment_of_a_frame_after_the_phase_started(tmp_path):
    harness = make_harness(tmp_path, [output_of()])
    # 段階に入る前のフレームの結果では呼ばない
    harness.judgments.add(START_T - 0.3, "home_menu")
    harness.frame["t"] = START_T
    assert step(harness) == outcome(SystemScreenStepOutcome.NO_JUDGMENT)
    assert harness.middle.inputs == []
    assert harness.judgments.after_values[-1] == START_T
    judged_t = show(harness, "user_select")
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    assert harness.middle.inputs[0].judged_frame_t == judged_t
    assert harness.middle.inputs[0].judged_screen == "user_select"


def test_next_call_waits_for_a_judgment_after_the_operations_plus_settle(tmp_path):
    first = output_of(press("home_menu", "DOWN"))
    harness = make_harness(tmp_path, [first, output_of()], [sent_all(1)])
    show(harness)
    step(harness)
    finished_t = read_rows(harness.paths.system_calls)[-1]["t"]
    # 前の列を送り終えた時刻 + SETTLE_S より後のフレームの結果が届くまで、middle を呼ばない
    assert step(harness) == outcome(SystemScreenStepOutcome.NO_JUDGMENT)
    assert harness.judgments.after_values[-1] == pytest.approx(finished_t + SETTLE_S)
    assert len(harness.middle.inputs) == 1
    judged_t = show(harness)
    assert judged_t > finished_t + SETTLE_S
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    second = harness.middle.inputs[1]
    assert second.judged_frame_t == judged_t
    # この段階の履歴に、前の回の列と送った数が入る
    [record] = second.history
    assert record.operations == first.operations
    assert record.sent_count == 1
    assert record.cut_reason is None
    assert record.judged_screen == "home_menu"


def test_goal_and_history_follow_the_phase(tmp_path):
    harness = make_harness(
        tmp_path, [output_of(press("home_menu", "A"))], phase=RunPhase.RECOVER
    )
    show(harness)
    step(harness)
    assert harness.middle.inputs[0].goal is SystemGoal.RECOVER
    finished_t = read_rows(harness.paths.system_calls)[-1]["t"]
    # 前の列を送り終えてから SETTLE_S たつ前に段階が変わったら、履歴を空にし、前の列を送り終えた時刻 + SETTLE_S
    # より後のフレームの結果を待つ (前の段階の列の後の画面で、次の列を決める)
    harness.phases.phase = RunPhase.CLEAN_UP
    harness.phases.phase_started_t = harness.clock.now()
    assert harness.phases.phase_started_t < finished_t + SETTLE_S
    show(harness)
    step(harness)
    second = harness.middle.inputs[1]
    assert second.goal is SystemGoal.CLEAN_UP
    assert second.history == ()
    assert harness.judgments.after_values[-1] == pytest.approx(finished_t + SETTLE_S)


def test_phase_started_after_the_settle_waits_for_a_frame_of_the_new_phase(tmp_path):
    harness = make_harness(
        tmp_path, [output_of(press("home_menu", "A"))], phase=RunPhase.RECOVER
    )
    show(harness)
    step(harness)
    finished_t = read_rows(harness.paths.system_calls)[-1]["t"]
    # 前の列を送り終えた時刻 + SETTLE_S より後に段階が変わったら、新しい段階に入った時刻より後のフレームの結果を待つ
    harness.phases.phase = RunPhase.CLEAN_UP
    harness.phases.phase_started_t = finished_t + SETTLE_S + 1.0
    step(harness)
    assert harness.judgments.after_values[-1] == pytest.approx(
        harness.phases.phase_started_t
    )


def test_output_is_discarded_when_the_phase_changed_during_the_call(tmp_path):
    harness = make_harness(tmp_path, [output_of(press("user_select", "A"))])

    def begin_clean_up() -> None:
        harness.phases.phase = RunPhase.CLEAN_UP
        harness.phases.phase_started_t = harness.clock.now()

    harness.middle.on_decide = begin_clean_up
    show(harness, "user_select")
    assert step(harness) == outcome(SystemScreenStepOutcome.DISCARDED)
    # 前の段階の出力の列は送らず、段階の判定にも渡さない
    assert harness.runner.calls == []
    assert harness.phases.calls == []
    assert [row["kind"] for row in read_rows(harness.paths.system_calls)] == ["call"]


# --- middle の失敗 ---


def test_middle_failures_are_counted_and_stop_after_max_errors(tmp_path):
    error = SystemScreenMiddleError("luna failed")
    harness = make_harness(tmp_path, [error, output_of(), error, error], max_errors=2)
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.MIDDLE_ERROR)
    assert harness.loop.consecutive_errors == 1
    assert harness.phases.calls == ["failed"]
    [row] = read_rows(harness.paths.system_calls)
    assert row["kind"] == "call"
    assert row["output"] is None
    assert "luna failed" in row["error"]
    # 失敗した回と同じ照合の結果では、続けて呼ばない
    assert step(harness) == outcome(SystemScreenStepOutcome.NO_JUDGMENT)
    assert len(harness.middle.inputs) == 1
    # 成功で数え直す
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    assert harness.loop.consecutive_errors == 0
    show(harness)
    step(harness)
    assert harness.stops == []
    show(harness)
    step(harness)
    assert harness.loop.consecutive_errors == 2
    assert harness.stops == [middle_failure_reason(2)]


def test_failure_reaching_max_errors_is_passed_before_stopping(tmp_path):
    # 上限に達した回の失敗は、on_stop で片付けに移る前の段階 (prepare) のうちに段階の判定に渡す。片付けに移った後で
    # 同じ失敗を片付けの middle の失敗にしないので、片付けの middle をその後に呼べる
    error = SystemScreenMiddleError("luna failed")
    harness = make_harness(tmp_path, [error, error, output_of()], max_errors=2)
    harness.phases.clean_up_on_stop = True
    show(harness)
    step(harness)
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.MIDDLE_ERROR)
    assert harness.phases.calls == ["failed", "failed", "stop"]
    assert harness.phases.failed_phases == [RunPhase.PREPARE, RunPhase.PREPARE]
    assert harness.stops == [middle_failure_reason(2)]
    assert harness.phases.phase is RunPhase.CLEAN_UP
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    assert harness.middle.inputs[-1].goal is SystemGoal.CLEAN_UP


# --- 操作が 0 個の列 ---


def test_empty_operations_are_not_passed_as_sending(tmp_path):
    # 何も送らない列では、送り始めと送り終えを段階の判定に渡さない (recover で、ゲームの画面の照合の結果の
    # 続けた数を 0 に戻さないため)
    harness = make_harness(tmp_path, [output_of()], phase=RunPhase.RECOVER)
    show(harness, None)
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    assert harness.phases.calls == ["output"]


# --- 送信の失敗 ---


def test_send_failure_neutralizes_and_waits_for_the_bridge(tmp_path):
    first = output_of(press("home_menu", "A"))
    harness = make_harness(
        tmp_path,
        [first, output_of()],
        [SystemOutputError("sending operation 0 failed", send_failed(0)), sent_all(0)],
    )
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.SEND_FAILED)
    # 押下の状態をそろえるため、中立のレポートを送る
    assert harness.sender.reports[-1] == NEUTRAL_REPORT
    assert harness.events[-1].source == "neutral"
    assert harness.phases.calls == ["output", "started", "finished"]
    # 送信の失敗は、middle の失敗に数えない
    assert harness.loop.consecutive_errors == 0
    run_row = read_rows(harness.paths.system_calls)[-1]
    assert run_row["kind"] == "run"
    assert run_row["cut_reason"] == "send_failed"
    assert run_row["sent_count"] == 1
    # ブリッジが ok に戻るまで middle を呼ばない
    harness.bridge["state"] = "recovering"
    show(harness)
    assert step(harness) == SystemScreenStepResult(
        SystemScreenStepOutcome.BRIDGE_NOT_READY, SYSTEM_LOOP_POLL_S
    )
    assert len(harness.middle.inputs) == 1
    harness.bridge["state"] = "ok"
    show(harness)
    assert step(harness) == outcome(SystemScreenStepOutcome.SENT)
    # 送信に失敗した A は、届いたかもしれないものとして履歴の送った数に入る
    [record] = harness.middle.inputs[1].history
    assert record.sent_count == 1
    assert record.cut_reason is SystemCutReason.SEND_FAILED
