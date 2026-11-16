import dataclasses

import pytest

from switch2_layers.pad_action import (
    NEUTRAL_ACTION,
    PadAction,
    action_to_json,
    is_neutral,
    sort_buttons,
)


def test_neutral_action_is_empty():
    assert NEUTRAL_ACTION == PadAction(
        held=frozenset(),
        taps=frozenset(),
        left_stick=(0.0, 0.0),
        right_stick=(0.0, 0.0),
        hold_seconds={},
    )


def test_hold_seconds_default_is_empty():
    assert PadAction().hold_seconds == {}
    assert NEUTRAL_ACTION.hold_seconds == {}


def test_pad_action_with_hold_seconds_is_still_frozen():
    action = PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3})
    with pytest.raises(dataclasses.FrozenInstanceError):
        action.hold_seconds = {}  # type: ignore[misc]
    assert dataclasses.replace(action, hold_seconds={"A": 1.0}).hold_seconds == {
        "A": 1.0
    }
    assert action == PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3})
    assert action != PadAction(held=frozenset({"A"}), hold_seconds={"A": 1.0})


def test_is_neutral():
    assert is_neutral(NEUTRAL_ACTION)
    assert is_neutral(PadAction())
    assert not is_neutral(PadAction(held=frozenset({"A"})))
    assert not is_neutral(PadAction(taps=frozenset({"B"})))
    assert not is_neutral(PadAction(left_stick=(0.5, 0.0)))
    assert not is_neutral(PadAction(right_stick=(0.0, -1.0)))


def test_sort_buttons_uses_button_order_and_puts_unknown_names_last():
    assert sort_buttons(["LEFT", "B", "A", "HOME"]) == ["A", "B", "HOME", "LEFT"]
    assert sort_buttons(["ZZ", "A", "AA", "A"]) == ["A", "AA", "ZZ"]
    assert sort_buttons([]) == []


def test_action_to_json():
    action = PadAction(
        held=frozenset({"ZR", "A"}),
        taps=frozenset({"B"}),
        left_stick=(0.5, -1.0),
        right_stick=(0.0, 0.25),
        hold_seconds={"ZR": 1.0},
    )
    assert action_to_json(action) == {
        "held": ["A", "ZR"],
        "hold_seconds": {"ZR": 1.0},
        "taps": ["B"],
        "sticks": {"LS": [0.5, -1.0], "RS": [0.0, 0.25]},
    }


def test_action_to_json_writes_hold_seconds_of_held_buttons_only():
    action = PadAction(
        held=frozenset({"ZR", "A", "B"}),
        taps=frozenset({"X"}),
        hold_seconds={"ZR": 1.0, "A": 0.3, "X": 0.3, "Y": 1.0},
    )
    value = action_to_json(action)
    # 秒数の無い押し続け (B) と、held に無いボタン (X・Y) の秒数は入れない
    assert value["hold_seconds"] == {"A": 0.3, "ZR": 1.0}
    assert value["held"] == ["A", "B", "ZR"]
    assert value["taps"] == ["X"]


def test_action_to_json_of_neutral():
    assert action_to_json(NEUTRAL_ACTION) == {
        "held": [],
        "hold_seconds": {},
        "taps": [],
        "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
    }
