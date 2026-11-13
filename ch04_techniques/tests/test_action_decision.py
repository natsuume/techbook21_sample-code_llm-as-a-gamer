from dataclasses import fields

import pytest

from switch2_agent.action_decision import (
    PROBABILITY_TOLERANCE,
    STICK_MIN_STRENGTH,
    STICK_STRENGTH_STEP,
    PadAction,
    Thresholds,
    decide_action,
    probabilities_equal,
)
from switch2_agent.questions import NONE_CHOICE, build_questions


def neutral_answers() -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """選択肢の問いは none を 1.0 (ほかの選択肢を 0.0)、確率の問いは 0.0 にした答え (probabilities, choices) を返す。"""
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, float]] = {}
    for question in build_questions():
        if question.answer_type == "choice":
            choices[question.name] = {
                choice.name: 1.0 if choice.name == NONE_CHOICE else 0.0
                for choice in question.choices
            }
        else:
            probabilities[question.name] = 0.0
    return probabilities, choices


def decide(
    chosen: dict[str, dict[str, float]] | None = None,
    thresholds: Thresholds | None = None,
    **probability_overrides: float,
) -> PadAction:
    """neutral_answers の答えのうち、chosen (選択肢の問いの name → 選択肢ごとの確率) と probability_overrides
    (確率の問いの name → 確率) の問いだけを置き換えて decide_action を呼ぶ。

    置き換える問いが問いの一覧に無ければ、テストを失敗させる。
    """
    probabilities, choices = neutral_answers()
    for name, values in (chosen or {}).items():
        assert name in choices, f"unknown choice question: {name}"
        choices[name] = dict(values)
    for name, value in probability_overrides.items():
        assert name in probabilities, f"unknown probability question: {name}"
        probabilities[name] = value
    return decide_action(
        probabilities,
        choices,
        build_questions(),
        thresholds if thresholds is not None else Thresholds(),
    )


def button(none: float, hold: float, tap: float) -> dict[str, float]:
    return {"none": none, "hold": hold, "tap": tap}


def x_axis(none: float, left: float, right: float) -> dict[str, float]:
    return {"none": none, "left": left, "right": right}


def y_axis(none: float, up: float, down: float) -> dict[str, float]:
    return {"none": none, "up": up, "down": down}


def strengths(axis: str, p50: float, p75: float, p100: float) -> dict[str, float]:
    """軸 (例: LS_x) の強さの問いの確率を、decide の probability_overrides の形にする。"""
    return {
        f"stick_{axis}_at_least_50": p50,
        f"stick_{axis}_at_least_75": p75,
        f"stick_{axis}_at_least_100": p100,
    }


# --- Thresholds ---


def test_thresholds_have_only_advisor():
    assert [field.name for field in fields(Thresholds)] == ["advisor"]


def test_thresholds_default_to_half():
    assert Thresholds() == Thresholds(advisor=0.5)


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_thresholds_accept_bounds(value):
    assert Thresholds(advisor=value).advisor == value


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_thresholds_reject_out_of_range(value):
    with pytest.raises(ValueError):
        Thresholds(advisor=value)


@pytest.mark.parametrize("name", ["press", "tap", "stick"])
def test_thresholds_do_not_accept_button_or_stick_thresholds(name):
    with pytest.raises(TypeError):
        Thresholds(**{name: 0.5})


def test_stick_strength_constants():
    assert (STICK_MIN_STRENGTH, STICK_STRENGTH_STEP) == (0.25, 0.25)


# --- ボタン ---


def test_button_is_pressed_only_when_hold_or_tap_is_chosen():
    action = decide({"button_A": button(0.0, 1.0, 0.0)})
    assert action.held.buttons == frozenset({"A"})
    # ほかのボタンは none が 1.0 なので押さない
    assert action.taps == frozenset()


def test_button_is_pressed_when_press_side_sum_exceeds_none_even_if_none_is_largest():
    # none (0.4) が 3 つの中で最大でも、押す側の合計 (0.35 + 0.25 = 0.6) が上回るので押す。hold >= tap なので押下状態
    action = decide({"button_A": button(0.4, 0.35, 0.25)})
    assert action.held.buttons == frozenset({"A"})
    assert action.taps == frozenset()


def test_button_is_tapped_when_tap_is_more_probable_than_hold():
    action = decide({"button_B": button(0.2, 0.3, 0.5)})
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset({"B"})


def test_button_is_not_pressed_when_press_side_sum_equals_none():
    action = decide(
        {
            # 押す側の合計 0.25 + 0.25 が none の 0.5 と同じなので押さない
            "button_A": button(0.5, 0.25, 0.25),
            # 押す側の合計 0.375 + 0.25 が none の 0.375 を上回るので押す
            "button_B": button(0.375, 0.375, 0.25),
        }
    )
    assert action.held.buttons == frozenset({"B"})
    assert action.taps == frozenset()


def test_button_is_not_pressed_when_press_side_sum_equals_none_up_to_rounding():
    # 0.1 + 0.2 は浮動小数点数では 0.30000000000000004 になり、none の 0.3 をわずかに上回るが、同じ値として押さない
    assert 0.1 + 0.2 != 0.3
    action = decide({"button_A": button(0.3, 0.1, 0.2)})
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset()


def test_probabilities_equal_uses_tolerance():
    assert probabilities_equal(0.1 + 0.7, 0.3 + 0.5)
    assert probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE / 2)
    assert not probabilities_equal(0.8, 0.8 + PROBABILITY_TOLERANCE * 10)


def test_hold_wins_when_hold_and_tap_are_equal():
    action = decide({"button_X": button(0.0, 0.5, 0.5)})
    assert action.held.buttons == frozenset({"X"})
    assert action.taps == frozenset()


def test_each_button_is_decided_by_its_own_question():
    action = decide(
        {
            "button_A": button(0.1, 0.8, 0.1),
            "button_B": button(0.1, 0.1, 0.8),
            "button_ZR": button(0.9, 0.05, 0.05),
        }
    )
    assert action.held.buttons == frozenset({"A"})
    assert action.taps == frozenset({"B"})


# --- 十字キーの相反 ---


def test_opposite_dpad_keeps_higher_press_side_sum_across_hold_and_tap():
    action = decide(
        {
            "button_UP": button(0.3, 0.7, 0.0),
            "button_DOWN": button(0.1, 0.0, 0.9),
        }
    )
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset({"DOWN"})


def test_opposite_dpad_compares_press_side_sum_not_probability_of_adopted_choice():
    # UP は hold (0.5) を採るが、比べるのは押す側の合計 (0.9)。DOWN の合計 0.8 より高いので UP を残す
    action = decide(
        {
            "button_UP": button(0.1, 0.5, 0.4),
            "button_DOWN": button(0.2, 0.8, 0.0),
        }
    )
    assert action.held.buttons == frozenset({"UP"})
    assert action.taps == frozenset()


def test_opposite_dpad_with_equal_press_side_sum_drops_both():
    action = decide(
        {
            # LEFT と RIGHT は押す側の合計が同じ (0.75) なので両方とも外す
            "button_LEFT": button(0.25, 0.0, 0.75),
            "button_RIGHT": button(0.25, 0.75, 0.0),
            # 相反する方向が採られていない UP は残る
            "button_UP": button(0.0, 1.0, 0.0),
        }
    )
    assert action.held.buttons == frozenset({"UP"})
    assert action.taps == frozenset()


def test_opposite_dpad_with_press_side_sums_equal_up_to_rounding_drops_both():
    # UP の合計 0.1 + 0.7 は 0.7999999999999999、DOWN の合計 0.3 + 0.5 は 0.8 になるが、同じ値として両方とも外す
    assert 0.1 + 0.7 != 0.3 + 0.5
    action = decide(
        {
            "button_UP": button(0.2, 0.1, 0.7),
            "button_DOWN": button(0.2, 0.3, 0.5),
        }
    )
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset()


def test_non_opposite_dpad_directions_can_be_combined():
    action = decide(
        {
            "button_UP": button(0.1, 0.9, 0.0),
            "button_RIGHT": button(0.2, 0.8, 0.0),
        }
    )
    assert action.held.buttons == frozenset({"UP", "RIGHT"})


# --- スティックの方向 ---


def test_stick_direction_takes_most_probable_choice():
    # 強さの問いがすべて 0 なら、傾きは最小の 0.25
    action = decide({"stick_LS_x": x_axis(0.2, 0.1, 0.7)})
    assert action.held.sticks == {"LS": (0.25, 0.0)}


def test_stick_direction_tie_including_none_is_none():
    action = decide(
        {
            # none と right が同じ最大値なので、x は none
            "stick_LS_x": x_axis(0.4, 0.2, 0.4),
            "stick_LS_y": y_axis(0.3, 0.5, 0.2),
        }
    )
    assert action.held.sticks == {"LS": (0.0, 0.25)}


def test_stick_direction_tie_between_opposite_directions_is_none():
    action = decide(
        {
            # left と right が同じ最大値なので、x は none
            "stick_RS_x": x_axis(0.0, 0.5, 0.5),
            "stick_RS_y": y_axis(0.0, 0.0, 1.0),
        }
    )
    assert action.held.sticks == {"RS": (0.0, -0.25)}


def test_stick_with_both_axes_none_is_not_held():
    action = decide({"stick_RS_x": x_axis(0.1, 0.0, 0.9)})
    # LS はどちらの軸も none なので held に入れない
    assert action.held.sticks == {"RS": (0.25, 0.0)}


# --- スティックの強さ ---


def test_stick_strength_is_full_when_all_levels_are_one():
    action = decide(
        {"stick_LS_x": x_axis(0.0, 0.0, 1.0)}, **strengths("LS_x", 1.0, 1.0, 1.0)
    )
    assert action.held.sticks == {"LS": (1.0, 0.0)}


def test_stick_strength_adds_step_for_each_level_probability():
    # s = 0.25 + 0.25 × (0.5 + 0.5 + 0.0) = 0.5
    action = decide(
        {"stick_LS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("LS_y", 0.5, 0.5, 0.0)
    )
    assert action.held.sticks == {"LS": (0.0, pytest.approx(0.5))}


def test_stick_strength_clamps_higher_levels_to_lower_levels():
    # q75 = min(1.0, 0.25) = 0.25、q100 = min(1.0, 0.25) = 0.25 なので s = 0.25 + 0.25 × 0.75 = 0.4375
    # (切り下げなければ 0.25 + 0.25 × 2.25 = 0.8125)
    action = decide(
        {"stick_RS_x": x_axis(0.0, 0.0, 1.0)}, **strengths("RS_x", 0.25, 1.0, 1.0)
    )
    assert action.held.sticks == {"RS": (pytest.approx(0.4375), 0.0)}


def test_stick_strength_clamps_100_to_clamped_75():
    # q75 = 0.5、q100 = min(0.75, 0.5) = 0.5 なので s = 0.25 + 0.25 × 2.0 = 0.75
    # (切り下げなければ 0.25 + 0.25 × 2.25 = 0.8125)
    action = decide(
        {"stick_RS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("RS_y", 1.0, 0.5, 0.75)
    )
    assert action.held.sticks == {"RS": (0.0, pytest.approx(0.75))}


def test_stick_negative_directions_have_negative_values():
    action = decide(
        {
            "stick_LS_x": x_axis(0.0, 1.0, 0.0),
            "stick_RS_y": y_axis(0.0, 0.0, 1.0),
        },
        **strengths("LS_x", 1.0, 1.0, 0.0),
        **strengths("RS_y", 1.0, 1.0, 1.0),
    )
    assert action.held.sticks == {
        "LS": (pytest.approx(-0.75), 0.0),
        "RS": (0.0, -1.0),
    }


def test_stick_strength_of_none_axis_is_not_used():
    # LS の x は none なので、x の強さの問いの確率は使わない
    action = decide(
        {"stick_LS_y": y_axis(0.0, 1.0, 0.0)}, **strengths("LS_x", 1.0, 1.0, 1.0)
    )
    assert action.held.sticks == {"LS": (0.0, 0.25)}


def test_stick_strength_uses_only_its_own_axis():
    action = decide(
        {
            "stick_LS_x": x_axis(0.0, 0.0, 1.0),
            "stick_LS_y": y_axis(0.0, 1.0, 0.0),
        },
        **strengths("LS_x", 1.0, 1.0, 1.0),
        **strengths("RS_y", 1.0, 1.0, 1.0),
    )
    assert action.held.sticks == {"LS": (1.0, 0.25)}


def test_sticks_and_buttons_are_held_together():
    action = decide(
        {
            "button_A": button(0.1, 0.9, 0.0),
            "stick_LS_x": x_axis(0.0, 1.0, 0.0),
            "stick_RS_y": y_axis(0.0, 1.0, 0.0),
        }
    )
    assert action.held.buttons == frozenset({"A"})
    assert action.held.sticks == {"LS": (-0.25, 0.0), "RS": (0.0, 0.25)}


# --- advisor ---


def test_advisor_probabilities_are_keyed_by_role_name_in_role_order():
    action = decide(
        advisor_controls=0.125,
        advisor_strategy=0.25,
        advisor_advice=0.375,
        advisor_tips=0.5,
    )
    assert list(action.advisor_probabilities.items()) == [
        ("controls", 0.125),
        ("strategy", 0.25),
        ("advice", 0.375),
        ("tips", 0.5),
    ]


def test_advisor_requested_has_roles_above_threshold():
    action = decide(advisor_strategy=0.51, advisor_advice=0.9, advisor_tips=0.2)
    assert action.advisor_requested == frozenset({"strategy", "advice"})


def test_advisor_not_requested_at_threshold():
    # しきい値ちょうど (0.5) の役割は入れない。少しでも超えた役割は入れる
    action = decide(advisor_controls=0.5, advisor_tips=0.5000001)
    assert action.advisor_requested == frozenset({"tips"})
    assert action.advisor_probabilities["controls"] == 0.5


def test_advisor_requested_is_empty_when_no_role_exceeds_threshold():
    action = decide(advisor_strategy=0.4)
    assert action.advisor_requested == frozenset()
    assert action.advisor_probabilities == {
        "controls": 0.0,
        "strategy": 0.4,
        "advice": 0.0,
        "tips": 0.0,
    }


def test_advisor_uses_given_threshold_for_all_roles():
    action = decide(
        thresholds=Thresholds(advisor=0.8),
        advisor_controls=0.7,
        advisor_strategy=0.85,
        advisor_advice=0.8,
        advisor_tips=0.9,
    )
    assert action.advisor_requested == frozenset({"strategy", "tips"})


# --- 入力の検証 ---


def test_missing_probability_question_is_value_error():
    probabilities, choices = neutral_answers()
    del probabilities["stick_LS_x_at_least_75"]
    with pytest.raises(ValueError):
        decide_action(probabilities, choices, build_questions(), Thresholds())


def test_missing_strength_answer_is_value_error_even_if_axis_is_none():
    # すべての軸が none でも、強さの問いの答えは要る
    probabilities, choices = neutral_answers()
    del probabilities["stick_RS_y_at_least_100"]
    with pytest.raises(ValueError):
        decide_action(probabilities, choices, build_questions(), Thresholds())


def test_missing_choice_question_is_value_error():
    probabilities, choices = neutral_answers()
    del choices["button_HOME"]
    with pytest.raises(ValueError):
        decide_action(probabilities, choices, build_questions(), Thresholds())


def test_missing_choice_of_choice_question_is_value_error():
    probabilities, choices = neutral_answers()
    del choices["stick_LS_y"]["down"]
    with pytest.raises(ValueError):
        decide_action(probabilities, choices, build_questions(), Thresholds())
