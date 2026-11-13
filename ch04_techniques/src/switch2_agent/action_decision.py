"""問いごとの答え (選択肢ごとの確率と、真である確率) から、押下状態・短押し・スティックの傾き・advisor の呼び出しを
決める純粋な関数。

ボタンとスティックの方向は選択肢の問い、スティックの強さと advisor は確率の問い (questions のモジュールの docstring)。
しきい値と比べるのは advisor の問いだけで、確率がしきい値を超えた (>) ときに真とする。しきい値ちょうどは偽。
I/O・スレッド・時刻を扱わない。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_remote.controller_state import PadState

from switch2_agent.questions import (
    ADVISOR_QUESTION_NAME,
    HOLD_CHOICE,
    NONE_CHOICE,
    STICK_AXIS_DIRECTIONS,
    STICK_STRENGTH_LEVELS,
    TAP_CHOICE,
    Question,
)

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
    check_answers(probabilities, choices, questions)
    adopted = adopt_buttons(choices, questions)
    remove_opposite_dpad(adopted)
    held_buttons = frozenset(
        b for b, (kind, _) in adopted.items() if kind == HOLD_CHOICE
    )
    taps = frozenset(b for b, (kind, _) in adopted.items() if kind == TAP_CHOICE)
    advisor_probability = probabilities[ADVISOR_QUESTION_NAME]
    return PadAction(
        held=PadState(held_buttons, decide_sticks(probabilities, choices, questions)),
        taps=taps,
        advisor_requested=advisor_probability > thresholds.advisor,
        advisor_probability=advisor_probability,
    )


def check_answers(
    probabilities: Mapping[str, float],
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[Question],
) -> None:
    """questions のすべての問いの答えがあるかを確かめ、足りなければ ValueError にする (decide_action の docstring)。"""
    missing: list[str] = []
    for question in questions:
        if question.answer_type == "probability":
            if question.name not in probabilities:
                missing.append(question.name)
            continue
        values = choices.get(question.name)
        if values is None:
            missing.append(question.name)
            continue
        missing.extend(
            f"{question.name}.{choice.name}"
            for choice in question.choices
            if choice.name not in values
        )
    if missing:
        raise ValueError(f"missing answers: {' '.join(missing)}")


def adopt_buttons(
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[Question],
) -> dict[str, tuple[str, float]]:
    """ボタンの問いの答えから、押すボタン → (HOLD_CHOICE か TAP_CHOICE, 押す側の合計) を返す (decide_action の 1)。

    押す側の合計 p_hold + p_tap が p_none より大きいボタンだけを入れる。p_hold >= p_tap なら HOLD_CHOICE、
    そうでなければ TAP_CHOICE。
    """
    adopted: dict[str, tuple[str, float]] = {}
    for question in questions:
        if question.kind != "button":
            continue
        values = choices[question.name]
        hold = values[HOLD_CHOICE]
        tap = values[TAP_CHOICE]
        press_side = hold + tap
        if press_side > values[NONE_CHOICE]:
            kind = HOLD_CHOICE if hold >= tap else TAP_CHOICE
            adopted[question.target] = (kind, press_side)
    return adopted


def remove_opposite_dpad(adopted: dict[str, tuple[str, float]]) -> None:
    """adopted (adopt_buttons の結果) で十字キーの相反する方向が両方とも採られていたら、押す側の合計が低いほうを外す。

    同じ値なら両方とも外す。adopted をその場で書き換える (decide_action の 2)。
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


def choose_direction(question: Question, values: Mapping[str, float]) -> str:
    """スティックの方向の問いの答え values から、確率が最も高い選択肢の name を返す (decide_action の 3)。

    最大の確率の選択肢が 2 つ以上あれば NONE_CHOICE を返す。question.choices に無い name は見ない。
    """
    probabilities = {choice.name: values[choice.name] for choice in question.choices}
    highest = max(probabilities.values())
    top = [
        name for name, probability in probabilities.items() if probability == highest
    ]
    return top[0] if len(top) == 1 else NONE_CHOICE


def stick_strength(p50: float, p75: float, p100: float) -> float:
    """強さの問いの 50・75・100 の確率から、傾きの大きさ (0.25〜1.0) を返す (decide_action の 4)。

    上の段階が下の段階を超えないよう切り下げ (q50 = p50、q75 = min(p75, q50)、q100 = min(p100, q75))、
    STICK_MIN_STRENGTH + STICK_STRENGTH_STEP × (q50 + q75 + q100) を返す。
    """
    q50 = p50
    q75 = min(p75, q50)
    q100 = min(p100, q75)
    return STICK_MIN_STRENGTH + STICK_STRENGTH_STEP * (q50 + q75 + q100)


def decide_sticks(
    probabilities: Mapping[str, float],
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[Question],
) -> dict[str, tuple[float, float]]:
    """スティックの方向と強さの問いから、スティック名 → (x, y) を返す (decide_action の 3 と 4)。

    x と y の両方が 0 のスティックは含めない。
    """
    # 軸 (例: LS_x) → 段階 → その強さの問いの確率
    levels: dict[str, dict[int, float]] = {}
    for question in questions:
        if question.kind == "stick_strength":
            axis_target, level = question.target.rsplit("_", 1)
            levels.setdefault(axis_target, {})[int(level)] = probabilities[
                question.name
            ]
    values: dict[str, dict[str, float]] = {}
    for question in questions:
        if question.kind != "stick_direction":
            continue
        stick, axis = question.target.split("_")
        direction = choose_direction(question, choices[question.name])
        value = 0.0
        if direction != NONE_CHOICE:
            strengths = levels[question.target]
            magnitude = stick_strength(
                strengths[STICK_STRENGTH_LEVELS[0]],
                strengths[STICK_STRENGTH_LEVELS[1]],
                strengths[STICK_STRENGTH_LEVELS[2]],
            )
            _, positive = STICK_AXIS_DIRECTIONS[axis]
            value = magnitude if direction == positive else -magnitude
        values.setdefault(stick, {})[axis] = value
    sticks: dict[str, tuple[float, float]] = {}
    for stick, axes in values.items():
        x = axes.get("x", 0.0)
        y = axes.get("y", 0.0)
        if x != 0.0 or y != 0.0:
            sticks[stick] = (x, y)
    return sticks
