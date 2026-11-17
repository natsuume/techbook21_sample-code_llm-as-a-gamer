"""主ループの応答の本文から JSON を読む parse_decision を、誤りにする条件と、範囲の端の値で確かめる。

誤りの説明は、文の言い回しではなく、先頭のパス (`<パス>: `) で確かめる。
"""

import json

import pytest

from switch2_layers.orchestrator.decision_parser import (
    MAX_DELEGATE_CAUTIONS,
    MAX_DELEGATE_CHOICES,
    MAX_DELEGATE_TASKS,
    parse_decision,
)
from switch2_layers.orchestrator.decision_types import (
    Action,
    Decision,
    DecisionError,
    DelegateChoice,
    DelegateContext,
    DelegateStart,
    DelegateStop,
    DelegateUpdate,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    MonitorStop,
    PressAction,
    ProcedureAction,
    WaitAction,
)
from switch2_layers.orchestrator.procedures import MAX_PROCEDURE_COUNT

# 主ループが書く JSON の例 (操作・マクロ・監視をすべて使う)
CONTRACT_EXAMPLE = {
    "actions": [
        {"type": "press", "buttons": ["A"]},
        {"type": "press", "buttons": ["B", "LEFT"], "seconds": 0.5},
        {"type": "press", "sticks": {"left": {"x": 0.0, "y": 1.0}}, "seconds": 1.2},
        {"type": "wait", "seconds": 0.3},
        {
            "type": "procedure",
            "name": "walk",
            "args": {
                "direction": "UP",
                "count": 3,
                "seconds": 0.2,
                "interval_seconds": 0.1,
            },
        },
        {"type": "macro", "name": "next_message"},
    ],
    "macros": {
        "set": {
            "next_message": [
                {"type": "press", "buttons": ["A"]},
                {"type": "wait", "seconds": 0.5},
            ]
        },
        "delete": ["old_name"],
    },
    "monitor": {
        "action": "create",
        "name": "dialog",
        "cases": [
            {
                "condition": "画面の下に会話の枠があり、右下に▼が出ている",
                "actions": [{"type": "press", "buttons": ["A"]}],
                "repeat": "every",
            },
            {
                "condition": "はい・いいえの選択肢が出ている",
                "actions": [{"type": "press", "buttons": ["B"]}],
                "repeat": "once",
            },
        ],
    },
}


def json_block(body: str) -> str:
    """body を ```json のブロックにして、前後に地の文を置いた応答の本文を返す。"""
    return f"画面を見て、次の操作を決めた。\n```json\n{body}\n```\n以上。\n"


def parse_value(value: object, registered: frozenset[str] = frozenset()):
    """value を JSON にして ```json のブロックに入れ、parse_decision に渡す。"""
    return parse_decision(json_block(json.dumps(value, ensure_ascii=False)), registered)


def parse_actions(*actions: object, registered: frozenset[str] = frozenset()):
    """actions だけの JSON を parse_decision に渡す。"""
    return parse_value({"actions": list(actions)}, registered)


def messages_of(result: Decision | DecisionError) -> tuple[str, ...]:
    assert isinstance(result, DecisionError), result
    assert result.messages
    return result.messages


def assert_error_at(result: Decision | DecisionError, path: str) -> None:
    """result が誤りで、説明のどれかが `<path>: ` で始まることを確かめる。"""
    messages = messages_of(result)
    assert any(message.startswith(f"{path}: ") for message in messages), messages


def assert_decision(result: Decision | DecisionError) -> Decision:
    assert isinstance(result, Decision), result
    return result


def press(**fields: object) -> dict[str, object]:
    return {"type": "press", **fields}


def create_monitor(cases: list[object], name: object = "dialog") -> dict[str, object]:
    return {"monitor": {"action": "create", "name": name, "cases": cases}}


def case(
    condition: object = "画面の下に会話の枠がある",
    actions: object = None,
    repeat: object = "every",
) -> dict[str, object]:
    return {
        "condition": condition,
        "actions": [press(buttons=["A"])] if actions is None else actions,
        "repeat": repeat,
    }


# --- コードブロックの読み取り ---


def test_contract_example_is_read_into_the_types():
    result = assert_decision(parse_value(CONTRACT_EXAMPLE))
    assert result == Decision(
        actions=(
            PressAction(buttons=("A",)),
            PressAction(buttons=("B", "LEFT"), seconds=0.5),
            PressAction(sticks={"left": (0.0, 1.0)}, seconds=1.2),
            WaitAction(seconds=0.3),
            ProcedureAction(
                name="walk",
                args={
                    "direction": "UP",
                    "count": 3,
                    "seconds": 0.2,
                    "interval_seconds": 0.1,
                },
            ),
            MacroAction(name="next_message"),
        ),
        macros_set={
            "next_message": (PressAction(buttons=("A",)), WaitAction(seconds=0.5))
        },
        macros_delete=("old_name",),
        monitor=MonitorCreate(
            name="dialog",
            cases=(
                MonitorCase(
                    condition="画面の下に会話の枠があり、右下に▼が出ている",
                    actions=(PressAction(buttons=("A",)),),
                    repeat="every",
                ),
                MonitorCase(
                    condition="はい・いいえの選択肢が出ている",
                    actions=(PressAction(buttons=("B",)),),
                    repeat="once",
                ),
            ),
        ),
    )


def test_empty_object_changes_nothing():
    assert assert_decision(parse_value({})) == Decision()


def test_only_the_last_json_block_is_read():
    text = (
        '最初の案:\n```json\n{"actions": [{"type": "jump"}]}\n```\n'
        '直した案:\n```json\n{"actions": [{"type": "press", "buttons": ["A"]}]}\n```\n'
    )
    result = assert_decision(parse_decision(text))
    assert result.actions == (PressAction(buttons=("A",)),)


def test_a_valid_earlier_block_does_not_rescue_an_invalid_last_block():
    text = (
        '```json\n{"actions": [{"type": "press", "buttons": ["A"]}]}\n```\n'
        '```json\n{"actions": [{"type": "jump"}]}\n```\n'
    )
    assert_error_at(parse_decision(text), "actions[0].type")


def test_block_with_trailing_spaces_and_crlf_newlines_is_read():
    text = '決めた。\r\n```json  \r\n{"actions": []}\r\n```  \r\n'
    assert assert_decision(parse_decision(text)) == Decision()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "JSON は書かない。",
        '{"actions": []}',
        '```\n{"actions": []}\n```\n',
        '```python\n{"actions": []}\n```\n',
        '```json\n{"actions": []}\n',
        '前置き ```json\n{"actions": []}\n```\n',
    ],
    ids=[
        "empty",
        "no_block",
        "bare_json",
        "block_without_language",
        "other_language",
        "unclosed_block",
        "fence_not_at_line_start",
    ],
)
def test_text_without_a_json_block_is_an_error(text):
    messages_of(parse_decision(text))


@pytest.mark.parametrize(
    "tail",
    [
        '```json\n{"actions": [{"type": "press", "butt',
        "```json\n",
        "```json  \r\n{",
    ],
    ids=["cut_in_the_middle", "only_the_opening_line", "crlf_opening_line"],
)
def test_unclosed_json_block_after_the_last_closed_block_is_an_error(tail):
    # 前の閉じたブロックは正しくても、途中で切れた応答として誤りにする (前の案を実行しない)
    text = (
        "最初の案:\n```json\n"
        '{"actions": [{"type": "press", "buttons": ["A"]}]}\n'
        "```\n直した案:\n" + tail
    )
    messages_of(parse_decision(text))


@pytest.mark.parametrize(
    "tail",
    [
        "以上。\n",
        "本文の途中の ```json は開きの行ではない\n",
        "```python\nprint('not json')\n",
    ],
    ids=["plain_text", "fence_not_at_line_start", "unclosed_other_language"],
)
def test_text_after_the_last_closed_block_without_a_json_opening_line_is_valid(tail):
    text = '```json\n{"actions": [{"type": "press", "buttons": ["A"]}]}\n```\n' + tail
    result = assert_decision(parse_decision(text))
    assert result.actions == (PressAction(buttons=("A",)),)


@pytest.mark.parametrize(
    "body",
    ["", "{", '{"actions": [}', "not json", "{'actions': []}"],
    ids=["empty", "open_brace", "broken_array", "words", "single_quotes"],
)
def test_block_that_is_not_json_is_an_error(body):
    messages_of(parse_decision(json_block(body)))


@pytest.mark.parametrize("body", ["[]", '"actions"', "3", "null", "true"])
def test_top_level_that_is_not_an_object_is_an_error(body):
    messages_of(parse_decision(json_block(body)))


def test_unknown_top_level_key_is_an_error():
    assert_error_at(parse_value({"actions": [], "plan": "go"}), "plan")


@pytest.mark.parametrize(
    "value, path",
    [
        ({"actions": None}, "actions"),
        ({"actions": {"type": "press"}}, "actions"),
        ({"macros": None}, "macros"),
        ({"macros": []}, "macros"),
        ({"monitor": "stop"}, "monitor"),
        ({"actions": ["press"]}, "actions[0]"),
    ],
)
def test_values_of_the_wrong_type_are_errors(value, path):
    assert_error_at(parse_value(value), path)


def test_all_errors_are_reported_together():
    result = parse_value(
        {
            "actions": [{"type": "jump"}, press(buttons=["START"])],
            "monitor": {"action": "create", "name": "dialog", "cases": []},
        }
    )
    assert_error_at(result, "actions[0].type")
    assert_error_at(result, "actions[1].buttons[0]")
    assert_error_at(result, "monitor.cases")


# --- 操作の type ---


@pytest.mark.parametrize("action_type", ["jump", "PRESS", "", None, 1])
def test_unknown_action_type_is_an_error(action_type):
    assert_error_at(
        parse_actions({"type": action_type, "buttons": ["A"]}), "actions[0].type"
    )


def test_action_without_type_is_an_error():
    assert_error_at(parse_actions({"buttons": ["A"]}), "actions[0].type")


def test_unknown_key_in_an_action_is_an_error():
    assert_error_at(
        parse_actions(press(buttons=["A"], second=1.0)), "actions[0].second"
    )


# --- press: buttons ---


@pytest.mark.parametrize("name", ["a", "START", "LS", "Home", ""])
def test_button_name_not_in_button_order_is_an_error(name):
    assert_error_at(parse_actions(press(buttons=["B", name])), "actions[0].buttons[1]")


def test_button_that_is_not_a_string_is_an_error():
    assert_error_at(parse_actions(press(buttons=[1])), "actions[0].buttons[0]")


def test_buttons_that_are_not_an_array_is_an_error():
    assert_error_at(parse_actions(press(buttons="A")), "actions[0].buttons")


def test_home_is_not_an_error_even_if_it_cannot_be_pressed():
    result = assert_decision(parse_actions(press(buttons=["HOME"])))
    assert result.actions == (PressAction(buttons=("HOME",)),)


def test_buttons_are_sorted_in_button_order_without_duplicates():
    result = assert_decision(parse_actions(press(buttons=["LEFT", "B", "A", "B"])))
    assert result.actions == (PressAction(buttons=("A", "B", "LEFT")),)


@pytest.mark.parametrize(
    "buttons",
    [
        ["UP", "LEFT"],
        ["UP", "DOWN"],
        ["RIGHT", "DOWN", "A"],
        ["UP", "RIGHT", "DOWN", "LEFT"],
    ],
)
def test_two_or_more_dpad_directions_in_one_press_is_an_error(buttons):
    assert_error_at(parse_actions(press(buttons=buttons)), "actions[0].buttons")


def test_one_dpad_direction_with_other_buttons_is_valid():
    result = assert_decision(parse_actions(press(buttons=["A", "UP", "ZR"])))
    assert result.actions == (PressAction(buttons=("A", "ZR", "UP")),)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"buttons": []},
        {"sticks": {}},
        {"buttons": [], "sticks": {}},
        {"seconds": 1.0},
    ],
    ids=["nothing", "empty_buttons", "empty_sticks", "both_empty", "seconds_only"],
)
def test_press_without_buttons_or_sticks_is_an_error(fields):
    assert_error_at(parse_actions(press(**fields)), "actions[0]")


# --- press と wait: seconds ---


@pytest.mark.parametrize(
    "seconds_text",
    [
        "0",
        "0.0",
        "-0.5",
        "-1",
        "NaN",
        "Infinity",
        "-Infinity",
        "1e999",
        "60.0001",
        "61",
        "3600",
    ],
)
def test_press_seconds_out_of_range_is_an_error(seconds_text):
    body = (
        '{"actions": [{"type": "press", "buttons": ["A"], "seconds": '
        + seconds_text
        + "}]}"
    )
    assert_error_at(parse_decision(json_block(body)), "actions[0].seconds")


@pytest.mark.parametrize(
    "seconds_text",
    ["0", "-0.1", "NaN", "Infinity", "1e999", "60.5"],
)
def test_wait_seconds_out_of_range_is_an_error(seconds_text):
    body = '{"actions": [{"type": "wait", "seconds": ' + seconds_text + "}]}"
    assert_error_at(parse_decision(json_block(body)), "actions[0].seconds")


@pytest.mark.parametrize("seconds", [0.001, 0.5, 1, 59.999, 60, 60.0])
def test_press_and_wait_seconds_up_to_sixty_are_valid(seconds):
    result = assert_decision(
        parse_actions(
            press(buttons=["A"], seconds=seconds), {"type": "wait", "seconds": seconds}
        )
    )
    assert result.actions == (
        PressAction(buttons=("A",), seconds=float(seconds)),
        WaitAction(seconds=float(seconds)),
    )
    assert isinstance(result.actions[0].seconds, float)
    assert isinstance(result.actions[1].seconds, float)


@pytest.mark.parametrize("seconds", ["1", True, None, [1]])
def test_seconds_that_are_not_a_number_is_an_error(seconds):
    assert_error_at(
        parse_actions(press(buttons=["A"], seconds=seconds)), "actions[0].seconds"
    )
    assert_error_at(
        parse_actions({"type": "wait", "seconds": seconds}), "actions[0].seconds"
    )


def test_wait_without_seconds_is_an_error():
    assert_error_at(parse_actions({"type": "wait"}), "actions[0].seconds")


def test_press_without_seconds_is_a_short_press():
    result = assert_decision(parse_actions(press(buttons=["A"])))
    assert result.actions == (PressAction(buttons=("A",), seconds=None),)


def test_unknown_key_in_wait_is_an_error():
    assert_error_at(
        parse_actions({"type": "wait", "seconds": 1, "buttons": ["A"]}),
        "actions[0].buttons",
    )


# --- press: sticks ---


@pytest.mark.parametrize(
    "x, y",
    [(0.0, 1.0), (-1.0, 0.0), (1, 0), (0.0, -1.0), (0.6, 0.8), (0.5, -0.5), (0, 0)],
)
def test_stick_within_the_unit_circle_is_valid(x, y):
    result = assert_decision(parse_actions(press(sticks={"right": {"x": x, "y": y}})))
    assert result.actions == (
        PressAction(sticks={"right": (float(x), float(y))}, seconds=None),
    )


def test_both_sticks_and_buttons_together_are_valid():
    result = assert_decision(
        parse_actions(
            press(
                buttons=["A"],
                sticks={"left": {"x": 0.0, "y": 1.0}, "right": {"x": -1.0, "y": 0.0}},
                seconds=0.4,
            )
        )
    )
    assert result.actions == (
        PressAction(
            buttons=("A",),
            sticks={"left": (0.0, 1.0), "right": (-1.0, 0.0)},
            seconds=0.4,
        ),
    )


@pytest.mark.parametrize(
    "value_text, path",
    [
        ('{"x": 1.01, "y": 0}', "actions[0].sticks.left.x"),
        ('{"x": -1.5, "y": 0}', "actions[0].sticks.left.x"),
        ('{"x": 0, "y": -1.01}', "actions[0].sticks.left.y"),
        ('{"x": 0, "y": 2}', "actions[0].sticks.left.y"),
        ('{"x": NaN, "y": 0}', "actions[0].sticks.left.x"),
        ('{"x": 0, "y": Infinity}', "actions[0].sticks.left.y"),
        ('{"x": "0.5", "y": 0}', "actions[0].sticks.left.x"),
        ('{"x": true, "y": 0}', "actions[0].sticks.left.x"),
        ('{"x": 0.5}', "actions[0].sticks.left.y"),
        ('{"y": 0.5}', "actions[0].sticks.left.x"),
        ('{"x": 0, "y": 0, "z": 1}', "actions[0].sticks.left.z"),
        ("[0, 1]", "actions[0].sticks.left"),
    ],
)
def test_stick_value_errors(value_text, path):
    body = '{"actions": [{"type": "press", "sticks": {"left": ' + value_text + "}}]}"
    assert_error_at(parse_decision(json_block(body)), path)


@pytest.mark.parametrize("x, y", [(0.8, 0.8), (1.0, 0.1), (-0.75, -0.75), (1.0, 1.0)])
def test_stick_longer_than_one_is_an_error(x, y):
    assert_error_at(
        parse_actions(press(sticks={"left": {"x": x, "y": y}})),
        "actions[0].sticks.left",
    )


@pytest.mark.parametrize("key", ["LS", "Left", "center", ""])
def test_unknown_stick_key_is_an_error(key):
    assert_error_at(
        parse_actions(press(sticks={key: {"x": 0.0, "y": 1.0}})),
        f"actions[0].sticks.{key}",
    )


def test_sticks_that_are_not_an_object_is_an_error():
    assert_error_at(parse_actions(press(sticks=[0.0, 1.0])), "actions[0].sticks")


# --- procedure ---


def test_tap_repeat_is_valid():
    args = {"button": "A", "count": 3, "interval_seconds": 0.2}
    result = assert_decision(
        parse_actions({"type": "procedure", "name": "tap_repeat", "args": args})
    )
    assert result.actions == (ProcedureAction(name="tap_repeat", args=args),)


def test_walk_is_valid():
    args = {"direction": "LEFT", "count": 1, "seconds": 60, "interval_seconds": 0}
    result = assert_decision(
        parse_actions({"type": "procedure", "name": "walk", "args": args})
    )
    assert result.actions == (ProcedureAction(name="walk", args=args),)


@pytest.mark.parametrize("name", ["run", "Walk", "", None])
def test_unknown_procedure_name_is_an_error(name):
    assert_error_at(
        parse_actions({"type": "procedure", "name": name, "args": {}}),
        "actions[0].name",
    )


def tap_repeat(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {"button": "A", "count": 2, "interval_seconds": 0.1}
    args.update(overrides)
    return {"type": "procedure", "name": "tap_repeat", "args": args}


def walk(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "direction": "UP",
        "count": 2,
        "seconds": 0.2,
        "interval_seconds": 0.1,
    }
    args.update(overrides)
    return {"type": "procedure", "name": "walk", "args": args}


@pytest.mark.parametrize(
    "action, path",
    [
        (tap_repeat(button="START"), "actions[0].args.button"),
        (tap_repeat(button=1), "actions[0].args.button"),
        (tap_repeat(count=0), "actions[0].args.count"),
        (tap_repeat(count=-1), "actions[0].args.count"),
        (tap_repeat(count=2.0), "actions[0].args.count"),
        (tap_repeat(count=True), "actions[0].args.count"),
        (tap_repeat(count="3"), "actions[0].args.count"),
        (tap_repeat(interval_seconds=-0.1), "actions[0].args.interval_seconds"),
        (tap_repeat(interval_seconds=60.1), "actions[0].args.interval_seconds"),
        (tap_repeat(interval_seconds="0.1"), "actions[0].args.interval_seconds"),
        (tap_repeat(speed=2), "actions[0].args.speed"),
        (walk(direction="A"), "actions[0].args.direction"),
        (walk(direction="up"), "actions[0].args.direction"),
        (walk(seconds=0), "actions[0].args.seconds"),
        (walk(seconds=60.5), "actions[0].args.seconds"),
        (walk(count=0), "actions[0].args.count"),
        (walk(interval_seconds=61), "actions[0].args.interval_seconds"),
        (walk(button="A"), "actions[0].args.button"),
    ],
)
def test_procedure_argument_errors(action, path):
    assert_error_at(parse_actions(action), path)


@pytest.mark.parametrize(
    "missing, action",
    [
        ("button", tap_repeat()),
        ("count", tap_repeat()),
        ("interval_seconds", tap_repeat()),
        ("direction", walk()),
        ("count", walk()),
        ("seconds", walk()),
        ("interval_seconds", walk()),
    ],
)
def test_missing_procedure_argument_is_an_error(missing, action):
    del action["args"][missing]
    assert_error_at(parse_actions(action), f"actions[0].args.{missing}")


@pytest.mark.parametrize("args", [None, [], "A"])
def test_procedure_args_that_are_not_an_object_is_an_error(args):
    assert_error_at(
        parse_actions({"type": "procedure", "name": "tap_repeat", "args": args}),
        "actions[0].args",
    )


def test_procedure_without_args_is_an_error():
    assert_error_at(
        parse_actions({"type": "procedure", "name": "tap_repeat"}), "actions[0].args"
    )


def test_procedure_boundaries_are_valid():
    result = parse_actions(
        tap_repeat(button="HOME", count=1, interval_seconds=0),
        tap_repeat(interval_seconds=60),
        walk(seconds=60, interval_seconds=60),
        walk(seconds=0.001),
    )
    assert_decision(result)


def test_procedure_count_limit_is_100():
    assert MAX_PROCEDURE_COUNT == 100


@pytest.mark.parametrize("make_action", [tap_repeat, walk], ids=["tap_repeat", "walk"])
def test_procedure_count_up_to_the_limit_is_valid(make_action):
    result = assert_decision(parse_actions(make_action(count=MAX_PROCEDURE_COUNT)))
    assert result.actions[0].args["count"] == 100


@pytest.mark.parametrize("make_action", [tap_repeat, walk], ids=["tap_repeat", "walk"])
@pytest.mark.parametrize("count", [MAX_PROCEDURE_COUNT + 1, 1000])
def test_procedure_count_over_the_limit_is_an_error(make_action, count):
    assert_error_at(parse_actions(make_action(count=count)), "actions[0].args.count")


def test_procedure_seconds_nan_is_an_error():
    body = (
        '{"actions": [{"type": "procedure", "name": "walk", "args": '
        '{"direction": "UP", "count": 1, "seconds": NaN, "interval_seconds": Infinity}}]}'
    )
    result = parse_decision(json_block(body))
    assert_error_at(result, "actions[0].args.seconds")
    assert_error_at(result, "actions[0].args.interval_seconds")


# --- macro ---


def macro(name: object) -> dict[str, object]:
    return {"type": "macro", "name": name}


def test_macro_defined_in_the_same_json_can_be_called():
    result = assert_decision(
        parse_value(
            {
                "actions": [macro("next")],
                "macros": {"set": {"next": [press(buttons=["A"])]}},
            }
        )
    )
    assert result.actions == (MacroAction(name="next"),)
    assert result.macros_set == {"next": (PressAction(buttons=("A",)),)}


def test_macro_registered_before_can_be_called():
    result = assert_decision(
        parse_actions(macro("next"), registered=frozenset({"next"}))
    )
    assert result.actions == (MacroAction(name="next"),)


def test_macro_not_registered_is_an_error():
    assert_error_at(
        parse_actions(macro("next"), registered=frozenset({"other"})), "actions[0].name"
    )


def test_calling_without_the_registry_treats_it_as_empty():
    text = json_block(json.dumps({"actions": [macro("next")]}))
    assert_error_at(parse_decision(text), "actions[0].name")


def test_macro_deleted_in_the_same_json_cannot_be_called():
    result = parse_value(
        {"actions": [macro("next")], "macros": {"delete": ["next"]}},
        registered=frozenset({"next"}),
    )
    assert_error_at(result, "actions[0].name")


def test_name_in_both_set_and_delete_ends_up_deleted():
    result = parse_value(
        {
            "actions": [macro("next")],
            "macros": {"set": {"next": [press(buttons=["A"])]}, "delete": ["next"]},
        }
    )
    assert_error_at(result, "actions[0].name")


def test_macro_name_that_is_not_a_string_is_an_error():
    assert_error_at(parse_actions(macro(3)), "actions[0].name")


def test_macro_inside_a_macro_is_an_error():
    result = parse_value(
        {
            "macros": {
                "set": {
                    "inner": [press(buttons=["A"])],
                    "outer": [press(buttons=["B"]), macro("inner")],
                }
            }
        }
    )
    assert_error_at(result, "macros.set.outer[1].type")


def test_procedure_inside_a_macro_is_valid():
    result = assert_decision(parse_value({"macros": {"set": {"walk_up": [walk()]}}}))
    assert result.macros_set == {
        "walk_up": (
            ProcedureAction(
                name="walk",
                args={
                    "direction": "UP",
                    "count": 2,
                    "seconds": 0.2,
                    "interval_seconds": 0.1,
                },
            ),
        )
    }


def test_errors_inside_a_macro_definition_have_the_macro_path():
    result = parse_value({"macros": {"set": {"next": [press(buttons=["START"])]}}})
    assert_error_at(result, "macros.set.next[0].buttons[0]")


def test_deleting_an_unknown_macro_is_valid():
    result = assert_decision(parse_value({"macros": {"delete": ["missing"]}}))
    assert result.macros_delete == ("missing",)


@pytest.mark.parametrize(
    "macros, path",
    [
        ({"set": []}, "macros.set"),
        ({"set": {"next": {"type": "press"}}}, "macros.set.next"),
        ({"delete": "next"}, "macros.delete"),
        ({"delete": [1]}, "macros.delete[0]"),
        ({"replace": {}}, "macros.replace"),
    ],
)
def test_macros_shape_errors(macros, path):
    assert_error_at(parse_value({"macros": macros}), path)


# --- monitor ---


@pytest.mark.parametrize("value", [{}, {"monitor": None}])
def test_monitor_absent_or_null_changes_nothing(value):
    assert assert_decision(parse_value(value)).monitor is None


def test_monitor_stop():
    assert (
        assert_decision(parse_value({"monitor": {"action": "stop"}})).monitor
        == MonitorStop()
    )


def test_monitor_stop_with_another_key_is_an_error():
    assert_error_at(
        parse_value({"monitor": {"action": "stop", "name": "dialog"}}), "monitor.name"
    )


@pytest.mark.parametrize("action", ["start", "STOP", None, ""])
def test_unknown_monitor_action_is_an_error(action):
    assert_error_at(parse_value({"monitor": {"action": action}}), "monitor.action")


def test_monitor_without_action_is_an_error():
    assert_error_at(
        parse_value({"monitor": {"name": "dialog", "cases": [case()]}}),
        "monitor.action",
    )


@pytest.mark.parametrize("count", [1, 8])
def test_monitor_with_one_to_eight_cases_is_valid(count):
    cases = [case(condition=f"条件 {index}") for index in range(count)]
    result = assert_decision(parse_value(create_monitor(cases)))
    assert isinstance(result.monitor, MonitorCreate)
    assert result.monitor.name == "dialog"
    assert [c.condition for c in result.monitor.cases] == [
        f"条件 {i}" for i in range(count)
    ]


@pytest.mark.parametrize("count", [0, 9])
def test_monitor_with_zero_or_nine_cases_is_an_error(count):
    cases = [case(condition=f"条件 {index}") for index in range(count)]
    assert_error_at(parse_value(create_monitor(cases)), "monitor.cases")


@pytest.mark.parametrize("condition", ["", " ", "　\t\n", None, 1])
def test_blank_or_non_string_condition_is_an_error(condition):
    assert_error_at(
        parse_value(create_monitor([case(condition=condition)])),
        "monitor.cases[0].condition",
    )


@pytest.mark.parametrize("repeat", ["always", "ONCE", "", None, 1])
def test_repeat_other_than_every_or_once_is_an_error(repeat):
    assert_error_at(
        parse_value(create_monitor([case(repeat=repeat)])), "monitor.cases[0].repeat"
    )


@pytest.mark.parametrize("missing", ["condition", "actions", "repeat"])
def test_case_without_a_key_is_an_error(missing):
    value = case()
    del value[missing]
    assert_error_at(parse_value(create_monitor([value])), f"monitor.cases[0].{missing}")


def test_unknown_key_in_a_case_is_an_error():
    value = case()
    value["priority"] = 1
    assert_error_at(parse_value(create_monitor([value])), "monitor.cases[0].priority")


def test_monitor_create_without_name_is_an_error():
    assert_error_at(
        parse_value({"monitor": {"action": "create", "cases": [case()]}}),
        "monitor.name",
    )


@pytest.mark.parametrize("name", [None, 1, ["dialog"]])
def test_monitor_name_that_is_not_a_string_is_an_error(name):
    assert_error_at(parse_value(create_monitor([case()], name=name)), "monitor.name")


def test_monitor_cases_that_are_not_an_array_is_an_error():
    assert_error_at(
        parse_value(
            {"monitor": {"action": "create", "name": "dialog", "cases": case()}}
        ),
        "monitor.cases",
    )


def test_errors_in_case_actions_have_the_case_path():
    result = parse_value(
        create_monitor([case(), case(actions=[press(buttons=["UP", "DOWN"])])])
    )
    assert_error_at(result, "monitor.cases[1].actions[0].buttons")


def test_case_actions_can_call_registered_macros_and_procedures():
    result = assert_decision(
        parse_value(
            create_monitor(
                [
                    case(
                        actions=[
                            macro("next"),
                            tap_repeat(),
                            {"type": "wait", "seconds": 1},
                        ]
                    )
                ]
            ),
            registered=frozenset({"next"}),
        )
    )
    assert isinstance(result.monitor, MonitorCreate)
    assert result.monitor.cases[0].actions == (
        MacroAction(name="next"),
        ProcedureAction(
            name="tap_repeat", args={"button": "A", "count": 2, "interval_seconds": 0.1}
        ),
        WaitAction(seconds=1.0),
    )


def test_case_actions_calling_an_unknown_macro_is_an_error():
    result = parse_value(create_monitor([case(actions=[macro("next")])]))
    assert_error_at(result, "monitor.cases[0].actions[0].name")


def test_case_with_empty_actions_is_valid():
    result = assert_decision(
        parse_value(create_monitor([case(actions=[], repeat="once")]))
    )
    assert isinstance(result.monitor, MonitorCreate)
    assert result.monitor.cases == (
        MonitorCase(condition="画面の下に会話の枠がある", actions=(), repeat="once"),
    )


def test_condition_text_is_kept_as_written():
    condition = "  画面の下に会話の枠があり、右下に▼が出ている  "
    result = assert_decision(parse_value(create_monitor([case(condition=condition)])))
    assert isinstance(result.monitor, MonitorCreate)
    assert result.monitor.cases[0].condition == condition


# --- どんな本文でも例外を出さない ---


@pytest.mark.parametrize(
    "text",
    [
        "```json\n```\n",
        '```json\n{"actions": [null, 1, "x", [], {}]}\n```\n',
        '```json\n{"macros": {"set": {"": [null]}}}\n```\n',
        '```json\n{"monitor": {"action": "create", "name": "d", "cases": [null, 1]}}\n```\n',
        '```json\n{"actions": [{"type": "press", "buttons": null, "sticks": null}]}\n```\n',
    ],
)
def test_broken_shapes_return_an_error_without_raising(text):
    messages_of(parse_decision(text))


# --- float に収まらない大きな整数 ---

# 1 の後に 0 が 400 個の整数 (JSON の整数としては読めるが、float にすると桁あふれする)
HUGE_INTEGER = "1" + "0" * 400


@pytest.mark.parametrize(
    "body, path",
    [
        (
            '{"actions": [{"type": "press", "buttons": ["A"], "seconds": HUGE}]}',
            "actions[0].seconds",
        ),
        (
            '{"actions": [{"type": "press", "buttons": ["A"], "seconds": -HUGE}]}',
            "actions[0].seconds",
        ),
        ('{"actions": [{"type": "wait", "seconds": HUGE}]}', "actions[0].seconds"),
        (
            '{"actions": [{"type": "press", "sticks": {"left": {"x": HUGE, "y": 0}}}]}',
            "actions[0].sticks.left.x",
        ),
        (
            '{"actions": [{"type": "press", "sticks": {"left": {"x": 0, "y": -HUGE}}}]}',
            "actions[0].sticks.left.y",
        ),
        (
            (
                '{"actions": [{"type": "procedure", "name": "walk", "args": '
                '{"direction": "UP", "count": 1, "seconds": HUGE, "interval_seconds": 0}}]}'
            ),
            "actions[0].args.seconds",
        ),
        (
            (
                '{"actions": [{"type": "procedure", "name": "tap_repeat", "args": '
                '{"button": "A", "count": 1, "interval_seconds": HUGE}}]}'
            ),
            "actions[0].args.interval_seconds",
        ),
        (
            (
                '{"actions": [{"type": "procedure", "name": "walk", "args": '
                '{"direction": "UP", "count": HUGE, "seconds": 0.2, "interval_seconds": 0}}]}'
            ),
            "actions[0].args.count",
        ),
        (
            '{"macros": {"set": {"m": [{"type": "wait", "seconds": HUGE}]}}}',
            "macros.set.m[0].seconds",
        ),
        (
            (
                '{"monitor": {"action": "create", "name": "dialog", "cases": '
                '[{"condition": "c", "actions": [{"type": "press", "buttons": ["A"], '
                '"seconds": HUGE}], "repeat": "every"}]}}'
            ),
            "monitor.cases[0].actions[0].seconds",
        ),
    ],
    ids=[
        "press_seconds",
        "negative_press_seconds",
        "wait_seconds",
        "stick_x",
        "negative_stick_y",
        "walk_seconds",
        "tap_repeat_interval_seconds",
        "walk_count",
        "macro_wait_seconds",
        "monitor_case_seconds",
    ],
)
def test_huge_integer_is_an_error_without_raising(body, path):
    result = parse_decision(json_block(body.replace("HUGE", HUGE_INTEGER)))
    assert_error_at(result, path)


# --- --mode delegate の委任の指示 (parse_decision の delegate に DelegateContext を渡す) ---

# 今登録しているマクロ (go_home は HOME を押す)
DELEGATE_MACROS: dict[str, tuple[Action, ...]] = {
    "attack": (PressAction(buttons=("A",)),),
    "move_up": (PressAction(buttons=("UP",)),),
    "go_home": (PressAction(buttons=("HOME",)),),
    **{f"m{index}": (PressAction(buttons=("B",)),) for index in range(13)},
}
PREVIOUS_DELEGATION = DelegateStart(
    tasks=("最重要: HP を 0 にせずに敵をすべて倒す",),
    cautions=("衝撃波は横一列に進む",),
    choices=(DelegateChoice(name="attack", description="前の敵を攻撃する"),),
)
# JSON のキーを省くことを表す値
OMIT = object()


def delegate_context(
    active: bool = False, previous: DelegateStart | None = None
) -> DelegateContext:
    return DelegateContext(active=active, previous=previous, macros=DELEGATE_MACROS)


def parse_delegate(value: object, context: DelegateContext | None = None):
    """value を JSON にして、--mode delegate の parse_decision に渡す (今のマクロは DELEGATE_MACROS)。"""
    context = context if context is not None else delegate_context()
    return parse_decision(
        json_block(json.dumps(value, ensure_ascii=False)),
        frozenset(DELEGATE_MACROS),
        context,
    )


def choice(
    name: object = "attack", description: object = "前の敵を攻撃する"
) -> dict[str, object]:
    return {"name": name, "description": description}


def delegate_with(action: str, **fields: object) -> dict[str, object]:
    """{"delegate": {"action": action, ...}}。start の既定は 1 つずつのタスクと行動、注意事項なし。OMIT のキーは省く。"""
    values: dict[str, object] = {"action": action}
    if action == "start":
        values.update(
            {"tasks": ["敵をすべて倒す"], "cautions": [], "choices": [choice()]}
        )
    values.update(fields)
    return {"delegate": {key: item for key, item in values.items() if item is not OMIT}}


def test_delegate_key_is_unknown_in_orchestrator_mode():
    assert_error_at(parse_value({"delegate": {"action": "stop"}}), "delegate")


def test_delegate_monitor_is_an_unknown_key_in_delegate_mode():
    assert_error_at(parse_delegate(create_monitor([case()])), "monitor")


def test_delegate_start_is_read_into_the_types():
    value = delegate_with(
        "start",
        tasks=["最重要: HP を 0 にせずに敵をすべて倒す", "敵の攻撃は、可能なら避ける"],
        cautions=["衝撃波は横一列に進む"],
        choices=[choice(), choice("move_up", "上に 1 マス移動する")],
    )
    assert assert_decision(parse_delegate(value)) == Decision(
        delegate=DelegateStart(
            tasks=(
                "最重要: HP を 0 にせずに敵をすべて倒す",
                "敵の攻撃は、可能なら避ける",
            ),
            cautions=("衝撃波は横一列に進む",),
            choices=(
                DelegateChoice(name="attack", description="前の敵を攻撃する"),
                DelegateChoice(name="move_up", description="上に 1 マス移動する"),
            ),
        )
    )


def test_delegate_start_while_delegating_is_an_error():
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    assert_error_at(parse_delegate(delegate_with("start"), context), "delegate.action")


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            delegate_with("update", tasks=["前に出すぎない"]),
            DelegateUpdate(tasks=("前に出すぎない",)),
        ),
        (delegate_with("stop"), DelegateStop()),
    ],
    ids=["update", "stop"],
)
def test_delegate_update_or_stop_while_not_delegating_is_read_with_the_actions_and_macros(
    value, expected
):
    # JSON を書く間に委任が終わっていても誤りにせず、同じ JSON の操作とマクロを当てられるようにする
    context = delegate_context(active=False, previous=PREVIOUS_DELEGATION)
    json_value = {
        "actions": [press(buttons=["A"])],
        "macros": {"set": {"dash": [press(buttons=["B"])]}},
        **value,
    }
    decision = assert_decision(parse_delegate(json_value, context))
    assert decision.delegate == expected
    assert decision.actions == (PressAction(buttons=("A",)),)
    assert set(decision.macros_set) == {"dash"}


@pytest.mark.parametrize(
    "value, path",
    [
        (delegate_with("update", tasks=[]), "delegate.tasks"),
        (
            delegate_with("update", choices=[choice("jump", "ジャンプする")]),
            "delegate.choices[0].name",
        ),
        (delegate_with("stop", tasks=["敵を倒す"]), "delegate.tasks"),
    ],
    ids=["update_without_tasks", "update_with_unregistered_choice", "stop_with_tasks"],
)
def test_delegate_update_or_stop_while_not_delegating_still_follows_the_value_rules(
    value, path
):
    context = delegate_context(active=False, previous=PREVIOUS_DELEGATION)
    assert_error_at(parse_delegate(value, context), path)


def test_delegate_start_without_the_keys_uses_the_previous_values():
    context = delegate_context(active=False, previous=PREVIOUS_DELEGATION)
    value = delegate_with("start", tasks=OMIT, cautions=OMIT, choices=OMIT)
    decision = assert_decision(parse_delegate(value, context))
    assert decision.delegate == PREVIOUS_DELEGATION


@pytest.mark.parametrize("key", ["tasks", "cautions", "choices"])
def test_delegate_start_without_a_key_and_no_previous_delegation_is_an_error(key):
    value = delegate_with("start", **{key: OMIT})
    assert_error_at(parse_delegate(value), f"delegate.{key}")


def test_delegate_update_keeps_the_omitted_keys():
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    value = delegate_with("update", tasks=["敵をすべて倒す", "前に出すぎない"])
    decision = assert_decision(parse_delegate(value, context))
    assert decision.delegate == DelegateUpdate(
        tasks=("敵をすべて倒す", "前に出すぎない"), cautions=None, choices=None
    )


def test_delegate_stop_is_read_while_delegating():
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    decision = assert_decision(parse_delegate(delegate_with("stop"), context))
    assert decision.delegate == DelegateStop()


@pytest.mark.parametrize(
    "fields",
    [
        {"choices": [choice()]},
        {"choices": [choice(f"m{index}", f"行動 {index}") for index in range(12)]},
        {"tasks": ["敵を倒す"]},
        {"tasks": [f"タスク {index}" for index in range(10)]},
        {"cautions": []},
        {"cautions": [f"注意 {index}" for index in range(10)]},
    ],
    ids=["1_choice", "12_choices", "1_task", "10_tasks", "0_cautions", "10_cautions"],
)
def test_delegate_counts_at_the_limits_are_valid(fields):
    assert MAX_DELEGATE_CHOICES == 12
    assert MAX_DELEGATE_TASKS == 10
    assert MAX_DELEGATE_CAUTIONS == 10
    assert_decision(parse_delegate(delegate_with("start", **fields)))


@pytest.mark.parametrize(
    "fields, path",
    [
        ({"choices": []}, "delegate.choices"),
        (
            {"choices": [choice(f"m{index}", f"行動 {index}") for index in range(13)]},
            "delegate.choices",
        ),
        ({"tasks": []}, "delegate.tasks"),
        ({"tasks": [f"タスク {index}" for index in range(11)]}, "delegate.tasks"),
        ({"cautions": [f"注意 {index}" for index in range(11)]}, "delegate.cautions"),
    ],
    ids=["0_choices", "13_choices", "0_tasks", "11_tasks", "11_cautions"],
)
def test_delegate_counts_out_of_the_limits_are_errors(fields, path):
    assert_error_at(parse_delegate(delegate_with("start", **fields)), path)


@pytest.mark.parametrize(
    "fields, path",
    [
        ({"tasks": [""]}, "delegate.tasks[0]"),
        ({"tasks": ["敵を倒す", "  "]}, "delegate.tasks[1]"),
        ({"cautions": [""]}, "delegate.cautions[0]"),
        ({"choices": [choice(description="")]}, "delegate.choices[0].description"),
        ({"choices": [choice(description=" \n")]}, "delegate.choices[0].description"),
    ],
    ids=[
        "empty_task",
        "blank_task",
        "empty_caution",
        "empty_description",
        "blank_description",
    ],
)
def test_delegate_empty_texts_are_errors(fields, path):
    assert_error_at(parse_delegate(delegate_with("start", **fields)), path)


def test_delegate_choice_of_a_macro_that_is_not_registered_is_an_error():
    value = delegate_with("start", choices=[choice("jump", "ジャンプする")])
    assert_error_at(parse_delegate(value), "delegate.choices[0].name")


def test_delegate_same_choice_name_twice_is_an_error():
    value = delegate_with(
        "start",
        choices=[choice("attack", "攻撃する"), choice("attack", "もう一度攻撃する")],
    )
    assert_error_at(parse_delegate(value), "delegate.choices[1].name")


def test_delegate_choice_of_a_macro_pressing_home_is_an_error():
    value = delegate_with("start", choices=[choice("go_home", "HOME メニューを開く")])
    assert_error_at(parse_delegate(value), "delegate.choices[0].name")


def test_delegate_deleting_a_macro_used_by_the_current_choices_is_an_error():
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    value = {"macros": {"delete": ["attack"]}}
    assert_error_at(parse_delegate(value, context), "macros.delete[0]")


@pytest.mark.parametrize(
    "delegate_value",
    [OMIT, None, {"action": "stop"}],
    ids=["no_delegate", "null_delegate", "stop"],
)
def test_delegate_replacing_a_macro_of_the_current_choices_with_home_is_an_error(
    delegate_value,
):
    # 委任中は、delegate のキーの有無と値に依らず、行動のマクロを HOME を押す列に置き換えられない
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    value: dict[str, object] = {
        "macros": {"set": {"attack": [press(buttons=["HOME"])]}}
    }
    if delegate_value is not OMIT:
        value["delegate"] = delegate_value
    assert_error_at(parse_delegate(value, context), "macros.set.attack")


def test_delegate_registering_a_home_macro_not_used_by_the_choices_is_valid():
    context = delegate_context(active=True, previous=PREVIOUS_DELEGATION)
    value = {"macros": {"set": {"open_home": [press(buttons=["HOME"])]}}}
    decision = assert_decision(parse_delegate(value, context))
    assert set(decision.macros_set) == {"open_home"}


def test_delegate_error_changes_nothing_of_the_actions_and_macros():
    # 委任の指示に誤りがあれば、同じ JSON の操作とマクロも当てない (Decision を返さない)
    value = {
        "actions": [press(buttons=["A"])],
        "macros": {"set": {"dash": [press(buttons=["B"])]}},
        **delegate_with("start", tasks=[]),
    }
    assert isinstance(parse_delegate(value), DecisionError)
