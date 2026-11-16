"""安全の確かめの答えと失敗から、止めるかと止まる理由を決める (I/O・スレッド・時刻を扱わない純粋な関数とクラス)。

止まる条件と理由 (app.LayersApp の run.jsonl の stop に書く):
    実行を始めるときの確かめ (1 回で決める。start_stop_reason)
        どれかの問いの確率がしきい値を超えた      unsafe screen at start: <問いの名前>
        確かめが失敗した                          safety check failed at start
    監視の確かめ (MonitorJudge)
        同じ問いの確率が、答えのあった確かめで続けて UNSAFE_STREAK_TO_STOP 回しきい値を超えた
                                                  unsafe screen: <問いの名前>
        確かめの失敗が続けて max_errors 回起きた    safety check failed <回数> times in a row
「続けて超えた」は、答えのあった確かめだけを並べて数える。失敗した確かめは数えず、それまでに続けて超えた回数も
消さない (例: 超えた → 失敗 → 超えた で止まる。超えた・失敗が交互に続いても止まる)。
「超えた」は、確率がしきい値より大きいこと (ちょうど同じ値は超えない)。--allow-home を付けたときは、
safety_questions.ALLOW_HOME_EXEMPT_NAMES の問い (home_menu・user_select) では、どちらの確かめでも止めない。
同時に複数の問いが条件に当たったら、safety_questions.SAFETY_QUESTION_NAMES の順で先の問いの名前を理由に書く。
"""

from collections.abc import Mapping

# 同じ問いがしきい値を超えた確かめがこの回数続いたら止める
UNSAFE_STREAK_TO_STOP = 2
REASON_SAFETY_FAILED_AT_START = "safety check failed at start"


def unsafe_screen_reason(name: str) -> str:
    """監視で問い name の画面を見つけて止まるときの理由 `unsafe screen: <name>` を返す。"""
    raise NotImplementedError


def unsafe_screen_at_start_reason(name: str) -> str:
    """実行を始めるときの確かめで問い name の画面を見つけて止まるときの理由 `unsafe screen at start: <name>` を返す。"""
    raise NotImplementedError


def safety_failure_reason(count: int) -> str:
    """監視の確かめの失敗が count 回続いて止まるときの理由 `safety check failed <count> times in a row` を返す。"""
    raise NotImplementedError


def watched_names(allow_home: bool) -> tuple[str, ...]:
    """止める対象の問いの名前を SAFETY_QUESTION_NAMES の順で返す。

    allow_home が True なら ALLOW_HOME_EXEMPT_NAMES の名前を除く。
    """
    raise NotImplementedError


def validate_probabilities(probabilities: Mapping[str, object]) -> dict[str, float]:
    """SafetyChecker の答えの確率を確かめ、SAFETY_QUESTION_NAMES の順の (名前 → 確率) の dict にして返す。

    SAFETY_QUESTION_NAMES のどれかの名前が無い、値が数 (int か float。bool は除く) でない、有限でない、0〜1 の外なら
    ValueError (安全の監視は、確かめの失敗として数える)。SAFETY_QUESTION_NAMES に無い名前は無視する。
    """
    raise NotImplementedError


def names_over_threshold(
    probabilities: Mapping[str, float], threshold: float, allow_home: bool
) -> tuple[str, ...]:
    """watched_names(allow_home) のうち、確率が threshold を超えた (ちょうど同じ値は超えない) 名前を、
    SAFETY_QUESTION_NAMES の順で返す。probabilities は validate_probabilities の値 (すべての名前を含む)。"""
    raise NotImplementedError


def start_stop_reason(
    probabilities: Mapping[str, float] | None, threshold: float, allow_home: bool
) -> str | None:
    """実行を始めるときの確かめの結果から、止まる理由を返す。止めなければ None。

    probabilities が None (確かめが失敗した) なら REASON_SAFETY_FAILED_AT_START。
    names_over_threshold(probabilities, threshold, allow_home) が空でなければ、その先頭の名前の
    unsafe_screen_at_start_reason。どちらでもなければ None。
    """
    raise NotImplementedError


class MonitorJudge:
    """監視の確かめの結果を順に受け取り、止まる条件 (モジュールの docstring) に当たったら理由を返す。

    threshold    しきい値 (--threshold-safety)
    allow_home   --allow-home。True なら home_menu と user_select の問いでは止めない
    max_errors   確かめの失敗がこの回数続いたら止める (--max-safety-errors)
    1 つのスレッド (安全の監視) からだけ呼ぶ。止まる理由を返した後も呼んでよい (同じ規則で数え続ける)。
    """

    def __init__(self, threshold: float, allow_home: bool, max_errors: int) -> None:
        """続けて超えた回数と、続けて失敗した回数を 0 にして作る。"""
        self.threshold = threshold
        self.allow_home = allow_home
        self.max_errors = max_errors

    @property
    def consecutive_errors(self) -> int:
        """確かめの失敗が続いている回数 (答えを受け取ると 0 に戻る)。"""
        raise NotImplementedError

    def streak(self, name: str) -> int:
        """問い name の確率が、続けてしきい値を超えた確かめの回数を返す。止める対象でない名前は常に 0。"""
        raise NotImplementedError

    def record_answer(self, probabilities: Mapping[str, float]) -> str | None:
        """答えのあった確かめ 1 回を数え、止まる理由を返す。止めなければ None。

        1. 続けて失敗した回数を 0 に戻す
        2. watched_names(allow_home) の問いごとに、確率が threshold を超えていれば続けて超えた回数を 1 増やし、
           超えていなければ 0 に戻す
        3. 続けて超えた回数が UNSAFE_STREAK_TO_STOP 以上の問いがあれば、SAFETY_QUESTION_NAMES の順で先のものの
           unsafe_screen_reason を返す
        probabilities は validate_probabilities の値 (すべての名前を含む)。
        """
        raise NotImplementedError

    def record_failure(self) -> str | None:
        """失敗した確かめ 1 回を数え、止まる理由を返す。止めなければ None。

        続けて失敗した回数を 1 増やし、max_errors 以上なら safety_failure_reason(回数) を返す。
        問いごとの続けて超えた回数は変えない (0 に戻さない)。失敗した確かめをはさんでも、その前後の答えを続けて数える
        ため、「超えた → 失敗 → 超えた」で止まり、超えた・失敗が交互に続いても止まる (失敗の回数は、答えのあった
        確かめのたびに 0 に戻るため、交互のときは失敗の回数では止まらない)。
        """
        raise NotImplementedError


def next_check_wait_s(interval_s: float, t_sent: float, t_received: float) -> float:
    """監視の確かめの応答を受け取った後、次の確かめを送るまでに待つ秒数を返す。

    前の確かめを送ってから interval_s 秒ごとに送る。応答を待つ間に interval_s 秒を過ぎていたら、待たずに送る
    (前の確かめの応答を待ってから次を送る): max(interval_s - (t_received - t_sent), 0.0)。
    """
    raise NotImplementedError
