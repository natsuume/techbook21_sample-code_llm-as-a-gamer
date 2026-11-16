import json

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.middle.system_screen_schema import (
    MAX_OPERATIONS,
    MAX_WAIT_SECONDS,
    OUTPUT_FORMAT_NAME,
    build_system_screen_schema,
    build_system_screen_text_format,
    parse_system_screen_output,
    system_operation_to_json,
    system_output_to_json,
)
from switch2_layers.middle.system_screen_types import (
    SystemOperation,
    SystemScreenOutput,
)

SCREEN_NAMES = ("home_menu", "controllers", "controller_order", "user_select")
PRESS_DOWN = {
    "screen": "home_menu",
    "type": "press",
    "buttons": ["DOWN"],
    "seconds": None,
}
WAIT_HALF = {"screen": "home_menu", "type": "wait", "buttons": [], "seconds": 0.5}
# 読める出力 (誤りの出力は、これを 1 か所だけ変えて作る)
VALID_OUTPUT = {
    "observation": "HOME メニュー。カーソルは上の段のソフトにある",
    "operations": [PRESS_DOWN, WAIT_HALF],
    "done": False,
}


def output_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def with_operations(*operations: dict) -> dict:
    return {**VALID_OUTPUT, "operations": list(operations)}


def with_first_operation(**changes: object) -> dict:
    """VALID_OUTPUT の先頭の操作だけを changes で変えた出力。"""
    return with_operations({**PRESS_DOWN, **changes}, WAIT_HALF)


def parse(value: object):
    return parse_system_screen_output(output_text(value), SCREEN_NAMES)


# --- スキーマ ---


def test_schema_top_level_is_strict_object():
    schema = build_system_screen_schema(SCREEN_NAMES)
    assert schema["type"] == "object"
    assert list(schema["properties"]) == ["observation", "operations", "done"]
    assert schema["required"] == ["observation", "operations", "done"]
    assert schema["additionalProperties"] is False


def test_schema_operation_items():
    items = build_system_screen_schema(SCREEN_NAMES)["properties"]["operations"][
        "items"
    ]
    assert list(items["properties"]) == ["screen", "type", "buttons", "seconds"]
    assert items["required"] == ["screen", "type", "buttons", "seconds"]
    assert items["additionalProperties"] is False
    properties = items["properties"]
    assert properties["screen"]["enum"] == ["none", *SCREEN_NAMES]
    assert properties["type"]["enum"] == ["press", "wait"]
    assert properties["buttons"]["items"]["enum"] == list(BUTTON_ORDER)


def test_schema_screen_choices_follow_given_names():
    items = build_system_screen_schema(["user_select", "home_menu"])["properties"][
        "operations"
    ]["items"]
    assert items["properties"]["screen"]["enum"] == ["none", "user_select", "home_menu"]


def test_text_format_is_strict_json_schema():
    assert build_system_screen_text_format(SCREEN_NAMES) == {
        "type": "json_schema",
        "name": OUTPUT_FORMAT_NAME,
        "schema": build_system_screen_schema(SCREEN_NAMES),
        "strict": True,
    }


def test_schema_is_json_serializable():
    schema = build_system_screen_schema(SCREEN_NAMES)
    assert json.loads(json.dumps(schema)) == schema


@pytest.mark.parametrize(
    "names",
    [("home_menu", "none"), ("home_menu", "home_menu")],
    ids=["none-in-names", "duplicate-name"],
)
def test_schema_rejects_invalid_screen_names(names):
    with pytest.raises(ValueError):
        build_system_screen_schema(names)


# --- 読み取り (読めるもの) ---


def test_parse_valid_output():
    assert parse(VALID_OUTPUT) == SystemScreenOutput(
        observation="HOME メニュー。カーソルは上の段のソフトにある",
        operations=(
            SystemOperation(
                screen="home_menu", kind="press", buttons=("DOWN",), seconds=None
            ),
            SystemOperation(screen="home_menu", kind="wait", buttons=(), seconds=0.5),
        ),
        done=False,
    )


def test_parse_zero_operations():
    result = parse({"observation": "ゲームの画面", "operations": [], "done": True})
    assert result == SystemScreenOutput(
        observation="ゲームの画面", operations=(), done=True
    )


def test_parse_max_operations():
    result = parse(with_operations(*([PRESS_DOWN] * MAX_OPERATIONS)))
    assert MAX_OPERATIONS == 10
    assert len(result.operations) == 10


def test_parse_screen_none_is_a_choice():
    result = parse(with_first_operation(screen="none", buttons=["HOME"]))
    assert result.operations[0].screen == "none"
    assert result.operations[0].buttons == ("HOME",)


def test_parse_press_with_both_shoulders_and_one_direction():
    result = parse(
        with_operations(
            {**PRESS_DOWN, "screen": "controller_order", "buttons": ["L", "R"]},
            {**PRESS_DOWN, "buttons": ["UP", "B"]},
        )
    )
    assert result.operations[0].buttons == ("L", "R")
    assert result.operations[1].buttons == ("UP", "B")


def test_parse_hold_seconds_up_to_max_hold_seconds():
    result = parse(with_first_operation(seconds=MAX_HOLD_SECONDS))
    assert result.operations[0].seconds == MAX_HOLD_SECONDS


def test_parse_integer_seconds_become_float():
    result = parse(with_first_operation(seconds=2))
    seconds = result.operations[0].seconds
    assert seconds == 2.0
    assert isinstance(seconds, float)


def test_parse_wait_seconds_up_to_max_wait_seconds():
    assert MAX_WAIT_SECONDS == 10.0
    result = parse(with_operations({**WAIT_HALF, "seconds": MAX_WAIT_SECONDS}))
    assert result.operations[0].seconds == MAX_WAIT_SECONDS


def test_parse_keeps_buttons_in_written_order():
    result = parse(with_first_operation(buttons=["R", "L"]))
    assert result.operations[0].buttons == ("R", "L")


# --- 読み取り (誤り) ---


def test_parse_rejects_text_that_is_not_json():
    with pytest.raises(ValueError):
        parse_system_screen_output("{not json", SCREEN_NAMES)


def test_parse_rejects_json_that_is_not_an_object():
    with pytest.raises(ValueError):
        parse([VALID_OUTPUT])


def test_parse_rejects_more_than_max_operations():
    with pytest.raises(ValueError):
        parse(with_operations(*([PRESS_DOWN] * (MAX_OPERATIONS + 1))))


@pytest.mark.parametrize(
    "buttons",
    [["UP", "RIGHT"], ["UP", "DOWN"], ["LEFT", "A", "RIGHT"]],
    ids=["adjacent", "opposite", "with-other-button"],
)
def test_parse_rejects_press_with_two_directions(buttons):
    with pytest.raises(ValueError):
        parse(with_first_operation(buttons=buttons))


def test_parse_rejects_press_without_buttons():
    with pytest.raises(ValueError):
        parse(with_first_operation(buttons=[]))


def test_parse_rejects_wait_with_buttons():
    with pytest.raises(ValueError):
        parse(with_operations({**WAIT_HALF, "buttons": ["A"]}))


@pytest.mark.parametrize(
    "seconds",
    [0, 0.0, -1.0, MAX_HOLD_SECONDS + 0.1, True, "1", float("nan")],
    ids=["zero", "zero-float", "negative", "over-max", "bool", "string", "nan"],
)
def test_parse_rejects_press_seconds_out_of_range(seconds):
    # NaN は json.dumps で NaN と書かれ、json.loads で float の nan に戻る
    with pytest.raises(ValueError):
        parse(with_first_operation(seconds=seconds))


@pytest.mark.parametrize(
    "seconds",
    [None, 0, -0.5, MAX_WAIT_SECONDS + 0.1],
    ids=["null", "zero", "negative", "over-max"],
)
def test_parse_rejects_wait_seconds_out_of_range(seconds):
    with pytest.raises(ValueError):
        parse(with_operations({**WAIT_HALF, "seconds": seconds}))


@pytest.mark.parametrize(
    "screen",
    ["settings", "HOME_MENU", "", None],
    ids=["unknown", "wrong-case", "empty", "null"],
)
def test_parse_rejects_screen_not_in_choices(screen):
    with pytest.raises(ValueError):
        parse(with_first_operation(screen=screen))


def test_parse_rejects_unknown_button():
    with pytest.raises(ValueError):
        parse(with_first_operation(buttons=["JUMP"]))


def test_parse_rejects_unknown_type():
    with pytest.raises(ValueError):
        parse(with_first_operation(type="hold"))


def test_parse_rejects_unknown_top_level_key():
    with pytest.raises(ValueError):
        parse({**VALID_OUTPUT, "extra": 1})


@pytest.mark.parametrize("key", ["observation", "operations", "done"])
def test_parse_rejects_missing_top_level_key(key):
    value = {name: item for name, item in VALID_OUTPUT.items() if name != key}
    with pytest.raises(ValueError):
        parse(value)


def test_parse_rejects_unknown_operation_key():
    with pytest.raises(ValueError):
        parse(with_first_operation(extra=1))


@pytest.mark.parametrize("key", ["screen", "type", "buttons", "seconds"])
def test_parse_rejects_missing_operation_key(key):
    operation = {name: item for name, item in PRESS_DOWN.items() if name != key}
    with pytest.raises(ValueError):
        parse(with_operations(operation, WAIT_HALF))


@pytest.mark.parametrize(
    ("key", "value"),
    [("observation", 1), ("done", "false"), ("operations", {})],
    ids=["observation-not-string", "done-not-bool", "operations-not-array"],
)
def test_parse_rejects_wrong_top_level_types(key, value):
    with pytest.raises(ValueError):
        parse({**VALID_OUTPUT, key: value})


def test_parse_rejects_operation_that_is_not_an_object():
    with pytest.raises(ValueError):
        parse(with_operations("press DOWN"))


def test_parse_rejects_invalid_screen_names():
    with pytest.raises(ValueError):
        parse_system_screen_output(output_text(VALID_OUTPUT), ("home_menu", "none"))


# --- 出力の JSON の形 ---


def test_operation_to_json():
    operation = SystemOperation(
        screen="controller_order", kind="press", buttons=("L", "R"), seconds=0.4
    )
    assert system_operation_to_json(operation) == {
        "screen": "controller_order",
        "type": "press",
        "buttons": ["L", "R"],
        "seconds": 0.4,
    }
    assert list(system_operation_to_json(operation)) == [
        "screen",
        "type",
        "buttons",
        "seconds",
    ]


def test_output_to_json_round_trips_through_parse():
    output = parse(VALID_OUTPUT)
    value = system_output_to_json(output)
    assert list(value) == ["observation", "operations", "done"]
    assert value == VALID_OUTPUT
    assert parse(value) == output
