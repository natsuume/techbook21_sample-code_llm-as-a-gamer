"""操作の決め方の規則。入出力の例は、現行の switch2_agent の規則のテスト (tests/test_action_decision.py) と同じ。"""

import pytest

from switch2_layers.buttons import allowed_buttons
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
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
    remove_opposite_dpad,
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
    chosen: dict[str, dict[str, float]] | None = None, **noul_overrides: float
) -> PadAction:
    """neutral_answers のうち、chosen と noul_overrides の問いだけを置き換えて decide_pad_action を呼ぶ。"""
    choices, nouls = neutral_answers()
    for name, values in (chosen or {}).items():
        assert name in choices, f"unknown choice question: {name}"
        choices[name] = dict(values)
    for name, value in noul_overrides.items():
        assert name in nouls, f"unknown noul question: {name}"
        nouls[name] = value
    return decide_pad_action(choices, nouls, questions())


def button(none: float, hold: float, tap: float) -> dict[str, float]:
    return {"none": none, "hold": hold, "tap": tap}


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


def test_button_is_pressed_only_when_hold_or_tap_is_chosen():
    action = decide({"button_A": button(0.0, 1.0, 0.0)})
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset()


def test_button_is_pressed_when_press_side_sum_exceeds_none_even_if_none_is_largest():
    action = decide({"button_A": button(0.4, 0.35, 0.25)})
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset()


def test_button_is_tapped_when_tap_is_more_probable_than_hold():
    action = decide({"button_B": button(0.2, 0.3, 0.5)})
    assert action.held == frozenset()
    assert action.taps == frozenset({"B"})


def test_button_is_not_pressed_when_press_side_sum_equals_none():
    action = decide(
        {
            "button_A": button(0.5, 0.25, 0.25),
            "button_B": button(0.375, 0.375, 0.25),
        }
    )
    assert action.held == frozenset({"B"})
    assert action.taps == frozenset()


def test_button_is_not_pressed_when_press_side_sum_equals_none_up_to_rounding():
    assert 0.1 + 0.2 != 0.3
    action = decide({"button_A": button(0.3, 0.1, 0.2)})
    assert action.held == frozenset()
    assert action.taps == frozenset()


def test_probabilities_equal_uses_tolerance():
    assert probabilities_equal(0.1 + 0.7, 0.3 + 0.5)
    assert probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE / 2)
    assert not probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE * 10)


def test_hold_wins_when_hold_and_tap_are_equal():
    action = decide({"button_X": button(0.0, 0.5, 0.5)})
    assert action.held == frozenset({"X"})
    assert action.taps == frozenset()


def test_each_button_is_decided_by_its_own_question():
    action = decide(
        {
            "button_A": button(0.1, 0.8, 0.1),
            "button_B": button(0.1, 0.1, 0.8),
            "button_ZR": button(0.9, 0.05, 0.05),
        }
    )
    assert action.held == frozenset({"A"})
    assert action.taps == frozenset({"B"})


# --- 十字キーの相反 ---


def test_opposite_dpad_keeps_higher_press_side_sum_across_hold_and_tap():
    action = decide(
        {"button_UP": button(0.3, 0.7, 0.0), "button_DOWN": button(0.1, 0.0, 0.9)}
    )
    assert action.held == frozenset()
    assert action.taps == frozenset({"DOWN"})


def test_opposite_dpad_compares_press_side_sum_not_probability_of_adopted_choice():
    action = decide(
        {"button_UP": button(0.1, 0.5, 0.4), "button_DOWN": button(0.2, 0.8, 0.0)}
    )
    assert action.held == frozenset({"UP"})
    assert action.taps == frozenset()


def test_opposite_dpad_with_equal_press_side_sum_drops_both():
    action = decide(
        {
            "button_LEFT": button(0.25, 0.0, 0.75),
            "button_RIGHT": button(0.25, 0.75, 0.0),
            "button_UP": button(0.0, 1.0, 0.0),
        }
    )
    assert action.held == frozenset({"UP"})
    assert action.taps == frozenset()


def test_opposite_dpad_with_press_side_sums_equal_up_to_rounding_drops_both():
    assert 0.1 + 0.7 != 0.3 + 0.5
    action = decide(
        {"button_UP": button(0.2, 0.1, 0.7), "button_DOWN": button(0.2, 0.3, 0.5)}
    )
    assert action.held == frozenset()
    assert action.taps == frozenset()


def test_non_opposite_dpad_directions_can_be_combined():
    action = decide(
        {"button_UP": button(0.1, 0.9, 0.0), "button_RIGHT": button(0.2, 0.8, 0.0)}
    )
    assert action.held == frozenset({"UP", "RIGHT"})


def test_remove_opposite_dpad_does_not_change_its_argument():
    adopted = {"UP": ("hold", 0.9), "DOWN": ("tap", 0.8), "A": ("hold", 0.7)}
    before = dict(adopted)
    assert remove_opposite_dpad(adopted) == {"UP": ("hold", 0.9), "A": ("hold", 0.7)}
    assert adopted == before


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
            "button_A": button(0.1, 0.9, 0.0),
            "stick_LS_x": x_axis(0.0, 1.0, 0.0),
            "stick_RS_y": y_axis(0.0, 1.0, 0.0),
        }
    )
    assert action.held == frozenset({"A"})
    assert action.left_stick == (-0.25, 0.0)
    assert action.right_stick == (0.0, 0.25)


# --- 答えが足りないとき ---


def test_missing_noul_answer_is_value_error():
    choices, nouls = neutral_answers()
    del nouls["stick_LS_x_at_least_50"]
    with pytest.raises(ValueError, match="stick_LS_x_at_least_50"):
        decide_pad_action(choices, nouls, questions())


def test_missing_choice_of_choice_question_is_value_error():
    choices, nouls = neutral_answers()
    del choices["button_A"]["tap"]
    with pytest.raises(ValueError, match="button_A.tap"):
        decide_pad_action(choices, nouls, questions())


# --- deep を呼ぶ種類・情報ラベル・場面転換・box を使う対象 ---


def advisor_nouls(**values: float) -> dict[str, float]:
    nouls = {
        f"advisor_{kind}": 0.0 for kind in ["controls", "tips", "strategy", "advice"]
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
