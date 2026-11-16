from collections.abc import Sequence

import pytest

from switch2_harness.press_args import (
    FORBIDDEN_BUTTON,
    HOME_REJECTED_MESSAGE,
    PRESS_ACTIONS,
    press_args_error,
    split_press_args,
    unknown_action_message,
)

HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def check(action: str, args: Sequence[str]) -> str | None:
    """commands.handle_press と同じく、分け直してから検証する。"""
    return press_args_error(action, split_press_args(args))


def test_constants():
    assert PRESS_ACTIONS == ("tap", "hold", "down", "up")
    assert FORBIDDEN_BUTTON == "HOME"
    assert HOME_REJECTED_MESSAGE == HOME_ERROR


def test_unknown_action_message():
    assert unknown_action_message("shot") == (
        "error press accepts tap / hold / down / up: shot"
    )


# --- split_press_args ---


@pytest.mark.parametrize(
    ("args", "tokens"),
    [
        ([], ()),
        (["A"], ("A",)),
        (["B+RIGHT"], ("B+RIGHT",)),
        (["RIGHT RIGHT", "A"], ("RIGHT", "RIGHT", "A")),
        (["  A   B  "], ("A", "B")),
        (["A\tHOME", "B\nC"], ("A", "HOME", "B", "C")),
        ([" ", ""], ()),
        (["b + Home"], ("b", "+", "Home")),
    ],
)
def test_split_press_args(args, tokens):
    assert split_press_args(args) == tokens


def test_split_press_args_returns_a_tuple():
    assert isinstance(split_press_args(["A B"]), tuple)


# --- press_args_error ---


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["RIGHT", "RIGHT", "A"]),
        ("tap", ["RIGHT RIGHT A"]),
        ("tap", ["B+RIGHT"]),
        ("hold", ["A", "500"]),
        ("hold", ["A 500"]),
        ("down", ["LS:0,1"]),
        ("down", ["A", "B+LS:1,0"]),
        ("up", ["LS"]),
        ("up", ["ALL"]),
        ("up", ["A+B"]),
    ],
)
def test_valid_press_args_pass(action, args):
    assert check(action, args) is None


@pytest.mark.parametrize("action", ["shot", "fps", "quit", "status", "press"])
def test_actions_other_than_the_four_are_rejected(action):
    assert press_args_error(action, ("A",)) == (
        f"error press accepts tap / hold / down / up: {action}"
    )


@pytest.mark.parametrize("action", ["tap A", "TAP", " tap", "tap "])
def test_action_must_match_exactly_including_spaces(action):
    assert press_args_error(action, ()) == (
        f"error press accepts tap / hold / down / up: {action}"
    )


def test_action_is_checked_before_home():
    assert press_args_error("shot", ("HOME",)) == (
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
    assert check(action, args) == HOME_ERROR


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["A HOME"]),
        ("tap", ["A\nHOME"]),
        ("down", ["B+HOME"]),
        ("down", ["LS:0,1 B+HOME"]),
        ("hold", ["HOME 500"]),
        ("up", ["LS HOME"]),
    ],
)
def test_home_hidden_behind_spaces_is_rejected(action, args):
    assert check(action, args) == HOME_ERROR


def test_hold_checks_only_the_chord():
    assert check("hold", ["A", "HOME"]) is None
    assert check("hold", ["A HOME"]) is None


def test_hold_without_args_is_left_to_the_daemon():
    assert check("hold", []) is None
    assert check("hold", [" "]) is None


def test_button_names_containing_home_letters_are_not_home():
    assert check("tap", ["HOMEX"]) is None
