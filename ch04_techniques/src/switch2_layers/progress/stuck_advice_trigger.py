"""新しい画面が出ていない秒数 (no_novel_s) から、advice の deep をきっかけ progress で要求するかを決める
(StuckAdviceTrigger)。I/O を扱わない。

規則 (しきい値 threshold_s は --stuck-advice-seconds):
    - no_novel_s がしきい値を超えたら (no_novel_s > threshold_s) 要求する
    - 超えたまま続く間は、前回このきっかけで要求してから threshold_s 秒たつごとに要求する。前回の要求の時刻は、
      要求すると決めた時刻 (deep のスケジューラーがジョブを起動したか、最小の起動間隔などで捨てたか、例外を
      出したかに依らない)。捨てられた要求を判定のたびに出し直さないため
    - no_novel_s がしきい値以下に戻ったら (値が無いときを含む)、前回の要求を忘れ、次に超えたときにすぐ要求できる
      状態に戻す
    - 実行中の advice のジョブがある間 (きっかけに依らない) は要求しない。ジョブが終わった後の判定で、上の規則で要求する
時刻は deep のスケジューラーの時計 (実行の経過秒) の値を渡す。
"""

# --stuck-advice-seconds の既定値 (秒)
DEFAULT_STUCK_ADVICE_SECONDS = 60.0


class StuckAdviceTrigger:
    """advice の deep をきっかけ progress で要求するかを決める。1 つのスレッドからだけ呼ぶ。

    threshold_s   しきい値 (秒)。no_novel_s がこれを超えたら要求し、超えたまま続く間はこの秒数ごとに要求する。
                  0 より大きい有限の値 (そうでなければ ValueError)
    """

    def __init__(self, threshold_s: float) -> None:
        """前回の要求が無い状態で作る。threshold_s が 0 より大きい有限の値でなければ ValueError。"""
        raise NotImplementedError

    def check(self, now: float, no_novel_s: float | None, advice_running: bool) -> bool:
        """時刻 now に要求するかを決め、要求するなら True を返す (モジュールの docstring の規則)。

        no_novel_s は最新の新しい画面が出ていない秒数 (値が無ければ None)、advice_running は実行中の advice の
        ジョブがあるか。
        1. no_novel_s が None か threshold_s 以下なら、前回の要求を忘れて False
        2. advice_running なら False (前回の要求は変えない)
        3. 前回の要求が無いか、now − 前回の要求の時刻 >= threshold_s なら、前回の要求の時刻を now にして True
        4. それ以外は False
        """
        raise NotImplementedError
