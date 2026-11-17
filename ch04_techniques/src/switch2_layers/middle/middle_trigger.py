"""middle を呼ぶきっかけ (呼ばれた理由) の決め方 (MiddleReasonTracker) と、それをスレッドから使う待ち (MiddleTrigger)。

理由 (MiddleCallReason)。1 回の実行に当たった理由はすべて、MIDDLE_CALL_REASON_ORDER の順に並べて渡す:
    queue_empty        現在の場面のキューが、完了か取り消しで空になった (空になったときに 1 回だけ。完了は surface、
                       取り消しは middle の出力の書き込みが notify で知らせる)
    cancel_requested   surface の取り消しの要求が新しく成立した (同じタスクにつき 1 回。surface が notify で知らせる)
    stuck              現在の場面の最も古いタスクの続いた秒数が stuck_seconds を超えた (ちょうど同じ値は超えない)。
                       超えたまま続く間は、前回この理由で呼んでから stuck_seconds 秒たつごと。現在の場面が無いときと、
                       キューが空のときは起きない
    scene_change       surface が場面転換を検知した (surface が notify で知らせる)
    interval           上のどれも無く、前回の応答から interval_s 秒たった
次の実行は、前回の応答から MIN_MIDDLE_GAP_S 秒たつまでは始めない。保留の理由があれば、その後すぐに始める。
middle の実行中に届いた理由は保留に残り、その実行が終わった後の回に持ち越す。失敗した回の理由 (interval を除く) も
保留に戻して次の回に持ち越す (carry_over)。ただし失敗した回の後は、保留の理由 (持ち越した理由と、その後に届いた
理由) があっても、前回の応答から interval_s 秒たつまでは始めない (失敗の後に再び呼ぶまでの間隔を、きっかけで縮めない)。
その後の回の理由は保留の理由 (無ければ interval) で、その回を始めたら失敗の後の待ちを解く。
最初の実行の理由は interval にし、それより前に届いた理由は捨てる (MiddleTrigger.first_reasons)。
MiddleReasonTracker は I/O もスレッドも持たず、時刻を引数で受け取る。MiddleTrigger はそれを threading.Condition で包み、
どのスレッドからでも notify を呼べるようにする。
"""

import threading
from collections.abc import Callable, Sequence
from typing import Literal

from switch2_layers.run_clock import RunClock

# middle を呼んだ理由
MiddleCallReason = Literal[
    "queue_empty", "cancel_requested", "stuck", "scene_change", "interval"
]
# 理由を並べる順 (middle の入力の「呼ばれた理由」の節と middle/calls.jsonl の reasons もこの順)
MIDDLE_CALL_REASON_ORDER: tuple[MiddleCallReason, ...] = (
    "queue_empty",
    "cancel_requested",
    "stuck",
    "scene_change",
    "interval",
)
# 前回の応答から次の実行を始めるまでの最小の秒数 (保留の理由があっても、これより前には始めない)
MIN_MIDDLE_GAP_S = 1.0
# MiddleTrigger.wait_next が、詰まりの秒数と待ちの明けを確かめ直す間隔 (秒)
TRIGGER_POLL_S = 0.1


class MiddleReasonTracker:
    """middle を呼ぶ理由の保留と、詰まり (stuck) の繰り返しの時刻と、直前の回が失敗したかを持つ。I/O もスレッドも
    持たない。1 つのスレッドから呼ぶ。

    interval_s      保留の理由が無いときと、直前の回が失敗したときに、前回の応答から次を始めるまでの秒数
                    (--middle-interval)
    stuck_seconds   最も古いタスクの続いた秒数がこれを超えたら stuck にする秒数 (--task-stuck-seconds)。超えたまま
                    続く間は、前回 stuck で呼んでからこの秒数たつごとに stuck にする
    """

    def __init__(self, interval_s: float, stuck_seconds: float) -> None:
        """保留の理由が無く、前回 stuck で呼んだ時刻も無く、直前の回が失敗していない状態で作る。"""
        self._interval_s = interval_s
        self._stuck_seconds = stuck_seconds
        self._pending: set[MiddleCallReason] = set()
        # 前回 stuck で呼んだ時刻 (take が stuck を返した時刻)。しきい値以下に戻ったら忘れる
        self._last_stuck_t: float | None = None
        # 直前の回が失敗したか (carry_over で立て、take で解く)
        self._after_failure = False

    def notify(
        self, reason: Literal["queue_empty", "cancel_requested", "scene_change"]
    ) -> None:
        """理由 reason を保留に足す (既に保留にあれば何もしない)。"""
        self._pending.add(reason)

    def observe_stuck(self, oldest_active_s: float | None, t: float) -> None:
        """時刻 t の、現在の場面の最も古いタスクの続いた秒数 oldest_active_s から、stuck を保留に足すかを決める。

        1. oldest_active_s が None (現在の場面が無いか、キューが空) か stuck_seconds 以下なら、前回 stuck で呼んだ時刻を
           忘れる (次に超えたときに、すぐ stuck にできる状態に戻す)。保留は変えない
        2. stuck_seconds を超えていて (ちょうど同じ値は超えない)、前回 stuck で呼んだ時刻が無いか、
           t − その時刻 ≥ stuck_seconds なら、stuck を保留に足す
        前回 stuck で呼んだ時刻は、ここでは決めない (take で決める)。
        """
        if oldest_active_s is None or oldest_active_s <= self._stuck_seconds:
            self._last_stuck_t = None
            return
        if self._last_stuck_t is None or t - self._last_stuck_t >= self._stuck_seconds:
            self._pending.add("stuck")

    def ready(self, t: float, last_response_t: float) -> bool:
        """時刻 t に次の実行を始めてよいなら True を返す。last_response_t は前回の応答 (失敗を含む) の時刻。

        1. 直前の回が失敗した状態 (carry_over の後、take の前) なら、保留の有無に依らず、
           t − last_response_t ≥ interval_s なら True、そうでなければ False
        2. t − last_response_t < MIN_MIDDLE_GAP_S なら False
        3. 保留の理由があれば True
        4. 保留の理由が無ければ、t − last_response_t ≥ interval_s なら True、そうでなければ False
        保留も、直前の回が失敗した状態も変えない。
        """
        elapsed = t - last_response_t
        if self._after_failure:
            return elapsed >= self._interval_s
        if elapsed < MIN_MIDDLE_GAP_S:
            return False
        if self._pending:
            return True
        return elapsed >= self._interval_s

    def take(self, t: float) -> tuple[MiddleCallReason, ...]:
        """時刻 t に始める実行の理由を返し、保留を空にして、直前の回が失敗した状態を解く。

        保留の理由を MIDDLE_CALL_REASON_ORDER の順に並べて返す。保留が無ければ ("interval",)。
        返す理由に stuck を含むなら、前回 stuck で呼んだ時刻を t にする。
        """
        reasons = tuple(
            reason for reason in MIDDLE_CALL_REASON_ORDER if reason in self._pending
        )
        self._pending.clear()
        self._after_failure = False
        if "stuck" in reasons:
            self._last_stuck_t = t
        return reasons if reasons else ("interval",)

    def carry_over(self, reasons: Sequence[MiddleCallReason]) -> None:
        """失敗した回の理由 reasons のうち、interval を除くものを保留に戻し (次の回に持ち越す)、直前の回が失敗した
        状態にする (次の take まで、ready は前回の応答から interval_s 秒たつまで False)。

        stuck を戻しても、前回 stuck で呼んだ時刻は変えない。
        """
        self._pending.update(reason for reason in reasons if reason != "interval")
        self._after_failure = True

    def _discard_pending(self) -> None:
        """保留の理由をすべて捨てる (MiddleTrigger.first_reasons が使う)。"""
        self._pending.clear()


class MiddleTrigger:
    """MiddleReasonTracker を threading.Condition で包み、middle のループの待ちとほかのスレッドの知らせをつなぐ。

    notify はどのスレッドからでも呼んでよい (surface のループ・middle の出力の書き込み)。wait_next・carry_over・
    first_reasons は middle のループのスレッドから呼ぶ。

    interval_s        MiddleReasonTracker の interval_s
    stuck_seconds     MiddleReasonTracker の stuck_seconds
    clock             実行の経過秒の時計 (wait_next が確かめるたびに clock.now() を読む)
    oldest_active_s   時刻 t を受け取り、キューの部品の現在の場面の最も古いタスクの続いた秒数を返す関数。現在の場面が
                      無ければ None を返す (run では tasks.task_board.TaskBoard の current_scene_id と oldest_active_s)
    """

    def __init__(
        self,
        interval_s: float,
        stuck_seconds: float,
        clock: RunClock,
        oldest_active_s: Callable[[float], float | None],
    ) -> None:
        """MiddleReasonTracker(interval_s, stuck_seconds) と threading.Condition を作って持つ。"""
        self._tracker = MiddleReasonTracker(interval_s, stuck_seconds)
        self._condition = threading.Condition()
        self._clock = clock
        self._oldest_active_s = oldest_active_s

    def notify(self, reason: MiddleCallReason) -> None:
        """理由 reason (queue_empty・cancel_requested・scene_change のどれか) を保留に足し
        (MiddleReasonTracker.notify)、wait_next で待っているスレッドを起こす。どのスレッドからでも呼べる。"""
        with self._condition:
            self._tracker.notify(reason)
            self._condition.notify_all()

    def wait_next(
        self, stop: threading.Event, last_response_t: float
    ) -> tuple[MiddleCallReason, ...] | None:
        """次の実行を始めてよくなるまで待ち、その実行の理由を返す。stop が立ったら None を返す。

        TRIGGER_POLL_S ごと (notify でも起きる) に、t = clock.now() で
        MiddleReasonTracker.observe_stuck(oldest_active_s(t), t) と MiddleReasonTracker.ready(t, last_response_t) を
        確かめ、ready なら MiddleReasonTracker.take(t) の理由を返す。確かめる前に stop が立っていれば None を返す。
        last_response_t は前回の応答 (失敗を含む) の時刻。carry_over の後 (直前の回が失敗した) は、notify で起きても、
        前回の応答から interval_s 秒たつまでは返さない。
        """
        while not stop.is_set():
            t = self._clock.now()
            # キューの部品は自分のロックを持つため、Condition の外で読む
            oldest_active_s = self._oldest_active_s(t)
            with self._condition:
                self._tracker.observe_stuck(oldest_active_s, t)
                if self._tracker.ready(t, last_response_t):
                    return self._tracker.take(t)
                self._condition.wait(TRIGGER_POLL_S)
        return None

    def carry_over(self, reasons: Sequence[MiddleCallReason]) -> None:
        """失敗した回の理由 reasons を保留に戻し、直前の回が失敗した状態にする (MiddleReasonTracker.carry_over)。

        次の wait_next は、前回の応答から interval_s 秒たつまで明けず、明けたら持ち越した理由 (と、その後に届いた理由。
        無ければ interval) を返す。
        """
        with self._condition:
            self._tracker.carry_over(reasons)

    def first_reasons(self) -> tuple[MiddleCallReason, ...]:
        """最初の実行の理由 ("interval",) を返す。それまでに届いた保留の理由は捨てる (保留を空にする)。"""
        with self._condition:
            self._tracker._discard_pending()
        return ("interval",)
