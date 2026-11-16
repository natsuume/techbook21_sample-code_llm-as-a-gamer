"""本体の画面の middle が返した操作の列を、照合の結果を確かめながら hardware.pad_output.PadOutput で送る
(SystemOperationRunner)。

操作は前から順に 1 つずつ送り、次の操作は前の操作を終えてから始める。操作を終えた時刻 (end_t) は次のとおり:
    press の短押し (seconds が None)   apply が戻った時刻 + hardware.pad_output.TAP_PRESS_S (離し終えた時刻)
    press の押し続け                    apply が戻った時刻 + seconds (離し終えた時刻)
    wait                                待ち始めた時刻 + seconds (何も送らない)
短押しは PadAction(taps=buttons)、押し続けは PadAction(held=buttons, hold_seconds={ボタン: seconds}) にして
(operation_pad_action)、PadOutput.apply(..., source="system") で送る。押せないボタンは PadOutput.apply が外す。

各操作を送る前に、照合の結果 (どの本体の画面か) を確かめる:
    先頭の操作      run に渡された照合の結果 (middle に渡したもの) の画面と、操作の screen を比べる
    2 つ目以降      前の操作を終えた時刻 + SETTLE_S より後に撮ったフレームの照合の結果を、JudgmentSource から待って比べる
照合の結果の None (見本のどの画面でもない) は、画面の名前 middle.system_screen_types.NO_SCREEN_NAME ("none") として比べる。
時刻はすべて run_clock.RunClock の t (照合の結果の frame_t と同じ時計) で扱う。

照合の結果を読む部分は JudgmentSource の Protocol で受け取り、照合の結果を作る部品には依存しない。結果は screen と
frame_t を持つもの (JudgmentResult) として扱う。
"""

import threading
from collections.abc import Callable
from typing import Protocol

from switch2_layers.hardware.pad_output import (
    TAP_PRESS_S,
    OutputStoppedError,
    PadOutput,
)
from switch2_layers.middle.system_screen_types import (
    NO_SCREEN_NAME,
    SystemCutReason,
    SystemOperation,
    SystemRunResult,
    SystemScreenOutput,
)
from switch2_layers.pad_action import PadAction
from switch2_layers.run_clock import RunClock

# 前の操作を終えてから、照合に使うフレームとして認めるまでの秒数 (入力の直後は、反映前や遷移の途中の画面になるため)
SETTLE_S = 0.5
# 2 つ目以降の操作の前に、照合の結果を待つ最大の秒数 (前の操作を終えた時刻 + SETTLE_S から数える)
JUDGMENT_WAIT_S = 3.0
# 照合の結果を待つ間に、止める合図を確かめる間隔 (秒)。JudgmentSource.wait_newer の 1 回の timeout_s の上限
JUDGMENT_POLL_S = 0.1
# 決定のボタン。操作の列の先頭の操作にだけ置ける
DECISION_BUTTON = "A"

# 止める合図 stop が立つか seconds 秒たつまで待ち、stop が立っていれば True を返す関数 (既定は threading.Event.wait)
WaitOrStop = Callable[[threading.Event, float], bool]


class JudgmentResult(Protocol):
    """照合の結果 1 つ。

    screen    照合の結果の画面の名前。見本のどの画面でもなければ None
    frame_t   照合したフレームの時刻 (run_clock.RunClock の t)
    """

    @property
    def screen(self) -> str | None:
        """照合の結果の画面の名前。見本のどの画面でもなければ None。"""
        ...

    @property
    def frame_t(self) -> float:
        """照合したフレームの時刻。"""
        ...


class JudgmentSource(Protocol):
    """最新の照合の結果を読む部品。複数のスレッドから呼べるもの。"""

    def latest(self) -> JudgmentResult | None:
        """今持っている、frame_t がいちばん新しい照合の結果を返す。まだ無ければ None。"""
        ...

    def wait_newer(
        self, after_frame_t: float, timeout_s: float
    ) -> JudgmentResult | None:
        """frame_t が after_frame_t より大きい照合の結果を返す。

        持っていればすぐ返し、無ければ届くまで最大 timeout_s 秒待つ。届かなければ None を返す。
        timeout_s が 0 以下ならすぐ返す。
        """
        ...


def operation_pad_action(operation: SystemOperation) -> PadAction:
    """press の操作 operation を、PadOutput.apply に渡す PadAction にする。

    seconds が None (短押し) なら PadAction(taps=frozenset(buttons))、数 (押し続け) なら
    PadAction(held=frozenset(buttons), hold_seconds={各ボタン: seconds})。スティックは倒さない。
    operation が wait なら ValueError にする (wait は何も送らない)。
    """
    if operation.kind != "press":
        raise ValueError(f"only a press becomes a pad action: {operation!r}")
    buttons = frozenset(operation.buttons)
    if operation.seconds is None:
        return PadAction(taps=buttons)
    return PadAction(
        held=buttons, hold_seconds={button: operation.seconds for button in buttons}
    )


def _screen_name(screen: str | None) -> str:
    """照合の結果の画面 screen を、操作の screen と比べる名前にする (None は NO_SCREEN_NAME)。"""
    return NO_SCREEN_NAME if screen is None else screen


class SystemOperationRunner:
    """操作の列を、照合の結果を確かめながら送る。run は 1 つのスレッドからだけ呼ぶ。

    pad_output     操作の送信先 (ボタンの制限を含む)
    judgments      最新の照合の結果を読む部品
    clock          実行の経過秒の時計 (照合の結果の frame_t と同じ時計)
    wait_or_stop   止める合図を見ながら待つ関数 (WaitOrStop)。テストでは偽の時計を進める関数を渡す
    """

    def __init__(
        self,
        pad_output: PadOutput,
        judgments: JudgmentSource,
        clock: RunClock,
        wait_or_stop: WaitOrStop = threading.Event.wait,
    ) -> None:
        """部品を持つ。ここでは何も送らず、待たない。"""
        self.pad_output = pad_output
        self.judgments = judgments
        self.clock = clock
        self.wait_or_stop = wait_or_stop

    def run(
        self,
        output: SystemScreenOutput,
        judged_screen: str | None,
        stop: threading.Event,
    ) -> SystemRunResult:
        """output.operations を前から順に送り、結果を返す。

        judged_screen は、output を決めるときに middle に渡した照合の結果の画面の名前 (見本のどの画面でもなければ None)。
        stop は止める合図。

        操作ごと (位置 i。0 から数える) に、次の順で行う:
            1. stop が立っていれば、STOPPED で打ち切る
            2. i が 0 なら、操作の screen と judged_screen の名前を比べ、違えば SCREEN_MISMATCH で打ち切る
            3. i が 1 以上なら:
               a. 操作の buttons に DECISION_BUTTON があれば、照合の結果を待たずに DECISION_NOT_FIRST で打ち切る
                  (押し続けと同時押しの中の A も同じ)
               b. after_frame_t = 前の操作を終えた時刻 + SETTLE_S とする。clock.now() が after_frame_t になるまで
                  wait_or_stop(stop, 残りの秒) で待つ。stop が立ったら STOPPED で打ち切る
               c. clock.now() が after_frame_t + JUDGMENT_WAIT_S を過ぎるまで、judgments.wait_newer(after_frame_t,
                  timeout_s) を繰り返し呼んで、frame_t が after_frame_t より大きい照合の結果を待つ。timeout_s は
                  JUDGMENT_POLL_S と残りの秒の小さい方 (0 以上) にし、呼ぶ前と戻った後に stop を確かめる。stop が
                  立っていたら、結果が届いていても STOPPED で打ち切る
               d. 結果が届かなければ NO_JUDGMENT、届いた結果の画面の名前が操作の screen と違えば SCREEN_MISMATCH で
                  打ち切る
            4. press なら pad_output.apply(operation_pad_action(操作), source="system") で送り、戻り値の blocked_buttons を
               結果の blocked_buttons に足す。wait なら何も送らない。どちらも、モジュールの docstring の時刻を、この操作を
               終えた時刻にする
        最後の操作まで送ったら、clock.now() が最後の操作を終えた時刻になるまで wait_or_stop で待ってから
        SystemRunResult(sent_count=操作の数, cut_index=None, cut_reason=None, blocked_buttons) を返す。この待ちの間に
        stop が立ったら、待つのをやめてすぐに同じ結果を返す (送っていない操作は無いため、打ち切りにしない)。
        操作が 0 個なら、何も送らず待たずに SystemRunResult(0, None, None, frozenset()) を返す。

        位置 i で打ち切ったら、待たずに SystemRunResult(sent_count=i, cut_index=i, cut_reason=理由, blocked_buttons) を
        返す (その操作から後は送らない)。
        apply が hardware.pad_output.OutputStoppedError を出したら (全体を止めるときに出力が止められた)、その操作は
        送らなかったものとして STOPPED で打ち切る。apply のほかの例外 (送信の失敗) は、そのまま外に出す。

        先頭の操作は、渡された judged_screen とだけ比べて送り、新しいフレームの照合の結果を待たない。そのため呼び出し側は、
        次の列を決める入力の照合の結果を、前の列の最後の操作を終えた時刻 + SETTLE_S より後に撮ったフレームのものにする
        前提とする (反映前の画面の照合の結果と一致しただけで、先頭の A を送らないため)。
        """
        operations = output.operations
        blocked: set[str] = set()
        # 前の操作を終えた時刻 (先頭の操作の確かめでは使わない)
        end_t = 0.0
        for index, operation in enumerate(operations):
            reason = self._check_before(index, operation, judged_screen, end_t, stop)
            if reason is not None:
                return _cut(index, reason, blocked)
            if operation.kind == "press":
                try:
                    filtered = self.pad_output.apply(
                        operation_pad_action(operation), source="system"
                    )
                except OutputStoppedError:
                    return _cut(index, SystemCutReason.STOPPED, blocked)
                blocked.update(filtered.blocked_buttons)
                press_s = (
                    TAP_PRESS_S if operation.seconds is None else operation.seconds
                )
                end_t = self.clock.now() + press_s
            else:
                # wait は何も送らない。秒数は、次の操作の前 (または最後の待ち) で待つ
                end_t = self.clock.now() + (operation.seconds or 0.0)
        if operations:
            self._wait_until(end_t, stop)
        return SystemRunResult(
            sent_count=len(operations),
            cut_index=None,
            cut_reason=None,
            blocked_buttons=frozenset(blocked),
        )

    def _check_before(
        self,
        index: int,
        operation: SystemOperation,
        judged_screen: str | None,
        previous_end_t: float,
        stop: threading.Event,
    ) -> SystemCutReason | None:
        """位置 index の操作を送る前の確かめ (run の 1〜3) を行い、打ち切るならその理由、送るなら None を返す。"""
        if stop.is_set():
            return SystemCutReason.STOPPED
        if index == 0:
            if operation.screen != _screen_name(judged_screen):
                return SystemCutReason.SCREEN_MISMATCH
            return None
        if DECISION_BUTTON in operation.buttons:
            return SystemCutReason.DECISION_NOT_FIRST
        after_frame_t = previous_end_t + SETTLE_S
        if self._wait_until(after_frame_t, stop):
            return SystemCutReason.STOPPED
        judgment = self._wait_judgment(after_frame_t, stop)
        if stop.is_set():
            return SystemCutReason.STOPPED
        if judgment is None:
            return SystemCutReason.NO_JUDGMENT
        if operation.screen != _screen_name(judgment.screen):
            return SystemCutReason.SCREEN_MISMATCH
        return None

    def _wait_until(self, target_t: float, stop: threading.Event) -> bool:
        """clock.now() が target_t になるまで wait_or_stop で待つ。stop が立っていれば True を返す。"""
        remaining_s = target_t - self.clock.now()
        if remaining_s <= 0:
            return stop.is_set()
        return self.wait_or_stop(stop, remaining_s)

    def _wait_judgment(
        self, after_frame_t: float, stop: threading.Event
    ) -> JudgmentResult | None:
        """frame_t が after_frame_t より大きい照合の結果を、after_frame_t + JUDGMENT_WAIT_S まで待って返す (run の 3c)。

        届かないか、stop が立ったら None を返す (呼び出し側が stop を確かめ直して理由を決める)。
        """
        deadline_t = after_frame_t + JUDGMENT_WAIT_S
        while not stop.is_set():
            remaining_s = deadline_t - self.clock.now()
            judgment = self.judgments.wait_newer(
                after_frame_t, max(0.0, min(JUDGMENT_POLL_S, remaining_s))
            )
            if judgment is not None or stop.is_set():
                return judgment
            if remaining_s <= 0 or self.clock.now() >= deadline_t:
                return None
        return None


def _cut(index: int, reason: SystemCutReason, blocked: set[str]) -> SystemRunResult:
    """位置 index で、理由 reason で打ち切ったときの結果を返す (blocked は、それまでに外したボタン)。"""
    return SystemRunResult(
        sent_count=index,
        cut_index=index,
        cut_reason=reason,
        blocked_buttons=frozenset(blocked),
    )
