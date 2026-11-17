from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.buttons import (
    ALL_BUTTONS,
    HOME_BUTTON,
    FilteredAction,
    allowed_buttons,
    filter_action,
)
from switch2_layers.pad_action import PadAction


def test_all_buttons_are_the_17_controller_buttons():
    assert ALL_BUTTONS == BUTTON_ORDER
    assert len(ALL_BUTTONS) == 17
    assert HOME_BUTTON in ALL_BUTTONS


def test_allowed_buttons_are_the_16_without_home():
    # surface は HOME を押さない (HOME を押すのは本体の画面の操作と press_home だけ)
    allowed = allowed_buttons()
    assert allowed == frozenset(BUTTON_ORDER) - {"HOME"}
    assert len(allowed) == 16


def test_filter_action_removes_home_from_held_and_taps():
    action = PadAction(
        held=frozenset({"A", "HOME"}),
        taps=frozenset({"HOME", "B"}),
        left_stick=(0.5, 0.5),
    )
    result = filter_action(action, allowed_buttons())
    assert result == FilteredAction(
        action=PadAction(
            held=frozenset({"A"}), taps=frozenset({"B"}), left_stick=(0.5, 0.5)
        ),
        blocked_buttons=("HOME",),
    )


def test_filter_action_keeps_home_with_all_buttons():
    action = PadAction(held=frozenset({"HOME"}), taps=frozenset({"A"}))
    result = filter_action(action, ALL_BUTTONS)
    assert result.action == action
    assert result.blocked_buttons == ()


def test_filter_action_without_blocked_buttons_returns_same_action():
    action = PadAction(
        held=frozenset({"ZL"}), taps=frozenset({"X"}), right_stick=(-1.0, 0.0)
    )
    result = filter_action(action, allowed_buttons())
    assert result.action == action
    assert result.blocked_buttons == ()


def test_filter_action_blocks_unknown_names_and_sorts_blocked_buttons():
    action = PadAction(
        held=frozenset({"HOME", "TURBO"}),
        taps=frozenset({"HOME", "CAPTURE", "A"}),
    )
    result = filter_action(action, allowed_buttons())
    assert result.action == PadAction(taps=frozenset({"A"}))
    # BUTTON_ORDER にある名前が先、知らない名前は後ろに文字列の順
    assert result.blocked_buttons == ("HOME", "CAPTURE", "TURBO")


def test_filter_action_with_custom_allowed_set():
    action = PadAction(held=frozenset({"A", "B"}), taps=frozenset({"X"}))
    result = filter_action(action, {"A"})
    assert result.action == PadAction(held=frozenset({"A"}))
    assert result.blocked_buttons == ("B", "X")
