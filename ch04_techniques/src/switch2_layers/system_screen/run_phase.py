"""run の段階 (RunPhase) と、段階の移り変わりの判定 (PhaseTracker)。I/O とスレッドを持たない。

段階と、それぞれの段階で動くもの (段階を変える処理と、段階に合わせて部品を動かす処理は app が行う):
    段階       surface・middle のゲーム用のループ   deep の自動の呼び出しと advice の要求   本体の画面の操作
    prepare    動かさない                           きっかけ system の advice の要求だけ    目的 prepare で middle を呼ぶ
    play       動かす                               する (きっかけ system を除く)           しない
    recover    止める                               きっかけ system の advice の要求だけ    目的 recover で middle を呼ぶ
    clean_up   止める                               しない                                  目的 clean_up で middle を呼ぶ
    (きっかけ system の advice の要求は、prepare・recover が --system-advice-seconds を超えて続いたときに、deep の
    スケジューラーが行う)

移り変わり (照合の結果は、安全の監視の答えのあった確かめ 1 回ごとに 1 件。続けた数は、段階が変わるたびに 0 に戻す):
    prepare → play       本体の画面の middle が done を真で返した後、照合の結果が続けて exit_streak 回 None
                         (見本のどの画面でもない) になった。done の後に None 以外が出たら、done を取り消す
    play → recover       照合の結果が続けて enter_streak 回 None 以外 (どの見本でもよい) になった。または、middle の
                         ゲーム用の出力の press_home が真で、HOME を短押しした
    recover → play       操作の列を送っていない間に、照合の結果が続けて exit_streak 回 None になった
    どの段階 → clean_up   片付けを行う理由で止めるとき (app が begin_clean_up を呼ぶ)
時間切れ (clean_up だけ。prepare と recover には時間切れが無く、止めるまでその段階を続ける):
    clean_up が始まってから clean_up_timeout_s 秒     片付けを終える (CleanUpResult.TIMEOUT)
片付けの終わり (PhaseTracker が返すもの):
    本体の画面の middle が done を真で返し、最新の照合の結果が home_menu       CleanUpResult.DONE
    片付けの時間切れ                                                       CleanUpResult.TIMEOUT
    片付けの本体の画面の middle の失敗 (1 回)                               CleanUpResult.MIDDLE_FAILED
片付けの間の 2 回目の SIGINT (INTERRUPTED)、安全の監視の理由 (UNSAFE)、キャプチャー・ブリッジの理由 (STOPPED) による
終わりは、app が決める。

時刻 t は、すべて run_clock.RunClock の t。テストでは時刻を進めて入力を渡し、戻り値を確かめる。
"""

from dataclasses import dataclass
from enum import StrEnum

# 照合の結果が続けてこの回数、本体の画面 (どの見本でもよい) になったら、play から recover に移る
ENTER_STREAK = 2
# 照合の結果が続けてこの回数 None になったら、prepare (done の後) と recover から play に移る
EXIT_STREAK = 3
# clean_up が始まってから、この秒数で片付けを終える
CLEAN_UP_TIMEOUT_S = 45.0

# 段階の変化の理由 (run.jsonl の phase の reason)。clean_up に移るときは、片付けを始めた理由 (止める理由) を使う
PHASE_REASON_START_CHECK_PASSED = (
    "start check passed"  # 実行を始めるときの確かめが通った
)
PHASE_REASON_PREPARED = (
    "prepared"  # prepare の done の後、照合の結果が続けて None になった
)
PHASE_REASON_SYSTEM_SCREEN = (
    "system screen"  # play で、照合の結果が続けて本体の画面になった
)
PHASE_REASON_PRESS_HOME = (
    "press_home"  # play で、middle のゲーム用の出力の press_home で HOME を押した
)
PHASE_REASON_RECOVERED = "recovered"  # recover で、照合の結果が続けて None になった

# 片付けを終えたと判定する、最新の照合の結果の見本の名前
CLEAN_UP_DONE_SCREEN = "home_menu"


class RunPhase(StrEnum):
    """run の段階。値は run.jsonl の phase の行の phase に書く。"""

    PREPARE = "prepare"  # 準備
    PLAY = "play"  # プレイ
    RECOVER = "recover"  # 復帰
    CLEAN_UP = "clean_up"  # 片付け


class CleanUpResult(StrEnum):
    """片付けの終わり方。値は run.jsonl の clean_up_end の行の result に書く。"""

    DONE = "done"  # middle が done を真で返し、最新の照合の結果が home_menu だった
    TIMEOUT = "timeout"  # 片付けを始めてから clean_up_timeout_s 秒たった
    MIDDLE_FAILED = "middle_failed"  # 片付けの本体の画面の middle が 1 回失敗した
    INTERRUPTED = "interrupted"  # 片付けの間に 2 回目の SIGINT を受けた
    UNSAFE = "unsafe"  # 片付けの間に、安全の監視の理由で止めることになった
    # 片付けの間に、キャプチャー・ブリッジの理由で止めることになった
    STOPPED = "stopped"


@dataclass(frozen=True)
class PhaseTimings:
    """段階の判定の回数と秒数 (既定はモジュールの定数の値)。テストと app で短くできる。

    enter_streak         play → recover に移る、続けた本体の画面の照合の結果の回数
    exit_streak          prepare (done の後) と recover → play に移る、続けた None の照合の結果の回数
    clean_up_timeout_s   clean_up の時間切れの秒数
    """

    enter_streak: int = ENTER_STREAK
    exit_streak: int = EXIT_STREAK
    clean_up_timeout_s: float = CLEAN_UP_TIMEOUT_S


# 既定の回数と秒数 (モジュールの定数の値)
DEFAULT_PHASE_TIMINGS = PhaseTimings()


@dataclass(frozen=True)
class PhaseChange:
    """段階の変化。

    phase    移った後の段階
    reason   変化の理由 (PHASE_REASON_* か、clean_up では片付けを始めた理由)
    """

    phase: RunPhase
    reason: str


@dataclass(frozen=True)
class PhaseEvent:
    """段階の判定に入力を 1 つ渡した結果。何も起きなければ、3 つとも None (NO_EVENT)。

    change         段階が変わったら、その変化。変わらなければ None
    stop_reason    止める理由。止めなければ None (今の判定は、止める理由を返す入力を持たない)
    clean_up_end   片付けが終わったら、その終わり方。終わらなければ None
    """

    change: PhaseChange | None = None
    stop_reason: str | None = None
    clean_up_end: CleanUpResult | None = None


# 何も起きなかったときの結果
NO_EVENT = PhaseEvent()


class PhaseTracker:
    """段階の移り変わりを判定する (モジュールの docstring)。スレッドを持たず、ロックもしない (呼び出し側がそろえる)。

    入力のメソッドは、段階の変化・止める理由・片付けの終わりを PhaseEvent で返す。段階が変わったら、続けた数と
    prepare の done の後の待ちを数え直す。片付けの終わりを 1 回返した後は、どの入力にも NO_EVENT を返す。
    """

    def __init__(
        self, start_t: float, timings: PhaseTimings = DEFAULT_PHASE_TIMINGS
    ) -> None:
        """時刻 start_t に prepare に入った状態で作る (phase は PREPARE、phase_started_t は start_t)。

        timings は判定の回数と秒数。
        """
        self.timings = timings
        self._phase = RunPhase.PREPARE
        self._phase_started_t = start_t
        # 照合の結果を続けて数えた回数 (段階ごとに、数える結果の種類が違う)
        self._streak = 0
        # prepare で、middle が done を真で返した後の待ちの間なら True
        self._waiting_after_done = False
        # 操作の列を送っている間なら True
        self._sending = False
        # 最新の照合の結果 (片付けの終わりの判定に使う)
        self._latest_screen: str | None = None
        # 片付けの終わりを返したら True
        self._clean_up_ended = False

    @property
    def phase(self) -> RunPhase:
        """今の段階。"""
        return self._phase

    @property
    def phase_started_t(self) -> float:
        """今の段階に入った時刻。"""
        return self._phase_started_t

    @property
    def wants_system_middle(self) -> bool:
        """本体の画面の middle を呼んでよいなら True。

        PREPARE・RECOVER・CLEAN_UP のとき True。ただし、PREPARE で done の後の待ちの間と、片付けを終えた後は False。
        PLAY では False。
        """
        if self._phase is RunPhase.PLAY or self._clean_up_ended:
            return False
        return not (self._phase is RunPhase.PREPARE and self._waiting_after_done)

    def on_judgment(self, t: float, screen: str | None) -> PhaseEvent:
        """照合の結果 1 件 (t は照合したフレームの時刻、screen は見本の名前か None) を受け取る。

        PREPARE    done の後の待ちの間なら、None が続けて exit_streak 回で PLAY (PHASE_REASON_PREPARED)。
                   None 以外が出たら、done を取り消し、本体の画面の middle をもう一度呼べる状態に戻す。
                   done の前の結果は数えない
        PLAY       None 以外が続けて enter_streak 回で RECOVER (PHASE_REASON_SYSTEM_SCREEN)。None で数え直す
        RECOVER    操作の列を送っていない間に、None が続けて exit_streak 回で PLAY (PHASE_REASON_RECOVERED)。
                   None 以外で数え直す。列を送っている間の結果は数えず、続けた数を 0 に戻す
        CLEAN_UP   段階を変えず、最新の結果として覚える (片付けの終わりの判定に使う)
        """
        if self._clean_up_ended:
            return NO_EVENT
        self._latest_screen = screen
        exit_streak = self.timings.exit_streak
        if self._phase is RunPhase.PREPARE:
            if not self._waiting_after_done:
                return NO_EVENT
            if screen is not None:
                # 本体の画面が出たので、done を取り消して middle をもう一度呼ぶ
                self._waiting_after_done = False
                self._streak = 0
                return NO_EVENT
            return self._count(t, exit_streak, RunPhase.PLAY, PHASE_REASON_PREPARED)
        if self._phase is RunPhase.PLAY:
            if screen is None:
                self._streak = 0
                return NO_EVENT
            return self._count(
                t,
                self.timings.enter_streak,
                RunPhase.RECOVER,
                PHASE_REASON_SYSTEM_SCREEN,
            )
        if self._phase is RunPhase.RECOVER:
            if self._sending or screen is not None:
                self._streak = 0
                return NO_EVENT
            return self._count(t, exit_streak, RunPhase.PLAY, PHASE_REASON_RECOVERED)
        return NO_EVENT

    def on_system_output(self, t: float, done: bool) -> PhaseEvent:
        """本体の画面の middle の出力 1 件 (done は出力の done) を受け取る。

        PREPARE で done が真なら、done の後の待ちに入る (続けた数を 0 にし、wants_system_middle を False にする)。
        CLEAN_UP で done が真で、最新の照合の結果が CLEAN_UP_DONE_SCREEN なら、片付けの終わり (CleanUpResult.DONE)。
        それ以外は何もしない。
        """
        if self._clean_up_ended or not done:
            return NO_EVENT
        if self._phase is RunPhase.PREPARE:
            self._waiting_after_done = True
            self._streak = 0
        elif (
            self._phase is RunPhase.CLEAN_UP
            and self._latest_screen == CLEAN_UP_DONE_SCREEN
        ):
            return self._end_clean_up(CleanUpResult.DONE)
        return NO_EVENT

    def on_operations_started(self, t: float) -> PhaseEvent:
        """操作の列を送り始めた。RECOVER では、続けた数を 0 にし、送り終えるまで照合の結果を数えない。

        本体の画面のループは、操作が 0 個の列では呼ばない (on_operations_finished も同じ)。
        """
        self._sending = True
        if self._phase is RunPhase.RECOVER:
            self._streak = 0
        return NO_EVENT

    def on_operations_finished(self, t: float) -> PhaseEvent:
        """操作の列を送り終えた (打ち切り・送信の失敗を含む)。この後の照合の結果を、また数える。"""
        self._sending = False
        return NO_EVENT

    def on_press_home(self, t: float) -> PhaseEvent:
        """middle のゲーム用の出力の press_home で HOME を短押しした。PLAY のときだけ RECOVER に移る
        (PHASE_REASON_PRESS_HOME)。ほかの段階では何もしない。"""
        if self._phase is not RunPhase.PLAY:
            return NO_EVENT
        return self._change_to(t, RunPhase.RECOVER, PHASE_REASON_PRESS_HOME)

    def on_system_middle_failed(self, t: float) -> PhaseEvent:
        """本体の画面の middle が失敗した。CLEAN_UP なら片付けの終わり (CleanUpResult.MIDDLE_FAILED)。

        ほかの段階では何もしない (続けた失敗は呼び出し側が数える)。
        """
        if self._phase is not RunPhase.CLEAN_UP or self._clean_up_ended:
            return NO_EVENT
        return self._end_clean_up(CleanUpResult.MIDDLE_FAILED)

    def begin_clean_up(self, t: float, reason: str) -> PhaseEvent:
        """どの段階からでも CLEAN_UP に移る (変化の理由は reason。片付けを始めた理由)。既に CLEAN_UP なら何もしない。"""
        if self._phase is RunPhase.CLEAN_UP:
            return NO_EVENT
        return self._change_to(t, RunPhase.CLEAN_UP, reason)

    def check_time(self, t: float) -> PhaseEvent:
        """時刻 t の時間切れを確かめる。

        CLEAN_UP に入ってから clean_up_timeout_s 秒たったら、片付けの終わり (CleanUpResult.TIMEOUT) を返す。
        PREPARE・PLAY・RECOVER には時間切れが無く、いつも NO_EVENT を返す。
        """
        if self._clean_up_ended or self._phase is not RunPhase.CLEAN_UP:
            return NO_EVENT
        if t - self._phase_started_t >= self.timings.clean_up_timeout_s:
            return self._end_clean_up(CleanUpResult.TIMEOUT)
        return NO_EVENT

    def _count(self, t: float, streak: int, phase: RunPhase, reason: str) -> PhaseEvent:
        """続けた数を 1 増やし、streak 回に達したら phase に移る (理由 reason)。達しなければ NO_EVENT。"""
        self._streak += 1
        if self._streak < streak:
            return NO_EVENT
        return self._change_to(t, phase, reason)

    def _change_to(self, t: float, phase: RunPhase, reason: str) -> PhaseEvent:
        """時刻 t に phase に移り、続けた数と done の後の待ちを数え直して、段階の変化を返す。"""
        self._phase = phase
        self._phase_started_t = t
        self._streak = 0
        self._waiting_after_done = False
        return PhaseEvent(change=PhaseChange(phase, reason))

    def _end_clean_up(self, result: CleanUpResult) -> PhaseEvent:
        """片付けの終わり result を 1 回だけ返す (この後の入力には NO_EVENT を返す)。"""
        self._clean_up_ended = True
        return PhaseEvent(clean_up_end=result)
