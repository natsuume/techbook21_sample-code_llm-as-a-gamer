import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import (
    TAP_PRESS_S,
    TIMER_INTERVAL_S,
    PadOutput,
)
from switch2_layers.middle.system_screen_types import (
    SystemCutReason,
    SystemOperation,
    SystemRunResult,
    SystemScreenOutput,
)
from switch2_layers.pad_action import PadAction
from switch2_layers.records.operation_log import OperationEvent
from switch2_layers.run_clock import RunClock
from switch2_layers.system_screen.system_operation_runner import (
    DECISION_BUTTON,
    JUDGMENT_WAIT_S,
    RELEASE_POLL_S,
    RELEASE_WAIT_S,
    SETTLE_S,
    SystemOperationRunner,
    SystemOutputError,
    operation_pad_action,
)

# 偽の時計の起点 (t = 0 の monotonic の値) と、run を始める時刻 t
ORIGIN = 1000.0
START_T = 10.0
# 送るたびに偽の時計が進む秒数 (apply が戻った時刻から数えることを確かめるため)
SEND_DELAY_S = 0.05
ALLOWED_WITHOUT_HOME = frozenset(BUTTON_ORDER) - {"HOME"}
ALL_ALLOWED = frozenset(BUTTON_ORDER)
# 偽物の呼び出しの回数の上限 (待ちの処理が終わらないときに、テストを止めずに失敗させる)
MAX_FAKE_CALLS = 100_000
# 偽の待ちで、1 回の呼び出しで少なくとも進める秒数 (実際の待ちでも、呼ぶたびに時間は進む)
MIN_FAKE_STEP_S = 0.001


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeTimer:
    """PadOutput のタイマースレッドの偽物。偽の時計を進める部品 (FakeWaits と FakeJudgments) が使う。

    advance は偽の時計を TIMER_INTERVAL_S ずつ進め、進めるたびに、実際のタイマー (PadOutput._run_timer) と同じ順で
    start_due_taps・release_due_taps・start_due_holds・release_due_holds を呼ぶ。running が False なら呼ばない
    (ボタンが離れない場合を作る)。呼んだ処理が送るレポートでも、送信先が偽の時計を進める。
    """

    def __init__(self, monotonic: FakeMonotonic):
        self.monotonic = monotonic
        self.pad_output: PadOutput | None = None
        self.running = True

    def advance(self, seconds: float) -> None:
        target = self.monotonic.now + seconds
        while self.monotonic.now < target:
            self.monotonic.now = min(self.monotonic.now + TIMER_INTERVAL_S, target)
            self._tick()

    def advance_to(self, monotonic_value: float) -> None:
        if monotonic_value > self.monotonic.now:
            self.advance(monotonic_value - self.monotonic.now)

    def _tick(self) -> None:
        if not self.running or self.pad_output is None:
            return
        self.pad_output.start_due_taps()
        self.pad_output.release_due_taps()
        self.pad_output.start_due_holds()
        self.pad_output.release_due_holds()


class FakeSender:
    """送るたびに偽の時計を delay 秒進める ReportSender。

    sent_t に、各送信が戻った時刻 t を記録する。on_send があれば送った後に呼ぶ。fail_with を入れると、その例外を出す。
    """

    def __init__(self, monotonic: FakeMonotonic, delay: float):
        self.monotonic = monotonic
        self.delay = delay
        self.sent_t: list[float] = []
        self.on_send: Callable[[], None] | None = None
        self.fail_with: Exception | None = None

    def set_report(self, report: bytes) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.monotonic.now += self.delay
        self.sent_t.append(self.monotonic.now - ORIGIN)
        if self.on_send is not None:
            self.on_send()
        return "ack"


@dataclass(frozen=True)
class FakeJudgment:
    """照合の結果 (JudgmentResult の形)。"""

    frame_t: float
    screen: str | None


class FakeJudgments:
    """JudgmentSource の偽物。結果ごとに届く時刻 t を持ち、偽の時計で届いた結果だけを返す。

    届いた結果のうち frame_t がいちばん新しいものを持つ。wait_newer は、持っている結果の frame_t が after_frame_t より
    大きければすぐ返し、無ければ、そうなる結果が届く時刻まで (最大 timeout_s 秒。届かなくても少なくとも
    MIN_FAKE_STEP_S 秒) 偽の時計を進める (timer で進めるので、その間も PadOutput のタイマーが動く)。渡された
    after_frame_t を after_values に記録する。on_wait があれば、wait_newer の始めに呼ぶ。
    """

    def __init__(
        self,
        monotonic: FakeMonotonic,
        clock: RunClock,
        timer: FakeTimer,
        arrivals: Iterable[tuple[float, FakeJudgment]],
    ):
        self.monotonic = monotonic
        self.clock = clock
        self.timer = timer
        self.arrivals = sorted(arrivals, key=lambda item: item[0])
        self.after_values: list[float] = []
        self.on_wait: Callable[[], None] | None = None

    def _held_at(self, t: float) -> FakeJudgment | None:
        arrived = [judgment for at, judgment in self.arrivals if at <= t]
        return max(arrived, key=lambda judgment: judgment.frame_t, default=None)

    def _advance_to(self, t: float) -> None:
        self.timer.advance_to(ORIGIN + t)

    def latest(self) -> FakeJudgment | None:
        return self._held_at(self.clock.now())

    def wait_newer(self, after_frame_t: float, timeout_s: float) -> FakeJudgment | None:
        self.after_values.append(after_frame_t)
        if len(self.after_values) > MAX_FAKE_CALLS:
            raise AssertionError("wait_newer was called too many times")
        if self.on_wait is not None:
            self.on_wait()
        now = self.monotonic.now - ORIGIN
        deadline = now + max(timeout_s, 0.0)
        candidates = [now] + [at for at, _ in self.arrivals if now < at <= deadline]
        for at in candidates:
            held = self._held_at(at)
            if held is not None and held.frame_t > after_frame_t:
                self._advance_to(at)
                return held
        self._advance_to(max(deadline, now + MIN_FAKE_STEP_S))
        return None


class FakeWaits:
    """wait_or_stop の偽物。stop が立っていればすぐ True を返し、立っていなければ偽の時計を seconds 秒
    (正の秒数なら少なくとも MIN_FAKE_STEP_S 秒) timer で進めて stop.is_set() を返す。on_wait があれば始めに
    on_wait(stop, seconds) を呼ぶ。"""

    def __init__(self, timer: FakeTimer):
        self.timer = timer
        self.calls = 0
        self.on_wait: Callable[[threading.Event, float], None] | None = None

    def __call__(self, stop: threading.Event, seconds: float) -> bool:
        self.calls += 1
        if self.calls > MAX_FAKE_CALLS:
            raise AssertionError("wait_or_stop was called too many times")
        if self.on_wait is not None:
            self.on_wait(stop, seconds)
        if stop.is_set():
            return True
        if seconds > 0:
            self.timer.advance(max(seconds, MIN_FAKE_STEP_S))
        return stop.is_set()


@dataclass
class Harness:
    monotonic: FakeMonotonic
    clock: RunClock
    sender: FakeSender
    pad_output: PadOutput
    judgments: FakeJudgments
    waits: FakeWaits
    runner: SystemOperationRunner
    events: list[OperationEvent] = field(default_factory=list)

    def now(self) -> float:
        return self.monotonic.now - ORIGIN


def make_harness(
    arrivals: Iterable[tuple[float, FakeJudgment]] = (),
    allowed: frozenset[str] = ALLOWED_WITHOUT_HOME,
    timer_running: bool = True,
) -> Harness:
    """偽の時計 (t = START_T から)・送信先・照合の結果・待ちを使う SystemOperationRunner を作る。

    PadOutput は本物を使い (タイマースレッドは起動せず、FakeTimer が偽の時計を進めるたびに代わりに動かす)、送った
    操作を OperationEvent で受け取る。timer_running が False なら FakeTimer は PadOutput を動かさない (ボタンが
    離れない)。
    """
    monotonic = FakeMonotonic(ORIGIN + START_T)
    clock = RunClock(monotonic, origin=ORIGIN)
    sender = FakeSender(monotonic, SEND_DELAY_S)
    events: list[OperationEvent] = []
    pad_output = PadOutput(
        sender, allowed, clock=monotonic, on_operation=events.append, start_timer=False
    )
    timer = FakeTimer(monotonic)
    timer.pad_output = pad_output
    timer.running = timer_running
    judgments = FakeJudgments(monotonic, clock, timer, arrivals)
    waits = FakeWaits(timer)
    runner = SystemOperationRunner(pad_output, judgments, clock, wait_or_stop=waits)
    return Harness(
        monotonic=monotonic,
        clock=clock,
        sender=sender,
        pad_output=pad_output,
        judgments=judgments,
        waits=waits,
        runner=runner,
        events=events,
    )


def steady(
    screen: str | None,
    switch_to: str | None = None,
    switch_t: float | None = None,
    start_t: float = START_T,
    end_t: float = START_T + 60.0,
    interval_s: float = 0.2,
    latency_s: float = 0.3,
) -> list[tuple[float, FakeJudgment]]:
    """frame_t が start_t から interval_s ごとの照合の結果。各結果は frame_t + latency_s に届く。

    switch_t を渡したら、frame_t が switch_t 以上の結果の画面を switch_to にする。
    """
    arrivals = []
    frame_t = start_t
    while frame_t <= end_t:
        shown = screen if switch_t is None or frame_t < switch_t else switch_to
        arrivals.append(
            (round(frame_t + latency_s, 3), FakeJudgment(frame_t=frame_t, screen=shown))
        )
        frame_t = round(frame_t + interval_s, 3)
    return arrivals


def press(screen: str, *buttons: str, seconds: float | None = None) -> SystemOperation:
    return SystemOperation(
        screen=screen, kind="press", buttons=buttons, seconds=seconds
    )


def wait(screen: str, seconds: float) -> SystemOperation:
    return SystemOperation(screen=screen, kind="wait", buttons=(), seconds=seconds)


def output_of(*operations: SystemOperation) -> SystemScreenOutput:
    return SystemScreenOutput(observation="", operations=operations, done=False)


def run(
    harness: Harness,
    *operations: SystemOperation,
    judged: str | None = "home_menu",
    stop: threading.Event | None = None,
) -> SystemRunResult:
    return harness.runner.run(
        output_of(*operations), judged, threading.Event() if stop is None else stop
    )


def cut(index: int, reason: SystemCutReason, blocked=frozenset()) -> SystemRunResult:
    return SystemRunResult(
        sent_count=index, cut_index=index, cut_reason=reason, blocked_buttons=blocked
    )


def sent_all(count: int, blocked=frozenset()) -> SystemRunResult:
    return SystemRunResult(
        sent_count=count, cut_index=None, cut_reason=None, blocked_buttons=blocked
    )


def system_actions(harness: Harness) -> list[PadAction]:
    return [event.action for event in harness.events if event.source == "system"]


def distinct_after_values(harness: Harness) -> list[float]:
    """wait_newer に渡された after_frame_t を、続けて同じ値を 1 つにまとめて並べる。"""
    values: list[float] = []
    for value in harness.judgments.after_values:
        if not values or values[-1] != value:
            values.append(value)
    return values


# --- 定数と操作の変換 ---


def test_constants():
    assert SETTLE_S == 0.5
    assert JUDGMENT_WAIT_S == 3.0
    assert DECISION_BUTTON == "A"


def test_release_constants():
    assert RELEASE_POLL_S == 0.01
    assert RELEASE_WAIT_S == 1.0


def test_tap_becomes_taps():
    assert operation_pad_action(press("home_menu", "DOWN")) == PadAction(
        taps=frozenset({"DOWN"})
    )


def test_hold_becomes_held_with_seconds():
    assert operation_pad_action(
        press("controller_order", "L", "R", seconds=0.4)
    ) == PadAction(held=frozenset({"L", "R"}), hold_seconds={"L": 0.4, "R": 0.4})


def test_wait_is_not_a_pad_action():
    with pytest.raises(ValueError):
        operation_pad_action(wait("home_menu", 1.0))


# --- 最後まで送る ---


def test_sends_all_operations_in_order_as_system():
    harness = make_harness(steady("home_menu"))
    result = run(
        harness,
        press("home_menu", "DOWN"),
        press("home_menu", "RIGHT"),
        press("home_menu", "L", "R", seconds=0.4),
    )
    assert result == sent_all(3)
    assert [event.source for event in harness.events] == ["system"] * 3
    assert system_actions(harness) == [
        PadAction(taps=frozenset({"DOWN"})),
        PadAction(taps=frozenset({"RIGHT"})),
        PadAction(held=frozenset({"L", "R"}), hold_seconds={"L": 0.4, "R": 0.4}),
    ]


def test_empty_operations_send_nothing_and_do_not_wait():
    harness = make_harness(steady("home_menu"))
    assert run(harness) == sent_all(0)
    assert harness.events == []
    assert harness.now() == pytest.approx(START_T)


def test_wait_sends_nothing_and_waits_its_seconds():
    harness = make_harness(steady("home_menu"))
    assert run(harness, wait("home_menu", 2.0)) == sent_all(1)
    assert harness.events == []
    assert harness.now() >= START_T + 2.0 - 1e-9


def test_returns_after_the_last_press_is_released():
    harness = make_harness(steady("home_menu"))
    assert run(harness, press("home_menu", "L", "R", seconds=0.4)) == sent_all(1)
    # 送信は 押す (0)・離す (1) の順。離すレポートの送信が戻った後に返る
    assert harness.pad_output.pressing_buttons() == frozenset()
    assert harness.now() >= harness.sender.sent_t[1] - 1e-9
    assert harness.sender.sent_t[1] > harness.sender.sent_t[0] + 0.4


def test_returns_after_the_last_tap_is_released():
    harness = make_harness(steady("home_menu"))
    assert run(harness, press("home_menu", "DOWN")) == sent_all(1)
    assert harness.pad_output.pressing_buttons() == frozenset()
    assert harness.now() >= harness.sender.sent_t[1] - 1e-9


# --- 2 つ目以降の照合に使うフレーム ---


def test_judgment_frame_is_after_tap_release_plus_settle():
    harness = make_harness(steady("home_menu"))
    run(
        harness,
        press("home_menu", "DOWN"),
        press("home_menu", "RIGHT"),
        press("home_menu", "B"),
    )
    # 送信は 押す (0)・離す (1)・押す (2)・離す (3)・押す (4)・離す (5) の順
    sent_t = harness.sender.sent_t
    # 前の操作のボタンを離すレポートの送信が戻った時刻 + SETTLE_S
    assert distinct_after_values(harness) == pytest.approx(
        [sent_t[1] + SETTLE_S, sent_t[3] + SETTLE_S]
    )
    # 離すのは、apply が戻った時刻 + TAP_PRESS_S より後 (タイマーが離す)
    assert sent_t[1] > sent_t[0] + TAP_PRESS_S


def test_judgment_frame_is_after_hold_release_plus_settle():
    harness = make_harness(steady("controller_order"))
    run(
        harness,
        press("controller_order", "L", "R", seconds=0.4),
        press("controller_order", "B"),
        judged="controller_order",
    )
    sent_t = harness.sender.sent_t
    assert distinct_after_values(harness) == pytest.approx([sent_t[1] + SETTLE_S])
    assert sent_t[1] > sent_t[0] + 0.4


def test_release_wait_stops_at_its_limit():
    # タイマーが動かず、ボタンが離れないときは、上限の時刻を終えた時刻にして進む
    harness = make_harness(steady("home_menu"), timer_running=False)
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    assert result == sent_all(2)
    assert distinct_after_values(harness) == pytest.approx(
        [harness.sender.sent_t[0] + TAP_PRESS_S + RELEASE_WAIT_S + SETTLE_S]
    )


def test_judgment_frame_is_after_wait_end_plus_settle():
    harness = make_harness(steady("home_menu"))
    run(harness, wait("home_menu", 1.0), press("home_menu", "DOWN"))
    # 先頭の wait は START_T に待ち始める
    assert distinct_after_values(harness) == pytest.approx([START_T + 1.0 + SETTLE_S])


def test_ignores_judgments_of_frames_before_settle():
    # B で戻る前の画面 (controllers) のフレームが、待っている間に届いても使わない
    harness = make_harness(steady("controllers", "home_menu", switch_t=10.5))
    result = run(
        harness,
        press("controllers", "B"),
        press("home_menu", "DOWN"),
        judged="controllers",
    )
    assert result == sent_all(2)


def first_after_frame_t(*operations: SystemOperation) -> float:
    """operations を、照合の結果がいつも home_menu の偽物で動かしたときの、最初の after_frame_t を返す。

    偽の時計は決まった進み方をするので、同じ列の先頭の操作を終えた時刻は、照合の結果の届き方によらず同じになる。
    """
    probe = make_harness(steady("home_menu"))
    run(probe, *operations)
    return probe.judgments.after_values[0]


def test_judgment_arriving_just_before_the_wait_limit_is_used():
    after = first_after_frame_t(press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    arrival = after + JUDGMENT_WAIT_S - 0.05
    harness = make_harness(
        [(arrival, FakeJudgment(frame_t=arrival - 0.1, screen="home_menu"))]
    )
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    assert result == sent_all(2)


def test_judgment_arriving_after_the_wait_limit_is_too_late():
    after = first_after_frame_t(press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    arrival = after + JUDGMENT_WAIT_S + 0.05
    harness = make_harness(
        [(arrival, FakeJudgment(frame_t=arrival - 0.1, screen="home_menu"))]
    )
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    assert result == cut(1, SystemCutReason.NO_JUDGMENT)
    assert len(system_actions(harness)) == 1


# --- 照合の結果が違う・届かない ---


def test_first_operation_mismatch_sends_nothing():
    harness = make_harness(steady("controllers"))
    result = run(harness, press("home_menu", "DOWN"), judged="controllers")
    assert result == cut(0, SystemCutReason.SCREEN_MISMATCH)
    assert harness.events == []


@pytest.mark.parametrize(
    ("judged", "screen", "sent"),
    [(None, "none", True), (None, "home_menu", False), ("home_menu", "none", False)],
    ids=["none-matches-null", "name-vs-null", "none-vs-name"],
)
def test_first_operation_compares_none_with_no_reference_screen(judged, screen, sent):
    harness = make_harness(steady(judged), allowed=ALL_ALLOWED)
    result = run(harness, press(screen, "HOME"), judged=judged)
    if sent:
        assert result == sent_all(1)
        assert system_actions(harness) == [PadAction(taps=frozenset({"HOME"}))]
    else:
        assert result == cut(0, SystemCutReason.SCREEN_MISMATCH)
        assert harness.events == []


def test_later_operation_mismatch_cuts_before_it():
    harness = make_harness(steady("controllers"))
    result = run(
        harness,
        press("home_menu", "DOWN"),
        press("home_menu", "RIGHT"),
        press("home_menu", "B"),
    )
    assert result == cut(1, SystemCutReason.SCREEN_MISMATCH)
    assert system_actions(harness) == [PadAction(taps=frozenset({"DOWN"}))]


@pytest.mark.parametrize(
    ("judgment_screen", "screen", "sent"),
    [(None, "none", True), (None, "home_menu", False), ("home_menu", "none", False)],
    ids=["none-matches-null", "name-vs-null", "none-vs-name"],
)
def test_later_operation_compares_none_with_no_reference_screen(
    judgment_screen, screen, sent
):
    harness = make_harness(steady(judgment_screen))
    result = run(harness, press("home_menu", "B"), press(screen, "DOWN"))
    if sent:
        assert result == sent_all(2)
    else:
        assert result == cut(1, SystemCutReason.SCREEN_MISMATCH)


def test_no_judgment_cuts_before_the_operation():
    harness = make_harness([])
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"))
    assert result == cut(1, SystemCutReason.NO_JUDGMENT)
    assert system_actions(harness) == [PadAction(taps=frozenset({"DOWN"}))]


# --- 決定の A ---


def test_first_operation_may_press_a():
    harness = make_harness(steady("user_select"))
    result = run(harness, press("user_select", "A"), judged="user_select")
    assert result == sent_all(1)
    assert system_actions(harness) == [PadAction(taps=frozenset({"A"}))]


@pytest.mark.parametrize(
    "decision",
    [
        press("home_menu", "A"),
        press("home_menu", "A", seconds=0.5),
        press("home_menu", "L", "A"),
    ],
    ids=["tap", "hold", "with-other-button"],
)
def test_a_after_the_first_operation_cuts_before_it(decision):
    harness = make_harness(steady("home_menu"))
    result = run(harness, press("home_menu", "DOWN"), decision)
    assert result == cut(1, SystemCutReason.DECISION_NOT_FIRST)
    assert system_actions(harness) == [PadAction(taps=frozenset({"DOWN"}))]


def test_a_after_a_wait_cuts_before_it():
    harness = make_harness(steady("home_menu"))
    result = run(harness, wait("home_menu", 0.5), press("home_menu", "A"))
    assert result == cut(1, SystemCutReason.DECISION_NOT_FIRST)
    assert harness.events == []


def test_a_in_the_third_operation_cuts_before_it():
    harness = make_harness(steady("home_menu"))
    result = run(
        harness,
        press("home_menu", "DOWN"),
        press("home_menu", "RIGHT"),
        press("home_menu", "A"),
    )
    assert result == cut(2, SystemCutReason.DECISION_NOT_FIRST)
    assert len(system_actions(harness)) == 2


def test_a_cut_returns_after_the_previous_hold_is_released():
    harness = make_harness(steady("controller_order"))
    result = run(
        harness,
        press("controller_order", "L", "R", seconds=0.5),
        press("controller_order", "A"),
        judged="controller_order",
    )
    assert result == cut(1, SystemCutReason.DECISION_NOT_FIRST)
    assert harness.now() >= harness.sender.sent_t[0] + 0.5 - 1e-9


def test_a_cut_returns_after_the_previous_wait_ends():
    harness = make_harness(steady("home_menu"))
    result = run(harness, wait("home_menu", 2.0), press("home_menu", "A"))
    assert result == cut(1, SystemCutReason.DECISION_NOT_FIRST)
    assert harness.now() >= START_T + 2.0 - 1e-9


def test_a_is_cut_without_waiting_for_a_judgment():
    # 照合の結果が届かなくても、理由は decision_not_first (A は照合の結果を待つ前に確かめる)
    harness = make_harness([])
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "A"))
    assert result == cut(1, SystemCutReason.DECISION_NOT_FIRST)


# --- 止める合図 ---


def test_stop_before_run_sends_nothing():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()
    stop.set()
    result = run(harness, press("home_menu", "DOWN"), stop=stop)
    assert result == cut(0, SystemCutReason.STOPPED)
    assert harness.events == []


def test_stop_after_the_first_send_cuts_the_rest():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()
    harness.sender.on_send = stop.set
    result = run(
        harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"), stop=stop
    )
    assert result == cut(1, SystemCutReason.STOPPED)
    assert len(system_actions(harness)) == 1


def test_stop_while_waiting_for_a_judgment_cuts_even_if_it_matches():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()
    harness.judgments.on_wait = stop.set
    result = run(
        harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"), stop=stop
    )
    assert result == cut(1, SystemCutReason.STOPPED)
    assert len(system_actions(harness)) == 1


def test_stop_while_waiting_for_the_settle_time_cuts_the_rest():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()

    def stop_after_release(event: threading.Event, seconds: float) -> None:
        # 前の操作のボタンを離し終えた後の待ち (SETTLE_S の待ち) で止める
        if not harness.pad_output.pressing_buttons():
            event.set()

    harness.waits.on_wait = stop_after_release
    result = run(
        harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"), stop=stop
    )
    assert result == cut(1, SystemCutReason.STOPPED)
    assert len(system_actions(harness)) == 1


def test_stop_while_waiting_for_the_release_cuts_the_rest():
    harness = make_harness(steady("home_menu"), timer_running=False)
    stop = threading.Event()

    def stop_while_polling(event: threading.Event, seconds: float) -> None:
        # 離したかを確かめる待ち (RELEASE_POLL_S 以下の待ち) で止める
        if seconds <= RELEASE_POLL_S + 1e-9:
            event.set()

    harness.waits.on_wait = stop_while_polling
    result = run(
        harness, press("home_menu", "DOWN"), press("home_menu", "RIGHT"), stop=stop
    )
    assert result == cut(1, SystemCutReason.STOPPED)
    assert len(system_actions(harness)) == 1
    # 離すのを待つ上限まで待たずに戻る
    assert harness.now() < harness.sender.sent_t[0] + TAP_PRESS_S + RELEASE_WAIT_S


def test_stop_while_waiting_for_the_last_release_is_not_a_cut():
    harness = make_harness(steady("home_menu"), timer_running=False)
    stop = threading.Event()
    harness.waits.on_wait = lambda event, seconds: event.set()
    result = run(harness, press("home_menu", "DOWN"), stop=stop)
    assert result == sent_all(1)


def test_stop_while_waiting_after_the_last_operation_is_not_a_cut():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()
    harness.waits.on_wait = lambda event, seconds: event.set()
    result = run(harness, press("home_menu", "DOWN"), stop=stop)
    assert result == sent_all(1)


def test_stop_during_the_last_wait_is_not_a_cut():
    harness = make_harness(steady("home_menu"))
    stop = threading.Event()
    harness.waits.on_wait = lambda event, seconds: event.set()
    result = run(harness, wait("home_menu", 1.0), stop=stop)
    assert result == sent_all(1)
    # 待つのをやめて、すぐに戻る
    assert harness.now() < START_T + 1.0


def test_stopped_output_is_treated_as_stopped():
    # 全体を止めるときに、出力が先に止められた (apply が OutputStoppedError を出す)
    harness = make_harness(steady("home_menu"))
    harness.pad_output.stop_output()
    result = run(harness, press("home_menu", "DOWN"))
    assert result == cut(0, SystemCutReason.STOPPED)
    assert system_actions(harness) == []


class BridgeDown(Exception):
    """送信の失敗 (偽物)。"""


def test_send_failure_is_raised():
    harness = make_harness(steady("home_menu"))
    harness.sender.fail_with = BridgeDown("bridge is down")
    with pytest.raises(BridgeDown):
        run(harness, press("home_menu", "DOWN"))


def fail_after_the_first_send(harness: Harness) -> None:
    """最初のレポート (押すレポート) の送信の後、次のレポート (離すレポート) から送信を失敗させる。"""

    def set_failure() -> None:
        if len(harness.sender.sent_t) == 1:
            harness.sender.fail_with = BridgeDown("release report failed")

    harness.sender.on_send = set_failure


@pytest.mark.parametrize(
    "first",
    [press("home_menu", "DOWN"), press("home_menu", "L", "R", seconds=0.4)],
    ids=["tap", "hold"],
)
def test_failed_release_report_raises_and_sends_nothing_more(first):
    harness = make_harness(steady("home_menu"))
    fail_after_the_first_send(harness)
    with pytest.raises(SystemOutputError, match="release report failed"):
        run(harness, first, press("home_menu", "B"))
    # 離すレポートの失敗の後は、次の操作を送らない
    assert len(system_actions(harness)) == 1
    assert harness.pad_output.timer_send_failures == 1


def test_output_error_is_a_runtime_error():
    assert issubclass(SystemOutputError, RuntimeError)


def test_no_timer_failure_sends_all_operations():
    harness = make_harness(steady("home_menu"))
    result = run(
        harness,
        press("home_menu", "DOWN"),
        press("home_menu", "L", "R", seconds=0.4),
        press("home_menu", "B"),
    )
    assert result == sent_all(3)
    assert harness.pad_output.timer_send_failures == 0


def test_timer_failure_before_the_run_is_not_raised():
    # run を始める前の失敗では出さない (送る前と終えた後の回数を比べる)
    harness = make_harness(steady("home_menu"))
    harness.pad_output.apply(PadAction(taps=frozenset({"X"})))
    harness.sender.fail_with = BridgeDown("earlier failure")
    harness.monotonic.now += TAP_PRESS_S + TIMER_INTERVAL_S
    harness.pad_output.release_due_taps()
    assert harness.pad_output.timer_send_failures == 1
    harness.sender.fail_with = None
    result = run(harness, press("home_menu", "DOWN"), press("home_menu", "B"))
    assert result == sent_all(2)
    assert harness.pad_output.timer_send_failures == 1


def test_timer_failure_during_a_wait_is_not_checked():
    harness = make_harness(steady("home_menu"))
    # run の外で押し続けを始めておき、wait の間にタイマーが離すレポートを失敗させる
    harness.pad_output.apply(PadAction(held=frozenset({"X"}), hold_seconds={"X": 0.2}))
    harness.sender.fail_with = BridgeDown("release report failed")
    result = run(harness, wait("home_menu", 1.0))
    assert result == sent_all(1)
    assert harness.pad_output.timer_send_failures == 1


# --- 押せないボタン ---


def test_blocked_buttons_are_in_the_result():
    harness = make_harness(steady(None))
    result = run(harness, press("none", "HOME"), judged=None)
    assert result == sent_all(1, blocked=frozenset({"HOME"}))


def test_blocked_buttons_of_all_sent_operations_are_collected():
    harness = make_harness(steady("home_menu"), allowed=frozenset({"DOWN"}))
    result = run(
        harness,
        press("home_menu", "B"),
        press("home_menu", "DOWN"),
        press("home_menu", "X"),
    )
    assert result == sent_all(3, blocked=frozenset({"B", "X"}))


def test_blocked_buttons_are_kept_when_cut():
    harness = make_harness(steady("controllers"))
    result = run(harness, press("home_menu", "HOME"), press("home_menu", "DOWN"))
    assert result == cut(
        1, SystemCutReason.SCREEN_MISMATCH, blocked=frozenset({"HOME"})
    )
