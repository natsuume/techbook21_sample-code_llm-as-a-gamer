"""操作の決め方の規則。スティックの入出力の例は、switch2_agent の規則のテスト (tests/test_action_decision.py) と同じ。"""

import pytest

from switch2_layers.buttons import allowed_buttons
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.settings import DEFAULT_HOLD_MARGIN
from switch2_layers.surface.action_rules import (
    BOX_EXISTS_THRESHOLD,
    PROBABILITY_TOLERANCE,
    SCENE_CHANGE_THRESHOLD,
    STICK_MIN_STRENGTH,
    STICK_STRENGTH_STEP,
    decide_deep_kinds,
    decide_info_labels,
    decide_pad_action,
    decide_scene_changed,
    probabilities_equal,
    visible_target_names,
)
from switch2_layers.surface.box_targets import BoxTarget
from switch2_layers.surface.surface_questions import (
    NONE_CHOICE,
    QuestionType,
    SurfaceQuestion,
    build_surface_questions,
)
from switch2_layers.surface.surface_types import InfoLabel

TARGETS = (BoxTarget("自機", "勇者"), BoxTarget("敵", "スライム"))


def questions() -> tuple[SurfaceQuestion, ...]:
    return build_surface_questions(
        allowed_buttons(allow_home=False), TARGETS, set(), frame_count=1
    )


def neutral_answers() -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """選択肢の問いは none を 1.0 (ほかを 0.0)、noul の問いは 0.0 にした答え (choices, nouls) を返す。"""
    choices: dict[str, dict[str, float]] = {}
    nouls: dict[str, float] = {}
    for question in questions():
        if question.question_type is QuestionType.CHOICE:
            choices[question.name] = {
                choice.name: 1.0 if choice.name == NONE_CHOICE else 0.0
                for choice in question.choices
            }
        elif question.question_type is QuestionType.NOUL:
            nouls[question.name] = 0.0
    return choices, nouls


def decide(
    chosen: dict[str, dict[str, float]] | None = None,
    *,
    hold_margin: float = DEFAULT_HOLD_MARGIN,
    **noul_overrides: float,
) -> PadAction:
    """neutral_answers のうち、chosen と noul_overrides の問いだけを置き換えて decide_pad_action を呼ぶ。

    hold_margin は decide_pad_action に渡す押し方の選び方の差の値 (既定は DEFAULT_HOLD_MARGIN)。
    """
    choices, nouls = neutral_answers()
    for name, values in (chosen or {}).items():
        assert name in choices, f"unknown choice question: {name}"
        choices[name] = dict(values)
    for name, value in noul_overrides.items():
        assert name in nouls, f"unknown noul question: {name}"
        nouls[name] = value
    return decide_pad_action(choices, nouls, questions(), hold_margin)


def button(none: float, tap: float = 0.0, hold: float = 0.0) -> dict[str, float]:
    return {"none": none, "tap": tap, "hold": hold}


# 十字キーの方向の問いの、none 以外の選択肢の名前
DPAD_DIRECTION_NAMES = (
    "up",
    "right",
    "down",
    "left",
    "up_right",
    "down_right",
    "down_left",
    "up_left",
)


def dpad(none: float, **directions: float) -> dict[str, float]:
    """dpad_direction の答え。directions に無い方向の確率は 0.0。"""
    assert set(directions) <= set(DPAD_DIRECTION_NAMES), directions
    return {"none": none} | {
        name: directions.get(name, 0.0) for name in DPAD_DIRECTION_NAMES
    }


def press(tap: float, hold: float) -> dict[str, float]:
    """dpad_press の答え (none は無い)。"""
    return {"tap": tap, "hold": hold}


def x_axis(none: float, left: float, right: float) -> dict[str, float]:
    return {"none": none, "left": left, "right": right}


def y_axis(none: float, up: float, down: float) -> dict[str, float]:
    return {"none": none, "up": up, "down": down}


def strengths(axis: str, p50: float, p75: float, p100: float) -> dict[str, float]:
    return {
        f"stick_{axis}_at_least_50": p50,
        f"stick_{axis}_at_least_75": p75,
        f"stick_{axis}_at_least_100": p100,
    }


def test_constants_follow_the_issue():
    assert STICK_MIN_STRENGTH == 0.25
    assert STICK_STRENGTH_STEP == 0.25
    assert SCENE_CHANGE_THRESHOLD == 0.5
    assert BOX_EXISTS_THRESHOLD == 0.5


def test_neutral_answers_give_neutral_action():
    assert decide() == PadAction()


# --- ボタン ---


def test_tap_choice_gives_tap():
    action = decide({"button_B": button(0.1, tap=0.9)})
    assert action.held == frozenset()
    assert action.taps == frozenset({"B"})
    assert action.hold_seconds == {}


def test_hold_choice_gives_hold_without_seconds():
    # 押し続けは秒数を付けず、次の操作まで押したままにする
    action = decide({"button_A": button(0.1, hold=0.9)})
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset()
    assert action.hold_seconds == {}


def test_button_is_pressed_when_press_side_sum_exceeds_none_even_if_none_is_largest():
    # 押す側の合計 0.1 + 0.5 = 0.6 が none の 0.4 を超える
    action = decide({"button_A": button(0.4, tap=0.1, hold=0.5)})
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset()
    assert action.hold_seconds == {}


def test_button_is_not_pressed_when_press_side_sum_equals_none():
    action = decide(
        {
            "button_A": button(0.5, tap=0.2, hold=0.3),
            "button_B": button(0.4, tap=0.1, hold=0.5),
        }
    )
    assert action.held == frozenset({"B"})
    assert action.taps == frozenset()
    assert action.hold_seconds == {}


def test_button_is_not_pressed_when_press_side_sum_equals_none_up_to_rounding():
    assert 0.1 + 0.2 != 0.3
    action = decide({"button_A": button(0.3, tap=0.1, hold=0.2)})
    assert action.held == frozenset()
    assert action.taps == frozenset()
    assert action.hold_seconds == {}


def test_probabilities_equal_uses_tolerance():
    assert probabilities_equal(0.1 + 0.7, 0.3 + 0.5)
    assert probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE / 2)
    assert not probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE * 10)


def test_hold_only_when_hold_exceeds_tap_by_more_than_the_margin():
    action = decide(
        {
            # hold − tap = 0.3 > 0.1 → 押し続け
            "button_A": button(0.1, tap=0.3, hold=0.6),
            # hold − tap = 0.05 < 0.1 → hold の方が高くても短押し
            "button_B": button(0.15, tap=0.4, hold=0.45),
            # hold < tap → 短押し
            "button_X": button(0.1, tap=0.6, hold=0.3),
        },
        hold_margin=0.1,
    )
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset({"B", "X"})
    assert action.hold_seconds == {}


def test_difference_equal_to_the_margin_up_to_rounding_gives_tap():
    # 0.4 − 0.3 は浮動小数点数では 0.1 より少し大きいが、同じ値とみなして短押しにする
    assert 0.4 - 0.3 != 0.1
    assert 0.4 - 0.3 > 0.1
    action = decide({"button_A": button(0.3, tap=0.3, hold=0.4)}, hold_margin=0.1)
    assert action.held == frozenset()
    assert action.taps == frozenset({"A"})


def test_margin_zero_holds_when_hold_is_higher_and_taps_on_a_tie():
    action = decide(
        {
            "button_A": button(0.1, tap=0.4, hold=0.5),
            "button_B": button(0.2, tap=0.4, hold=0.4),
        },
        hold_margin=0.0,
    )
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset({"B"})
    assert action.hold_seconds == {}


def test_margin_one_always_taps():
    action = decide({"button_A": button(0.0, tap=0.0, hold=1.0)}, hold_margin=1.0)
    assert action.held == frozenset()
    assert action.taps == frozenset({"A"})


def test_hold_margin_decides_between_hold_and_tap_for_the_same_answers():
    # hold − tap = 0.15。margin 0.1 なら押し続け、margin 0.2 なら短押し
    assert 0.1 < 0.5 - 0.35 < 0.2
    answers = {"button_A": button(0.15, tap=0.35, hold=0.5)}
    with_small_margin = decide(answers, hold_margin=0.1)
    assert with_small_margin.held == frozenset({"A"})
    assert with_small_margin.taps == frozenset()
    with_large_margin = decide(answers, hold_margin=0.2)
    assert with_large_margin.held == frozenset()
    assert with_large_margin.taps == frozenset({"A"})


def test_each_button_is_decided_by_its_own_question():
    action = decide(
        {
            "button_A": button(0.1, tap=0.1, hold=0.8),
            "button_B": button(0.1, tap=0.8, hold=0.1),
            "button_ZR": button(0.9, tap=0.05, hold=0.05),
        }
    )
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset({"B"})
    assert action.hold_seconds == {}


# --- 十字キー ---


def test_dpad_is_not_asked_as_button_questions():
    names = [question.name for question in questions()]
    for button_name in ["UP", "RIGHT", "DOWN", "LEFT"]:
        assert f"button_{button_name}" not in names
    assert "dpad_direction" in names
    assert "dpad_press" in names


@pytest.mark.parametrize(
    "direction, buttons",
    [
        ("up", {"UP"}),
        ("right", {"RIGHT"}),
        ("down", {"DOWN"}),
        ("left", {"LEFT"}),
        ("up_right", {"UP", "RIGHT"}),
        ("down_right", {"DOWN", "RIGHT"}),
        ("down_left", {"DOWN", "LEFT"}),
        ("up_left", {"UP", "LEFT"}),
    ],
)
def test_dpad_direction_presses_its_buttons_with_the_same_press(direction, buttons):
    tapped = decide(
        {
            "dpad_direction": dpad(0.1, **{direction: 0.9}),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert tapped.taps == frozenset(buttons)
    assert tapped.held == frozenset()
    held = decide(
        {
            "dpad_direction": dpad(0.1, **{direction: 0.9}),
            "dpad_press": press(tap=0.1, hold=0.9),
        }
    )
    # 斜めの 2 つのボタンも同じ押し方 (押し続けなら 2 つとも held) にし、秒数は付けない
    assert held.held == frozenset(buttons)
    assert held.taps == frozenset()
    assert held.hold_seconds == {}


def test_dpad_is_pressed_when_direction_sum_exceeds_none_even_if_none_is_largest():
    # 8 方向の和 0.35 + 0.25 = 0.6 が none の 0.4 を超える。最も高い up を押す
    action = decide(
        {
            "dpad_direction": dpad(0.4, up=0.35, left=0.25),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert action.taps == frozenset({"UP"})
    assert action.held == frozenset()


def test_dpad_is_not_pressed_when_direction_sum_equals_none():
    action = decide(
        {
            "dpad_direction": dpad(0.5, up=0.3, right=0.2),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert action == PadAction()


def test_dpad_is_not_pressed_when_direction_sum_equals_none_up_to_rounding():
    assert 0.1 + 0.2 != 0.3
    action = decide(
        {
            "dpad_direction": dpad(0.3, up=0.1, right=0.2),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert action == PadAction()


def test_dpad_is_not_pressed_when_the_highest_direction_is_tied():
    # 8 方向の和 0.6 は none の 0.4 を超えるが、最も高い方向が up と left の 2 つ
    action = decide(
        {
            "dpad_direction": dpad(0.4, up=0.3, left=0.3),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert action == PadAction()


def test_dpad_tie_between_diagonals_is_not_pressed():
    action = decide(
        {
            "dpad_direction": dpad(0.2, up_right=0.4, down_left=0.4),
            "dpad_press": press(tap=0.1, hold=0.9),
        }
    )
    assert action == PadAction()


def test_dpad_tie_up_to_rounding_is_not_pressed():
    assert 0.1 + 0.2 != 0.3
    action = decide(
        {
            "dpad_direction": dpad(0.4, up=0.3, right=0.1 + 0.2),
            "dpad_press": press(tap=0.9, hold=0.1),
        }
    )
    assert action == PadAction()


def test_dpad_press_follows_the_hold_margin_rule():
    direction = {"dpad_direction": dpad(0.1, down=0.9)}
    # hold − tap = 0.15。margin 0.1 なら押し続け、margin 0.2 なら短押し
    answers = {**direction, "dpad_press": press(tap=0.35, hold=0.5)}
    assert decide(answers, hold_margin=0.1).held == frozenset({"DOWN"})
    assert decide(answers, hold_margin=0.2).taps == frozenset({"DOWN"})
    # 差が margin と同じ値 (丸めの差を含む) なら短押し
    assert 0.4 - 0.3 != 0.1
    equal = {**direction, "dpad_press": press(tap=0.3, hold=0.4)}
    assert decide(equal, hold_margin=0.1).taps == frozenset({"DOWN"})
    # margin 0 で同じ確率なら短押し、margin 1 なら hold 1.0 でも短押し
    tie = {**direction, "dpad_press": press(tap=0.5, hold=0.5)}
    assert decide(tie, hold_margin=0.0).taps == frozenset({"DOWN"})
    only_hold = {**direction, "dpad_press": press(tap=0.0, hold=1.0)}
    assert decide(only_hold, hold_margin=1.0).taps == frozenset({"DOWN"})
    assert decide(only_hold, hold_margin=0.0).held == frozenset({"DOWN"})


def test_dpad_press_is_not_used_when_dpad_is_not_pressed():
    # 十字キーの方向が none なら、押し方が hold 1.0 でも十字キーは押さない
    action = decide({"dpad_press": press(tap=0.0, hold=1.0)})
    assert action == PadAction()


def test_dpad_is_pressed_together_with_other_buttons_and_sticks():
    action = decide(
        {
            "dpad_direction": dpad(0.1, up_left=0.9),
            "dpad_press": press(tap=0.1, hold=0.9),
            "button_A": button(0.1, tap=0.9),
            "button_B": button(0.1, hold=0.9),
            "stick_LS_x": x_axis(0.0, 0.0, 1.0),
        }
    )
    assert action.held == frozenset({"UP", "LEFT", "B"})
    assert action.taps == frozenset({"A"})
    assert action.hold_seconds == {}
    assert action.left_stick == (0.25, 0.0)


# --- スティックの向き ---


def test_stick_direction_takes_most_probable_choice():
    action = decide({"stick_LS_x": x_axis(0.2, 0.1, 0.7)})
    assert action.left_stick == (0.25, 0.0)
    assert action.right_stick == (0.0, 0.0)


def test_stick_direction_tie_including_none_is_none():
    action = decide(
        {
            "stick_LS_x": x_axis(0.4, 0.2, 0.4),
            "stick_LS_y": y_axis(0.3, 0.5, 0.2),
        }
    )
    assert action.left_stick == (0.0, 0.25)


def test_stick_direction_tie_between_opposite_directions_is_none():
    action = decide(
        {
            "stick_RS_x": x_axis(0.0, 0.5, 0.5),
            "stick_RS_y": y_axis(0.0, 0.0, 1.0),
        }
    )
    assert action.right_stick == (0.0, -0.25)


def test_stick_with_both_axes_none_is_neutral():
    action = decide({"stick_RS_x": x_axis(0.1, 0.0, 0.9)})
    assert action.left_stick == (0.0, 0.0)
    assert action.right_stick == (0.25, 0.0)


@pytest.mark.parametrize("hold_margin", [0.0, 1.0])
def test_stick_does_not_use_the_hold_margin(hold_margin):
    action = decide({"stick_LS_x": x_axis(0.2, 0.1, 0.7)}, hold_margin=hold_margin)
    assert action.left_stick == (0.25, 0.0)


# --- スティックの強さ ---


def test_stick_strength_is_full_when_all_levels_are_one():
    action = decide(
        {"stick_LS_x": x_axis(0.0, 0.0, 1.0)}, **strengths("LS_x", 1.0, 1.0, 1.0)
    )
    assert action.left_stick == (1.0, 0.0)


def test_stick_strength_adds_step_for_each_level_probability():
    action = decide(
        {"stick_LS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("LS_y", 0.5, 0.5, 0.0)
    )
    assert action.left_stick == (0.0, pytest.approx(0.5))


def test_stick_strength_clamps_higher_levels_to_lower_levels():
    # q75 = min(1.0, 0.25) = 0.25、q100 = min(1.0, 0.25) = 0.25 なので s = 0.25 + 0.25 × 0.75 = 0.4375
    action = decide(
        {"stick_RS_x": x_axis(0.0, 0.0, 1.0)}, **strengths("RS_x", 0.25, 1.0, 1.0)
    )
    assert action.right_stick == (pytest.approx(0.4375), 0.0)


def test_stick_strength_clamps_100_to_clamped_75():
    action = decide(
        {"stick_RS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("RS_y", 1.0, 0.5, 0.75)
    )
    assert action.right_stick == (0.0, pytest.approx(0.75))


def test_stick_negative_directions_have_negative_values():
    action = decide(
        {
            "stick_LS_x": x_axis(0.0, 1.0, 0.0),
            "stick_RS_y": y_axis(0.0, 0.0, 1.0),
        },
        **strengths("LS_x", 1.0, 1.0, 0.0),
        **strengths("RS_y", 1.0, 1.0, 1.0),
    )
    assert action.left_stick == (pytest.approx(-0.75), 0.0)
    assert action.right_stick == (0.0, -1.0)


def test_stick_strength_of_none_axis_is_not_used():
    action = decide(
        {"stick_LS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("LS_x", 1.0, 1.0, 1.0)
    )
    assert action.left_stick == (0.0, 0.25)


def test_sticks_and_buttons_are_decided_together():
    action = decide(
        {
            "button_A": button(0.1, hold=0.9),
            "stick_LS_x": x_axis(0.0, 1.0, 0.0),
            "stick_RS_y": y_axis(0.0, 1.0, 0.0),
        }
    )
    assert action.held == frozenset({"A"})
    assert action.hold_seconds == {}
    assert action.left_stick == (-0.25, 0.0)
    assert action.right_stick == (0.0, 0.25)


# --- 答えが足りないとき ---


def test_missing_noul_answer_is_value_error():
    choices, nouls = neutral_answers()
    del nouls["stick_LS_x_at_least_50"]
    with pytest.raises(ValueError, match="stick_LS_x_at_least_50"):
        decide_pad_action(choices, nouls, questions(), DEFAULT_HOLD_MARGIN)


def test_missing_choice_of_choice_question_is_value_error():
    choices, nouls = neutral_answers()
    del choices["button_A"]["tap"]
    with pytest.raises(ValueError, match="button_A.tap"):
        decide_pad_action(choices, nouls, questions(), DEFAULT_HOLD_MARGIN)


def test_missing_dpad_press_answer_is_value_error_even_without_pressing_dpad():
    # 中立の答え (十字キーの方向は none) でも、押し方の答えが無ければ surface の失敗にする
    choices, nouls = neutral_answers()
    del choices["dpad_press"]
    with pytest.raises(ValueError, match="dpad_press"):
        decide_pad_action(choices, nouls, questions(), DEFAULT_HOLD_MARGIN)


def test_missing_choice_of_dpad_press_is_value_error_even_without_pressing_dpad():
    choices, nouls = neutral_answers()
    del choices["dpad_press"]["hold"]
    with pytest.raises(ValueError, match="dpad_press.hold"):
        decide_pad_action(choices, nouls, questions(), DEFAULT_HOLD_MARGIN)


# --- deep を呼ぶ種類・情報ラベル・場面転換・box を使う対象 ---


def advisor_nouls(**values: float) -> dict[str, float]:
    nouls = {
        f"advisor_{kind}": 0.0
        for kind in ["controls", "tips", "strategy", "advice", "game"]
    }
    nouls.update({f"advisor_{name}": value for name, value in values.items()})
    return nouls


def test_deep_kinds_are_kinds_above_threshold():
    nouls = advisor_nouls(controls=0.51, tips=0.5, strategy=0.9, advice=0.1)
    assert decide_deep_kinds(nouls, 0.5) == frozenset(
        {DocumentKind.CONTROLS, DocumentKind.STRATEGY}
    )


def test_deep_kinds_use_given_threshold():
    nouls = advisor_nouls(controls=0.51, tips=0.5, strategy=0.9, advice=0.1)
    assert decide_deep_kinds(nouls, 0.8) == frozenset({DocumentKind.STRATEGY})
    assert decide_deep_kinds(nouls, 0.0) == frozenset(
        {
            DocumentKind.CONTROLS,
            DocumentKind.TIPS,
            DocumentKind.STRATEGY,
            DocumentKind.ADVICE,
        }
    )


def test_deep_kinds_include_game_above_threshold():
    nouls = advisor_nouls(game=0.51)
    assert decide_deep_kinds(nouls, 0.5) == frozenset({DocumentKind.GAME})
    # しきい値ちょうどは入れない
    assert decide_deep_kinds(advisor_nouls(game=0.5), 0.5) == frozenset()


def test_deep_kinds_missing_game_answer_is_value_error():
    nouls = advisor_nouls()
    del nouls["advisor_game"]
    with pytest.raises(ValueError):
        decide_deep_kinds(nouls, 0.5)


def test_deep_kinds_missing_answer_is_value_error():
    nouls = advisor_nouls()
    del nouls["advisor_advice"]
    with pytest.raises(ValueError):
        decide_deep_kinds(nouls, 0.5)


def test_info_labels_are_all_five_in_order_with_probabilities():
    nouls = {
        "info_controls": 0.1,
        "info_tips": 0.2,
        "info_strategy": 0.3,
        "info_stuck": 0.9,
        "info_map": 0.0,
    }
    assert decide_info_labels(nouls) == (
        InfoLabel("controls", 0.1),
        InfoLabel("tips", 0.2),
        InfoLabel("strategy", 0.3),
        InfoLabel("stuck", 0.9),
        InfoLabel("map", 0.0),
    )


def test_scene_changed_only_above_half():
    assert decide_scene_changed({"scene_changed": 0.51}) is True
    assert decide_scene_changed({"scene_changed": 0.5}) is False
    assert decide_scene_changed({"scene_changed": 0.1}) is False
    with pytest.raises(ValueError):
        decide_scene_changed({})


def test_visible_targets_need_existence_of_at_least_half():
    nouls = {"box_exists_1": 0.5, "box_exists_2": 0.49}
    assert visible_target_names(nouls, TARGETS) == frozenset({"自機"})
    with pytest.raises(ValueError):
        visible_target_names({"box_exists_1": 0.9}, TARGETS)
