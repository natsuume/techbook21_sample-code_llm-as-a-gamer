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

from collections.abc import Sequence

from switch2_layers.middle.system_screen_types import (
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


def build_system_screen_schema(screen_names: Sequence[str]) -> dict[str, object]:
    """出力の JSON Schema (モジュールの docstring の形) を返す。screen の選択肢は ["none", *screen_names]。

    screen_names に "none" があるか、同じ名前が 2 つ以上あれば ValueError にする。
    """
    raise NotImplementedError


def build_system_screen_text_format(screen_names: Sequence[str]) -> dict[str, object]:
    """要求の text.format の値 {"type": "json_schema", "name": OUTPUT_FORMAT_NAME,
    "schema": build_system_screen_schema(screen_names), "strict": True} を返す。

    screen_names の誤りは build_system_screen_schema と同じく ValueError にする。
    """
    raise NotImplementedError


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
    raise NotImplementedError


def system_operation_to_json(operation: SystemOperation) -> dict[str, object]:
    """operation を出力の operations の要素の形 {"screen", "type", "buttons": [...], "seconds"} (この順) にする。

    type は operation.kind、buttons は operation.buttons の順のリスト、seconds は operation.seconds (None は null)。
    """
    raise NotImplementedError


def system_output_to_json(output: SystemScreenOutput) -> dict[str, object]:
    """output を出力の形 {"observation", "operations": [system_operation_to_json の値, ...], "done"} (この順) にする。

    parse_system_screen_output で読める形 (読み直すと output と等しい値になる) にする。
    """
    raise NotImplementedError
