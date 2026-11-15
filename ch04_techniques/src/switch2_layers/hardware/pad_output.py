"""PadAction を 8 バイトのレポートにして、コントローラーのブリッジに送る。

押下状態の 2 層:
    held      apply で置き換える、押し続けるボタンとスティック
    tapping   短押しの最中のボタンと、離す時刻 (clock の値)
送るレポートは 2 層を合わせた押下状態を switch2_remote.controller_state.build_report にしたもの。
短押しは、押すレポートの送信が戻ってから TAP_PRESS_S 秒後に、タイマースレッドが release_due_taps で離す。
ボタンの制限 (buttons.filter_action) は apply の中で、送る直前に行う。十字キーの相反する方向は、例外にせず両方とも外して送る。

送信先は set_report(report) を持つもの (ReportSender)。run では switch2_remote.bridge_supervisor.BridgeSupervisor、
テストでは偽物を渡す。apply は surface のスレッド、release_due_taps はタイマースレッド、neutral と stop_output は
終了処理のスレッドから呼ぶため、状態の変更と送信は 1 つのロックの中で行う。
"""

import time
from collections.abc import Callable, Collection
from typing import Protocol

from switch2_layers.buttons import FilteredAction
from switch2_layers.pad_action import PadAction
from switch2_layers.records.operation_log import OperationEvent

TAP_PRESS_S = 0.12  # 短押しで押している時間 (秒)
TIMER_INTERVAL_S = 0.01  # タイマースレッドが離す時刻を過ぎた短押しを確かめる間隔 (秒)


class ReportSender(Protocol):
    """レポートの送信先。run では switch2_remote.bridge_supervisor.BridgeSupervisor を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。送れなければ例外を出す。"""
        ...


class OutputStoppedError(Exception):
    """stop_output() の後に apply を呼んだ。操作は送っていない。"""


class PadOutput:
    """押下状態と短押しを持ち、変化のたびに合わせたレポートを sender.set_report で送る。

    sender            レポートの送信先
    allowed_buttons   押せるボタンの集合 (buttons.allowed_buttons の値)。apply が送る直前にこれで制限する
    clock             短押しの離す時刻に使う単調な時計 (秒。既定 time.monotonic)
    on_operation      apply・neutral・stop_output で送ろうとするたびに、送信の成否が決まった後で OperationEvent を
                      受け取る関数 (run では records.operation_log.OperationLog.write)。None なら呼ばない。
                      短押しを離すレポート (release_due_taps) では呼ばない
    start_timer       True (既定) なら TIMER_INTERVAL_S ごとに release_due_taps を呼ぶタイマースレッド (daemon) を起動する。
                      False ならテストから release_due_taps を直接呼ぶ
    作るときにはレポートを送らない。
    """

    def __init__(
        self,
        sender: ReportSender,
        allowed_buttons: Collection[str],
        clock: Callable[[], float] = time.monotonic,
        on_operation: Callable[[OperationEvent], None] | None = None,
        start_timer: bool = True,
    ) -> None:
        """何も押していない状態で作る。"""
        raise NotImplementedError

    def apply(self, action: PadAction) -> FilteredAction:
        """action をボタンの制限に通してから、押下状態を置き換えて送る。送った操作と、制限で外したボタンを返す。

        ロックの中で次を行う:
            1. stop_output() の後なら、何も送らず (on_operation も呼ばず) OutputStoppedError を出す
            2. buttons.filter_action(action, allowed_buttons) で押せないボタンを外す
            3. 十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) は、held と taps のそれぞれで、両方あれば両方とも外す
               (例外にしない。外した方向は blocked_buttons に入れない)
            4. スティックの各値は -1.0〜1.0 に収める (nan は 0.0 にする)
            5. held を 2〜4 の後の held とスティックに置き換え、taps のうち短押しの最中でなく held にも無いボタンを
               短押しとして始める (離す時刻 = clock() + TAP_PRESS_S)。短押しの最中のボタンはそのまま続ける
            6. held と短押しの最中のボタンとスティックを合わせたレポートを送る。短押しの十字キーの方向と held の
               反対の方向が重なったら、短押しを優先し、送るレポートからだけ held の方向を外す
            7. 送信が成功したら、この apply で始めた短押しの離す時刻を、送信が戻った後の clock() + TAP_PRESS_S に置き直す
            8. on_operation に OperationEvent(source="apply", action=2〜4 の後の操作, ok, error) を渡す
        戻り値は FilteredAction(action=2〜4 の後の操作, blocked_buttons=2 で外したボタン)。
        送信の例外は、on_operation に ok=False で渡してからそのまま出す (状態は 5 で置き換えたまま)。
        変化が無くても送る。
        """
        raise NotImplementedError

    def release_due_taps(self) -> None:
        """離す時刻を過ぎた (離す時刻 <= clock()) 短押しを外し、外したものがあるときだけレポートを送る。

        送信の例外 (Exception) は外に出さず、説明を last_error に記録する (タイマースレッドから呼ぶため)。
        """
        raise NotImplementedError

    def neutral(self) -> None:
        """held と短押しを空にし、中立のレポート (controller_state.NEUTRAL_REPORT) を送る。

        on_operation に OperationEvent(source="neutral", action=NEUTRAL_ACTION, ok, error) を渡す。
        送信の例外 (Exception) は外に出さず、説明を last_error に記録する。stop_output の後でも送る。
        """
        raise NotImplementedError

    def stop_output(self) -> None:
        """以後の apply を止めたうえで、neutral と同じく中立のレポートを送る。止めた状態は戻さない。

        on_operation には source="stop" で渡す。送信の例外は外に出さず、last_error に記録する。
        呼ぶたびに中立のレポートを送る。
        """
        raise NotImplementedError

    @property
    def allowed_buttons(self) -> frozenset[str]:
        """押せるボタンの集合 (作るときに受け取ったもの)。"""
        raise NotImplementedError

    @property
    def last_error(self) -> str | None:
        """release_due_taps・neutral・stop_output の送信の最後の失敗の説明。無ければ None。"""
        raise NotImplementedError

    def close(self) -> None:
        """タイマースレッドを止め、終わるのを待つ。タイマースレッドが無ければ何もしない。レポートは送らない。"""
        raise NotImplementedError
