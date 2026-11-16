"""OrchestratorController を、偽の時計・偽の送信先・応答をテストが渡す Jev で動かす。

確かめるのは、外から呼ぶメソッド (apply・take_events・macro_names・set_started・run) と、外から見えるもの
(送信先が受け取ったレポート、orchestrator/monitor.jsonl と operations.jsonl の行、Jev が受け取った要求) だけにする。
時間は偽の時計 (FakeMonotonic) を進めて動かす。run のループの待ち (OrchestratorParts.wait) には LoopRounds を渡し、
回りの数を数えて「N 回回っても起きない」を確かめる (実時間の長い待ちに頼らない)。LoopRounds は、待つたびに PadOutput の
タイマーの処理 (start_due_taps など) も呼ぶ (タイマースレッドを起動しない PadOutput の代わり)。
"""

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytest
from switch2_remote.controller_state import (
    BUTTON_BITS,
    BUTTON_ORDER,
    DPAD_HAT,
    NEUTRAL_REPORT,
    stick_axis_to_byte,
)

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.hardware.pad_output import TAP_PRESS_S, TAP_RELEASE_GAP_S, PadOutput
from switch2_layers.orchestrator.decision_types import (
    Action,
    Decision,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    MonitorStop,
    PressAction,
    ProcedureAction,
    RepeatMode,
    WaitAction,
)
from switch2_layers.orchestrator.monitor_questions import MONITOR_INSTRUCTIONS
from switch2_layers.orchestrator.orchestrator_controller import (
    MONITOR_FRAME_SETTLE_S,
    OrchestratorController,
    OrchestratorParts,
    OrchestratorSettings,
)
from switch2_layers.orchestrator.orchestrator_events import CaseHit, OrchestratorEvents
from switch2_layers.records.operation_log import OperationLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.jev_client import JevAnswers, JevError, JevImage
from switch2_layers.surface.surface_questions import (
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)

logger = logging.getLogger(__name__)

ALLOWED = frozenset(BUTTON_ORDER) - {"HOME"}
# 実時間で条件を待つ上限 (秒)。満たされればすぐ戻る
WAIT_TIMEOUT_S = 5.0
# 条件を確かめ直す間隔と、LoopRounds が 1 回の回りで実時間で待つ上限 (秒)
POLL_S = 0.002
# 「起きない」ことを確かめるときに待つ run のループの回りの数
QUIET_ROUNDS = 10
# 既定のフレームの時刻。監視は、最後に操作を離した時刻より後のフレームでだけ問うため、どのテストの偽の時刻よりも
# 後にしておく (フレームの新しさを確かめるテストは frame_at で時刻を決める)
LATEST_FRAME_T = 100.0
SMALL_FRAME = CapturedFrame(
    t=LATEST_FRAME_T, nv12=np.full((360 * 3 // 2, 640), 128, dtype=np.uint8)
)
WIDE_FRAME = CapturedFrame(
    t=LATEST_FRAME_T, nv12=np.full((1080 * 3 // 2, 1920), 128, dtype=np.uint8)
)
NEUTRAL_STICK = (128, 128)
HAT_DIRECTIONS = {hat: directions for directions, hat in DPAD_HAT.items()}


# NV12 として読めない形 (高さが 3 の倍数でなく、幅が奇数) のフレーム。監視の要求のスレッドで画像の作成が例外を出す
BROKEN_FRAME = CapturedFrame(t=LATEST_FRAME_T, nv12=np.zeros((5, 7), dtype=np.uint8))


def frame_at(t: float) -> CapturedFrame:
    """時刻 t に撮った小さいフレーム。"""
    return CapturedFrame(t=t, nv12=SMALL_FRAME.nv12)


# --- レポートの読み取り ---


def pressed_buttons(report: bytes) -> frozenset[str]:
    """8 バイトのレポートで押されているボタン名の集合を返す。"""
    names = {name for name, (index, bit) in BUTTON_BITS.items() if report[index] & bit}
    return frozenset(names) | HAT_DIRECTIONS[report[2]]


def left_stick(report: bytes) -> tuple[int, int]:
    return (report[3], report[4])


def right_stick(report: bytes) -> tuple[int, int]:
    return (report[5], report[6])


def tilted(x: float, y: float) -> tuple[int, int]:
    """スティックを (x, y) に倒したときのレポートの 2 バイト (Y は下向きに増える)。"""
    return (stick_axis_to_byte(x), stick_axis_to_byte(-y))


@dataclass(frozen=True)
class SentReport:
    """送信先が受け取ったレポート 1 つと、受け取った偽の時刻。"""

    t: float
    report: bytes

    @property
    def buttons(self) -> frozenset[str]:
        return pressed_buttons(self.report)


class FakeMonotonic:
    """偽の time.monotonic。now を書き換えて時間を進める。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingSender:
    """受け取ったレポートを、偽の時計の時刻と一緒に記録する ReportSender。"""

    def __init__(self, monotonic: FakeMonotonic) -> None:
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._sent: list[SentReport] = []

    def set_report(self, report: bytes) -> str:
        with self._lock:
            self._sent.append(SentReport(self.monotonic.now, bytes(report)))
        return "ack"

    def reports(self) -> list[SentReport]:
        with self._lock:
            return list(self._sent)

    def last_report(self) -> bytes | None:
        reports = self.reports()
        return reports[-1].report if reports else None

    def pressed_now(self) -> frozenset[str]:
        """最後に受け取ったレポートで押されているボタン (まだ無ければ空)。"""
        last = self.last_report()
        return frozenset() if last is None else pressed_buttons(last)

    def press_starts(self, button: str) -> list[float]:
        """button が離された状態から押された状態に変わったレポートの時刻の並び。"""
        starts: list[float] = []
        pressed = False
        for sent in self.reports():
            now_pressed = button in sent.buttons
            if now_pressed and not pressed:
                starts.append(sent.t)
            pressed = now_pressed
        return starts

    def release_times(self, button: str) -> list[float]:
        """button が押された状態から離された状態に変わったレポートの時刻の並び。"""
        releases: list[float] = []
        pressed = False
        for sent in self.reports():
            now_pressed = button in sent.buttons
            if pressed and not now_pressed:
                releases.append(sent.t)
            pressed = now_pressed
        return releases

    def tilt_starts(self, stick: str) -> list[float]:
        """スティック stick (left・right) が中立から倒された状態に変わったレポートの時刻の並び。"""
        return self._stick_changes(stick, to_tilted=True)

    def neutral_times(self, stick: str) -> list[float]:
        """スティック stick (left・right) が倒された状態から中立に戻ったレポートの時刻の並び。"""
        return self._stick_changes(stick, to_tilted=False)

    def _stick_changes(self, stick: str, to_tilted: bool) -> list[float]:
        read = left_stick if stick == "left" else right_stick
        times: list[float] = []
        tilted_before = False
        for sent in self.reports():
            tilted_now = read(sent.report) != NEUTRAL_STICK
            if tilted_now != tilted_before and tilted_now == to_tilted:
                times.append(sent.t)
            tilted_before = tilted_now
        return times


# --- Jev ---


@dataclass(frozen=True)
class JevRequest:
    """偽の Jev が受け取った要求 1 つ。"""

    state: dict[str, object]
    questions: tuple[SurfaceQuestion, ...]
    images: tuple[JevImage, ...]

    @property
    def question(self) -> SurfaceQuestion:
        assert len(self.questions) == 1
        return self.questions[0]


class ScriptedJev:
    """ask のたびに要求を記録し、テストが respond で結果を渡すまで戻らない Jev の偽物。

    結果が例外なら、それを出す。close の後は、待っている ask が JevError を出す (後片付けのため)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: list[JevRequest] = []
        self._results: queue.Queue[JevAnswers | Exception] = queue.Queue()
        self._closed = threading.Event()

    def ask(self, state, questions, images) -> JevAnswers:
        with self._lock:
            self._requests.append(
                JevRequest(dict(state), tuple(questions), tuple(images))
            )
        while True:
            try:
                result = self._results.get(timeout=POLL_S)
            except queue.Empty:
                if self._closed.is_set():
                    raise JevError("closed by the test") from None
                continue
            if isinstance(result, Exception):
                raise result
            return result

    def respond(self, result: JevAnswers | Exception) -> None:
        self._results.put(result)

    def requests(self) -> list[JevRequest]:
        with self._lock:
            return list(self._requests)

    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def close(self) -> None:
        self._closed.set()


def answer(
    request: JevRequest, usage: object = None, **probabilities: float
) -> JevAnswers:
    """request の問いの答え。選択肢の確率は probabilities の値 (無い選択肢は 0)。"""
    question = request.question
    values = {
        choice.name: probabilities.get(choice.name, 0.0) for choice in question.choices
    }
    return JevAnswers(choices={question.name: values}, nouls={}, boxes={}, usage=usage)


def hit(request: JevRequest, case_index: int) -> JevAnswers:
    """条件 case_index に当たる答え。"""
    return answer(request, NONE=0.05, **{f"C{case_index}": 0.95})


def nothing(request: JevRequest) -> JevAnswers:
    """どの条件にも当たらない答え。"""
    return answer(request, NONE=1.0)


# --- run のループ ---


class LoopRounds:
    """run のループの待ち (OrchestratorParts.wait) の偽物。

    待つたびに PadOutput のタイマーの処理を呼び、実時間で最長 POLL_S 秒だけ wake を待ち、回りの数を数える。
    """

    def __init__(self, pad_output: PadOutput) -> None:
        self.pad_output = pad_output
        self.count = 0

    def wait(self, wake: threading.Event, timeout: float) -> None:
        self.pad_output.start_due_taps()
        self.pad_output.release_due_taps()
        self.pad_output.start_due_holds()
        self.pad_output.release_due_holds()
        wake.wait(min(timeout, POLL_S))
        self.count += 1


class FrameSource:
    """latest_frame の偽物。frame を書き換えて最新のフレームを変える。"""

    def __init__(self, frame: CapturedFrame | None) -> None:
        self.frame = frame

    def latest(self) -> CapturedFrame | None:
        return self.frame


@dataclass
class Harness:
    controller: OrchestratorController
    sender: RecordingSender
    jev: ScriptedJev
    monotonic: FakeMonotonic
    rounds: LoopRounds
    frames: FrameSource
    paths: RunPaths
    pad_output: PadOutput
    stop: threading.Event = field(default_factory=threading.Event)
    run_errors: list[Exception] = field(default_factory=list)
    thread: threading.Thread | None = None

    def start_run(self) -> None:
        self.thread = threading.Thread(
            target=self._run, name="test-orchestrator", daemon=True
        )
        self.thread.start()

    def _run(self) -> None:
        try:
            self.controller.run(self.stop)
        except Exception as error:
            # テストのスレッドの check_run で出し直すため、run のスレッドでは記録だけする
            logger.debug("controller run failed", exc_info=True)
            self.run_errors.append(error)

    def check_run(self) -> None:
        """run のスレッドが例外で終わっていたら、その例外をテストのスレッドで出す。"""
        if self.run_errors:
            raise self.run_errors[0]

    def wait_until(self, condition: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        while True:
            self.check_run()
            if condition():
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting until {what}")
            time.sleep(POLL_S)

    def wait_rounds(self, rounds: int = QUIET_ROUNDS) -> None:
        """run のループが rounds 回回るまで待つ。"""
        target = self.rounds.count + rounds
        self.wait_until(lambda: self.rounds.count >= target, f"{rounds} loop rounds")

    def set_time(self, t: float) -> None:
        self.monotonic.now = t

    def drive(self, until: float, step: float = 0.05) -> None:
        """偽の時計を step 秒ずつ until まで進め、進めるたびに run のループを数回回す。"""
        t = self.monotonic.now
        while t < until - 1e-9:
            t = min(t + step, until)
            self.set_time(round(t, 6))
            self.wait_rounds(5)

    def wait_request(self, count: int) -> JevRequest:
        """Jev が count 個目の要求を受け取るまで待ち、それを返す。"""
        self.wait_until(
            lambda: self.jev.request_count() >= count, f"jev request {count}"
        )
        return self.jev.requests()[count - 1]

    def wait_pressed(self, buttons: set[str]) -> None:
        self.wait_until(
            lambda: self.sender.pressed_now() == frozenset(buttons),
            f"{buttons} pressed",
        )

    def wait_neutral(self) -> None:
        self.wait_until(
            lambda: self.sender.last_report() == NEUTRAL_REPORT, "neutral report"
        )

    def monitor_rows(self) -> list[dict]:
        return read_rows(self.paths.orchestrator_monitor_log)

    def operation_rows(self) -> list[dict]:
        return read_rows(self.paths.operations_log)

    def shutdown(self) -> None:
        self.stop.set()
        self.jev.close()
        if self.thread is not None:
            self.thread.join(WAIT_TIMEOUT_S)


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def rows_pressing(rows: list[dict], button: str) -> list[dict]:
    """operations.jsonl の行のうち、button を押し続けるか短押しする行。"""
    return [row for row in rows if button in row["held"] or button in row["taps"]]


@pytest.fixture
def cleanups():
    """テストの最後に逆順で呼ぶ後片付けの関数の並び (テストの本体で部品を作るため、fixture では作らない)。"""
    functions: list[Callable[[], None]] = []
    yield functions
    for function in reversed(functions):
        function()


def make_harness(
    tmp_path: Path,
    cleanups: list[Callable[[], None]],
    *,
    allowed: frozenset[str] = ALLOWED,
    threshold: float = 0.5,
    max_errors: int = 5,
    frame: CapturedFrame | None = SMALL_FRAME,
    started: bool = True,
) -> Harness:
    """OrchestratorController を作って run のスレッドを起動し、started なら set_started を呼ぶ。

    PadOutput の on_operation には、run と同じく operations.jsonl の OperationLog.write をつなぐ。
    """
    monotonic = FakeMonotonic()
    sender = RecordingSender(monotonic)
    clock = RunClock(monotonic, origin=0.0)
    paths = RunPaths(tmp_path)
    pad_output = PadOutput(
        sender,
        allowed,
        clock=monotonic,
        on_operation=OperationLog(paths.operations_log, clock).write,
        start_timer=False,
    )
    cleanups.append(pad_output.close)
    jev = ScriptedJev()
    cleanups.append(jev.close)
    frames = FrameSource(frame)
    rounds = LoopRounds(pad_output)
    controller = OrchestratorController(
        OrchestratorParts(
            pad_output=pad_output,
            jev=jev,
            latest_frame=frames.latest,
            clock=clock,
            paths=paths,
            wait=rounds.wait,
        ),
        OrchestratorSettings(
            monitor_threshold=threshold, max_monitor_errors=max_errors
        ),
    )
    harness = Harness(
        controller=controller,
        sender=sender,
        jev=jev,
        monotonic=monotonic,
        rounds=rounds,
        frames=frames,
        paths=paths,
        pad_output=pad_output,
    )
    cleanups.append(harness.shutdown)
    harness.start_run()
    if started:
        controller.set_started()
    return harness


# --- 操作と指示を作る ---


def tap(*buttons: str) -> PressAction:
    return PressAction(buttons=tuple(buttons))


def hold(
    seconds: float, *buttons: str, left: tuple[float, float] | None = None
) -> PressAction:
    sticks = {} if left is None else {"left": left}
    return PressAction(buttons=tuple(buttons), sticks=sticks, seconds=seconds)


def act(*actions: Action) -> Decision:
    return Decision(actions=tuple(actions))


def case(condition: str, *actions: Action, repeat: RepeatMode = "every") -> MonitorCase:
    return MonitorCase(condition=condition, actions=tuple(actions), repeat=repeat)


def monitor(*cases: MonitorCase, name: str = "dialog") -> Decision:
    return Decision(monitor=MonitorCreate(name=name, cases=tuple(cases)))


def tap_repeat(button: str, count: int, interval_seconds: float) -> ProcedureAction:
    return ProcedureAction(
        name="tap_repeat",
        args={"button": button, "count": count, "interval_seconds": interval_seconds},
    )


# --- 操作の実行: press・wait ---


def test_short_press_keeps_the_button_down_for_the_tap_press_time(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap("A")))
    h.wait_pressed({"A"})
    assert h.sender.press_starts("A") == [0.0]
    h.set_time(TAP_PRESS_S - 0.01)
    h.wait_rounds()
    assert h.sender.pressed_now() == {"A"}
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_neutral()


def test_press_with_seconds_holds_buttons_and_stick_then_returns_to_neutral(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "B", left=(0.0, 1.0))))
    h.wait_until(
        lambda: (
            h.sender.pressed_now() == {"B"}
            and left_stick(h.sender.last_report()) == tilted(0.0, 1.0)
        ),
        "B and the left stick are down",
    )
    h.set_time(0.99)
    h.wait_rounds()
    last = h.sender.last_report()
    assert pressed_buttons(last) == {"B"}
    assert left_stick(last) == tilted(0.0, 1.0)
    h.set_time(1.001)
    h.wait_neutral()


def test_short_press_of_only_a_stick_tilts_it_for_the_tap_press_time(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(PressAction(sticks={"right": (1.0, 0.0)})))
    h.wait_until(
        lambda: (
            h.sender.last_report() is not None
            and right_stick(h.sender.last_report()) == tilted(1.0, 0.0)
        ),
        "the right stick is tilted",
    )
    assert h.sender.pressed_now() == frozenset()
    h.set_time(TAP_PRESS_S - 0.01)
    h.wait_rounds()
    assert right_stick(h.sender.last_report()) == tilted(1.0, 0.0)
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_neutral()


def test_wait_presses_nothing_for_its_seconds_after_the_previous_release(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap("A"), WaitAction(seconds=0.5), tap("B")))
    h.wait_pressed({"A"})
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_until(lambda: h.sender.release_times("A"), "A is released")
    released_at = h.sender.release_times("A")[0]
    h.set_time(released_at + 0.49)
    h.wait_rounds()
    assert h.sender.press_starts("B") == []
    h.set_time(released_at + 0.501)
    h.wait_pressed({"B"})
    assert h.sender.press_starts("B")[0] >= released_at + 0.5


def test_next_action_starts_after_the_previous_one_is_released(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(0.5, "A"), tap("B")))
    h.wait_pressed({"A"})
    h.set_time(0.4)
    h.wait_rounds()
    assert h.sender.press_starts("B") == []
    h.set_time(0.501)
    h.wait_pressed({"B"})
    reports = h.sender.reports()
    assert not any({"A", "B"} <= sent.buttons for sent in reports)
    first_b = next(index for index, sent in enumerate(reports) if "B" in sent.buttons)
    assert first_b >= 1
    assert "A" not in reports[first_b - 1].buttons


@pytest.mark.parametrize(
    "first, press_seconds",
    [(tap("A"), TAP_PRESS_S), (hold(0.3, "A"), 0.3)],
    ids=["short_press", "press_with_seconds"],
)
def test_pressing_the_same_button_again_waits_the_release_gap(
    tmp_path, cleanups, first, press_seconds
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(first, first))
    h.wait_pressed({"A"})
    h.set_time(press_seconds + 0.001)
    h.wait_until(lambda: h.sender.release_times("A"), "A is released")
    released_at = h.sender.release_times("A")[0]
    h.set_time(released_at + TAP_RELEASE_GAP_S - 0.01)
    h.wait_rounds()
    assert len(h.sender.press_starts("A")) == 1
    h.set_time(released_at + TAP_RELEASE_GAP_S + 0.001)
    h.wait_until(lambda: len(h.sender.press_starts("A")) == 2, "A is pressed again")


def assert_next_press_waits_the_release_gap(h: Harness, released_at: float) -> None:
    """A を released_at に離した後、TAP_RELEASE_GAP_S 秒たつまで A を押し直さず、たったら押し直すことを確かめる。"""
    h.set_time(released_at + TAP_RELEASE_GAP_S - 0.01)
    h.wait_rounds()
    assert len(h.sender.press_starts("A")) == 1
    h.set_time(released_at + TAP_RELEASE_GAP_S + 0.001)
    h.wait_until(lambda: len(h.sender.press_starts("A")) == 2, "A is pressed again")


@pytest.mark.parametrize(
    "actions",
    [
        (tap_repeat("A", 2, 0.0),),
        (tap("A"), WaitAction(seconds=0.0), tap("A")),
        (tap("A"), WaitAction(seconds=0.05), tap("A")),
    ],
    ids=["tap_repeat_with_zero_interval", "zero_wait", "wait_shorter_than_the_gap"],
)
def test_short_or_zero_wait_between_presses_still_keeps_the_release_gap(
    tmp_path, cleanups, actions
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(*actions))
    h.wait_pressed({"A"})
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_until(lambda: h.sender.release_times("A"), "A is released")
    assert_next_press_waits_the_release_gap(h, h.sender.release_times("A")[0])


def test_actions_starting_with_the_same_button_after_a_cancel_wait_the_release_gap(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "A")))
    h.wait_pressed({"A"})
    # 新しい actions で前の残りを捨てて中立に戻し、同じボタンから始める
    h.controller.apply(act(tap("A")))
    h.wait_until(lambda: h.sender.release_times("A"), "A is released by the cancel")
    assert_next_press_waits_the_release_gap(h, h.sender.release_times("A")[0])


def test_main_actions_after_monitor_actions_on_the_same_button_wait_the_release_gap(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", hold(1.0, "A"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"A"})
    h.controller.apply(act(tap("A")))
    h.wait_until(lambda: h.sender.release_times("A"), "A is released by the cancel")
    assert_next_press_waits_the_release_gap(h, h.sender.release_times("A")[0])


def test_no_extra_wait_when_the_wait_is_longer_than_the_release_gap(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap("A"), WaitAction(seconds=0.3), tap("A")))
    h.wait_pressed({"A"})
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_until(lambda: h.sender.release_times("A"), "A is released")
    released_at = h.sender.release_times("A")[0]
    h.set_time(released_at + 0.29)
    h.wait_rounds()
    assert len(h.sender.press_starts("A")) == 1
    # 待ちが TAP_RELEASE_GAP_S より長ければ、待ちを終えたときにすぐ押す
    h.set_time(released_at + 0.301)
    h.wait_until(lambda: len(h.sender.press_starts("A")) == 2, "A is pressed again")


def test_tilting_the_same_stick_again_waits_the_release_gap(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    stick_only = PressAction(sticks={"left": (0.0, 1.0)})
    h.controller.apply(act(stick_only, WaitAction(seconds=0.0), stick_only))
    h.wait_until(lambda: h.sender.tilt_starts("left") == [0.0], "the stick is tilted")
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_until(lambda: h.sender.neutral_times("left"), "the stick is back")
    returned_at = h.sender.neutral_times("left")[0]
    h.set_time(returned_at + TAP_RELEASE_GAP_S - 0.01)
    h.wait_rounds()
    assert len(h.sender.tilt_starts("left")) == 1
    h.set_time(returned_at + TAP_RELEASE_GAP_S + 0.001)
    h.wait_until(
        lambda: len(h.sender.tilt_starts("left")) == 2, "the stick is tilted again"
    )
    assert left_stick(h.sender.last_report()) == tilted(0.0, 1.0)


def test_main_loop_operations_are_recorded_once_with_the_orchestrator_source(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap("A"), hold(0.2, "B")))
    h.drive(1.0)
    rows = h.operation_rows()
    # 押す操作 1 つにつき、orchestrator の行が 1 行だけある (apply の行として重ねて書かない)
    assert [row["source"] for row in rows_pressing(rows, "A")] == ["orchestrator"]
    assert [row["source"] for row in rows_pressing(rows, "B")] == ["orchestrator"]
    assert "apply" not in {row["source"] for row in rows}


# --- 操作の実行: procedure・macro ---


def test_tap_repeat_presses_the_button_count_times_with_intervals(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap_repeat("X", 3, 0.2)))
    events = h.controller.take_events()
    assert events.running is True
    assert events.remaining_action_count == 6
    h.drive(2.0)
    starts = h.sender.press_starts("X")
    releases = h.sender.release_times("X")
    assert len(starts) == 3
    assert len(releases) == 3
    for previous_release, next_start in zip(releases, starts[1:], strict=False):
        assert next_start >= previous_release + 0.2 - 1e-9
    events = h.controller.take_events()
    assert events.running is False
    assert events.remaining_action_count == 0


def test_walk_holds_the_direction_for_seconds_count_times(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        act(
            ProcedureAction(
                name="walk",
                args={
                    "direction": "LEFT",
                    "count": 2,
                    "seconds": 0.3,
                    "interval_seconds": 0.1,
                },
            )
        )
    )
    assert h.controller.take_events().remaining_action_count == 4
    h.drive(2.0)
    starts = h.sender.press_starts("LEFT")
    releases = h.sender.release_times("LEFT")
    assert len(starts) == 2
    for start, release in zip(starts, releases, strict=True):
        assert release - start >= 0.3 - 1e-9
    assert starts[1] >= releases[0] + 0.1 - 1e-9
    assert h.sender.pressed_now() == frozenset()


def test_macro_set_in_the_same_decision_runs(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        Decision(
            actions=(MacroAction(name="next"),),
            macros_set={"next": (tap("A"), WaitAction(seconds=0.2), tap("B"))},
        )
    )
    assert h.controller.take_events().remaining_action_count == 3
    h.drive(1.5)
    assert len(h.sender.press_starts("A")) == 1
    assert len(h.sender.press_starts("B")) == 1
    assert h.sender.press_starts("A")[0] < h.sender.press_starts("B")[0]


def test_macro_registered_earlier_expands_its_procedures_when_called(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        Decision(macros_set={"mash": (tap_repeat("A", 2, 0.1), tap("B"))})
    )
    h.wait_rounds()
    assert h.sender.reports() == []
    assert h.controller.take_events().remaining_action_count == 0
    h.controller.apply(act(MacroAction(name="mash"), tap("Y")))
    assert h.controller.take_events().remaining_action_count == 6
    h.drive(2.0)
    assert len(h.sender.press_starts("A")) == 2
    assert len(h.sender.press_starts("B")) == 1
    assert len(h.sender.press_starts("Y")) == 1


def test_buttons_that_cannot_be_pressed_are_removed_and_reported(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, allowed=ALLOWED - {"MINUS"})
    h.controller.apply(act(tap("HOME", "A"), tap("MINUS"), tap("HOME")))
    h.wait_pressed({"A"})
    h.drive(1.5)
    assert not any(
        "HOME" in sent.buttons or "MINUS" in sent.buttons for sent in h.sender.reports()
    )
    assert h.controller.take_events().blocked_buttons == ("MINUS", "HOME")
    assert h.controller.take_events().blocked_buttons == ()


# --- 新しい actions・空の actions ---


def test_new_actions_discard_the_rest_and_return_to_neutral_first(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "A", left=(0.0, 1.0)), tap("X")))
    h.wait_pressed({"A"})
    h.controller.apply(act(tap("B")))
    h.wait_pressed({"B"})
    reports = h.sender.reports()
    first_b = next(index for index, sent in enumerate(reports) if "B" in sent.buttons)
    assert "A" not in reports[first_b - 1].buttons
    assert left_stick(reports[first_b].report) == NEUTRAL_STICK
    assert not any({"A", "B"} <= sent.buttons for sent in reports)
    events = h.controller.take_events()
    assert events.discarded_action_count == 2
    assert events.remaining_action_count == 1
    h.drive(2.0)
    assert h.sender.press_starts("X") == []


def test_discarded_counts_add_up_until_taken(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "A"), tap("X")))
    h.wait_pressed({"A"})
    h.controller.apply(act(hold(1.0, "B"), tap("Y"), tap("Z")))
    h.controller.apply(act(tap("X")))
    events = h.controller.take_events()
    assert events.discarded_action_count == 2 + 3
    assert events.remaining_action_count == 1
    assert h.controller.take_events().discarded_action_count == 0


def test_discarded_count_is_the_expanded_number(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(tap_repeat("A", 3, 0.5)))
    h.wait_pressed({"A"})
    h.controller.apply(act(tap("B")))
    assert h.controller.take_events().discarded_action_count == 6


def test_empty_actions_keep_the_running_actions(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "A")))
    h.wait_pressed({"A"})
    h.controller.apply(Decision())
    h.controller.apply(Decision(macros_set={"m": (tap("B"),)}))
    h.wait_rounds()
    assert h.sender.pressed_now() == {"A"}
    events = h.controller.take_events()
    assert events.discarded_action_count == 0
    assert events.running is True
    assert events.remaining_action_count == 1


def test_running_and_remaining_follow_the_progress(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(0.5, "A"), WaitAction(seconds=0.5)))
    events = h.controller.take_events()
    assert (events.running, events.remaining_action_count) == (True, 2)
    h.wait_pressed({"A"})
    h.set_time(0.501)
    h.wait_until(lambda: h.sender.release_times("A"), "A is released")
    h.wait_rounds()
    events = h.controller.take_events()
    assert (events.running, events.remaining_action_count) == (True, 1)
    h.set_time(1.002)
    h.wait_rounds()
    events = h.controller.take_events()
    assert (events.running, events.remaining_action_count) == (False, 0)


def test_nothing_runs_before_set_started(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, started=False)
    h.controller.apply(
        Decision(
            actions=(tap("A"),),
            monitor=MonitorCreate("dialog", (case("c0", tap("B")),)),
        )
    )
    h.wait_rounds()
    assert h.sender.reports() == []
    assert h.jev.request_count() == 0
    events = h.controller.take_events()
    assert (events.running, events.remaining_action_count) == (True, 1)
    h.controller.set_started()
    h.wait_pressed({"A"})
    h.set_time(TAP_PRESS_S + 0.001)
    h.wait_request(1)


def test_actions_applied_before_set_started_can_still_be_replaced(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, started=False)
    h.controller.apply(act(tap("A"), tap("B")))
    h.controller.apply(act(tap("X")))
    assert h.controller.take_events().discarded_action_count == 2
    h.controller.set_started()
    h.wait_pressed({"X"})
    h.drive(1.0)
    assert h.sender.press_starts("A") == []
    assert h.sender.press_starts("B") == []


# --- マクロ ---


def test_macros_are_set_replaced_and_deleted(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(Decision(macros_set={"b": (tap("B"),), "a": (tap("A"),)}))
    assert h.controller.macro_names() == {"a", "b"}
    events = h.controller.take_events()
    assert events.macros == {"a": (tap("A"),), "b": (tap("B"),)}
    assert list(events.macros) == ["a", "b"]
    h.controller.apply(
        Decision(macros_set={"a": (tap("X"),)}, macros_delete=("b", "missing"))
    )
    assert h.controller.macro_names() == {"a"}
    assert h.controller.take_events().macros == {"a": (tap("X"),)}
    h.controller.apply(Decision(macros_set={"c": (tap("Y"),)}, macros_delete=("c",)))
    assert h.controller.macro_names() == {"a"}


def test_macros_stay_across_decisions(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(Decision(macros_set={"next": (tap("A"),)}))
    for button in ("X", "Y", "B"):
        h.controller.apply(act(tap(button)))
        h.controller.apply(Decision(monitor=MonitorStop()))
    assert h.controller.macro_names() == {"next"}
    assert h.controller.take_events().macros == {"next": (tap("A"),)}


@pytest.mark.parametrize(
    "decision",
    [
        Decision(
            actions=(MacroAction(name="missing"),),
            macros_set={"new": (tap("B"),)},
            monitor=MonitorCreate("dialog", (case("c0", tap("A")),)),
        ),
        Decision(
            macros_set={"new": (tap("B"),)},
            monitor=MonitorCreate("dialog", (case("c0", MacroAction(name="missing")),)),
        ),
        Decision(actions=(MacroAction(name="known"),), macros_delete=("known",)),
    ],
    ids=["in_actions", "in_monitor_case", "deleted_in_the_same_decision"],
)
def test_calling_an_unknown_macro_changes_nothing(tmp_path, cleanups, decision):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(Decision(macros_set={"known": (tap("X"),)}))
    with pytest.raises(ValueError):
        h.controller.apply(decision)
    assert h.controller.macro_names() == {"known"}
    events = h.controller.take_events()
    assert events.monitor is None
    assert events.remaining_action_count == 0
    h.wait_rounds()
    assert h.sender.reports() == []
    assert h.jev.request_count() == 0


def test_monitor_actions_use_the_macros_at_creation(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        Decision(
            macros_set={"m": (tap("A"),)},
            monitor=MonitorCreate("dialog", (case("c0", MacroAction(name="m")),)),
        )
    )
    h.controller.apply(Decision(macros_set={"m": (tap("B"),)}))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"A"})
    assert h.sender.press_starts("B") == []
    assert h.controller.take_events().case_hits == (
        CaseHit("dialog", 0, "c0", 1, (MacroAction(name="m"),)),
    )


# --- 監視: 要求 ---


def test_no_request_without_a_monitor(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.wait_rounds()
    assert h.jev.request_count() == 0


def test_no_request_before_set_started(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, started=False)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.controller.set_started()
    h.wait_request(1)


def test_request_is_one_choice_question_on_the_latest_frame(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, frame=WIDE_FRAME)
    first = "  画面の下に会話の枠があり、右下に▼が出ている  "
    second = "はい・いいえの選択肢が出ている"
    h.controller.apply(
        monitor(case(first, tap("A")), case(second, tap("B"), repeat="once"))
    )
    request = h.wait_request(1)
    question = request.question
    assert question.question_type is QuestionType.CHOICE
    assert question.kind is QuestionKind.MONITOR
    assert [choice.name for choice in question.choices] == ["NONE", "C0", "C1"]
    assert [choice.description for choice in question.choices[1:]] == [first, second]
    assert len(request.images) == 1
    reference = request.images[0].reference
    assert request.state == {"instructions": MONITOR_INSTRUCTIONS, "frame": reference}
    decoded = cv2.imdecode(
        np.frombuffer(request.images[0].jpeg, np.uint8), cv2.IMREAD_COLOR
    )
    assert decoded.shape[:2] == (720, 1280)


def test_no_request_until_a_frame_arrives(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, frame=None)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.frames.frame = SMALL_FRAME
    h.wait_request(1)


def test_next_request_is_sent_as_soon_as_the_answer_returns(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.jev.respond(nothing(h.wait_request(1)))
    h.jev.respond(nothing(h.wait_request(2)))
    h.wait_request(3)
    h.wait_rounds()
    assert h.jev.request_count() == 3
    assert h.sender.reports() == []


def test_no_request_while_main_actions_run(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        Decision(
            actions=(hold(1.0, "A"),),
            monitor=MonitorCreate("dialog", (case("c0", tap("B")),)),
        )
    )
    h.wait_pressed({"A"})
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.set_time(1.001)
    h.wait_request(1)


def test_no_request_while_monitor_actions_run(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", hold(1.0, "B"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.wait_rounds()
    assert h.jev.request_count() == 1
    h.set_time(1.001)
    h.wait_request(2)


# --- 監視: 問いに使うフレームの新しさ ---


def test_monitor_frame_settle_time_is_0_3_seconds():
    assert MONITOR_FRAME_SETTLE_S == 0.3


def test_no_request_until_a_frame_after_the_settle_time_of_monitor_actions(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups, frame=frame_at(9.0))
    h.set_time(10.0)
    h.controller.apply(monitor(case("c0", tap("B"))))
    # まだ何も離していないので、前に撮ったフレームでも問う
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.set_time(10.2)
    h.wait_neutral()
    assert h.sender.release_times("B") == [10.2]
    h.wait_rounds()
    assert h.jev.request_count() == 1
    # 離した時刻より前のフレームと、離してから MONITOR_FRAME_SETTLE_S の内側のフレームでは問わない
    for frame_t in (10.1, 10.2 + 0.05, 10.2 + MONITOR_FRAME_SETTLE_S - 0.05):
        h.frames.frame = frame_at(frame_t)
        h.wait_rounds()
        assert h.jev.request_count() == 1
    h.frames.frame = frame_at(10.2 + MONITOR_FRAME_SETTLE_S + 0.05)
    request = h.wait_request(2)
    assert len(request.images) == 1


def test_no_request_until_a_frame_after_the_settle_time_of_main_actions(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups, frame=frame_at(9.0))
    h.set_time(10.0)
    h.controller.apply(
        Decision(
            actions=(tap("A"),),
            monitor=MonitorCreate("dialog", (case("c0", tap("B")),)),
        )
    )
    h.wait_pressed({"A"})
    h.set_time(10.2)
    h.wait_neutral()
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.frames.frame = frame_at(10.2 + MONITOR_FRAME_SETTLE_S - 0.05)
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.frames.frame = frame_at(10.2 + MONITOR_FRAME_SETTLE_S + 0.05)
    h.wait_request(1)


def test_frame_must_be_after_the_settle_time_of_a_cancel_too(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, frame=frame_at(9.0))
    h.set_time(10.0)
    h.controller.apply(
        Decision(
            actions=(hold(5.0, "A"),),
            monitor=MonitorCreate("dialog", (case("c0", tap("B")),)),
        )
    )
    h.wait_pressed({"A"})
    h.set_time(10.5)
    # 主ループの actions を空でない列で置き換えて中立に戻し、その列も終える
    h.controller.apply(act(WaitAction(seconds=0.1)))
    h.wait_neutral()
    h.set_time(10.7)
    h.wait_rounds()
    assert h.controller.take_events().running is False
    assert h.jev.request_count() == 0
    h.frames.frame = frame_at(10.5 + MONITOR_FRAME_SETTLE_S - 0.05)
    h.wait_rounds()
    assert h.jev.request_count() == 0
    h.frames.frame = frame_at(10.5 + MONITOR_FRAME_SETTLE_S + 0.05)
    h.wait_request(1)


# --- 監視: 当たりの判定 ---


@pytest.mark.parametrize(
    "probabilities, threshold, expected",
    [
        ({"NONE": 0.3, "C0": 0.5, "C1": 0.2}, 0.5, None),
        ({"NONE": 0.29, "C0": 0.51, "C1": 0.2}, 0.5, 0),
        ({"NONE": 0.1, "C0": 0.1, "C1": 0.8}, 0.5, 1),
        ({"NONE": 0.6, "C0": 0.55, "C1": 0.0}, 0.5, None),
        ({"NONE": 0.1, "C0": 0.45, "C1": 0.45}, 0.4, 0),
        ({"NONE": 0.45, "C0": 0.45, "C1": 0.1}, 0.4, None),
        ({"NONE": 0.0, "C0": 0.69, "C1": 0.31}, 0.7, None),
        ({"NONE": 0.0, "C0": 0.71, "C1": 0.29}, 0.7, 0),
    ],
    ids=[
        "exactly_the_threshold",
        "just_over_the_threshold",
        "second_case",
        "none_is_highest",
        "tie_between_cases_takes_the_first",
        "tie_with_none_takes_none",
        "under_a_higher_threshold",
        "over_a_higher_threshold",
    ],
)
def test_hit_is_the_highest_case_over_the_threshold(
    tmp_path, cleanups, probabilities, threshold, expected
):
    h = make_harness(tmp_path, cleanups, threshold=threshold)
    h.controller.apply(monitor(case("c0", tap("A")), case("c1", tap("B"))))
    h.jev.respond(answer(h.wait_request(1), **probabilities))
    if expected is None:
        h.wait_request(2)
        assert h.sender.reports() == []
        assert h.controller.take_events().case_hits == ()
        assert h.monitor_rows()[0]["hit"] is None
    else:
        button = ("A", "B")[expected]
        h.wait_pressed({button})
        h.wait_until(lambda: len(h.monitor_rows()) == 1, "the answer is recorded")
        assert h.monitor_rows()[0]["hit"] == f"C{expected}"
        assert h.controller.take_events().case_hits == (
            CaseHit("dialog", expected, f"c{expected}", 1, (tap(button),)),
        )


def test_every_case_runs_each_time_it_hits(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    created = MonitorCreate("dialog", (case("c0", tap("A")),))
    h.controller.apply(Decision(monitor=created))
    for count in (1, 2):
        h.jev.respond(hit(h.wait_request(count), 0))
        # 2 回目は、前に A を離してから TAP_RELEASE_GAP_S 秒あけて押す
        h.set_time(h.monotonic.now + TAP_RELEASE_GAP_S + 0.01)
        h.wait_until(
            lambda expected=count: len(h.sender.press_starts("A")) == expected,
            f"A pressed {count}",
        )
        h.set_time(h.monotonic.now + 0.3)
        h.wait_request(count + 1)
    events = h.controller.take_events()
    assert events.case_hits == (CaseHit("dialog", 0, "c0", 2, (tap("A"),)),)
    assert events.monitor_ends == ()
    assert events.monitor == created


def test_case_hits_are_counted_per_case_in_the_order_of_the_first_hit(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A")), case("c1", tap("B"))))
    for count, case_index in enumerate((1, 0, 1), start=1):
        h.jev.respond(hit(h.wait_request(count), case_index))
        h.wait_until(lambda: h.sender.pressed_now(), "a case button is pressed")
        h.set_time(h.monotonic.now + 0.3)
        h.wait_request(count + 1)
    assert h.controller.take_events().case_hits == (
        CaseHit("dialog", 1, "c1", 2, (tap("B"),)),
        CaseHit("dialog", 0, "c0", 1, (tap("A"),)),
    )
    assert h.controller.take_events().case_hits == ()


def test_case_hits_of_a_recreated_monitor_with_the_same_name_are_separate(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("old condition", tap("A"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"A"})
    h.set_time(0.3)
    old_request = h.wait_request(2)
    # 取り出す前に、同じ名前で条件の違う監視に作り直す
    h.controller.apply(monitor(case("new condition", tap("B"))))
    h.jev.respond(nothing(old_request))
    h.jev.respond(hit(h.wait_request(3), 0))
    h.wait_pressed({"B"})
    assert h.controller.take_events().case_hits == (
        CaseHit("dialog", 0, "old condition", 1, (tap("A"),)),
        CaseHit("dialog", 0, "new condition", 1, (tap("B"),)),
    )


def test_once_case_completes_the_monitor_after_its_actions(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        monitor(case("c0", tap("A")), case("c1", tap("B"), repeat="once"))
    )
    h.jev.respond(hit(h.wait_request(1), 1))
    h.wait_pressed({"B"})
    h.set_time(0.3)
    h.wait_neutral()
    h.wait_rounds()
    events = h.controller.take_events()
    assert events.monitor is None
    assert events.case_hits == (CaseHit("dialog", 1, "c1", 1, (tap("B"),)),)
    [end] = events.monitor_ends
    assert (end.name, end.reason) == ("dialog", "completed")
    assert "c1" in end.detail
    h.wait_rounds()
    assert h.jev.request_count() == 1
    assert h.controller.take_events().monitor_ends == ()


def test_once_case_with_empty_actions_completes_at_once(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", repeat="once")))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_until(lambda: h.controller.take_events().monitor is None, "the monitor ends")
    h.wait_rounds()
    assert h.jev.request_count() == 1
    assert h.sender.reports() == []


def test_once_case_whose_macro_expands_to_nothing_completes_at_once(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        Decision(
            macros_set={"noop": ()},
            monitor=MonitorCreate(
                "dialog", (case("c0", MacroAction(name="noop"), repeat="once"),)
            ),
        )
    )
    h.jev.respond(hit(h.wait_request(1), 0))
    collected: list[OrchestratorEvents] = []

    def monitor_ended() -> bool:
        collected.append(h.controller.take_events())
        return collected[-1].monitor is None

    h.wait_until(monitor_ended, "the monitor ends")
    ends = [end for events in collected for end in events.monitor_ends]
    assert [(end.name, end.reason) for end in ends] == [("dialog", "completed")]
    assert [case_hit for events in collected for case_hit in events.case_hits] == [
        CaseHit("dialog", 0, "c0", 1, (MacroAction(name="noop"),))
    ]
    h.wait_rounds()
    assert h.jev.request_count() == 1
    assert h.sender.reports() == []


def test_once_case_interrupted_by_main_actions_does_not_complete(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    created = MonitorCreate("dialog", (case("c0", hold(1.0, "B"), repeat="once"),))
    h.controller.apply(Decision(monitor=created))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.controller.apply(act(tap("A")))
    h.wait_pressed({"A"})
    h.set_time(0.3)
    h.wait_request(2)
    events = h.controller.take_events()
    assert events.monitor_ends == ()
    assert events.monitor == created


def test_main_actions_stop_monitor_actions_and_return_to_neutral_first(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(
        monitor(
            case(
                "c0",
                PressAction(buttons=("B",), sticks={"left": (1.0, 0.0)}, seconds=1.0),
            )
        )
    )
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.controller.apply(act(tap("A")))
    h.wait_pressed({"A"})
    reports = h.sender.reports()
    first_a = next(index for index, sent in enumerate(reports) if "A" in sent.buttons)
    assert "B" not in reports[first_a - 1].buttons
    assert "B" not in reports[first_a].buttons
    assert left_stick(reports[first_a].report) == NEUTRAL_STICK


@pytest.mark.parametrize(
    "change",
    [
        Decision(monitor=MonitorStop()),
        Decision(monitor=MonitorCreate("other", (case("o0", tap("X")),))),
    ],
    ids=["stop", "create"],
)
def test_stopping_or_replacing_the_monitor_stops_its_actions(
    tmp_path, cleanups, change
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", hold(1.0, "B"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.controller.apply(change)
    h.wait_neutral()


def test_empty_actions_do_not_stop_monitor_actions(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", hold(1.0, "B"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.controller.apply(Decision())
    h.wait_rounds()
    assert h.sender.pressed_now() == {"B"}


def test_buttons_removed_from_monitor_actions_are_reported(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("HOME", "A"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"A"})
    assert "HOME" not in h.sender.pressed_now()
    assert h.controller.take_events().blocked_buttons == ("HOME",)


# --- 監視: 応答を待つ間の主ループの操作 ---


def test_main_actions_start_while_a_request_is_in_flight(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("B"))))
    request = h.wait_request(1)
    h.controller.apply(act(hold(1.0, "A")))
    h.wait_pressed({"A"})
    h.jev.respond(hit(request, 0))
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the answer is recorded")
    h.wait_rounds()
    assert h.sender.press_starts("B") == []
    assert h.jev.request_count() == 1
    [row] = h.monitor_rows()
    assert row["hit"] is None
    assert row["probabilities"]["C0"] == 0.95
    assert h.controller.take_events().case_hits == ()
    h.set_time(1.001)
    h.wait_request(2)


def test_answer_to_a_request_sent_before_main_actions_is_not_used(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("B"))))
    request = h.wait_request(1)
    h.controller.apply(act(tap("A")))
    h.wait_pressed({"A"})
    h.set_time(0.3)
    h.wait_neutral()
    h.wait_rounds()
    # 前の要求が返るまで、次の要求は送らない
    assert h.jev.request_count() == 1
    h.jev.respond(hit(request, 0))
    h.wait_request(2)
    assert h.sender.press_starts("B") == []


def test_create_replaces_the_monitor_after_the_old_request_returns(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("old condition", tap("A")), name="a"))
    old_request = h.wait_request(1)
    new = MonitorCreate("b", (case("new 0", tap("B")), case("new 1", tap("X"))))
    h.controller.apply(Decision(monitor=new))
    assert h.controller.take_events().monitor == new
    h.wait_rounds()
    assert h.jev.request_count() == 1
    h.jev.respond(hit(old_request, 0))
    new_request = h.wait_request(2)
    assert [choice.description for choice in new_request.question.choices[1:]] == [
        "new 0",
        "new 1",
    ]
    assert h.sender.reports() == []
    [row] = h.monitor_rows()
    assert (row["monitor"], row["hit"]) == ("a", None)


def test_answer_for_an_identical_monitor_created_again_is_not_used(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    same = monitor(case("c0", tap("A")))
    h.controller.apply(same)
    old_request = h.wait_request(1)
    # 同じ名前・同じ条件の監視を作り直す (世代が変わる)
    h.controller.apply(same)
    h.wait_rounds()
    assert h.jev.request_count() == 1
    h.jev.respond(hit(old_request, 0))
    h.wait_request(2)
    assert h.sender.press_starts("A") == []
    assert h.controller.take_events().case_hits == ()
    [row] = h.monitor_rows()
    assert row["hit"] is None


def test_failure_for_an_identical_monitor_created_again_is_not_counted(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups, max_errors=2)
    same = monitor(case("c0", tap("A")))
    h.controller.apply(same)
    h.wait_request(1)
    h.jev.respond(JevError("first"))
    h.wait_request(2)
    h.controller.apply(same)
    # 作り直す前の世代の要求の失敗は、新しい世代の回数に数えない
    h.jev.respond(JevError("old generation"))
    h.wait_request(3)
    h.jev.respond(JevError("new 1"))
    h.wait_request(4)
    assert h.controller.take_events().monitor_ends == ()
    h.jev.respond(JevError("new 2"))
    h.wait_until(lambda: len(h.monitor_rows()) == 4, "four requests are recorded")
    h.wait_rounds()
    [end] = h.controller.take_events().monitor_ends
    assert (end.name, end.reason) == ("dialog", "failed")
    assert "new 2" in end.detail


def test_stop_ends_the_requests_and_ignores_the_answer(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A"))))
    request = h.wait_request(1)
    h.controller.apply(Decision(monitor=MonitorStop()))
    assert h.controller.take_events().monitor is None
    h.jev.respond(hit(request, 0))
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the answer is recorded")
    h.wait_rounds()
    assert h.jev.request_count() == 1
    assert h.sender.reports() == []
    assert h.monitor_rows()[0]["hit"] is None
    assert h.controller.take_events().monitor_ends == ()


# --- 監視: Jev の失敗 ---


def test_monitor_fails_after_max_errors_in_a_row_and_the_run_goes_on(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups, max_errors=3)
    h.controller.apply(monitor(case("c0", tap("A"))))
    for count in (1, 2, 3):
        h.wait_request(count)
        h.jev.respond(JevError("jev is down"))
    h.wait_until(lambda: len(h.monitor_rows()) == 3, "three failures are recorded")
    h.wait_rounds()
    events = h.controller.take_events()
    assert events.monitor is None
    [end] = events.monitor_ends
    assert (end.name, end.reason) == ("dialog", "failed")
    assert "jev is down" in end.detail
    assert "3" in end.detail
    h.wait_rounds()
    assert h.jev.request_count() == 3
    h.controller.apply(act(tap("B")))
    h.wait_pressed({"B"})


def test_answer_resets_the_error_count(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, max_errors=3)
    h.controller.apply(monitor(case("c0", tap("A"))))
    error = JevError("timeout")
    for count, result in enumerate([error, error, None, error, error], start=1):
        request = h.wait_request(count)
        h.jev.respond(nothing(request) if result is None else result)
    h.wait_request(6)
    events = h.controller.take_events()
    assert events.monitor_ends == ()
    assert events.monitor is not None


@pytest.mark.parametrize(
    "make_result",
    [
        lambda request: RuntimeError("boom"),
        lambda request: JevAnswers(
            choices={request.question.name: {"NONE": 1.0}}, nouls={}, boxes={}
        ),
        lambda request: JevAnswers(choices={}, nouls={}, boxes={}),
    ],
    ids=["other_exception", "missing_case_probability", "missing_question"],
)
def test_other_failures_also_count(tmp_path, cleanups, make_result):
    h = make_harness(tmp_path, cleanups, max_errors=1)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.jev.respond(make_result(h.wait_request(1)))
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the failure is recorded")
    h.wait_rounds()
    [end] = h.controller.take_events().monitor_ends
    assert end.reason == "failed"
    [row] = h.monitor_rows()
    assert row["probabilities"] is None
    assert row["error"]


def test_exception_in_the_request_thread_counts_as_a_failure(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, max_errors=3, frame=BROKEN_FRAME)
    h.controller.apply(monitor(case("c0", tap("A"))))
    # 画像の作成の例外を失敗にし、返っていない要求の状態を消して、次の要求を送り直す
    h.wait_until(
        lambda: len(h.monitor_rows()) == 3, "three failed requests are recorded"
    )
    h.wait_rounds()
    rows = h.monitor_rows()
    assert len(rows) == 3
    assert all(row["probabilities"] is None and row["error"] for row in rows)
    assert all(row["hit"] is None for row in rows)
    assert h.jev.request_count() == 0
    events = h.controller.take_events()
    assert events.monitor is None
    [end] = events.monitor_ends
    assert (end.name, end.reason) == ("dialog", "failed")
    assert "3" in end.detail
    h.check_run()


def test_new_monitor_starts_counting_errors_from_zero(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups, max_errors=2)
    h.controller.apply(monitor(case("c0", tap("A")), name="a"))
    h.wait_request(1)
    h.jev.respond(JevError("first"))
    h.wait_request(2)
    h.controller.apply(monitor(case("c0", tap("A")), name="b"))
    # 置き換えた前の監視の失敗は、新しい監視の回数に数えない
    h.jev.respond(JevError("old monitor"))
    h.wait_request(3)
    h.jev.respond(JevError("new 1"))
    h.wait_request(4)
    assert h.controller.take_events().monitor_ends == ()
    h.jev.respond(JevError("new 2"))
    h.wait_until(lambda: len(h.monitor_rows()) == 4, "four requests are recorded")
    h.wait_rounds()
    [end] = h.controller.take_events().monitor_ends
    assert (end.name, end.reason) == ("b", "failed")


# --- 記録 ---


def test_monitor_row_records_the_request(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    assert not h.paths.orchestrator_dir.exists()
    h.set_time(2.0)
    h.controller.apply(monitor(case("c0", tap("A")), case("c1", tap("B"))))
    request = h.wait_request(1)
    h.set_time(2.5)
    h.jev.respond(
        JevAnswers(
            choices={request.question.name: {"C1": 0.7, "C0": 0.2, "NONE": 0.1}},
            nouls={},
            boxes={},
            usage={"input_tokens": 10},
        )
    )
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the answer is recorded")
    [row] = h.monitor_rows()
    assert list(row) == [
        "t_sent",
        "t_received",
        "monitor",
        "frame_t",
        "probabilities",
        "hit",
        "usage",
        "error",
    ]
    assert row == {
        "t_sent": 2.0,
        "t_received": 2.5,
        "monitor": "dialog",
        "frame_t": SMALL_FRAME.t,
        "probabilities": {"NONE": 0.1, "C0": 0.2, "C1": 0.7},
        "hit": "C1",
        "usage": {"input_tokens": 10},
        "error": None,
    }
    assert list(row["probabilities"]) == ["NONE", "C0", "C1"]


def test_failed_request_row(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.wait_request(1)
    h.jev.respond(JevError("timeout"))
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the failure is recorded")
    [row] = h.monitor_rows()
    assert row["monitor"] == "dialog"
    assert row["probabilities"] is None
    assert row["hit"] is None
    assert row["usage"] is None
    assert "JevError" in row["error"]
    assert "timeout" in row["error"]


def test_monitor_operations_are_recorded_once_with_the_monitor_source(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("B"))))
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"B"})
    h.controller.apply(act(tap("A")))
    h.wait_pressed({"A"})
    rows = h.operation_rows()
    # 監視の操作は monitor、主ループの操作は orchestrator の行が、それぞれ 1 行だけある
    assert [row["source"] for row in rows_pressing(rows, "B")] == ["monitor"]
    assert [row["source"] for row in rows_pressing(rows, "A")] == ["orchestrator"]
    assert "apply" not in {row["source"] for row in rows}


# --- 主ループへの知らせ ---


def test_take_events_returns_happenings_once_and_current_values_every_time(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    created = MonitorCreate("dialog", (case("c0", tap("HOME", "A")),))
    h.controller.apply(
        Decision(
            actions=(hold(1.0, "X"), tap("Y")),
            macros_set={"m": (tap("B"),)},
            monitor=created,
        )
    )
    h.wait_pressed({"X"})
    h.controller.apply(act(hold(1.0, "Y")))
    h.wait_pressed({"Y"})
    h.set_time(1.001)
    h.jev.respond(hit(h.wait_request(1), 0))
    h.wait_pressed({"A"})
    h.set_time(1.3)
    h.wait_request(2)
    first = h.controller.take_events()
    assert first == OrchestratorEvents(
        case_hits=(CaseHit("dialog", 0, "c0", 1, (tap("HOME", "A"),)),),
        monitor_ends=(),
        discarded_action_count=2,
        blocked_buttons=("HOME",),
        running=False,
        remaining_action_count=0,
        macros={"m": (tap("B"),)},
        monitor=created,
    )
    second = h.controller.take_events()
    assert second == OrchestratorEvents(macros={"m": (tap("B"),)}, monitor=created)


# --- 止める ---


def test_stop_returns_to_neutral_and_returns(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(act(hold(1.0, "A", left=(0.0, 1.0))))
    h.wait_pressed({"A"})
    h.stop.set()
    assert h.thread is not None
    h.thread.join(WAIT_TIMEOUT_S)
    assert not h.thread.is_alive()
    h.check_run()
    assert h.sender.last_report() == NEUTRAL_REPORT


def test_stop_does_not_wait_for_a_request_in_flight(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A"))))
    h.wait_request(1)
    h.stop.set()
    assert h.thread is not None
    h.thread.join(WAIT_TIMEOUT_S)
    assert not h.thread.is_alive()
    h.check_run()
    assert h.monitor_rows() == []


def test_run_can_be_called_only_once(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.stop.set()
    assert h.thread is not None
    h.thread.join(WAIT_TIMEOUT_S)
    h.check_run()
    with pytest.raises(RuntimeError):
        h.controller.run(threading.Event())


def test_nothing_is_pressed_after_the_output_is_stopped(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.pad_output.stop_output()
    h.controller.apply(act(tap("A"), tap("B")))
    h.drive(1.0)
    assert not any(sent.buttons for sent in h.sender.reports())
    assert h.thread is not None and h.thread.is_alive()


# --- 出力を止めた後の監視 ---


def test_no_request_after_the_output_stop_is_known(tmp_path, cleanups):
    h = make_harness(tmp_path, cleanups)
    h.pad_output.stop_output()
    # 主ループの操作を送ろうとして、出力が止まったことを知る
    h.controller.apply(act(tap("A")))
    h.wait_until(
        lambda: h.controller.take_events().running is False, "the actions are dropped"
    )
    h.controller.apply(monitor(case("c0", tap("B"))))
    h.wait_rounds()
    assert h.jev.request_count() == 0
    assert not any(sent.buttons for sent in h.sender.reports())


def test_hit_returned_after_the_output_stopped_is_not_counted_or_recorded(
    tmp_path, cleanups
):
    h = make_harness(tmp_path, cleanups)
    h.controller.apply(monitor(case("c0", tap("A"), repeat="once")))
    request = h.wait_request(1)
    # 要求の応答を待つ間に出力を止める
    h.pad_output.stop_output()
    h.jev.respond(hit(request, 0))
    h.wait_until(lambda: len(h.monitor_rows()) == 1, "the answer is recorded")
    h.wait_rounds()
    [row] = h.monitor_rows()
    assert row["hit"] is None
    assert row["probabilities"]["C0"] == 0.95
    events = h.controller.take_events()
    assert events.case_hits == ()
    assert events.monitor_ends == ()
    assert h.jev.request_count() == 1
    assert not any(sent.buttons for sent in h.sender.reports())
