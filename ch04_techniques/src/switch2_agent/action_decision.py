"""問いごとの確率から、押下状態・短押し・スティックの傾き・advisor の呼び出しを決める純粋な関数。

問いが「真」になるのは、確率がその種類のしきい値を超えた (>) とき。しきい値ちょうどは偽。
I/O・スレッド・時刻を扱わない。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_remote.controller_state import PadState

from switch2_agent.questions import ADVISOR_QUESTION_NAME, Question

DEFAULT_THRESHOLD = 0.5  # 各種類のしきい値の既定値
# 十字キーの相反する方向の組
OPPOSITE_DPAD_PAIRS = (("UP", "DOWN"), ("LEFT", "RIGHT"))


@dataclass(frozen=True)
class Thresholds:
    """問いの種類ごとのしきい値。各値は 0〜1 (両端を含む)。範囲外は ValueError。"""

    press: float = DEFAULT_THRESHOLD
    tap: float = DEFAULT_THRESHOLD
    stick: float = DEFAULT_THRESHOLD
    advisor: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        """press・tap・stick・advisor のどれかが 0〜1 の範囲の外なら ValueError にする。"""
        for kind in ("press", "tap", "stick", "advisor"):
            value = getattr(self, kind)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"threshold for {kind} must be 0..1: {value}")


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
    missing = [q.name for q in questions if q.name not in probabilities]
    if missing:
        raise ValueError(f"missing probabilities: {' '.join(missing)}")
    adopted = adopt_buttons(probabilities, questions, thresholds)
    remove_opposite_dpad(adopted)
    held_buttons = frozenset(b for b, (kind, _) in adopted.items() if kind == "press")
    taps = frozenset(b for b, (kind, _) in adopted.items() if kind == "tap")
    advisor_probability = probabilities[ADVISOR_QUESTION_NAME]
    return PadAction(
        held=PadState(
            held_buttons, decide_sticks(probabilities, questions, thresholds)
        ),
        taps=taps,
        advisor_requested=advisor_probability > thresholds.advisor,
        advisor_probability=advisor_probability,
    )


def adopt_buttons(
    probabilities: Mapping[str, float],
    questions: Sequence[Question],
    thresholds: Thresholds,
) -> dict[str, tuple[str, float]]:
    """真になった press / tap の問いから、ボタン → (採った種類 "press" / "tap", その問いの確率) を返す。

    同じボタンで両方が真なら、確率の高いほうを採る (同じ値なら press)。
    """
    adopted: dict[str, tuple[str, float]] = {}
    for question in questions:
        if question.kind == "press":
            threshold = thresholds.press
        elif question.kind == "tap":
            threshold = thresholds.tap
        else:
            continue
        probability = probabilities[question.name]
        if probability <= threshold:
            continue
        current = adopted.get(question.target)
        if current is None:
            adopted[question.target] = (question.kind, probability)
            continue
        _, current_probability = current
        if probability > current_probability or (
            probability == current_probability and question.kind == "press"
        ):
            adopted[question.target] = (question.kind, probability)
    return adopted


def remove_opposite_dpad(adopted: dict[str, tuple[str, float]]) -> None:
    """adopted (adopt_buttons の結果) で十字キーの相反する方向が両方とも採られていたら、確率の低いほうを外す。

    同じ確率なら両方とも外す。adopted をその場で書き換える。
    """
    for first, second in OPPOSITE_DPAD_PAIRS:
        if first not in adopted or second not in adopted:
            continue
        first_probability = adopted[first][1]
        second_probability = adopted[second][1]
        if first_probability >= second_probability:
            del adopted[second]
        if second_probability >= first_probability:
            del adopted[first]


def decide_sticks(
    probabilities: Mapping[str, float],
    questions: Sequence[Question],
    thresholds: Thresholds,
) -> dict[str, tuple[float, float]]:
    """stick の問いから、スティック名 → (x, y) を返す。x と y の両方が 0 のスティックは含めない。

    真になった方向 (確率が thresholds.stick を超えた方向) の確率だけを使い、
    x = RIGHT の確率 − LEFT の確率、y = UP の確率 − DOWN の確率 (偽の方向は 0 として扱う) とする。
    """
    true_probabilities: dict[str, dict[str, float]] = {}
    for question in questions:
        if question.kind != "stick":
            continue
        stick, direction = question.target.split("_", 1)
        probability = probabilities[question.name]
        directions = true_probabilities.setdefault(stick, {})
        directions[direction] = probability if probability > thresholds.stick else 0.0
    sticks: dict[str, tuple[float, float]] = {}
    for stick, directions in true_probabilities.items():
        x = directions.get("RIGHT", 0.0) - directions.get("LEFT", 0.0)
        y = directions.get("UP", 0.0) - directions.get("DOWN", 0.0)
        if x != 0.0 or y != 0.0:
            sticks[stick] = (x, y)
    return sticks
