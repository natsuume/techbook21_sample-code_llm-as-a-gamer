from collections.abc import Sequence

import pytest

from switch2_harness.press_args import (
    FORBIDDEN_BUTTON,
    HOME_REJECTED_MESSAGE,
    MAX_TAP_CHORDS,
    PRESS_ACTIONS,
    press_args_error,
    split_press_args,
    too_many_tap_chords_message,
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
    assert MAX_TAP_CHORDS == 10


def test_too_many_tap_chords_message():
    assert (
        too_many_tap_chords_message(12)
        == "error press tap accepts up to 10 chords at a time: 12"
    )


def test_tap_with_up_to_the_limit_of_chords_passes():
    assert check("tap", ["A"] * MAX_TAP_CHORDS) is None
    assert check("tap", [" ".join(["RIGHT"] * MAX_TAP_CHORDS)]) is None


def test_tap_with_more_chords_than_the_limit_is_rejected():
    too_many = MAX_TAP_CHORDS + 1
    assert check("tap", ["A"] * too_many) == too_many_tap_chords_message(too_many)
    # 空白を含む 1 つの引数も、分け直した CHORD の数で数える
    assert check("tap", [" ".join(["A"] * too_many)]) == too_many_tap_chords_message(
        too_many
    )


def test_chord_limit_applies_only_to_tap():
    many = ["LS:0,1"] * (MAX_TAP_CHORDS + 1)
    assert check("down", many) is None
    assert check("up", ["A"] * (MAX_TAP_CHORDS + 1)) is None


def test_home_is_rejected_before_the_chord_limit():
    assert check("tap", ["A"] * MAX_TAP_CHORDS + ["HOME"]) == HOME_ERROR


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
