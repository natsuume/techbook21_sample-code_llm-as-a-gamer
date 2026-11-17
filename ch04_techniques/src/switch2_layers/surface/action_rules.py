"""Jev の答えから、surface の出力 (操作・deep を呼ぶ種類・情報ラベル・場面転換・box を使う対象) を決める純粋な関数。

答えは 2 つの map で受け取る (surface.jev_client.JevAnswers の choices と nouls):
    choices   選択肢の問いの ID → (選択肢の名前 → 確率)
    nouls     noul の問いの ID → 真である確率
問いの ID と選択肢の名前は surface.surface_questions の規則に従う。I/O・スレッド・時刻を扱わない。

操作の決め方 (decide_pad_action。hold_margin は引数で受け取る 0〜1 の値):
    1. ボタン (十字キー以外): none・tap・hold の確率を p_none・p_tap・p_hold とする。p_tap + p_hold が p_none より
       大きい (>) ときだけ押す (同じ値なら押さない。同じ値かは probabilities_equal で判定する)。押し方は、下の
       「押し方の選び方」で決める
    2. 十字キー: dpad_direction の none の確率を p_none、8 方向の確率の和を p_dirs とする。p_dirs が p_none より
       大きい (>) ときだけ押す (同じ値なら押さない。同じ値かは probabilities_equal で判定する)。押すときは、8 方向の
       うち確率が最も高い方向を採り、DPAD_DIRECTION_BUTTONS のボタン (斜めは 2 つ) を押す。最も高い確率の方向が
       2 つ以上 (probabilities_equal) なら押さない。押し方は dpad_press の答えから「押し方の選び方」で決め、斜めの
       2 つのボタンは同じ押し方にする。押さないときは dpad_press の答えを使わない。十字キーと、ほかのボタン・
       スティックは同時に押せる
    3. スティックの向き: 軸ごとに、確率が最も高い選択肢を採る。最大の確率の選択肢が 2 つ以上あれば none
    4. スティックの強さ: 向きが none でない軸だけ計算する。50・75・100 の確率を p50・p75・p100 とし、
       q50 = p50、q75 = min(p75, q50)、q100 = min(p100, q75) と単調に下がるよう切り下げて、
       強さ s = STICK_MIN_STRENGTH + STICK_STRENGTH_STEP × (q50 + q75 + q100) (0.25〜1.0) にする。
       軸の値は、正の向き (right / up) なら +s、負の向き (left / down) なら −s、none なら 0
押し方の選び方 (1 のボタンと 2 の十字キーで共通。スティックには使わない):
    tap と hold の確率を p_tap・p_hold とする。p_hold − p_tap が hold_margin より大きい (>) ときだけ押し続け (held)
    にし、それ以外は短押し (taps) にする。差が hold_margin と同じ値 (probabilities_equal で判定する) なら短押し。
    hold_margin が 0 なら p_hold が p_tap より高いときに押し続け、1 なら常に短押しになる。押し続けには秒数を
    付けない (hold_seconds は空)。押し続けは次の操作を送るまで続き、次の判断でも押し続けを選べば離さずに続く
ほかの出力:
    deep を呼ぶ種類   advisor の問いの確率が threshold_advisor を超えた (>) 種類
    情報ラベル        INFO_LABEL_NAMES の順に、名前と確率 (しきい値で選ばず、すべて記録する)
    場面転換          scene_changed の確率が SCENE_CHANGE_THRESHOLD を超えた (>) とき True
    box を使う対象    box の存在の確率が BOX_EXISTS_THRESHOLD 以上の対象 (未満なら、その対象の box を使わない)
"""

import math
from collections.abc import Mapping, Sequence

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.surface.box_targets import BoxTarget
from switch2_layers.surface.surface_questions import (
    ADVISOR_KINDS,
    DPAD_DIRECTION_BUTTONS,
    DPAD_PRESS_QUESTION_NAME,
    HOLD_CHOICE,
    INFO_LABEL_NAMES,
    NONE_CHOICE,
    SCENE_CHANGE_QUESTION_NAME,
    STICK_AXIS_DIRECTIONS,
    STICK_STRENGTH_LEVELS,
    TAP_CHOICE,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
    box_exists_question_name,
)
from switch2_layers.surface.surface_types import InfoLabel

# スティックを倒すときの傾きの最小値と、強さの段階 1 つあたりの増分。傾きは 0.25〜1.0 になる
STICK_MIN_STRENGTH = 0.25
STICK_STRENGTH_STEP = 0.25
# 確率を足した値どうしを比べるときに、同じ値とみなす差の上限 (浮動小数点数の丸めの差を同じ値として扱う)
PROBABILITY_TOLERANCE = 1e-9
SCENE_CHANGE_THRESHOLD = 0.5  # 場面転換の確率がこれを超えたら (>) 検知したとする
BOX_EXISTS_THRESHOLD = 0.5  # box の存在の確率がこれ未満なら、その対象の box を使わない


def probabilities_equal(a: float, b: float) -> bool:
    """a と b の差が PROBABILITY_TOLERANCE 以下なら True を返す。"""
    return math.isclose(a, b, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE)


def check_answers(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
) -> None:
    """questions のうち choice と noul の問いの答えがすべてあるかを確かめ、足りなければ ValueError にする。

    choice の問いは choices に ID があり、その問いのすべての選択肢の確率があること。noul の問いは nouls に ID が
    あること。box の問いは確かめない。メッセージには足りない ID (選択肢なら `<ID>.<選択肢>`) を並べる。
    """
    missing: list[str] = []
    for question in questions:
        if question.question_type is QuestionType.NOUL:
            if question.name not in nouls:
                missing.append(question.name)
        elif question.question_type is QuestionType.CHOICE:
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


def _is_greater(a: float, b: float) -> bool:
    """a が b より大きく、同じ値 (probabilities_equal) でなければ True を返す。"""
    return a > b and not probabilities_equal(a, b)


def _is_hold(values: Mapping[str, float], hold_margin: float) -> bool:
    """押し方の答え values (tap と hold の確率) から、押し続けにするなら True を返す (押し方の選び方)。

    hold − tap が hold_margin より大きいときだけ True。同じ値 (probabilities_equal) なら False (短押し)。
    """
    return _is_greater(values[HOLD_CHOICE] - values[TAP_CHOICE], hold_margin)


def _decide_buttons(
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[SurfaceQuestion],
    hold_margin: float,
) -> tuple[set[str], set[str]]:
    """ボタン (十字キー以外) の問いの答えから、(押し続けるボタン, 短押しのボタン) を返す (操作の決め方の 1)。"""
    held: set[str] = set()
    taps: set[str] = set()
    for question in questions:
        if question.kind is not QuestionKind.BUTTON:
            continue
        values = choices[question.name]
        if not _is_greater(
            values[TAP_CHOICE] + values[HOLD_CHOICE], values[NONE_CHOICE]
        ):
            continue
        (held if _is_hold(values, hold_margin) else taps).add(question.target)
    return held, taps


def _decide_dpad(
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[SurfaceQuestion],
    hold_margin: float,
) -> tuple[set[str], set[str]]:
    """十字キーの方向と押し方の問いの答えから、(押し続けるボタン, 短押しのボタン) を返す (操作の決め方の 2)。

    押さないときは空の集合 2 つを返し、押し方の答えは見ない。
    """
    for question in questions:
        if question.kind is not QuestionKind.DPAD_DIRECTION:
            continue
        values = choices[question.name]
        directions = [
            choice.name for choice in question.choices if choice.name != NONE_CHOICE
        ]
        if not _is_greater(
            sum(values[name] for name in directions), values[NONE_CHOICE]
        ):
            return set(), set()
        highest = max(values[name] for name in directions)
        top = [
            name for name in directions if probabilities_equal(values[name], highest)
        ]
        if len(top) > 1:
            return set(), set()
        buttons = set(DPAD_DIRECTION_BUTTONS[top[0]])
        if _is_hold(choices[DPAD_PRESS_QUESTION_NAME], hold_margin):
            return buttons, set()
        return set(), buttons
    return set(), set()


def choose_direction(question: SurfaceQuestion, values: Mapping[str, float]) -> str:
    """スティックの向きの問いの答え values から、確率が最も高い選択肢の名前を返す (操作の決め方の 3)。

    最大の確率の選択肢が 2 つ以上あれば NONE_CHOICE。question.choices に無い名前は見ない。
    """
    probabilities = {choice.name: values[choice.name] for choice in question.choices}
    highest = max(probabilities.values())
    top = [name for name, value in probabilities.items() if value == highest]
    return top[0] if len(top) == 1 else NONE_CHOICE


def stick_strength(p50: float, p75: float, p100: float) -> float:
    """強さの問いの 50・75・100 の確率から、傾きの大きさ (0.25〜1.0) を返す (操作の決め方の 4)。"""
    q50 = p50
    q75 = min(p75, q50)
    q100 = min(p100, q75)
    return STICK_MIN_STRENGTH + STICK_STRENGTH_STEP * (q50 + q75 + q100)


def decide_sticks(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
) -> dict[str, tuple[float, float]]:
    """スティックの向きと強さの問いから、スティック名 (LS / RS) → (x, y) を返す (操作の決め方の 3 と 4)。

    両方の軸が 0 のスティックも (0.0, 0.0) として含める。
    """
    # 軸 (例: LS_x) → 段階 → その強さの問いの確率
    levels: dict[str, dict[int, float]] = {}
    for question in questions:
        if question.kind is QuestionKind.STICK_STRENGTH:
            axis_target, level = question.target.rsplit("_", 1)
            levels.setdefault(axis_target, {})[int(level)] = nouls[question.name]
    # スティック → 軸 → 値
    values: dict[str, dict[str, float]] = {}
    for question in questions:
        if question.kind is not QuestionKind.STICK_DIRECTION:
            continue
        stick, axis = question.target.split("_")
        direction = choose_direction(question, choices[question.name])
        value = 0.0
        if direction != NONE_CHOICE:
            strengths = levels[question.target]
            magnitude = stick_strength(
                *(strengths[level] for level in STICK_STRENGTH_LEVELS)
            )
            _, positive = STICK_AXIS_DIRECTIONS[axis]
            value = magnitude if direction == positive else -magnitude
        values.setdefault(stick, {})[axis] = value
    return {
        stick: (axes.get("x", 0.0), axes.get("y", 0.0))
        for stick, axes in values.items()
    }


def decide_pad_action(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
    hold_margin: float,
) -> PadAction:
    """答えと questions から、操作 (モジュールの docstring の操作の決め方) を決めて返す。

    hold_margin は「押し方の選び方」の差の値 (0〜1。run では --hold-margin)。
    はじめに check_answers で答えがそろっているかを確かめる (スティックの強さの答えは、その軸の向きが none でも要る。
    dpad_press の答えは、十字キーを押さないときも要る)。
    held は押し続けにしたボタン (十字キーのボタンを含む)、taps は短押しにしたボタン (十字キーのボタンを含む)、
    hold_seconds は空、left_stick と right_stick は decide_sticks の LS と RS。
    questions に無い ID と選択肢は無視する。
    """
    check_answers(choices, nouls, questions)
    button_held, button_taps = _decide_buttons(choices, questions, hold_margin)
    dpad_held, dpad_taps = _decide_dpad(choices, questions, hold_margin)
    sticks = decide_sticks(choices, nouls, questions)
    return PadAction(
        held=frozenset(button_held | dpad_held),
        taps=frozenset(button_taps | dpad_taps),
        left_stick=sticks.get("LS", (0.0, 0.0)),
        right_stick=sticks.get("RS", (0.0, 0.0)),
    )


def decide_deep_kinds(
    nouls: Mapping[str, float], threshold_advisor: float
) -> frozenset[DocumentKind]:
    """advisor の問い (advisor_<種類>。surface_questions.ADVISOR_KINDS) の確率が threshold_advisor を超えた (>)
    種類の集合を返す。しきい値ちょうどは入れない。答えが足りなければ ValueError。"""
    probabilities = _noul_values(
        nouls, [f"advisor_{kind.value}" for kind in ADVISOR_KINDS]
    )
    return frozenset(
        kind
        for kind, probability in zip(ADVISOR_KINDS, probabilities, strict=True)
        if probability > threshold_advisor
    )


def decide_info_labels(nouls: Mapping[str, float]) -> tuple[InfoLabel, ...]:
    """情報ラベルの問い (info_<名前>) の答えを、INFO_LABEL_NAMES の順の InfoLabel(名前, 確率) にして返す。

    5 つすべてを返す (しきい値で選ばない)。答えが足りなければ ValueError。
    """
    probabilities = _noul_values(nouls, [f"info_{name}" for name in INFO_LABEL_NAMES])
    return tuple(
        InfoLabel(name, probability)
        for name, probability in zip(INFO_LABEL_NAMES, probabilities, strict=True)
    )


def decide_scene_changed(nouls: Mapping[str, float]) -> bool:
    """scene_changed の確率が SCENE_CHANGE_THRESHOLD を超えたら (>) True を返す。ちょうどは False。
    答えが無ければ ValueError。"""
    [probability] = _noul_values(nouls, [SCENE_CHANGE_QUESTION_NAME])
    return probability > SCENE_CHANGE_THRESHOLD


def visible_target_names(
    nouls: Mapping[str, float], targets: Sequence[BoxTarget]
) -> frozenset[str]:
    """box の存在の問い (box_exists_<番号>) の確率が BOX_EXISTS_THRESHOLD 以上の対象の名前の集合を返す。

    <番号> は targets の中の 1 始まりの位置。ちょうど BOX_EXISTS_THRESHOLD は含める。答えが足りなければ ValueError。
    """
    names = [box_exists_question_name(number) for number in range(1, len(targets) + 1)]
    probabilities = _noul_values(nouls, names)
    return frozenset(
        target.name
        for target, probability in zip(targets, probabilities, strict=True)
        if probability >= BOX_EXISTS_THRESHOLD
    )


def _noul_values(nouls: Mapping[str, float], names: Sequence[str]) -> list[float]:
    """names の順に nouls の確率を返す。足りない ID があれば、それを並べた ValueError にする。"""
    missing = [name for name in names if name not in nouls]
    if missing:
        raise ValueError(f"missing answers: {' '.join(missing)}")
    return [nouls[name] for name in names]
