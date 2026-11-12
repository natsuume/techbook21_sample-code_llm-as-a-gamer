"""押下状態 (held) と短押し (tapping) を合わせたレポートを、ブリッジ (BridgeSupervisor) に送る。

第2章の Controller の tap / hold の層は使わず、判断のたびに押下状態をまるごと置き換える。
短押しは、押すレポートの送信が戻ってから TAP_PRESS_S 秒後に、タイマースレッドが離す。
押したままから外したボタンを同じ判断で短押しにするときは、すぐには押さず、離したレポートの送信が戻ってから
TAP_RELEASE_GAP_S 秒後にタイマースレッドが短押しを始める (押したまま続くと、ゲームから見て新しい押下にならないため)。
時刻を送信が戻った後から測るのは、送信 (ブリッジの ack まで) に時間がかかっても、届いてからの時間を保つため
(第2章の Controller.tap も、押すレポートの送信が戻ってから待つ)。送信が失敗したら、送信前の時刻の値のままにする。

押下状態の 2 層:
    held      apply で置き換える、押したままの状態 (ボタンとスティック)
    tapping   短押しの最中のボタンと、離す時刻 (clock の値)
送るレポートは 2 層を合成したもの。始める時刻を待っている短押し (pending_taps) はレポートに含めない。
十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) が held と tapping の合成で生じたら、短押しを優先し、送るレポートからだけ held の反対の方向を外す
(held 自体は書き換えないため、短押しが離れたら held の方向がレポートに戻る)。
tapping どうしでは相反する方向が生じないよう、反対の方向を短押ししている最中か、反対の方向の短押しが始める時刻を
待っている間は、新しい短押しを始めない。
controller_state.build_report は相反する方向で ValueError を出すため、合成の段階で必ず解消する。

apply は Pipeline のスレッドから、start_due_taps と release_due_taps はタイマースレッドから呼ぶため、
状態の変更と送信は 1 つのロックの中で行う。
"""

import threading
import time
from collections.abc import Callable
from typing import Protocol

from switch2_remote.controller_state import DPAD_DIRECTIONS, PadState, build_report
from switch2_remote.error_message import describe_error

from switch2_agent.action_decision import PadAction

TAP_PRESS_S = 0.12  # 短押しで押している時間 (秒)。第2章の tap と同じ
# 同じボタンを離してから次に押すまでの最小の間隔 (秒)。第2章の tap と同じ
TAP_RELEASE_GAP_S = 0.1
# タイマースレッドが、始める時刻と離す時刻を過ぎた短押しを確かめる間隔 (秒)
TIMER_INTERVAL_S = 0.01
# 十字キーの方向 → 反対の方向
OPPOSITE_DIRECTION = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}


class ReportSender(Protocol):
    """PadOutput がレポートを送る先。run では switch2_remote.bridge_supervisor.BridgeSupervisor を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。"""
        ...


def compose_report_state(held: PadState, tapping: frozenset[str]) -> PadState:
    """held と短押しの最中のボタン tapping を合成した、送る押下状態を返す (純粋な関数)。

    ボタンは held のボタンと tapping の和、スティックは held のもの。tapping に十字キーの方向があれば、
    その反対の方向を held のボタンから外す (短押しを優先する)。tapping どうしは相反する方向を含まない前提とする。
    """
    overridden = frozenset(OPPOSITE_DIRECTION[d] for d in tapping & DPAD_DIRECTIONS)
    return PadState((held.buttons - overridden) | tapping, held.sticks)


class PadOutput:
    """押下状態と短押しを持ち、変化のたびに合成したレポートを sender.set_report で送る。

    属性:
        sender            レポートを送る先
        clock             現在時刻 (秒) を返す関数。短押しの離す時刻と間隔の判定に使う
        lock              状態の変更と送信を守るロック
        held              押したままの状態 (apply で置き換える)
        tapping           短押しの最中のボタン → 離す時刻 (clock の値。押すレポートの送信が戻った時刻 + TAP_PRESS_S)
        pending_taps      始める時刻を待っている短押しのボタン → 始める時刻 (clock の値。離すレポートの送信が
                          戻った時刻 + TAP_RELEASE_GAP_S)。押したままから外したボタンを、同じ判断で短押しにするときに使う
        last_released     ボタン → 前回離した時刻 (clock の値。離すレポートの送信が戻った時刻)。
                          短押しを離した時刻と、押したままから外した時刻
                          (送信が失敗したときは、3 つとも送信前の時刻から計算した値のまま)
        last_error        start_due_taps・release_due_taps・neutral の送信の最後の失敗の説明
                          (switch2_remote.error_message.describe_error)。無ければ None
        stop_timer        close() で立て、タイマースレッドを止める
        timer_thread      タイマースレッド。start_timer が False なら None
    """

    def __init__(
        self,
        sender: ReportSender,
        clock: Callable[[], float] = time.monotonic,
        start_timer: bool = True,
    ):
        """何も押していない状態で作る (作るときにはレポートを送らない)。

        start_timer が True なら、TIMER_INTERVAL_S ごとに start_due_taps と release_due_taps を呼ぶタイマースレッド
        (daemon スレッド) を起動する。False なら起動しない (テストでは start_due_taps と release_due_taps を直接呼ぶ)。
        """
        self.sender = sender
        self.clock = clock
        self.lock = threading.Lock()
        self.held = PadState()
        self.tapping: dict[str, float] = {}
        self.pending_taps: dict[str, float] = {}
        self.last_released: dict[str, float] = {}
        self.last_error: str | None = None
        self.stop_timer = threading.Event()
        self.timer_thread: threading.Thread | None = None
        if start_timer:
            self.timer_thread = threading.Thread(target=self._run_timer, daemon=True)
            self.timer_thread.start()

    def apply(self, action: PadAction) -> None:
        """held を action.held に置き換え、action.taps の短押しを始めて (または始める時刻を決めて)、合成したレポートを送る。

        action.held は十字キーの相反する方向を含まない前提とする (action_decision.decide_action が保証する)。
        now = clock() として、ロックの中で次を行う:
            1. released = 古い held のボタン - action.held のボタン。released の各ボタンの last_released を now にする
            2. held を action.held に置き換える
            3. action.taps の各ボタンを名前の順に扱う:
               - released に含まれるボタン (押したままから外したボタン) は、次のすべてを満たせば、
                 pending_taps に始める時刻 now + TAP_RELEASE_GAP_S を入れる (今は短押しを始めない)。
                 満たさなければ何もしない
                   - tapping にも pending_taps にも無い
                   - 十字キーの方向なら、その反対の方向が tapping にも pending_taps にも無い
               - それ以外のボタンは、_can_start_tap の条件をすべて満たせば短押しとして始める
                 (離す時刻 = now + TAP_PRESS_S)
            4. compose_report_state(held, tapping のボタン) の build_report を送る (pending_taps は含めない)
            5. 送信が成功したら、sent_at = clock() (送信が戻った後) として、この apply で始めた短押しの離す時刻を
               sent_at + TAP_PRESS_S に、この apply で決めた pending_taps の始める時刻を sent_at + TAP_RELEASE_GAP_S に、
               released の last_released を sent_at に置き直す
        前の apply から続く短押しの離す時刻と、待っている短押しの始める時刻は変えない。変化が無くても送る。
        送信の例外はそのまま呼び出し側に出す (状態は更新したまま。時刻は送信前の now から計算した値のまま)。
        """
        with self.lock:
            now = self.clock()
            released = self.held.buttons - action.held.buttons
            for button in released:
                self.last_released[button] = now
            self.held = action.held
            started: list[str] = []
            waiting: list[str] = []
            for button in sorted(action.taps):
                if button in released:
                    if self._can_wait_for_tap(button):
                        self.pending_taps[button] = now + TAP_RELEASE_GAP_S
                        waiting.append(button)
                elif self._can_start_tap(button, now):
                    self.tapping[button] = now + TAP_PRESS_S
                    started.append(button)
            self._send_locked()
            sent_at = self.clock()
            for button in started:
                self.tapping[button] = sent_at + TAP_PRESS_S
            for button in waiting:
                self.pending_taps[button] = sent_at + TAP_RELEASE_GAP_S
            for button in released:
                self.last_released[button] = sent_at

    def start_due_taps(self) -> None:
        """始める時刻を過ぎた (始める時刻 <= clock()) pending_taps を取り出し、始められるものを短押しとして始める。

        取り出したボタンのうち、次のすべてを満たすものだけを tapping に入れる (離す時刻 = clock() + TAP_PRESS_S)。
        満たさないものは捨てる (始めない):
            - そのときの held のボタンに含まれない
            - 短押しの最中ではない (tapping に無い)
            - 十字キーの方向なら、その反対の方向を短押ししている最中ではない
        tapping に入れたものがあるときだけ、合成したレポートを送る。送信が成功したら、この呼び出しで始めた短押しの
        離す時刻を、送信が戻った後の clock() + TAP_PRESS_S に置き直す。
        送信の例外 (RuntimeError / OSError) は外に出さず、説明を last_error に記録する (タイマースレッドから呼ぶため)。
        このとき離す時刻は送信前の値のまま。
        タイマースレッドから TIMER_INTERVAL_S ごとに呼ぶ。テストから直接呼べるよう公開している。
        """
        with self.lock:
            now = self.clock()
            due = sorted(
                button
                for button, start_at in self.pending_taps.items()
                if start_at <= now
            )
            started: list[str] = []
            for button in due:
                del self.pending_taps[button]
                if self._can_start_due_tap(button):
                    self.tapping[button] = now + TAP_PRESS_S
                    started.append(button)
            if started and self._send_quietly_locked():
                sent_at = self.clock()
                for button in started:
                    self.tapping[button] = sent_at + TAP_PRESS_S

    def release_due_taps(self) -> None:
        """離す時刻を過ぎた (離す時刻 <= clock()) 短押しを tapping から外し、外したときだけ合成したレポートを送る。

        外したボタンの last_released に clock() の値を入れる。外すものが無ければ何も送らない。
        送信が成功したら、外したボタンの last_released を、送信が戻った後の clock() に置き直す。
        送信の例外 (RuntimeError / OSError) は外に出さず、説明を last_error に記録する (タイマースレッドから呼ぶため)。
        このとき last_released は送信前の値のまま。
        タイマースレッドから TIMER_INTERVAL_S ごとに呼ぶ。テストから直接呼べるよう公開している。
        """
        with self.lock:
            now = self.clock()
            due = [
                button
                for button, release_at in self.tapping.items()
                if release_at <= now
            ]
            if not due:
                return
            for button in due:
                del self.tapping[button]
                self.last_released[button] = now
            if self._send_quietly_locked():
                sent_at = self.clock()
                for button in due:
                    self.last_released[button] = sent_at

    def neutral(self) -> None:
        """held・tapping・pending_taps を空にし、中立のレポート (controller_state.NEUTRAL_REPORT) を送る。

        送信の例外 (RuntimeError / OSError) は外に出さず、説明を last_error に記録する。
        """
        with self.lock:
            self.held = PadState()
            self.tapping.clear()
            self.pending_taps.clear()
            self._send_quietly_locked()

    def close(self) -> None:
        """タイマースレッドを止め、終わるのを待つ。タイマースレッドが無ければ何もしない。"""
        self.stop_timer.set()
        if self.timer_thread is not None:
            self.timer_thread.join()
            self.timer_thread = None

    def _can_start_tap(self, button: str, now: float) -> bool:
        """apply で、押したままから外したのではない button の短押しを今始めてよいかを返す。呼び出し側がロックを持つ。

        次のすべてを満たせば True:
            - 新しい held のボタンに含まれない
            - 短押しの最中ではなく (tapping に無い)、始める時刻を待ってもいない (pending_taps に無い)
            - 十字キーの方向なら、その反対の方向が tapping にも pending_taps にも無い
            - 前回離してから TAP_RELEASE_GAP_S 秒以上たった (last_released に無ければ満たす)
        """
        if (
            button in self.held.buttons
            or button in self.tapping
            or button in self.pending_taps
        ):
            return False
        opposite = OPPOSITE_DIRECTION.get(button)
        if opposite is not None and (
            opposite in self.tapping or opposite in self.pending_taps
        ):
            return False
        released_at = self.last_released.get(button)
        return released_at is None or now - released_at >= TAP_RELEASE_GAP_S

    def _can_wait_for_tap(self, button: str) -> bool:
        """apply で、押したままから外した button の短押しの始める時刻を決めてよいかを返す。呼び出し側がロックを持つ。

        button が tapping にも pending_taps にも無く、十字キーの方向なら、その反対の方向も tapping にも
        pending_taps にも無ければ True。
        """
        if button in self.tapping or button in self.pending_taps:
            return False
        opposite = OPPOSITE_DIRECTION.get(button)
        return opposite is None or (
            opposite not in self.tapping and opposite not in self.pending_taps
        )

    def _can_start_due_tap(self, button: str) -> bool:
        """start_due_taps で、始める時刻を過ぎた button の短押しを始めてよいかを返す。呼び出し側がロックを持つ。

        button が held のボタンにも tapping にも無く、十字キーの方向なら、その反対の方向が tapping に無ければ True。
        """
        if button in self.held.buttons or button in self.tapping:
            return False
        opposite = OPPOSITE_DIRECTION.get(button)
        return opposite is None or opposite not in self.tapping

    def _send_locked(self) -> None:
        """held と tapping を合成したレポートを送る。呼び出し側がロックを持つ。失敗したら例外をそのまま出す。"""
        state = compose_report_state(self.held, frozenset(self.tapping))
        self.sender.set_report(build_report(state))

    def _send_quietly_locked(self) -> bool:
        """_send_locked を呼び、送信できたら True を返す。呼び出し側がロックを持つ。

        送信の失敗 (RuntimeError / OSError) は外に出さず、説明を last_error に記録して False を返す。
        """
        try:
            self._send_locked()
        except (RuntimeError, OSError) as e:
            self.last_error = describe_error(e)
            return False
        return True

    def _run_timer(self) -> None:
        """close() まで、TIMER_INTERVAL_S ごとに start_due_taps を呼んでから release_due_taps を呼ぶ (タイマースレッドの本体)。"""
        while not self.stop_timer.wait(TIMER_INTERVAL_S):
            self.start_due_taps()
            self.release_due_taps()
