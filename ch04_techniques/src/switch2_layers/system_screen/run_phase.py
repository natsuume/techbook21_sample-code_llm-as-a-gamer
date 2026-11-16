"""run の段階 (RunPhase) と、段階の移り変わりの判定 (PhaseTracker)。I/O とスレッドを持たない。

段階と、それぞれの段階で動くもの (段階を変える処理と、段階に合わせて部品を動かす処理は app が行う):
    段階       surface・middle のゲーム用のループ   deep の自動の呼び出しと advice の要求   本体の画面の操作
    prepare    動かさない                           しない                                  目的 prepare で middle を呼ぶ
    play       動かす                               する                                    しない
    recover    止める                               しない                                  目的 recover で middle を呼ぶ
    clean_up   止める                               しない                                  目的 clean_up で middle を呼ぶ

移り変わり (照合の結果は、安全の監視の答えのあった確かめ 1 回ごとに 1 件。続けた数は、段階が変わるたびに 0 に戻す):
    prepare → play       本体の画面の middle が done を真で返した後、照合の結果が続けて exit_streak 回 None
                         (見本のどの画面でもない) になった。done の後に None 以外が出たら、done を取り消す
    play → recover       照合の結果が続けて enter_streak 回 None 以外 (どの見本でもよい) になった。または、middle の
                         ゲーム用の出力の press_home が真で、HOME を短押しした
    recover → play       操作の列を送っていない間に、照合の結果が続けて exit_streak 回 None になった
    どの段階 → clean_up   片付けを行う理由で止めるとき (app が begin_clean_up を呼ぶ)
時間切れ:
    prepare が始まってから prepare_timeout_s 秒       止める (REASON_PREPARATION_TIMED_OUT。片付けはしない)
    recover が始まってから recover_timeout_s 秒       止める (REASON_RECOVERY_TIMED_OUT。片付けはしない)
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
# prepare が始まってから、この秒数で play に移れなければ止める
PREPARE_TIMEOUT_S = 180.0
# recover が始まってから、この秒数で play に戻れなければ止める
RECOVER_TIMEOUT_S = 180.0
# clean_up が始まってから、この秒数で片付けを終える
CLEAN_UP_TIMEOUT_S = 45.0

# 止める理由
REASON_PREPARATION_TIMED_OUT = "preparation timed out"
REASON_RECOVERY_TIMED_OUT = "recovery timed out"

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
    prepare_timeout_s    prepare の時間切れの秒数
    recover_timeout_s    recover の時間切れの秒数
    clean_up_timeout_s   clean_up の時間切れの秒数
    """

    enter_streak: int = ENTER_STREAK
    exit_streak: int = EXIT_STREAK
    prepare_timeout_s: float = PREPARE_TIMEOUT_S
    recover_timeout_s: float = RECOVER_TIMEOUT_S
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
    stop_reason    止める理由 (prepare と recover の時間切れ)。止めなければ None
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
        raise NotImplementedError

    @property
    def phase(self) -> RunPhase:
        """今の段階。"""
        raise NotImplementedError

    @property
    def phase_started_t(self) -> float:
        """今の段階に入った時刻。"""
        raise NotImplementedError

    @property
    def wants_system_middle(self) -> bool:
        """本体の画面の middle を呼んでよいなら True。

        PREPARE・RECOVER・CLEAN_UP のとき True。ただし、PREPARE で done の後の待ちの間と、片付けを終えた後は False。
        PLAY では False。
        """
        raise NotImplementedError

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
        raise NotImplementedError

    def on_system_output(self, t: float, done: bool) -> PhaseEvent:
        """本体の画面の middle の出力 1 件 (done は出力の done) を受け取る。

        PREPARE で done が真なら、done の後の待ちに入る (続けた数を 0 にし、wants_system_middle を False にする)。
        CLEAN_UP で done が真で、最新の照合の結果が CLEAN_UP_DONE_SCREEN なら、片付けの終わり (CleanUpResult.DONE)。
        それ以外は何もしない。
        """
        raise NotImplementedError

    def on_operations_started(self, t: float) -> PhaseEvent:
        """操作の列を送り始めた。RECOVER では、続けた数を 0 にし、送り終えるまで照合の結果を数えない。"""
        raise NotImplementedError

    def on_operations_finished(self, t: float) -> PhaseEvent:
        """操作の列を送り終えた (打ち切り・送信の失敗を含む)。この後の照合の結果を、また数える。"""
        raise NotImplementedError

    def on_press_home(self, t: float) -> PhaseEvent:
        """middle のゲーム用の出力の press_home で HOME を短押しした。PLAY のときだけ RECOVER に移る
        (PHASE_REASON_PRESS_HOME)。ほかの段階では何もしない。"""
        raise NotImplementedError

    def on_system_middle_failed(self, t: float) -> PhaseEvent:
        """本体の画面の middle が失敗した。CLEAN_UP なら片付けの終わり (CleanUpResult.MIDDLE_FAILED)。

        ほかの段階では何もしない (続けた失敗は呼び出し側が数える)。
        """
        raise NotImplementedError

    def begin_clean_up(self, t: float, reason: str) -> PhaseEvent:
        """どの段階からでも CLEAN_UP に移る (変化の理由は reason。片付けを始めた理由)。既に CLEAN_UP なら何もしない。"""
        raise NotImplementedError

    def check_time(self, t: float) -> PhaseEvent:
        """時刻 t の時間切れを確かめる (今の段階に入ってから、それぞれの秒数がたったら時間切れとする)。

        PREPARE    prepare_timeout_s → 止める理由 REASON_PREPARATION_TIMED_OUT
        RECOVER    recover_timeout_s → 止める理由 REASON_RECOVERY_TIMED_OUT
        CLEAN_UP   clean_up_timeout_s → 片付けの終わり (CleanUpResult.TIMEOUT)
        PLAY       時間切れは無い
        prepare と recover の止める理由は、時間切れの間は呼ぶたびに返す (止めるのは呼び出し側)。
        """
        raise NotImplementedError
