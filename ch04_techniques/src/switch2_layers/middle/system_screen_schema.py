"""本体の画面の middle (gpt-6-luna) の出力の JSON Schema (Structured Outputs) と、出力の JSON の文を SystemScreenOutput に
する読み取り。I/O を扱わない。

出力の形 (すべて required。additionalProperties は false。キーの順に書かせる):
    {"observation": 文字列,
     "operations": [{"screen": 画面の名前, "type": "press" | "wait", "buttons": [ボタン名, ...], "seconds": 数か null},
                    ...],
     "done": 真偽}
screen の選択肢 (enum) は ["none", *screen_names] (screen_names は呼び出し側が渡す見本の画面の名前の一覧。"none" は
見本のどの画面でもない)。type の選択肢は ["press", "wait"]、buttons の要素の選択肢は buttons.ALL_BUTTONS (HOME を含む
17 個。BUTTON_ORDER の順)。seconds は anyOf で {"type": "number"} と {"type": "null"} を並べる。operations の要素も、
すべて required で additionalProperties は false。
スキーマで表さない決まり (操作の数、press と wait ごとの buttons と seconds の範囲、十字キーの方向の数) は、
parse_system_screen_output が確かめる (スキーマの description にも書いて、モデルに伝える)。
要求の text.format には {"type": "json_schema", "name": OUTPUT_FORMAT_NAME,
"schema": build_system_screen_schema(screen_names), "strict": true} を入れる (build_system_screen_text_format)。
"""

import json
import math
from collections.abc import Mapping, Sequence

from switch2_remote.controller_state import DPAD_DIRECTIONS

from switch2_layers.buttons import ALL_BUTTONS
from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.middle.system_screen_types import (
    NO_SCREEN_NAME,
    SystemOperation,
    SystemScreenOutput,
)

OUTPUT_FORMAT_NAME = "system_screen_output"
# 1 回の出力の操作の最大の数
MAX_OPERATIONS = 10
# wait の秒数の上限 (秒)
MAX_WAIT_SECONDS = 10.0
# 操作の種類 (スキーマの type の選択肢。この順)
OPERATION_KINDS: tuple[str, ...] = ("press", "wait")

# 出力のキー (スキーマの properties と required と同じ順)
_OUTPUT_KEYS: tuple[str, ...] = ("observation", "operations", "done")
# operations の要素のキー (スキーマの items の properties と同じ順)
_OPERATION_KEYS: tuple[str, ...] = ("screen", "type", "buttons", "seconds")


def _screen_choices(screen_names: Sequence[str]) -> list[str]:
    """出力の screen の選択肢 ["none", *screen_names] を返す。

    screen_names に "none" があるか、同じ名前が 2 つ以上あれば ValueError。
    """
    names = list(screen_names)
    if NO_SCREEN_NAME in names:
        raise ValueError(f"screen names must not include {NO_SCREEN_NAME!r}: {names}")
    if len(set(names)) != len(names):
        raise ValueError(f"screen names must be unique: {names}")
    return [NO_SCREEN_NAME, *names]


def build_system_screen_schema(screen_names: Sequence[str]) -> dict[str, object]:
    """出力の JSON Schema (モジュールの docstring の形) を返す。screen の選択肢は ["none", *screen_names]。

    screen_names に "none" があるか、同じ名前が 2 つ以上あれば ValueError にする。
    """
    choices = _screen_choices(screen_names)
    return {
        "type": "object",
        "properties": {
            "observation": {
                "type": "string",
                "description": (
                    "今の画像の画面 (どの画面か) と、カーソル (選ばれている絵・アイコン・項目・ユーザー) の位置"
                ),
            },
            "operations": {
                "type": "array",
                "description": (
                    f"送る操作の列 (0〜{MAX_OPERATIONS} 個)。前から順に送る。A は先頭の操作にだけ置く。"
                    "送る操作が無ければ空の配列"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "screen": {
                            "type": "string",
                            "enum": choices,
                            "description": (
                                "この操作を送る前に映っているはずの画面の名前。先頭の操作は、入力の照合の結果の"
                                "画面の名前 (見本のどの画面でもなければ none)"
                            ),
                        },
                        "type": {
                            "type": "string",
                            "enum": list(OPERATION_KINDS),
                            "description": "press はボタンを押す、wait は何も押さずに待つ",
                        },
                        "buttons": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(ALL_BUTTONS)},
                            "description": (
                                "press で同時に押すボタン (1 個以上。十字キーは 1 方向まで)。wait では空の配列"
                            ),
                        },
                        "seconds": {
                            "anyOf": [{"type": "number"}, {"type": "null"}],
                            "description": (
                                "press では、null なら短押し、数なら押し続ける秒数 (0 より大きく "
                                f"{MAX_HOLD_SECONDS:g} 以下)。wait では待つ秒数 (0 より大きく "
                                f"{MAX_WAIT_SECONDS:g} 以下)"
                            ),
                        },
                    },
                    "required": list(_OPERATION_KEYS),
                    "additionalProperties": False,
                },
            },
            "done": {
                "type": "boolean",
                "description": "目的を果たしたら true (このときは operations を空にする)",
            },
        },
        "required": list(_OUTPUT_KEYS),
        "additionalProperties": False,
    }


def build_system_screen_text_format(screen_names: Sequence[str]) -> dict[str, object]:
    """要求の text.format の値 {"type": "json_schema", "name": OUTPUT_FORMAT_NAME,
    "schema": build_system_screen_schema(screen_names), "strict": True} を返す。

    screen_names の誤りは build_system_screen_schema と同じく ValueError にする。
    """
    return {
        "type": "json_schema",
        "name": OUTPUT_FORMAT_NAME,
        "schema": build_system_screen_schema(screen_names),
        "strict": True,
    }


def parse_system_screen_output(
    text: str, screen_names: Sequence[str]
) -> SystemScreenOutput:
    """出力の JSON の文 text を読み、決まりに合うかを確かめて SystemScreenOutput にする。

    次のときは ValueError にする (呼び出し側は、操作を 1 つも送らない):
        - JSON として読めない、JSON の object でない
        - キーが observation・operations・done と一致しない (足りない・余分なキーがある)
        - observation が文字列でない、done が真偽でない、operations が配列でない
        - operations の要素が MAX_OPERATIONS 個を超える (0 個は誤りでない)
        - operations の要素が object でない、要素のキーが screen・type・buttons・seconds と一致しない
        - screen が文字列でないか、"none" と screen_names のどれでもない (大文字と小文字を区別する)
        - type が "press" と "wait" のどちらでもない
        - buttons が配列でない、要素が文字列でないか buttons.ALL_BUTTONS の名前でない
        - press で、buttons が空、十字キーの方向 (UP・RIGHT・DOWN・LEFT) が異なる 2 方向以上ある (相反する方向も、隣り
          合う方向も誤り)、seconds が null でも「0 より大きく hardware.pad_output.MAX_HOLD_SECONDS 以下の有限の数」
          でもない
        - wait で、buttons が空でない、seconds が「0 より大きく MAX_WAIT_SECONDS 以下の有限の数」でない (null も誤り)
        - 数は bool を除く (true は数でない)。NaN と無限大は有限の数でない
        - screen_names に "none" があるか、同じ名前が 2 つ以上ある
    読めたら、operations を出力の順に並べた SystemOperation のタプルにする。SystemOperation の kind は type の値、
    buttons は書かれた順のタプル (同じ名前の重複も書かれたまま)、seconds は float (整数で書かれていても float) か None。
    observation と done は書かれたまま。
    """
    choices = frozenset(_screen_choices(screen_names))
    value = _json_object(text)
    _check_keys(value, _OUTPUT_KEYS, "system screen output")
    observation = _string(value["observation"], "'observation'")
    done = _boolean(value["done"], "'done'")
    items = _array(value["operations"], "'operations'")
    if len(items) > MAX_OPERATIONS:
        raise ValueError(
            f"'operations' must have at most {MAX_OPERATIONS} items: {len(items)}"
        )
    return SystemScreenOutput(
        observation=observation,
        operations=tuple(_operation(item, choices) for item in items),
        done=done,
    )


def system_operation_to_json(operation: SystemOperation) -> dict[str, object]:
    """operation を出力の operations の要素の形 {"screen", "type", "buttons": [...], "seconds"} (この順) にする。

    type は operation.kind、buttons は operation.buttons の順のリスト、seconds は operation.seconds (None は null)。
    """
    return {
        "screen": operation.screen,
        "type": operation.kind,
        "buttons": list(operation.buttons),
        "seconds": operation.seconds,
    }


def system_output_to_json(output: SystemScreenOutput) -> dict[str, object]:
    """output を出力の形 {"observation", "operations": [system_operation_to_json の値, ...], "done"} (この順) にする。

    parse_system_screen_output で読める形 (読み直すと output と等しい値になる) にする。
    """
    return {
        "observation": output.observation,
        "operations": [
            system_operation_to_json(operation) for operation in output.operations
        ],
        "done": output.done,
    }


# 以下の読み取りは、契約 (parse_system_screen_output) どおり型の誤りも ValueError にする。
# 型が合えば値を返し、合わなければ if の後で ValueError を出す。集合と比べる前に型を確かめる
# (リストや object の値で TypeError にしないため)


def _string(item: object, where: str) -> str:
    """item が文字列ならその値を返す。それ以外は ValueError。where は誤りの説明に入れる、値の名前。"""
    if isinstance(item, str):
        return item
    raise ValueError(f"{where} in system screen output must be a string: {item!r}")


def _boolean(item: object, where: str) -> bool:
    """item が真偽ならその値を返す。それ以外は ValueError。"""
    if isinstance(item, bool):
        return item
    raise ValueError(f"{where} in system screen output must be a boolean: {item!r}")


def _array(item: object, where: str) -> list[object]:
    """item が配列 (list) ならその値を返す。それ以外は ValueError。"""
    if isinstance(item, list):
        return item
    raise ValueError(f"{where} in system screen output must be an array: {item!r}")


def _element_object(item: object, where: str) -> dict[str, object]:
    """配列の要素 item が JSON の object ならその値を返す。それ以外は ValueError。"""
    if isinstance(item, dict):
        return item
    raise ValueError(f"{where} must be a JSON object: {item!r}")


def _json_object(text: str) -> dict[str, object]:
    """text を JSON として読み、object ならその値を返す。読めないか object でなければ ValueError。"""
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ValueError(f"system screen output is not JSON: {error}") from error
    if isinstance(value, dict):
        return value
    raise ValueError(f"system screen output must be a JSON object: {value!r}")


def _check_keys(
    value: Mapping[str, object], expected_keys: tuple[str, ...], where: str
) -> None:
    """value のキーが expected_keys と一致しなければ ValueError。where は誤りの説明に入れる、確かめるものの名前。"""
    expected = set(expected_keys)
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ValueError(
            f"{where} keys do not match the schema: missing {missing}, extra {extra}"
        )


def _operation(item: object, choices: frozenset[str]) -> SystemOperation:
    """operations の要素 item を SystemOperation にする。決まりに合わなければ ValueError。"""
    where = "an element of operations"
    value = _element_object(item, where)
    _check_keys(value, _OPERATION_KEYS, where)
    screen = value["screen"]
    if not isinstance(screen, str) or screen not in choices:
        raise ValueError(f"'screen' in {where} is not a choice: {screen!r}")
    buttons = _buttons(value["buttons"], where)
    kind = value["type"]
    seconds = value["seconds"]
    if kind == "press":
        if not buttons:
            raise ValueError(f"'buttons' of a press in {where} must not be empty")
        directions = set(buttons) & DPAD_DIRECTIONS
        if len(directions) >= 2:
            raise ValueError(
                f"a press in {where} must have at most one direction: {sorted(directions)}"
            )
        hold_seconds = (
            None
            if seconds is None
            else _seconds(seconds, MAX_HOLD_SECONDS, f"a press in {where}")
        )
        return SystemOperation(
            screen=screen, kind="press", buttons=buttons, seconds=hold_seconds
        )
    if kind == "wait":
        if buttons:
            raise ValueError(f"'buttons' of a wait in {where} must be empty: {buttons}")
        return SystemOperation(
            screen=screen,
            kind="wait",
            buttons=(),
            seconds=_seconds(seconds, MAX_WAIT_SECONDS, f"a wait in {where}"),
        )
    raise ValueError(f"'type' in {where} must be 'press' or 'wait': {kind!r}")


def _buttons(item: object, where: str) -> tuple[str, ...]:
    """buttons の値 item が、ALL_BUTTONS の名前の文字列の配列なら、書かれた順のタプルにする。それ以外は ValueError。"""
    names: list[str] = []
    for button in _array(item, f"'buttons' of {where}"):
        if not isinstance(button, str) or button not in ALL_BUTTONS:
            raise ValueError(f"'buttons' in {where} has an unknown button: {button!r}")
        names.append(button)
    return tuple(names)


def _seconds(item: object, maximum: float, where: str) -> float:
    """秒数の値 item が、0 より大きく maximum 以下の有限の数 (bool を除く) なら float にして返す。それ以外は ValueError。"""
    # 範囲を先に確かめる (float の範囲を超える int は、math.isfinite が OverflowError を出すため)
    if (
        isinstance(item, int | float)
        and not isinstance(item, bool)
        and 0 < item <= maximum
        and math.isfinite(item)
    ):
        return float(item)
    raise ValueError(
        f"'seconds' of {where} must be a finite number over 0 up to {maximum:g}: {item!r}"
    )
