"""問いごとの確率から、押下状態・短押し・スティックの傾き・advisor の呼び出しを決める純粋な関数。

問いが「真」になるのは、確率がその種類のしきい値を超えた (>) とき。しきい値ちょうどは偽。
I/O・スレッド・時刻を扱わない。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_remote.controller_state import PadState

from switch2_agent.questions import Question

DEFAULT_THRESHOLD = 0.5  # 各種類のしきい値の既定値


@dataclass(frozen=True)
class Thresholds:
    """問いの種類ごとのしきい値。各値は 0〜1 (両端を含む)。範囲外は ValueError。"""

    press: float = DEFAULT_THRESHOLD
    tap: float = DEFAULT_THRESHOLD
    stick: float = DEFAULT_THRESHOLD
    advisor: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        """press・tap・stick・advisor のどれかが 0〜1 の範囲の外なら ValueError にする。"""
        raise NotImplementedError


@dataclass(frozen=True)
class PadAction:
    """1 回の応答から決めた操作。

    held                  押した状態にするボタンと、倒すスティック (次の判断まで続く)
    taps                  今 1 回だけ短く押すボタン名の集合 (held のボタンとは重ならない)
    advisor_requested     advisor に戦略の更新を求めるか
    advisor_probability   advisor の問いの確率
    """

    held: PadState
    taps: frozenset[str]
    advisor_requested: bool
    advisor_probability: float


def decide_action(
    probabilities: Mapping[str, float],
    questions: Sequence[Question],
    thresholds: Thresholds,
) -> PadAction:
    """probabilities (問いの name → 確率) と questions から PadAction を決める。

    questions には advisor の問い (name が "advisor") を含む一覧 (questions.build_questions) を渡す。
    probabilities に questions の name が 1 つでも足りなければ ValueError にする。

    1. ボタン: press_<ボタン> が thresholds.press を、tap_<ボタン> が thresholds.tap を超えたら真。
       同じボタンで両方が真なら、確率の高いほうだけを採る (同じ値なら押下状態を採る)
    2. 十字キー: 相反する方向 (UP と DOWN、LEFT と RIGHT) が、押下状態と短押しを合わせて両方とも採られたら、
       採った側の問いの確率が高いほうを残す (同じ値なら両方とも外す)
    3. スティック: stick_<スティック>_<方向> が thresholds.stick を超えたら真。
       横は RIGHT と LEFT、縦は UP と DOWN から決め、右・上を正、左・下を負とする。
       片方だけが真ならその確率を傾きにし (例: LEFT だけが 0.7 なら -0.7)、両方が真なら差
       (RIGHT − LEFT、UP − DOWN) にし、どちらも偽なら 0 にする。横と縦の両方が 0 のスティックは held に入れない
    4. advisor_requested = probabilities["advisor"] > thresholds.advisor、advisor_probability = probabilities["advisor"]
    """
    raise NotImplementedError
