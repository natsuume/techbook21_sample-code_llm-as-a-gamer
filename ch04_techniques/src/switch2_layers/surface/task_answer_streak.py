"""先頭のタスクの 2 つの問い (task_done・task_cancel) の答えを、同じタスクについて続けて数え、完了と取り消しの要求の
成立を決める (TaskAnswerStreak)。I/O もスレッドも持たない。

規則 (しきい値 threshold は --threshold-task):
    - 答えのあった要求ごとに 1 回呼ぶ。失敗した要求では呼ばない (数えず、それまでに続けて超えた回数も消さない)
    - 先頭のタスクの ID が前回と違えば (None を含む)、2 つの数を 0 にしてから、この要求を数える。ID が None
      (先頭のタスクが無い) なら、数えずにどちらも成立しない
    - 問いごとに、確率が threshold を超えたら (ちょうど同じ値は超えない) 数を 1 足し、超えなければ (確率が None の
      ときを含む) 0 にする
    - 数が TASK_ANSWER_STREAK に達したら成立 (成立した数は 0 に戻す)
    - 同じ要求で両方が成立したら、完了 (done) だけを成立にし、両方の数を 0 にする
"""

from dataclasses import dataclass

# 続けてしきい値を超えたら成立にする要求の数
TASK_ANSWER_STREAK = 2


@dataclass(frozen=True)
class TaskAnswerDecision:
    """1 回の要求の答えから決めた、成立したもの。

    done     task_done が成立した (先頭のタスクを完了にする)
    cancel   task_cancel が成立した (先頭のタスクに取り消しの要求を付ける)。done が True のときは常に False
    """

    done: bool
    cancel: bool


class TaskAnswerStreak:
    """先頭のタスクの ID と、2 つの問いが続けてしきい値を超えた数を持つ。1 つのスレッド (surface のループ) から呼ぶ。"""

    def __init__(self) -> None:
        """前回の ID が無く、2 つの数が 0 の状態で作る。"""
        # 前回数えた要求の、先頭のタスクの ID (まだ数えていなければ None)
        self._task_id: str | None = None
        # task_done と task_cancel が、続けてしきい値を超えた数
        self._done_count = 0
        self._cancel_count = 0

    def observe(
        self,
        task_id: str | None,
        done_p: float | None,
        cancel_p: float | None,
        threshold: float,
    ) -> TaskAnswerDecision:
        """答えのあった要求 1 回分を数え、成立したものを返す (モジュールの docstring の規則)。

        task_id はその要求の先頭のタスクの ID (無ければ None)、done_p と cancel_p は task_done と task_cancel の確率
        (問いを送らなかったら None)、threshold はしきい値。
        1. task_id が前回と違えば (None を含む)、2 つの数を 0 にし、前回の ID を task_id にする
        2. task_id が None なら TaskAnswerDecision(done=False, cancel=False) を返す
        3. done_p が threshold を超えたら (>) done の数を 1 足し、そうでなければ (None を含む) 0 にする。cancel_p も同じ
        4. 数が TASK_ANSWER_STREAK に達した問いを成立にし、その数を 0 に戻す
        5. 両方が成立したら、done だけを True にし、両方の数を 0 にする
        """
        if task_id != self._task_id:
            self._done_count = 0
            self._cancel_count = 0
            self._task_id = task_id
        if task_id is None:
            return TaskAnswerDecision(done=False, cancel=False)
        self._done_count = _next_count(self._done_count, done_p, threshold)
        self._cancel_count = _next_count(self._cancel_count, cancel_p, threshold)
        done = self._done_count >= TASK_ANSWER_STREAK
        cancel = self._cancel_count >= TASK_ANSWER_STREAK
        if done:
            self._done_count = 0
        if cancel:
            self._cancel_count = 0
        # 同じ要求で両方が成立したら、完了だけにする (両方の数は上で 0 に戻している)
        return TaskAnswerDecision(done=done, cancel=cancel and not done)


def _next_count(count: int, probability: float | None, threshold: float) -> int:
    """確率 probability がしきい値 threshold を超えたら (>) count + 1、超えなければ (None を含む) 0 を返す。"""
    if probability is not None and probability > threshold:
        return count + 1
    return 0
