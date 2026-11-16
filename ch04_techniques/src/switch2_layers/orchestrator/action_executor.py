"""主ループと監視の操作の列を、1 つずつ PadOutput に送る。

展開 (expand_actions。I/O を持たない):
    procedure は procedures.expand_procedure で、macro は登録した操作の列 (その中の procedure も展開する) で置き換え、
    PressAction と WaitAction だけの列 (PrimitiveAction の列) にする。主ループに知らせる残りの操作の数は、この列の数
実行 (ActionExecutor):
    列の操作を前から順に 1 つずつ実行し、次の操作は前の操作を離し終えてから始める。待ちはしない (run のループが回りごとに
    advance を呼び、今の時刻までに来た期限の送信をする)。時刻は clock (実行の経過秒) で測り、押した時刻と離した時刻は、
    そのレポートを送る直前に読んだ clock.now() にする (送信が戻るまでの間に時計が進んでも、期限をずらさないため)。
    press   ボタンを押してスティックを倒したレポートを送り、押した時刻から押す秒数 (seconds。None なら
            hardware.pad_output.TAP_PRESS_S) たったら、ボタンを離してスティックを中立にしたレポートを送る
    wait    何も押さずに seconds 秒待つ (前の操作を離した時刻から数える。列の最初なら始めた時刻から)
    押す・離すの送信は PadOutput.apply で行う。押せないボタンは apply が外し、外したボタンを take_blocked_buttons で返す。
    列を途中で捨てる (cancel・start) ときだけ、PadOutput.neutral で押下状態を中立に戻す。
押し直しの間:
    ボタンごとに最後に離した時刻 (そのボタンを離したレポートを送る直前に読んだ clock.now())、スティック (left・right)
    ごとに最後に中立に戻した時刻 (そのスティックを中立に戻したレポートを送る直前に読んだ clock.now()) を持つ。どちらも、
    押す操作を離す送信と、cancel・start の PadOutput.neutral の両方で決め、列・待ち・cancel・列の持ち主 (主ループと
    監視) をまたいで持ち続ける。
    press は、その操作のどのボタンも最後に離した時刻から、倒すどのスティックも最後に中立に戻した時刻から、
    hardware.pad_output.TAP_RELEASE_GAP_S 秒たってから始める (たっていなければ、たつ時刻まで操作を始めない。押す秒数は、
    実際に押したときの押した時刻から数える)。間をあけずに押し直す・倒し直すと、ゲームから見て新しい入力にならない
    ため。0 秒か TAP_RELEASE_GAP_S より短い wait を挟んだときも、cancel の直後に同じボタンかスティックで始まる列も、
    この規則で間をあける。TAP_RELEASE_GAP_S 秒以上たっていれば (間に十分な wait があれば)、待たずに始める。
最後に離した時刻 (last_release_t):
    ボタンかスティックを離したレポート (押す操作を離す送信と、cancel・start の PadOutput.neutral) のうち最後のものの、
    離した時刻 (送る直前に読んだ clock.now())。OrchestratorController が、監視の要求に使うフレームが、これに
    orchestrator_controller.MONITOR_FRAME_SETTLE_S を足した時刻より後に撮ったものかを確かめるのに使う。
操作の記録:
    PadOutput.apply を呼ぶたびに、source に列の持ち主 (orchestrator か monitor) を渡す
    (PadOutput.apply(操作, source=持ち主))。operations.jsonl の行は、PadOutput が on_operation でその source のまま
    書く (このモジュールは記録の関数を持たず、行を書かない)。
    apply の送信の例外は外に出さない (PadOutput が ok=False の行を書く。その操作は時刻どおりに進める)。
    apply が OutputStoppedError を出したら (全体を止める処理が出力を止めた)、今の列とこの後に始める列をすべて捨てる
    (以後は何も送らない)。start の時点で PadOutput.output_stopped が True なら、OutputStoppedError を受けたときと
    同じく、列を持たず以後も持たない (送る前に止まったことが分かるので、最初の操作が wait でも、押し直しの間を待つ
    押下でも、列を始めない)。
送る前に止まったか (stopped_before_sending):
    最後の start の後、押す操作を 1 つも送らないうちに出力の停止が分かったら True。分かるのは、start の時点で
    PadOutput.output_stopped が True だったときと、押す操作を送ろうとして OutputStoppedError を受けたときだけ
    (このモジュールを呼ぶスレッドで決まる)。start の後に別のスレッドが stop_output を呼んでも、送ろうとするまでは
    False のまま。押す操作を送った後に止まった列は、始めた列として扱う。
OrchestratorController の run のループのスレッドからだけ呼ぶ (ロックを持たない)。
"""

import logging
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Literal

from switch2_layers.buttons import filter_action
from switch2_layers.hardware.pad_output import (
    TAP_PRESS_S,
    TAP_RELEASE_GAP_S,
    OutputStoppedError,
    PadOutput,
)
from switch2_layers.orchestrator.decision_types import (
    Action,
    MacroAction,
    PressAction,
    ProcedureAction,
    StickName,
    WaitAction,
)
from switch2_layers.orchestrator.procedures import expand_procedure
from switch2_layers.pad_action import (
    NEUTRAL_ACTION,
    STICK_NEUTRAL,
    PadAction,
    sort_buttons,
)
from switch2_layers.run_clock import RunClock

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# 展開した後の操作 (押す操作と待ち)
PrimitiveAction = PressAction | WaitAction
# 操作の列の持ち主。PadOutput.apply の source に渡し、operations.jsonl の source になる
ActionOwner = Literal["orchestrator", "monitor"]


def expand_actions(
    actions: Sequence[Action], macros: Mapping[str, tuple[Action, ...]]
) -> tuple[PrimitiveAction, ...]:
    """actions の procedure と macro を展開した、PressAction と WaitAction だけの列を返す (モジュールの docstring)。

    macros はマクロの名前 → 登録した操作の列。マクロの中の procedure も展開する。macros に無いマクロの名前、または
    マクロの中に MacroAction があれば ValueError (decision_parser.parse_decision が先に確かめる)。
    """
    expanded: list[PrimitiveAction] = []
    for action in actions:
        if not isinstance(action, MacroAction):
            expanded.extend(_expand_without_macro(action))
            continue
        body = macros.get(action.name)
        if body is None:
            raise ValueError(f"macro is not registered: {action.name!r}")
        inner_actions = [inner for inner in body if not isinstance(inner, MacroAction)]
        if len(inner_actions) != len(body):
            raise ValueError(f"macro {action.name!r} calls another macro")
        for inner in inner_actions:
            expanded.extend(_expand_without_macro(inner))
    return tuple(expanded)


def _expand_without_macro(
    action: PressAction | WaitAction | ProcedureAction,
) -> tuple[PrimitiveAction, ...]:
    """MacroAction でない操作 1 つを展開する (procedure は手順の列に、ほかはそのまま)。"""
    if isinstance(action, ProcedureAction):
        return expand_procedure(action.name, action.args)
    return (action,)


def press_pad_action(press: PressAction) -> PadAction:
    """press のボタンを押し続け、スティックを倒す PadAction を返す (押す秒数は ActionExecutor が測る)。"""
    return PadAction(
        held=frozenset(press.buttons),
        left_stick=press.sticks.get("left", STICK_NEUTRAL),
        right_stick=press.sticks.get("right", STICK_NEUTRAL),
    )


def tilted_sticks(action: PadAction) -> frozenset[StickName]:
    """action で倒している (中立でない) スティックの名前 (left・right) の集合を返す。"""
    names: set[StickName] = set()
    if tuple(action.left_stick) != STICK_NEUTRAL:
        names.add("left")
    if tuple(action.right_stick) != STICK_NEUTRAL:
        names.add("right")
    return frozenset(names)


class _Phase(Enum):
    """列の先頭の操作の段階。"""

    READY = "ready"  # まだ始めていない (始めてよい時刻を待つ)
    PRESSING = "pressing"  # 押している (離す時刻を待つ)
    WAITING = "waiting"  # 待っている (待ちを終える時刻を待つ)


class ActionExecutor:
    """操作の列を 1 つ持ち、advance のたびに、今の時刻までに来た押す・離すを送る。

    pad_output   操作の送信 (ボタンの制限と operations.jsonl への記録を含む)
    clock        押す秒数と待ちの秒数を測る時計 (PadOutput の clock と同じ monotonic から作ったもの)
    """

    def __init__(self, pad_output: PadOutput, clock: RunClock) -> None:
        """列を持たない状態で作る。レポートは送らない。"""
        self._pad_output = pad_output
        self._clock = clock
        # 残りの操作 (先頭が実行中か、次に始める操作)
        self._operations: list[PrimitiveAction] = []
        self._source: ActionOwner = "orchestrator"
        self._phase = _Phase.READY
        # 先頭の操作を始めてよい時刻 (前の操作を離した時刻・待ちを終えた時刻・列を始めた時刻)
        self._ready_at = 0.0
        # 押している操作を離す時刻、または待ちを終える時刻
        self._deadline = 0.0
        # 今押しているボタンと、今倒しているスティック (送った操作の値)
        self._pressed_buttons: frozenset[str] = frozenset()
        self._tilted_sticks: frozenset[StickName] = frozenset()
        # ボタンごとの最後に離した時刻と、スティックごとの最後に中立に戻した時刻
        self._button_released_at: dict[str, float] = {}
        self._stick_neutral_at: dict[StickName, float] = {}
        self._last_release_t: float | None = None
        self._blocked: set[str] = set()
        self._finished_owner: ActionOwner | None = None
        # 出力の停止を、このモジュールを呼ぶスレッドで知ったか (start の確かめか、OutputStoppedError)
        self._output_stopped = False
        # 最後の start の後に、押す操作を送ったか (送信の失敗を含む。OutputStoppedError で拒まれたものは含めない)
        self._pressed_since_start = False

    def start(self, operations: Sequence[PrimitiveAction], owner: ActionOwner) -> int:
        """今の列を捨て (cancel と同じ)、operations を owner の列として持つ。捨てた操作の数を返す。

        最初の操作は、この後の advance で送る。operations が空なら、列を持たない状態になる。
        """
        discarded = self.cancel()
        self._pressed_since_start = False
        if self._pad_output.output_stopped:
            self._stop_after_output_stopped()
        if self._output_stopped or not operations:
            return discarded
        self._operations = list(operations)
        self._source = owner
        self._phase = _Phase.READY
        self._ready_at = self._clock.now()
        return discarded

    def cancel(self) -> int:
        """今の列の残り (実行中の操作を含む) を捨て、押しているボタンかスティックがあれば PadOutput.neutral で中立に
        戻す。捨てた操作の数を返す (列が無ければ 0 を返し、何も送らない)。"""
        discarded = len(self._operations)
        if discarded == 0:
            return 0
        if self._pressed_buttons or self._tilted_sticks:
            released_at = self._clock.now()
            self._pad_output.neutral()
            self._record_release(released_at)
        self._operations = []
        self._phase = _Phase.READY
        return discarded

    def advance(self) -> None:
        """clock.now() までに期限の来た送信 (押す・離す・次の操作の開始) を、モジュールの docstring の規則で行う。

        列の最後の操作を離し終えたら (wait なら待ち終えたら)、列を持たない状態にし、finished_owner にその持ち主を残す。
        """
        while self._operations and not self._output_stopped:
            if not self._advance_first(self._clock.now()):
                return

    def take_finished_owner(self) -> ActionOwner | None:
        """前回の呼び出し以降に、列を最後まで実行し終えたら、その列の持ち主を返す (捨てた列は含めない)。無ければ None。"""
        owner = self._finished_owner
        self._finished_owner = None
        return owner

    def take_blocked_buttons(self) -> tuple[str, ...]:
        """前回の呼び出し以降に、PadOutput.apply が押せずに外したボタン (pad_action.sort_buttons の順、重複なし) を返す。"""
        blocked = tuple(sort_buttons(self._blocked))
        self._blocked.clear()
        return blocked

    @property
    def owner(self) -> ActionOwner | None:
        """今の列の持ち主。列を持っていなければ None。"""
        return self._source if self._operations else None

    @property
    def remaining_count(self) -> int:
        """今の列の残りの操作の数 (実行中の操作を含む)。列を持っていなければ 0。"""
        return len(self._operations)

    @property
    def output_stopped(self) -> bool:
        """PadOutput.stop_output の後 (PadOutput.output_stopped が True か、apply が OutputStoppedError を出した後) なら True。"""
        return self._output_stopped or self._pad_output.output_stopped

    @property
    def stopped_before_sending(self) -> bool:
        """最後の start の後、押す操作を 1 つも送らないうちに出力の停止が分かったら True (モジュールの docstring)。"""
        return self._output_stopped and not self._pressed_since_start

    @property
    def last_release_t(self) -> float | None:
        """ボタンかスティックを最後に離した時刻 (モジュールの docstring)。まだ離していなければ None。"""
        return self._last_release_t

    def _advance_first(self, now: float) -> bool:
        """列の先頭の操作を 1 段進める。進めたら True、時刻がまだ来ていなければ False を返す。"""
        operation = self._operations[0]
        if self._phase is _Phase.READY:
            if isinstance(operation, WaitAction):
                self._phase = _Phase.WAITING
                self._deadline = self._ready_at + operation.seconds
                return True
            if now < self._press_start_time(operation):
                return False
            self._press(operation)
            return True
        if now < self._deadline:
            return False
        if self._phase is _Phase.PRESSING:
            self._release()
        else:
            self._ready_at = self._deadline
        if self._operations:
            self._finish_first()
        return True

    def _press_start_time(self, press: PressAction) -> float:
        """press を始めてよい時刻 (前の操作の後で、押し直しの間をあけた時刻) を返す。"""
        action = filter_action(
            press_pad_action(press), self._pad_output.allowed_buttons
        ).action
        times = [self._ready_at]
        times.extend(
            self._button_released_at[button] + TAP_RELEASE_GAP_S
            for button in action.held
            if button in self._button_released_at
        )
        times.extend(
            self._stick_neutral_at[stick] + TAP_RELEASE_GAP_S
            for stick in tilted_sticks(action)
            if stick in self._stick_neutral_at
        )
        return max(times)

    def _press(self, press: PressAction) -> None:
        """press のボタンを押してスティックを倒したレポートを送り、離す時刻を決める。"""
        action = press_pad_action(press)
        started_at = self._clock.now()
        try:
            result = self._pad_output.apply(action, source=self._source)
        except OutputStoppedError:
            self._stop_after_output_stopped()
            return
        except Exception:
            # 送信の失敗は PadOutput が ok=False の行に書く。押下状態は送ろうとした値に変わっているので、そのまま進める
            logger.debug("pressing failed", exc_info=True)
            result = filter_action(action, self._pad_output.allowed_buttons)
        self._pressed_since_start = True
        self._blocked.update(result.blocked_buttons)
        self._pressed_buttons = frozenset(result.action.held)
        self._tilted_sticks = tilted_sticks(result.action)
        seconds = TAP_PRESS_S if press.seconds is None else press.seconds
        self._deadline = started_at + seconds
        self._phase = _Phase.PRESSING

    def _release(self) -> None:
        """押しているボタンを離してスティックを中立にしたレポートを送り、次の操作を始めてよい時刻を決める。"""
        released_at = self._clock.now()
        try:
            self._pad_output.apply(NEUTRAL_ACTION, source=self._source)
        except OutputStoppedError:
            self._stop_after_output_stopped()
            return
        except Exception:
            logger.debug("releasing failed", exc_info=True)
        if self._pressed_buttons or self._tilted_sticks:
            self._record_release(released_at)
        self._ready_at = released_at

    def _finish_first(self) -> None:
        """列の先頭の操作を終えて外す。列が空になったら、実行し終えた列の持ち主を残す。"""
        self._operations.pop(0)
        self._phase = _Phase.READY
        if not self._operations:
            self._finished_owner = self._source

    def _record_release(self, released_at: float) -> None:
        """押していたボタンとスティックを released_at に離したとして記録する。"""
        for button in self._pressed_buttons:
            self._button_released_at[button] = released_at
        for stick in self._tilted_sticks:
            self._stick_neutral_at[stick] = released_at
        self._last_release_t = released_at
        self._pressed_buttons = frozenset()
        self._tilted_sticks = frozenset()

    def _stop_after_output_stopped(self) -> None:
        """出力が止まった後: 今の列を捨て、以後は列を持たない (PadOutput.stop_output が中立にしている)。"""
        self._output_stopped = True
        self._operations = []
        self._phase = _Phase.READY
        self._pressed_buttons = frozenset()
        self._tilted_sticks = frozenset()
