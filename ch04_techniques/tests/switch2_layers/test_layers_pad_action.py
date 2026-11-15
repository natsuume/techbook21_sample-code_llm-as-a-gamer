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
    )


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
    )
    assert action_to_json(action) == {
        "held": ["A", "ZR"],
        "taps": ["B"],
        "sticks": {"LS": [0.5, -1.0], "RS": [0.0, 0.25]},
    }


def test_action_to_json_of_neutral():
    assert action_to_json(NEUTRAL_ACTION) == {
        "held": [],
        "taps": [],
        "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
    }
