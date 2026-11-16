import pytest

from switch2_harness.press_args import (
    FORBIDDEN_BUTTON,
    HOME_REJECTED_MESSAGE,
    PRESS_ACTIONS,
    press_args_error,
    unknown_action_message,
)

HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def test_constants():
    assert PRESS_ACTIONS == ("tap", "hold", "down", "up")
    assert FORBIDDEN_BUTTON == "HOME"
    assert HOME_REJECTED_MESSAGE == HOME_ERROR


def test_unknown_action_message():
    assert unknown_action_message("shot") == (
        "error press accepts tap / hold / down / up: shot"
    )


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["RIGHT", "RIGHT", "A"]),
        ("tap", ["B+RIGHT"]),
        ("hold", ["A", "500"]),
        ("down", ["LS:0,1"]),
        ("down", ["A", "B+LS:1,0"]),
        ("up", ["LS"]),
        ("up", ["ALL"]),
        ("up", ["A+B"]),
    ],
)
def test_valid_press_args_pass(action, args):
    assert press_args_error(action, args) is None


@pytest.mark.parametrize("action", ["shot", "fps", "quit", "status", "press"])
def test_actions_other_than_the_four_are_rejected(action):
    assert press_args_error(action, ["A"]) == (
        f"error press accepts tap / hold / down / up: {action}"
    )


def test_action_is_checked_before_home():
    assert press_args_error("shot", ["HOME"]) == (
        "error press accepts tap / hold / down / up: shot"
    )


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["HOME"]),
        ("tap", ["A", "HOME"]),
        ("tap", ["home"]),
        ("tap", ["B+HOME"]),
        ("tap", ["b + Home"]),
        ("hold", ["HOME", "500"]),
        ("hold", ["B+home", "500"]),
        ("down", ["HOME"]),
        ("down", ["LS:0,1", "A+HOME"]),
        ("up", ["HOME"]),
        ("up", ["A+HOME"]),
    ],
)
def test_home_is_rejected_for_every_action(action, args):
    assert press_args_error(action, args) == HOME_ERROR


def test_hold_checks_only_the_chord():
    assert press_args_error("hold", ["A", "HOME"]) is None


def test_hold_without_args_is_left_to_the_daemon():
    assert press_args_error("hold", []) is None


def test_button_names_containing_home_letters_are_not_home():
    assert press_args_error("tap", ["HOMEX"]) is None
