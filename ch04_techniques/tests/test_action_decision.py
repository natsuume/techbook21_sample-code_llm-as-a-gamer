import pytest

from switch2_agent.action_decision import PadAction, Thresholds, decide_action
from switch2_agent.questions import build_questions


def probabilities(**overrides: float) -> dict[str, float]:
    """すべての問いの確率を 0.0 にし、overrides で指定した問いだけを置き換えた辞書を返す。"""
    probs = {question.name: 0.0 for question in build_questions()}
    probs.update(overrides)
    return probs


def decide(thresholds: Thresholds | None = None, **overrides: float) -> PadAction:
    return decide_action(
        probabilities(**overrides),
        build_questions(),
        thresholds if thresholds is not None else Thresholds(),
    )


# --- Thresholds ---


def test_thresholds_default_to_half():
    assert Thresholds() == Thresholds(press=0.5, tap=0.5, stick=0.5, advisor=0.5)


@pytest.mark.parametrize(
    "kwargs", [{"press": 0.0}, {"tap": 1.0}, {"stick": 0.0}, {"advisor": 1.0}]
)
def test_thresholds_accept_bounds(kwargs):
    Thresholds(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"press": -0.1}, {"tap": 1.1}, {"stick": 2.0}, {"advisor": -1.0}],
)
def test_thresholds_reject_out_of_range(kwargs):
    with pytest.raises(ValueError):
        Thresholds(**kwargs)


# --- しきい値 ---


def test_probability_equal_to_threshold_is_false():
    action = decide(press_A=0.5, tap_B=0.5, stick_LS_RIGHT=0.5, advisor=0.5)
    assert action.held.buttons == frozenset()
    assert action.held.sticks == {}
    assert action.taps == frozenset()
    assert action.advisor_requested is False


def test_probability_above_threshold_is_true():
    action = decide(press_A=0.51, tap_B=0.51)
    assert action.held.buttons == frozenset({"A"})
    assert action.taps == frozenset({"B"})


def test_uses_threshold_of_each_kind():
    action = decide(Thresholds(press=0.8, tap=0.2), press_A=0.7, tap_B=0.3)
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset({"B"})


# --- 押下状態と短押し ---


def test_tap_wins_when_tap_probability_is_higher():
    action = decide(press_A=0.7, tap_A=0.8)
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset({"A"})


def test_press_wins_when_press_probability_is_higher():
    action = decide(press_B=0.8, tap_B=0.6)
    assert action.held.buttons == frozenset({"B"})
    assert action.taps == frozenset()


def test_press_wins_when_press_and_tap_are_equal():
    action = decide(press_X=0.7, tap_X=0.7)
    assert action.held.buttons == frozenset({"X"})
    assert action.taps == frozenset()


# --- 十字キーの相反 ---


def test_opposite_dpad_keeps_higher_probability_across_press_and_tap():
    action = decide(press_UP=0.7, tap_DOWN=0.9)
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset({"DOWN"})


def test_opposite_dpad_keeps_higher_pressed_direction():
    action = decide(press_LEFT=0.8, press_RIGHT=0.6)
    assert action.held.buttons == frozenset({"LEFT"})


def test_opposite_dpad_with_equal_probability_drops_both():
    action = decide(tap_LEFT=0.8, press_RIGHT=0.8)
    assert action.held.buttons == frozenset()
    assert action.taps == frozenset()


def test_opposite_dpad_compares_probability_of_adopted_question():
    # UP は押下状態 (0.9) を採る。短押し (0.6) ではなく、採った押下状態の 0.9 で DOWN (0.8) と比べる
    action = decide(press_UP=0.9, tap_UP=0.6, press_DOWN=0.8)
    assert action.held.buttons == frozenset({"UP"})
    assert action.taps == frozenset()


def test_non_opposite_dpad_directions_can_be_combined():
    action = decide(press_UP=0.9, press_RIGHT=0.8)
    assert action.held.buttons == frozenset({"UP", "RIGHT"})


# --- スティック ---


def test_stick_with_one_horizontal_direction_uses_its_probability():
    assert decide(stick_LS_RIGHT=0.8).held.sticks == {"LS": (0.8, 0.0)}


def test_stick_left_is_negative():
    assert decide(stick_LS_LEFT=0.7).held.sticks == {"LS": (-0.7, 0.0)}


def test_stick_with_both_horizontal_directions_uses_difference():
    x, y = decide(stick_LS_RIGHT=0.9, stick_LS_LEFT=0.6).held.sticks["LS"]
    assert x == pytest.approx(0.3)
    assert y == 0.0


def test_stick_without_true_direction_is_not_held():
    assert decide(stick_LS_RIGHT=0.4, stick_LS_UP=0.2).held.sticks == {}


def test_stick_with_equal_opposite_directions_is_not_held():
    assert decide(stick_LS_RIGHT=0.7, stick_LS_LEFT=0.7).held.sticks == {}


def test_stick_vertical_up_is_positive_and_down_is_negative():
    assert decide(stick_RS_UP=0.7).held.sticks == {"RS": (0.0, 0.7)}
    assert decide(stick_RS_DOWN=0.9).held.sticks == {"RS": (0.0, -0.9)}


def test_stick_with_both_vertical_directions_uses_difference():
    x, y = decide(stick_RS_UP=0.6, stick_RS_DOWN=0.8).held.sticks["RS"]
    assert x == 0.0
    assert y == pytest.approx(-0.2)


def test_stick_combines_horizontal_and_vertical():
    assert decide(stick_LS_RIGHT=0.8, stick_LS_UP=0.6).held.sticks == {"LS": (0.8, 0.6)}


def test_sticks_and_buttons_are_held_together():
    action = decide(press_A=0.9, stick_LS_LEFT=0.6, stick_RS_UP=0.7)
    assert action.held.buttons == frozenset({"A"})
    assert action.held.sticks == {"LS": (-0.6, 0.0), "RS": (0.0, 0.7)}


def test_stick_uses_stick_threshold():
    assert decide(Thresholds(stick=0.9), stick_LS_RIGHT=0.8).held.sticks == {}


# --- advisor ---


def test_advisor_requested_above_threshold():
    action = decide(advisor=0.51)
    assert action.advisor_requested is True
    assert action.advisor_probability == 0.51


def test_advisor_not_requested_at_threshold():
    action = decide(advisor=0.5)
    assert action.advisor_requested is False
    assert action.advisor_probability == 0.5


# --- 入力の検証 ---


def test_missing_probability_is_value_error():
    probs = probabilities()
    del probs["tap_HOME"]
    with pytest.raises(ValueError):
        decide_action(probs, build_questions(), Thresholds())
