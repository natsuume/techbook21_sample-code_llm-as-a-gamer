"""問いごとの答え (選択肢ごとの確率と、真である確率) から、押下状態・短押し・スティックの傾き・advisor の呼び出しを
決める純粋な関数。

ボタンとスティックの方向は選択肢の問い、スティックの強さと advisor は確率の問い (questions のモジュールの docstring)。
しきい値と比べるのは advisor の問いだけで、確率がしきい値を超えた (>) ときに真とする。しきい値ちょうどは偽。
I/O・スレッド・時刻を扱わない。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_remote.controller_state import PadState

from switch2_agent.questions import ADVISOR_QUESTION_NAME, Question

DEFAULT_THRESHOLD = 0.5  # advisor の問いのしきい値の既定値
# 十字キーの相反する方向の組
OPPOSITE_DPAD_PAIRS = (("UP", "DOWN"), ("LEFT", "RIGHT"))
# スティックを倒すときの傾きの最小値と、強さの問いの段階 1 つあたりの傾きの増分。傾きは 0.25〜1.0 になる
STICK_MIN_STRENGTH = 0.25
STICK_STRENGTH_STEP = 0.25


@dataclass(frozen=True)
class Thresholds:
    """しきい値。advisor の問いの確率がこれを超えたら (>) 真にする。0〜1 (両端を含む)。範囲外は ValueError。

    ボタンとスティックの問いは、選択肢の問いと段階の確率の問いで決めるため、しきい値を使わない (decide_action)。
    """

    advisor: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        """advisor が 0〜1 の範囲の外なら ValueError にする。"""
        if not 0.0 <= self.advisor <= 1.0:
            raise ValueError(f"threshold for advisor must be 0..1: {self.advisor}")


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
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[Question],
    thresholds: Thresholds,
) -> PadAction:
    """答え (確率の問いの probabilities と、選択肢の問いの choices) と questions から PadAction を決める。

    probabilities は確率の問いの name → 確率 (Decision.probabilities)、choices は選択肢の問いの name →
    (選択肢の name → 確率) (Decision.choices)。questions には questions.build_questions の一覧を渡す。
    questions の確率の問いの name が probabilities に、選択肢の問いの name が choices に 1 つでも足りないか、
    choices の答えに、その問いのどれかの選択肢の確率が足りなければ ValueError にする (スティックの強さの問いの
    答えは、その軸の方向が none になるときも要る)。questions に無い name と選択肢の name は無視する。

    1. ボタン: ボタンの問い (button_<ボタン>) の none・hold・tap の確率を p_none・p_hold・p_tap とする。
       押す側の合計 p_hold + p_tap が p_none より大きい (>) ときだけ押す (同じ値なら押さない)。押すなら、
       p_hold >= p_tap のとき押下状態 (held)、そうでなければ短押し (taps) にする (同じ値なら押下状態)。
       押すボタンは、押す側の合計 p_hold + p_tap を「採った確率」として持つ
    2. 十字キー: 相反する方向 (UP と DOWN、LEFT と RIGHT) が、押下状態と短押しを合わせて両方とも採られたら、
       1 の採った確率 (押す側の合計) が高いほうを残す (同じ値なら両方とも外す)
    3. スティックの方向: 軸の問い (stick_<スティック>_x と stick_<スティック>_y) ごとに、確率が最も高い選択肢を採る。
       最大の確率の選択肢が 2 つ以上あれば none にする (none が最大に含まれる場合も、左と右や上と下が同じ値の場合も)
    4. スティックの強さ: 方向が none でない軸だけ計算する。その軸の強さの問い (stick_<スティック>_<軸>_at_least_<段階>)
       の 50・75・100 の確率を p50・p75・p100 とし、上の段階が下の段階を超えないよう切り下げる:
       q50 = p50、q75 = min(p75, q50)、q100 = min(p100, q75)。
       強さ s = STICK_MIN_STRENGTH + STICK_STRENGTH_STEP × (q50 + q75 + q100) (0.25〜1.0)。
       軸の値は、正の向き (right / up) なら +s、負の向き (left / down) なら −s、方向が none なら 0
       (questions.STICK_AXIS_DIRECTIONS)。x と y の両方が 0 のスティックは held に入れない
    5. advisor: advisor_requested = probabilities["advisor"] > thresholds.advisor (同じ値なら偽)、
       advisor_probability = probabilities["advisor"]
    """
    missing = [q.name for q in questions if q.name not in probabilities]
    if missing:
        raise ValueError(f"missing probabilities: {' '.join(missing)}")
    adopted = adopt_buttons(probabilities, questions)
    remove_opposite_dpad(adopted)
    held_buttons = frozenset(b for b, (kind, _) in adopted.items() if kind == "press")
    taps = frozenset(b for b, (kind, _) in adopted.items() if kind == "tap")
    advisor_probability = probabilities[ADVISOR_QUESTION_NAME]
    return PadAction(
        held=PadState(held_buttons, decide_sticks(probabilities, questions)),
        taps=taps,
        advisor_requested=advisor_probability > thresholds.advisor,
        advisor_probability=advisor_probability,
    )


def adopt_buttons(
    probabilities: Mapping[str, float],
    questions: Sequence[Question],
) -> dict[str, tuple[str, float]]:
    """kind が press / tap の問いのうち、確率が DEFAULT_THRESHOLD を超えた問いから、
    ボタン → (採った種類 "press" / "tap", その問いの確率) を返す。

    同じボタンで両方が超えたら、確率の高いほうを採る (同じ値なら press)。
    """
    adopted: dict[str, tuple[str, float]] = {}
    for question in questions:
        if question.kind not in ("press", "tap"):
            continue
        probability = probabilities[question.name]
        if probability <= DEFAULT_THRESHOLD:
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
) -> dict[str, tuple[float, float]]:
    """kind が stick の問いから、スティック名 → (x, y) を返す。x と y の両方が 0 のスティックは含めない。

    確率が DEFAULT_THRESHOLD を超えた方向の確率だけを使い、
    x = RIGHT の確率 − LEFT の確率、y = UP の確率 − DOWN の確率 (超えなかった方向は 0 として扱う) とする。
    """
    true_probabilities: dict[str, dict[str, float]] = {}
    for question in questions:
        if question.kind != "stick":
            continue
        stick, direction = question.target.split("_", 1)
        probability = probabilities[question.name]
        directions = true_probabilities.setdefault(stick, {})
        directions[direction] = probability if probability > DEFAULT_THRESHOLD else 0.0
    sticks: dict[str, tuple[float, float]] = {}
    for stick, directions in true_probabilities.items():
        x = directions.get("RIGHT", 0.0) - directions.get("LEFT", 0.0)
        y = directions.get("UP", 0.0) - directions.get("DOWN", 0.0)
        if x != 0.0 or y != 0.0:
            sticks[stick] = (x, y)
    return sticks
