"""主ループと監視の操作の列を、1 つずつ PadOutput に送る。

展開 (expand_actions。I/O を持たない):
    procedure は procedures.expand_procedure で、macro は登録した操作の列 (その中の procedure も展開する) で置き換え、
    PressAction と WaitAction だけの列 (PrimitiveAction の列) にする。主ループに知らせる残りの操作の数は、この列の数
実行 (ActionExecutor):
    列の操作を前から順に 1 つずつ実行し、次の操作は前の操作を離し終えてから始める。待ちはしない (run のループが回りごとに
    advance を呼び、今の時刻までに来た期限の送信をする)。時刻は clock (実行の経過秒) で測る。
    press   ボタンを押してスティックを倒したレポートを送り、その送信が戻った時刻から押す秒数 (seconds。None なら
            hardware.pad_output.TAP_PRESS_S) たったら、ボタンを離してスティックを中立にしたレポートを送る。
            前の操作で押したボタンをもう一度押すときは、離したレポートの送信が戻った時刻から
            hardware.pad_output.TAP_RELEASE_GAP_S 秒あけてから押す (間をあけずに押し直すと、ゲームから見て新しい押下に
            ならないため)
    wait    何も押さずに seconds 秒待つ (前の操作を離した時刻から数える。列の最初なら始めた時刻から)
    押す・離すの送信は PadOutput.apply で行う。押せないボタンは apply が外し、外したボタンを take_blocked_buttons で返す。
    列を途中で捨てる (cancel・start) ときだけ、PadOutput.neutral で押下状態を中立に戻す。
操作の記録:
    PadOutput.apply を呼ぶたびに、record_operation に OperationEvent(source=列の持ち主 (orchestrator か monitor),
    action=apply が返した操作 (ボタンの制限を通した後), ok=True) を渡す。apply が例外を出したら、action を
    buttons.filter_action(渡した操作, pad_output.allowed_buttons).action、ok=False、error=error_text.describe_exception(例外)
    にして渡し、例外は外に出さない (その操作は時刻どおりに進める)。
    apply が OutputStoppedError を出したら (全体を止める処理が出力を止めた)、記録せずに、今の列とこの後に始める列を
    すべて捨てる (以後は何も送らない)。
OrchestratorController の run のループのスレッドからだけ呼ぶ (ロックを持たない)。
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.orchestrator.decision_types import Action, PressAction, WaitAction
from switch2_layers.records.operation_log import OperationEvent
from switch2_layers.run_clock import RunClock

# 展開した後の操作 (押す操作と待ち)
PrimitiveAction = PressAction | WaitAction
# 操作の列の持ち主。operations.jsonl の source に書く
ActionOwner = Literal["orchestrator", "monitor"]


def expand_actions(
    actions: Sequence[Action], macros: Mapping[str, tuple[Action, ...]]
) -> tuple[PrimitiveAction, ...]:
    """actions の procedure と macro を展開した、PressAction と WaitAction だけの列を返す (モジュールの docstring)。

    macros はマクロの名前 → 登録した操作の列。マクロの中の procedure も展開する。macros に無いマクロの名前、または
    マクロの中に MacroAction があれば ValueError (decision_parser.parse_decision が先に確かめる)。
    """
    raise NotImplementedError


class ActionExecutor:
    """操作の列を 1 つ持ち、advance のたびに、今の時刻までに来た押す・離すを送る。

    pad_output         操作の送信 (ボタンの制限を含む)
    clock              押す秒数と待ちの秒数を測る時計 (PadOutput の clock と同じ monotonic から作ったもの)
    record_operation   PadOutput.apply を呼ぶたびに OperationEvent を受け取る関数 (run では OperationLog.write)
    """

    def __init__(
        self,
        pad_output: PadOutput,
        clock: RunClock,
        record_operation: Callable[[OperationEvent], None],
    ) -> None:
        """列を持たない状態で作る。レポートは送らない。"""
        raise NotImplementedError

    def start(self, operations: Sequence[PrimitiveAction], owner: ActionOwner) -> int:
        """今の列を捨て (cancel と同じ)、operations を owner の列として持つ。捨てた操作の数を返す。

        最初の操作は、この後の advance で送る。operations が空なら、列を持たない状態になる。
        """
        raise NotImplementedError

    def cancel(self) -> int:
        """今の列の残り (実行中の操作を含む) を捨て、押しているボタンかスティックがあれば PadOutput.neutral で中立に
        戻す。捨てた操作の数を返す (列が無ければ 0 を返し、何も送らない)。"""
        raise NotImplementedError

    def advance(self) -> None:
        """clock.now() までに期限の来た送信 (押す・離す・次の操作の開始) を、モジュールの docstring の規則で行う。

        列の最後の操作を離し終えたら (wait なら待ち終えたら)、列を持たない状態にし、finished_owner にその持ち主を残す。
        """
        raise NotImplementedError

    def take_finished_owner(self) -> ActionOwner | None:
        """前回の呼び出し以降に、列を最後まで実行し終えたら、その列の持ち主を返す (捨てた列は含めない)。無ければ None。"""
        raise NotImplementedError

    def take_blocked_buttons(self) -> tuple[str, ...]:
        """前回の呼び出し以降に、PadOutput.apply が押せずに外したボタン (pad_action.sort_buttons の順、重複なし) を返す。"""
        raise NotImplementedError

    @property
    def owner(self) -> ActionOwner | None:
        """今の列の持ち主。列を持っていなければ None。"""
        raise NotImplementedError

    @property
    def remaining_count(self) -> int:
        """今の列の残りの操作の数 (実行中の操作を含む)。列を持っていなければ 0。"""
        raise NotImplementedError

    @property
    def output_stopped(self) -> bool:
        """PadOutput.apply が OutputStoppedError を出した後なら True。"""
        raise NotImplementedError
