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
「超えた」は、確率がしきい値より大きいこと (ちょうど同じ値は超えない)。
止める条件に使う問いは safety_questions.SAFETY_QUESTION_NAMES の 3 つ (online・purchase・system_settings) だけで、
本体の画面の照合 (system_screen) の確率と結果では止めない (HOME メニューやユーザー選択が映っていても、3 つの問いが
超えなければ止めない)。照合の確率は validate_system_screen_probabilities で確かめ、合わなければ確かめの失敗とする。
同時に複数の問いが条件に当たったら、SAFETY_QUESTION_NAMES の順で先の問いの名前を理由に書く。
"""

import math
from collections.abc import Mapping

from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
)

# 同じ問いがしきい値を超えた確かめがこの回数続いたら止める
UNSAFE_STREAK_TO_STOP = 2
REASON_SAFETY_FAILED_AT_START = "safety check failed at start"


def unsafe_screen_reason(name: str) -> str:
    """監視で問い name の画面を見つけて止まるときの理由 `unsafe screen: <name>` を返す。"""
    return f"unsafe screen: {name}"


def unsafe_screen_at_start_reason(name: str) -> str:
    """実行を始めるときの確かめで問い name の画面を見つけて止まるときの理由 `unsafe screen at start: <name>` を返す。"""
    return f"unsafe screen at start: {name}"


def safety_failure_reason(count: int) -> str:
    """監視の確かめの失敗が count 回続いて止まるときの理由 `safety check failed <count> times in a row` を返す。"""
    return f"safety check failed {count} times in a row"


def validate_probabilities(probabilities: Mapping[str, object]) -> dict[str, float]:
    """SafetyChecker の答えの確率を確かめ、SAFETY_QUESTION_NAMES の順の (名前 → 確率) の dict にして返す。

    SAFETY_QUESTION_NAMES のどれかの名前が無い、値が数 (int か float。bool は除く) でない、有限でない、0〜1 の外なら
    ValueError (安全の監視は、確かめの失敗として数える)。SAFETY_QUESTION_NAMES に無い名前は無視する。
    """
    return _validate_named_probabilities(
        probabilities, SAFETY_QUESTION_NAMES, "the safety question"
    )


def validate_system_screen_probabilities(
    probabilities: Mapping[str, object],
) -> dict[str, float]:
    """SafetyChecker の答えの照合の確率 (SafetyAnswer.system_screen) を確かめ、
    system_screen.system_screen_references.SYSTEM_SCREEN_CHOICE_NAMES の順の (選択肢の名前 → 確率) の dict にして返す。

    SYSTEM_SCREEN_CHOICE_NAMES のどれかの名前が無い (答えが空のときを含む)、値が数 (int か float。bool は除く) でない、
    有限でない、0〜1 の外なら ValueError (安全の監視は、確かめの失敗として数える)。SYSTEM_SCREEN_CHOICE_NAMES に無い
    名前は無視する。確率の合計は確かめない。
    """
    return _validate_named_probabilities(
        probabilities, SYSTEM_SCREEN_CHOICE_NAMES, "the system screen choice"
    )


def _validate_named_probabilities(
    probabilities: Mapping[str, object], names: tuple[str, ...], label: str
) -> dict[str, float]:
    """probabilities から names の順の (名前 → 確率) の dict を作る。names に無い名前は無視する。

    names のどれかの名前が無いか、値が 0〜1 の有限の数 (_as_probability) でなければ ValueError。label は、名前が
    無いときの例外のメッセージに書く、名前の種類の説明。
    """
    result: dict[str, float] = {}
    for name in names:
        if name not in probabilities:
            raise ValueError(f"no probability for {label}: {name}")
        probability = _as_probability(probabilities[name])
        if probability is None:
            raise ValueError(
                f"probability of {name} is not a number in 0..1: "
                f"{probabilities[name]!r}"
            )
        result[name] = probability
    return result


def _as_probability(value: object) -> float | None:
    """value が 0〜1 の有限の数 (int か float。bool は除く) なら float にして返し、そうでなければ None を返す。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    probability = float(value)
    if math.isfinite(probability) and 0.0 <= probability <= 1.0:
        return probability
    return None


def names_over_threshold(
    probabilities: Mapping[str, float], threshold: float
) -> tuple[str, ...]:
    """SAFETY_QUESTION_NAMES のうち、確率が threshold を超えた (ちょうど同じ値は超えない) 名前を、
    SAFETY_QUESTION_NAMES の順で返す。probabilities は validate_probabilities の値 (すべての名前を含む)。"""
    return tuple(
        name for name in SAFETY_QUESTION_NAMES if probabilities[name] > threshold
    )


def start_stop_reason(
    probabilities: Mapping[str, float] | None, threshold: float
) -> str | None:
    """実行を始めるときの確かめの結果から、止まる理由を返す。止めなければ None。

    probabilities が None (確かめが失敗した) なら REASON_SAFETY_FAILED_AT_START。
    names_over_threshold(probabilities, threshold) が空でなければ、その先頭の名前の
    unsafe_screen_at_start_reason。どちらでもなければ None。
    """
    if probabilities is None:
        return REASON_SAFETY_FAILED_AT_START
    over = names_over_threshold(probabilities, threshold)
    if over:
        return unsafe_screen_at_start_reason(over[0])
    return None


class MonitorJudge:
    """監視の確かめの結果を順に受け取り、止まる条件 (モジュールの docstring) に当たったら理由を返す。

    threshold    しきい値 (--threshold-safety)
    max_errors   確かめの失敗がこの回数続いたら止める (--max-safety-errors)
    1 つのスレッド (安全の監視) からだけ呼ぶ。止まる理由を返した後も呼んでよい (同じ規則で数え続ける)。
    """

    def __init__(self, threshold: float, max_errors: int) -> None:
        """続けて超えた回数と、続けて失敗した回数を 0 にして作る。"""
        self.threshold = threshold
        self.max_errors = max_errors
        # 問いの名前 → 答えのあった確かめで続けてしきい値を超えた回数
        self._streaks: dict[str, int] = dict.fromkeys(SAFETY_QUESTION_NAMES, 0)
        self._consecutive_errors = 0

    @property
    def consecutive_errors(self) -> int:
        """確かめの失敗が続いている回数 (答えを受け取ると 0 に戻る)。"""
        return self._consecutive_errors

    def streak(self, name: str) -> int:
        """問い name の確率が、続けてしきい値を超えた確かめの回数を返す。SAFETY_QUESTION_NAMES に無い名前は常に 0。"""
        return self._streaks.get(name, 0)

    def record_answer(self, probabilities: Mapping[str, float]) -> str | None:
        """答えのあった確かめ 1 回を数え、止まる理由を返す。止めなければ None。

        1. 続けて失敗した回数を 0 に戻す
        2. SAFETY_QUESTION_NAMES の問いごとに、確率が threshold を超えていれば続けて超えた回数を 1 増やし、
           超えていなければ 0 に戻す
        3. 続けて超えた回数が UNSAFE_STREAK_TO_STOP 以上の問いがあれば、SAFETY_QUESTION_NAMES の順で先のものの
           unsafe_screen_reason を返す
        probabilities は validate_probabilities の値 (すべての名前を含む)。
        """
        self._consecutive_errors = 0
        for name in SAFETY_QUESTION_NAMES:
            if probabilities[name] > self.threshold:
                self._streaks[name] += 1
            else:
                self._streaks[name] = 0
        for name in SAFETY_QUESTION_NAMES:
            if self._streaks[name] >= UNSAFE_STREAK_TO_STOP:
                return unsafe_screen_reason(name)
        return None

    def record_failure(self) -> str | None:
        """失敗した確かめ 1 回を数え、止まる理由を返す。止めなければ None。

        続けて失敗した回数を 1 増やし、max_errors 以上なら safety_failure_reason(回数) を返す。
        問いごとの続けて超えた回数は変えない (0 に戻さない)。失敗した確かめをはさんでも、その前後の答えを続けて数える
        ため、「超えた → 失敗 → 超えた」で止まり、超えた・失敗が交互に続いても止まる (失敗の回数は、答えのあった
        確かめのたびに 0 に戻るため、交互のときは失敗の回数では止まらない)。
        """
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_errors:
            return safety_failure_reason(self._consecutive_errors)
        return None


def next_check_wait_s(interval_s: float, t_sent: float, t_received: float) -> float:
    """監視の確かめの応答を受け取った後、次の確かめを送るまでに待つ秒数を返す。

    前の確かめを送ってから interval_s 秒ごとに送る。応答を待つ間に interval_s 秒を過ぎていたら、待たずに送る
    (前の確かめの応答を待ってから次を送る): max(interval_s - (t_received - t_sent), 0.0)。
    """
    return max(interval_s - (t_received - t_sent), 0.0)
