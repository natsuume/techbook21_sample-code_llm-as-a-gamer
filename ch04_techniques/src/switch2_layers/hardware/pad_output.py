"""PadAction を 8 バイトのレポートにして、コントローラーのブリッジに送る。

押下状態:
    held             apply で置き換える、押し続けている (レポートで押す) ボタンとスティック
    hold_deadlines   held のうち秒数を指定したボタンと、離す時刻 (期限。clock の値)
    pending_holds    間をあけてから始める押し続けのボタンと、始める時刻 (clock の値) と秒数。レポートには含めない
    tapping          短押しの最中のボタンと、離す時刻 (clock の値)
    pending_taps     いったん離してから始める短押しのボタンと、始める時刻 (clock の値)。レポートには含めない
送るレポートは held と tapping を合わせた押下状態を switch2_remote.controller_state.build_report にしたもの。
短押しは、押すレポートの送信が戻ってから TAP_PRESS_S 秒後に、タイマースレッドが release_due_taps で離す。

押し続け (PadAction.held) は、秒数 (PadAction.hold_seconds) を指定したボタンなら、押すレポートの送信が戻ってから
その秒数後に、次の apply を待たずにタイマースレッドが release_due_holds で離す。秒数を指定していないボタンは、次の
apply まで押したままにする。押し続けている (held にある) ボタンを次の apply も押し続けに指定したら、離さずに、期限を
「その apply の送信が戻った時刻 + 新しい秒数」に置き換える (新しい指定に秒数が無ければ期限を外す)。期限を過ぎても
タイマーがまだ離していないボタンは、held にある間は押し続けているものとして扱う。スティックは次の apply まで倒したままにする。

同じボタンを押し直すときは、離してから TAP_RELEASE_GAP_S 秒の間をあける。間をあけずに押し直すと、ゲームから見て
新しい押下にならず、短押しが失われるため。ボタンごとに最後に離した時刻 (last_released。離すレポートの送信が戻った
時刻) を持ち、次のように短押しを始める:
    - 直前に送ったレポートで押されていたボタン (held か、まだ離していない短押し) の短押しは、すぐには押さない。
      まずそのボタンを離したレポートを送り、送信が戻ってから TAP_RELEASE_GAP_S 秒後に、タイマースレッドが
      start_due_taps で短押しを始める
    - 離してから TAP_RELEASE_GAP_S 秒たっていないボタン (タイマーが短押しや押し続けを離した直後など) の短押しは、
      「最後に離した時刻 + TAP_RELEASE_GAP_S」に start_due_taps で始める
期限で離したボタン (release_due_holds) は、押し続けも同じく間をあける。離してから TAP_RELEASE_GAP_S 秒たっていない
押し続けは、「期限で離した時刻 + TAP_RELEASE_GAP_S」にタイマースレッドが start_due_holds で始め、その送信が戻った
時刻から秒数を数える。
例 (A を押し続けた後に、A の短押しを求めた場合。s は各レポートの送信が戻った時刻):
    apply(held={A})              → A を押したレポート
    apply(taps={A}) (s1 に戻る)   → A を離したレポート (ほかのボタンとスティックは、この apply の値)
    start_due_taps (s1 + 0.1 以降) → A を押したレポート (s2 に戻る)
    release_due_taps (s2 + 0.12 以降) → A を離したレポート
短押しの最中に同じボタンの短押しをもう一度求めた場合も、同じく、いったん離してから押し直す。

ボタンの制限 (buttons.filter_action) は apply の中で、送る直前に行う。秒数が有限の正の値でない押し続けのボタンは
押さずに外し、MAX_HOLD_SECONDS を超えた秒数は MAX_HOLD_SECONDS にする (normalize_hold_seconds)。十字キーの相反する
方向は、例外にせず両方とも外して送る。

送信先は set_report(report) を持つもの (ReportSender)。run では switch2_remote.bridge_supervisor.BridgeSupervisor、
テストでは偽物を渡す。apply は surface のスレッドと本体の画面の操作のスレッド (system_screen.system_operation_runner)、
start_due_taps・release_due_taps・start_due_holds・release_due_holds はタイマースレッド、neutral と stop_output は
終了処理のスレッドから呼ぶため、状態の変更と送信は 1 つのロックの中で行う。
"""

import logging
import math
import threading
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from typing import Protocol

from switch2_remote.controller_state import (
    DPAD_DIRECTIONS,
    NEUTRAL_REPORT,
    PadState,
    build_report,
)

from switch2_layers.buttons import FilteredAction, filter_action
from switch2_layers.error_text import describe_exception
from switch2_layers.pad_action import NEUTRAL_ACTION, PadAction
from switch2_layers.records.operation_log import (
    ApplySource,
    OperationEvent,
    OperationSource,
)

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

TAP_PRESS_S = 0.12  # 短押しで押している時間 (秒)
# 同じボタンを離してから押し直すまでの間隔 (秒)
TAP_RELEASE_GAP_S = 0.1
# タイマースレッドが、始める時刻と離す時刻を過ぎた短押しと押し続けを確かめる間隔 (秒)
TIMER_INTERVAL_S = 0.01
# 十字キーの方向 → 反対の方向
OPPOSITE_DIRECTION = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
# 押し続けの秒数の上限 (秒)。これを超えた秒数は、この値として扱う
MAX_HOLD_SECONDS = 60.0


class ReportSender(Protocol):
    """レポートの送信先。run では switch2_remote.bridge_supervisor.BridgeSupervisor を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。送れなければ例外を出す。"""
        ...


class OutputStoppedError(Exception):
    """stop_output() の後に apply を呼んだ。操作は送っていない。"""


def remove_conflicting_directions(buttons: frozenset[str]) -> frozenset[str]:
    """buttons に十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) が両方あれば、両方とも外した集合を返す。"""
    removed = {
        direction
        for direction, opposite in OPPOSITE_DIRECTION.items()
        if direction in buttons and opposite in buttons
    }
    return buttons - removed


def normalize_hold_seconds(
    held: Collection[str], hold_seconds: Mapping[str, float]
) -> tuple[frozenset[str], dict[str, float]]:
    """押し続けるボタン held と秒数 hold_seconds を、送れる形にして (held, 秒数) を返す。引数は変えない。

    - 秒数が有限の正の値でない (0 以下・nan・無限大) ボタンは、held から外す (例外にしない)
    - MAX_HOLD_SECONDS を超えた秒数は MAX_HOLD_SECONDS にする
    - held に無いボタンの秒数は捨てる
    秒数の無い held のボタンは held に残す (次の apply まで押し続ける)。戻り値の秒数は、残した held のボタンのうち
    秒数のあるものだけを持つ。
    """
    kept: set[str] = set()
    seconds_of_kept: dict[str, float] = {}
    for button in held:
        if button not in hold_seconds:
            kept.add(button)
            continue
        seconds = float(hold_seconds[button])
        if not math.isfinite(seconds) or seconds <= 0.0:
            continue
        kept.add(button)
        seconds_of_kept[button] = min(seconds, MAX_HOLD_SECONDS)
    return frozenset(kept), seconds_of_kept


def clamp_stick(value: tuple[float, float]) -> tuple[float, float]:
    """スティックの (x, y) の各値を -1.0〜1.0 に収める。nan は 0.0 にする。"""
    return (_clamp_axis(value[0]), _clamp_axis(value[1]))


def _clamp_axis(value: float) -> float:
    """スティックの 1 軸の値を -1.0〜1.0 に収める。nan は 0.0 にする。"""
    if math.isnan(value):
        return 0.0
    return min(1.0, max(-1.0, float(value)))


class PadOutput:
    """押下状態と短押しを持ち、変化のたびに合わせたレポートを sender.set_report で送る。

    sender            レポートの送信先
    allowed_buttons   押せるボタンの集合 (buttons.allowed_buttons の値)。apply が送る直前にこれで制限する
    clock             短押しと押し続けの始める時刻・離す時刻に使う単調な時計 (秒。既定 time.monotonic)
    on_operation      apply・neutral・stop_output で送ろうとするたびに、送信の成否が決まった後で OperationEvent を
                      受け取る関数 (run では records.operation_log.OperationLog.write)。None なら呼ばない。
                      タイマースレッドが送るレポート (start_due_taps・release_due_taps・start_due_holds・
                      release_due_holds) では呼ばない
    start_timer       True (既定) なら TIMER_INTERVAL_S ごとに start_due_taps・release_due_taps・start_due_holds・
                      release_due_holds をこの順に呼ぶタイマースレッド (daemon) を起動する。False ならテストから直接呼ぶ
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
        self.sender = sender
        self._allowed_buttons = frozenset(allowed_buttons)
        self.clock = clock
        self.on_operation = on_operation
        self._lock = threading.Lock()
        self._held = PadState()
        # 押し続けのボタン → 離す時刻 (期限)
        self._hold_deadlines: dict[str, float] = {}
        # 間をあけてから始める押し続けのボタン → (始める時刻, 秒数。無ければ None)
        self._pending_holds: dict[str, tuple[float, float | None]] = {}
        self._tapping: dict[str, float] = {}
        self._pending_taps: dict[str, float] = {}
        self._last_released: dict[str, float] = {}
        # 期限で離したボタン → 離した時刻 (押し続けを始める前に間をあけるため)
        self._deadline_released: dict[str, float] = {}
        self._last_error: str | None = None
        self._output_stopped = False
        self._stop_timer = threading.Event()
        self._timer_thread: threading.Thread | None = None
        if start_timer:
            self._timer_thread = threading.Thread(
                target=self._run_timer, name="pad-output-timer", daemon=True
            )
            self._timer_thread.start()

    def apply(
        self, action: PadAction, *, source: ApplySource = "apply"
    ) -> FilteredAction:
        """action をボタンの制限に通してから、押下状態を置き換えて送る。送った操作と、制限で外したボタンを返す。

        source は記録 (OperationEvent と operations.jsonl の行の source) に入れる、送った処理の名前。surface の操作は
        "apply" (既定)、本体の画面の操作 (system_screen.system_operation_runner) は "system"。source は記録だけに使い、
        送り方は変えない。

        ロックの中で次を行う:
            1. stop_output() の後なら、何も送らず (on_operation も呼ばず) OutputStoppedError を出す
            2. buttons.filter_action(action, allowed_buttons) で押せないボタンを外す
            3. normalize_hold_seconds で押し続けを整える。秒数が有限の正の値でない (0 以下・nan・無限大) ボタンは
               held から外し (例外にしない。blocked_buttons に入れない。taps にもあれば短押しは残す)、
               MAX_HOLD_SECONDS を超えた秒数は MAX_HOLD_SECONDS にし、held に無いボタンの秒数は捨てる
            4. 十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) は、held と taps のそれぞれで、両方あれば両方とも外す
               (例外にしない。外した方向は blocked_buttons に入れない。held から外した方向の秒数も捨てる)
            5. スティックの各値は -1.0〜1.0 に収める (nan は 0.0 にする)
            6. 直前に送ったレポートで押されていたボタン (古い held と、tapping にあるボタン) を pressed_before とする。
               2〜5 の後の held (新しい held) の各ボタンを次のように扱う (now = clock()):
               - 古い held にあるボタン (期限を過ぎてタイマーがまだ離していないものを含む): 離さずに押し続ける
                 (期限は 9 で決め直す)
               - 既に pending_holds にあるボタン: 始める時刻は変えず、秒数をこの操作の値にする
               - 期限で離してから TAP_RELEASE_GAP_S 秒たっていないボタン: pending_holds に入れ、始める時刻を
                 「期限で離した時刻 + TAP_RELEASE_GAP_S」にする (このレポートでは押さない)
               - それ以外のボタンは、このレポートで押す
               新しい held に無いボタンの pending_holds と期限は取り消す。held を、新しい held のうち pending_holds に
               入れなかったボタンとスティックに置き換える。新しい held にあるボタンの pending_taps は取り消す
            7. taps の各ボタンを名前の順に扱う:
               - 新しい held にあるボタン (pending_holds に入れたものを含む) は何もしない (押し続ける)
               - 既に pending_taps にあるボタンは何もしない (始める時刻は変えない)
               - pressed_before にあるボタン (押し続けから短押しへの切り替えと、短押しの最中の再度の短押し):
                 tapping にあれば外し (このレポートで離す)、pending_taps に入れる (始める時刻は 9 で決める)
               - 最後に離した時刻から TAP_RELEASE_GAP_S 秒たっていないボタン: pending_taps に入れ、始める時刻を
                 「最後に離した時刻 + TAP_RELEASE_GAP_S」にする
               - 十字キーの方向で、反対の方向が tapping か pending_taps にあるボタンは捨てる (始めない)
               - それ以外のボタンは、すぐに短押しとして始める (tapping に入れる。離す時刻は 9 で決める)
            8. held と tapping のボタンとスティックを合わせたレポートを送る (pending_holds と pending_taps は含めない。
               7 で離すと決めたボタンは、このレポートで離れる)。短押しの十字キーの方向と held の反対の方向が重なったら、
               短押しを優先し、送るレポートからだけ held の方向を外す
            9. 送信が成功したら、送信が戻った時刻 sent_at = clock() として、この apply で始めた短押しの離す時刻を
               sent_at + TAP_PRESS_S、pressed_before から pending_taps に入れたボタンの始める時刻を
               sent_at + TAP_RELEASE_GAP_S、このレポートで離したボタン (古い held から外れたものと、7 で tapping から
               外したもの) の最後に離した時刻を sent_at にする。held の各ボタンの期限は、秒数があれば sent_at + 秒数に
               置き換え、無ければ外す (次の apply まで押し続ける)
            10. on_operation に OperationEvent(source=source, action=2〜5 の後の操作, ok, error) を渡す
        戻り値は FilteredAction(action=2〜5 の後の操作, blocked_buttons=2 で外したボタン)。操作の hold_seconds は、
        3 と 4 で整えた後の秒数 (held のボタンのうち秒数のあるものだけ)。
        送信の例外は、on_operation に ok=False で渡してからそのまま出す。状態は 6・7 で変えたままにし、9 の時刻は
        送信の前の clock() から決める。
        変化が無くても送る。
        """
        with self._lock:
            if self._output_stopped:
                raise OutputStoppedError("pad output was stopped")
            filtered = filter_action(action, self._allowed_buttons)
            valid_held, hold_seconds = normalize_hold_seconds(
                filtered.action.held, filtered.action.hold_seconds
            )
            new_held = remove_conflicting_directions(valid_held)
            sent_action = replace(
                filtered.action,
                held=new_held,
                taps=remove_conflicting_directions(frozenset(filtered.action.taps)),
                left_stick=clamp_stick(filtered.action.left_stick),
                right_stick=clamp_stick(filtered.action.right_stick),
                hold_seconds={
                    button: seconds
                    for button, seconds in hold_seconds.items()
                    if button in new_held
                },
            )
            now = self.clock()
            pressed_before = self._held.buttons | frozenset(self._tapping)
            pressed_held = self._replace_holds_locked(sent_action, now)
            self._held = PadState(
                pressed_held,
                {"LS": sent_action.left_stick, "RS": sent_action.right_stick},
            )
            for button in list(self._pending_taps):
                if button in new_held:
                    del self._pending_taps[button]
            released = set(pressed_before - new_held)
            started: list[str] = []
            waiting_after_release: list[str] = []
            for button in sorted(sent_action.taps):
                if button in new_held or button in self._pending_taps:
                    continue
                if button in pressed_before:
                    self._tapping.pop(button, None)
                    released.add(button)
                    self._pending_taps[button] = now + TAP_RELEASE_GAP_S
                    waiting_after_release.append(button)
                    continue
                released_at = self._last_released.get(button)
                if released_at is not None and now - released_at < TAP_RELEASE_GAP_S:
                    self._pending_taps[button] = released_at + TAP_RELEASE_GAP_S
                    continue
                if self._opposite_is_tapping_or_waiting(button):
                    continue
                self._tapping[button] = now + TAP_PRESS_S
                started.append(button)
            # 古い held から外れたボタンのうち、まだ短押しの最中のものは、このレポートでは離れない
            released -= set(self._tapping)
            error = self._send_locked()
            sent_at = self.clock() if error is None else now
            for button in started:
                self._tapping[button] = sent_at + TAP_PRESS_S
            for button in waiting_after_release:
                self._pending_taps[button] = sent_at + TAP_RELEASE_GAP_S
            for button in released:
                self._last_released[button] = sent_at
            self._set_hold_deadlines_locked(
                pressed_held, sent_action.hold_seconds, sent_at
            )
            self._report_operation_locked(source, sent_action, error)
            if error is not None:
                raise error
            return FilteredAction(
                action=sent_action, blocked_buttons=filtered.blocked_buttons
            )

    def start_due_taps(self) -> None:
        """始める時刻を過ぎた (始める時刻 <= clock()) pending_taps を取り出し、短押しとして始める。

        取り出したボタンのうち、次のどれかに当たるものは始めずに捨てる: held にある (押し続けに戻った)、
        tapping にある、十字キーの方向で反対の方向が tapping にある。
        始めたボタンがあるときだけ、held と tapping を合わせたレポートを送り、送信が戻った後の clock() + TAP_PRESS_S を
        離す時刻にする。送信の例外 (Exception) は外に出さず、説明を last_error に記録する (このときの離す時刻は、
        送信の前の clock() から決める)。タイマースレッドから呼ぶ。テストから直接呼べるよう公開している。
        """
        with self._lock:
            now = self.clock()
            due = sorted(
                button
                for button, start_at in self._pending_taps.items()
                if start_at <= now
            )
            started: list[str] = []
            for button in due:
                del self._pending_taps[button]
                if button in self._held.buttons or button in self._tapping:
                    continue
                opposite = OPPOSITE_DIRECTION.get(button)
                if opposite is not None and opposite in self._tapping:
                    continue
                self._tapping[button] = now + TAP_PRESS_S
                started.append(button)
            if not started:
                return
            error = self._send_locked()
            if error is not None:
                self._last_error = describe_exception(error)
                return
            sent_at = self.clock()
            for button in started:
                self._tapping[button] = sent_at + TAP_PRESS_S

    def release_due_taps(self) -> None:
        """離す時刻を過ぎた (離す時刻 <= clock()) 短押しを外し、外したものがあるときだけレポートを送る。

        外したボタンの最後に離した時刻を、送信が戻った後の clock() にする (送信に失敗したら送信の前の clock())。
        送信の例外 (Exception) は外に出さず、説明を last_error に記録する (タイマースレッドから呼ぶため)。
        """
        with self._lock:
            now = self.clock()
            due = [
                button
                for button, release_at in self._tapping.items()
                if release_at <= now
            ]
            if not due:
                return
            for button in due:
                del self._tapping[button]
            error = self._send_locked()
            if error is not None:
                self._last_error = describe_exception(error)
            released_at = now if error is not None else self.clock()
            for button in due:
                if button not in self._held.buttons:
                    self._last_released[button] = released_at

    def start_due_holds(self) -> None:
        """始める時刻を過ぎた (始める時刻 <= clock())、間をあけて待っている押し続けを始める。

        待っている押し続けは、期限で離したボタンを、離してから TAP_RELEASE_GAP_S 秒たつ前に apply が押し続けに
        指定したもの。始める時刻は「期限で離した時刻 + TAP_RELEASE_GAP_S」。
        始めたボタンがあるときだけ、held と tapping を合わせたレポートを送る。秒数のあるボタンの期限 (離す時刻) は、
        送信が戻った後の clock() + 秒数にし、秒数の無いボタンは次の apply まで押し続ける。送信の例外 (Exception) は
        外に出さず、説明を last_error に記録する (このときの期限は、送信の前の clock() から決める)。on_operation は
        呼ばない。タイマースレッドから呼ぶ。テストから直接呼べるよう公開している。
        """
        with self._lock:
            now = self.clock()
            due = sorted(
                button
                for button, (start_at, _) in self._pending_holds.items()
                if start_at <= now
            )
            if not due:
                return
            started: dict[str, float | None] = {}
            for button in due:
                _, seconds = self._pending_holds.pop(button)
                started[button] = seconds
            self._held = PadState(
                self._held.buttons | frozenset(started), self._held.sticks
            )
            error = self._send_locked()
            if error is not None:
                self._last_error = describe_exception(error)
            sent_at = now if error is not None else self.clock()
            self._set_hold_deadlines_locked(frozenset(started), started, sent_at)

    def release_due_holds(self) -> None:
        """期限 (離す時刻) を過ぎた (期限 <= clock()) 押し続けを held から外し、外したものがあるときだけレポートを送る。

        ほかのボタンとスティックは変えない (スティックは次の apply まで倒したまま)。外したボタンの最後に離した時刻を、
        送信が戻った後の clock() にする (送信に失敗したら送信の前の clock())。期限で離したボタンの短押しと押し続けは、
        離してから TAP_RELEASE_GAP_S 秒たつまで始めない。送信の例外 (Exception) は外に出さず、説明を last_error に
        記録する。on_operation は呼ばない。タイマースレッドから呼ぶ。テストから直接呼べるよう公開している。
        """
        with self._lock:
            now = self.clock()
            due = [
                button
                for button, deadline in self._hold_deadlines.items()
                if deadline <= now
            ]
            if not due:
                return
            for button in due:
                del self._hold_deadlines[button]
            self._held = PadState(
                self._held.buttons - frozenset(due), self._held.sticks
            )
            error = self._send_locked()
            if error is not None:
                self._last_error = describe_exception(error)
            released_at = now if error is not None else self.clock()
            for button in due:
                # 短押しの最中のボタンは、このレポートでは離れていない
                if button not in self._tapping:
                    self._last_released[button] = released_at
                    self._deadline_released[button] = released_at

    def neutral(self) -> None:
        """held・hold_deadlines・pending_holds・tapping・pending_taps を空にし、中立のレポート
        (controller_state.NEUTRAL_REPORT) を送る。秒数を指定した押し続けも、ここですべて離す。

        on_operation に OperationEvent(source="neutral", action=NEUTRAL_ACTION, ok, error) を渡す。
        送信の例外 (Exception) は外に出さず、説明を last_error に記録する。stop_output の後でも送る。
        """
        with self._lock:
            self._send_neutral_locked("neutral")

    def stop_output(self) -> None:
        """以後の apply を止めたうえで、neutral と同じく押下状態 (秒数を指定した押し続けを含む) を空にして中立の
        レポートを送る。止めた状態は戻さない。

        on_operation には source="stop" で渡す。送信の例外は外に出さず、last_error に記録する。
        呼ぶたびに中立のレポートを送る。
        """
        with self._lock:
            self._output_stopped = True
            self._send_neutral_locked("stop")

    def pressing_buttons(self) -> frozenset[str]:
        """押している、または押す予定のボタンの集合を返す。

        ロックの中で、held (押し続けているボタン。期限を過ぎてタイマーがまだ離していないものを含む)・tapping
        (短押しの最中のボタン)・pending_taps (いったん離してから始める短押しのボタン)・pending_holds (間をあけてから
        始める押し続けのボタン) の和集合を返す。スティックは含めない。どのボタンも押していなければ空の集合。
        送ったボタンを実際に離し終えたか (タイマースレッドが離すレポートを送ったか) を確かめるのに使う。
        """
        with self._lock:
            return (
                self._held.buttons
                | frozenset(self._tapping)
                | frozenset(self._pending_taps)
                | frozenset(self._pending_holds)
            )

    @property
    def allowed_buttons(self) -> frozenset[str]:
        """押せるボタンの集合 (作るときに受け取ったもの)。"""
        return self._allowed_buttons

    @property
    def last_error(self) -> str | None:
        """start_due_taps・release_due_taps・start_due_holds・release_due_holds・neutral・stop_output の送信の
        最後の失敗の説明。無ければ None。"""
        with self._lock:
            return self._last_error

    def close(self) -> None:
        """タイマースレッドを止め、終わるのを待つ。タイマースレッドが無ければ何もしない。レポートは送らない。"""
        self._stop_timer.set()
        if self._timer_thread is not None:
            self._timer_thread.join()
            self._timer_thread = None

    def _replace_holds_locked(self, action: PadAction, now: float) -> frozenset[str]:
        """apply の 6 の押し続けの扱いを行い、新しい held (action.held) のうち、このレポートで押すボタンを返す。

        pending_holds を置き換え、このレポートで押さないボタンの期限を取り消す。呼び出し側がロックを持つ。
        """
        old_held = self._held.buttons
        pending: dict[str, tuple[float, float | None]] = {}
        for button in sorted(action.held):
            if button in old_held:
                continue
            seconds = action.hold_seconds.get(button)
            waiting = self._pending_holds.get(button)
            if waiting is not None:
                start_at, _ = waiting
                pending[button] = (start_at, seconds)
                continue
            released_at = self._deadline_released.get(button)
            if released_at is not None and now - released_at < TAP_RELEASE_GAP_S:
                pending[button] = (released_at + TAP_RELEASE_GAP_S, seconds)
        self._pending_holds = pending
        pressed = frozenset(action.held) - frozenset(pending)
        self._hold_deadlines = {
            button: deadline
            for button, deadline in self._hold_deadlines.items()
            if button in pressed
        }
        return pressed

    def _set_hold_deadlines_locked(
        self,
        buttons: frozenset[str],
        hold_seconds: Mapping[str, float | None],
        sent_at: float,
    ) -> None:
        """buttons の各ボタンの期限を、秒数があれば sent_at + 秒数にし、無ければ外す。呼び出し側がロックを持つ。"""
        for button in buttons:
            seconds = hold_seconds.get(button)
            if seconds is None:
                self._hold_deadlines.pop(button, None)
            else:
                self._hold_deadlines[button] = sent_at + seconds

    def _opposite_is_tapping_or_waiting(self, button: str) -> bool:
        """button が十字キーの方向で、その反対の方向が tapping か pending_taps にあれば True。呼び出し側がロックを持つ。"""
        opposite = OPPOSITE_DIRECTION.get(button)
        return opposite is not None and (
            opposite in self._tapping or opposite in self._pending_taps
        )

    def _compose_state_locked(self) -> PadState:
        """held と tapping を合わせた、送る押下状態を返す。短押しの十字キーの方向の反対は held から外す。"""
        tapping = frozenset(self._tapping)
        overridden = frozenset(
            OPPOSITE_DIRECTION[button] for button in tapping & DPAD_DIRECTIONS
        )
        return PadState((self._held.buttons - overridden) | tapping, self._held.sticks)

    def _send_locked(self) -> Exception | None:
        """held と tapping を合わせたレポートを送る。呼び出し側がロックを持つ。送信の例外を返す (成功なら None)。"""
        try:
            self.sender.set_report(build_report(self._compose_state_locked()))
        except Exception as error:
            # 呼び出し側が、記録して外に出すか (apply)、last_error に残すかを決める
            logger.debug("set_report failed", exc_info=True)
            return error
        return None

    def _send_neutral_locked(self, source: OperationSource) -> None:
        """押下状態を空にして中立のレポートを送り、on_operation に知らせる。呼び出し側がロックを持つ。"""
        pressed = self._held.buttons | frozenset(self._tapping)
        self._held = PadState()
        self._hold_deadlines.clear()
        self._pending_holds.clear()
        self._tapping.clear()
        self._pending_taps.clear()
        now = self.clock()
        error: Exception | None = None
        try:
            self.sender.set_report(NEUTRAL_REPORT)
        except Exception as caught:
            # 中立に戻す送信の失敗は外に出さない (終了処理やブリッジの復旧中に呼ぶため)
            logger.debug("neutral report failed", exc_info=True)
            error = caught
            self._last_error = describe_exception(caught)
        released_at = now if error is not None else self.clock()
        for button in pressed:
            self._last_released[button] = released_at
        self._report_operation_locked(source, NEUTRAL_ACTION, error)

    def _report_operation_locked(
        self, source: OperationSource, action: PadAction, error: Exception | None
    ) -> None:
        """on_operation があれば OperationEvent を渡す。on_operation の例外は外に出さず、logging で警告を書く。"""
        if self.on_operation is None:
            return
        event = OperationEvent(
            source=source,
            action=action,
            ok=error is None,
            error=None if error is None else describe_exception(error),
        )
        try:
            self.on_operation(event)
        except Exception:
            logger.warning("on_operation failed", exc_info=True)

    def _run_timer(self) -> None:
        """close() まで、TIMER_INTERVAL_S ごとに start_due_taps・release_due_taps・start_due_holds・
        release_due_holds を呼ぶ (タイマースレッドの本体)。"""
        while not self._stop_timer.wait(TIMER_INTERVAL_S):
            self.start_due_taps()
            self.release_due_taps()
            self.start_due_holds()
            self.release_due_holds()
