"""gpt-6-luna の出力の JSON Schema (Structured Outputs) と、出力の JSON の文を MiddleOutput にする読み取り。I/O を扱わない。

出力の形 (すべて required。additionalProperties は false。キーの順に書かせる):
    {"place_name": 文字列, "current_tile": 座標か null,
     "new_connections": [{"from": 文字列, "to": 文字列, "how": 文字列, "from_tile": 座標か null,
                          "to_tile": 座標か null}, ...],
     "new_observations": [{"kind": "exit" | "landmark" | "blocked", "place": 文字列, "description": 文字列,
                           "tile": 座標か null}, ...],
     "is_stuck": 真偽, "stuck_description": 文字列, "press_home": 真偽,
     "surface_wait_s": 数 (SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S), "timeline": 文字列,
     "scene_name": 文字列,
     "cancel_task_ids": [文字列, ...],
     "new_tasks": [{"text": 文字列, "destination": {"place": 文字列, "tile": 座標か null} か null}, ...]}
座標は {"x": 整数, "y": 整数} (入力のマップのその場所の座標。x は右に、y は下に増える)。座標か null の値は、
スキーマでは anyOf で座標の object と {"type": "null"} を並べる (_tile_or_null_schema)。行き先か null の値も同じく、
anyOf で行き先の object と {"type": "null"} を並べる。
new_connections・new_observations・new_tasks の要素と、座標と行き先の object も、すべて required で
additionalProperties は false。
場所の項目 (place_name・current_tile・new_connections・new_observations) は、タスクの項目 (cancel_task_ids・new_tasks)
より前に書かせる。scene_name は timeline の直後 (cancel_task_ids の前) に書かせる。画面の移り変わり (timeline の
「場面の移り変わり」の節) を書いてから場面を決めさせるため。scene_name の説明は、指示 (luna_instructions) の場面ラベルの
節と同じ 3 つの条件 (前回と同じ種類の画面なら前回の場面名、違う種類なら一覧から選ぶ、一覧に無ければ新しい名前を作る) にする。
new_tasks の数の上限 (3 個まで) はスキーマ (maxItems) に置かず、説明文に書く。
cancel_task_ids は取り消すタスクの ID (ID の形は確かめない)、new_tasks は現在の場面のキューの末尾に足すタスクで、
行き先は MiddleOutput に入れる前には直さない (マップと照らして直すのは middle.task_destination_fix)。
scene_name が空 (空白だけを含む) なら、場面を決められなかったものとして MiddleOutput の scene_name を None にする。
place_name も同じく、空なら場所を決められなかった (ゲームの世界の中の場所が映っていない場合を含む) ものとして None にする。
press_home は、HOME を押して本体の画面に出るか (ゲームが操作を受け付けないなど、本体の画面に出る必要があるときだけ真)。
詰まり (is_stuck・stuck_description) の直後に書かせる。
要求の text.format には {"type": "json_schema", "name": OUTPUT_FORMAT_NAME, "schema": MIDDLE_OUTPUT_SCHEMA,
"strict": true} を入れる (build_text_format)。
"""

import json
import math
from collections.abc import Mapping

from switch2_layers.middle.middle_types import (
    MapObservation,
    MiddleOutput,
    ObservationKind,
)
from switch2_layers.notes.map_document import MapConnection, TilePosition
from switch2_layers.surface.middle_state_reader import (
    SURFACE_WAIT_MAX_S,
    SURFACE_WAIT_MIN_S,
)
from switch2_layers.tasks.task_queue import NewTask, TaskDestination

OUTPUT_FORMAT_NAME = "middle_output"


def _tile_or_null_schema(description: str) -> dict[str, object]:
    """座標 {"x": 整数, "y": 整数} か null の値のスキーマを返す (MIDDLE_OUTPUT_SCHEMA の座標の項目に使う)。

    形: {"anyOf": [{"type": "object", "properties": {"x": {"type": "integer", ...}, "y": {"type": "integer", ...}},
    "required": ["x", "y"], "additionalProperties": False}, {"type": "null"}], "description": description}
    (x と y にも description を付ける)。Structured Outputs の strict では、すべてのキーを required にし、書かない値は
    null との union で表す。object と null の union は anyOf で書く。
    """
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "右に増える座標"},
                    "y": {"type": "integer", "description": "下に増える座標"},
                },
                "required": ["x", "y"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ],
        "description": description,
    }


# 座標の項目を書かせる条件 (current_tile・from_tile・to_tile・tile の説明の末尾に付ける)
_TILE_CONDITION = (
    "。入力のマップのその場所の座標 (origin と tiles) で書く。マップが grid で、その場所に tiles があるときだけ"
    "書き、それ以外 (マップなし・free・場所がマップに無い・tiles が空・決められない) は null"
)

MIDDLE_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "place_name": {
            "type": "string",
            "description": (
                "現在地の場所の名前。場所の名前の一覧に当てはまるものがあれば、その名前をそのまま書く。"
                "当てはまるものが無いときだけ新しい名前を作る。ゲームの世界の中の場所が映っていない"
                " (メニュー・タイトル・戦闘など) か、決められなければ空文字列"
            ),
        },
        "current_tile": _tile_or_null_schema(
            "最新のフレームの主人公がいる、現在地 (place_name) のマス" + _TILE_CONDITION
        ),
        "new_connections": {
            "type": "array",
            "description": "前回の実行より後に新しく分かった、場所から場所への移動。無ければ空の配列",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "移る前の場所の名前"},
                    "to": {"type": "string", "description": "移った後の場所の名前"},
                    "how": {
                        "type": "string",
                        "description": "移り方 (出た位置・方向・直前の操作)",
                    },
                    "from_tile": _tile_or_null_schema(
                        "移る前の場所 (from) で、出たマス" + _TILE_CONDITION
                    ),
                    "to_tile": _tile_or_null_schema(
                        "移った後の場所 (to) で、着いたマス" + _TILE_CONDITION
                    ),
                },
                "required": ["from", "to", "how", "from_tile", "to_tile"],
                "additionalProperties": False,
            },
        },
        "new_observations": {
            "type": "array",
            "description": (
                "新しく分かった、出口 (exit)・目印 (landmark)・通れなかった位置 (blocked)。無ければ空の配列"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in ObservationKind],
                        "description": "観察の種類",
                    },
                    "place": {"type": "string", "description": "観察した場所の名前"},
                    "description": {
                        "type": "string",
                        "description": "画面上の位置などの説明",
                    },
                    "tile": _tile_or_null_schema(
                        "観察した場所 (place) で、観察したマス" + _TILE_CONDITION
                    ),
                },
                "required": ["kind", "place", "description", "tile"],
                "additionalProperties": False,
            },
        },
        "is_stuck": {
            "type": "boolean",
            "description": "ゲームの進行が止まっている (詰まっている) なら true",
        },
        "stuck_description": {
            "type": "string",
            "description": "どのように詰まっているかの説明。詰まっていなければ空文字列",
        },
        "press_home": {
            "type": "boolean",
            "description": (
                "HOME を押して本体の画面に出るなら true。ゲームが操作を受け付けないなど、"
                "本体の画面に出る必要があるときだけ true にする"
            ),
        },
        "surface_wait_s": {
            "type": "number",
            "minimum": SURFACE_WAIT_MIN_S,
            "maximum": SURFACE_WAIT_MAX_S,
            "description": "surface が応答を受け取ってから次の要求を送るまでの待ち秒",
        },
        "timeline": {
            "type": "string",
            "description": "系列情報。決められた節に分けて書く",
        },
        "scene_name": {
            "type": "string",
            "description": (
                "現在の場面ラベル。最新のフレームが前回の場面と同じ種類の画面なら、前回の場面名をそのまま書く。"
                "違う種類の画面なら、場面ラベルの一覧から当てはまる名前を探して、そのまま書く。一覧に当てはまる"
                "名前が無ければ新しい名前を作る (一覧に無いことを理由に前回の場面名を保たない)。"
                "決められなければ空文字列"
            ),
        },
        "cancel_task_ids": {
            "type": "array",
            "description": (
                "取り消すタスクの ID (入力のタスクのキューの ID)。取り消さなければ空の配列"
            ),
            "items": {"type": "string", "description": "取り消すタスクの ID"},
        },
        "new_tasks": {
            "type": "array",
            "description": (
                "現在の場面のキューの末尾に足すタスク (3 個まで)。足さなければ空の配列"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "達成したときに画面で確かめられる状態を書いた 1 文",
                    },
                    "destination": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "place": {
                                        "type": "string",
                                        "description": "行き先の場所の名前 (入力のマップの場所の名前)",
                                    },
                                    "tile": _tile_or_null_schema(
                                        "行き先の場所の中のマス。入力のマップと経路の節に載っている出口・"
                                        "つながりの出たマス・目印から選ぶ。場所だけが分かるときは null"
                                    ),
                                },
                                "required": ["place", "tile"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ],
                        "description": "行き先。場所に関わらないタスクは null",
                    },
                },
                "required": ["text", "destination"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "place_name",
        "current_tile",
        "new_connections",
        "new_observations",
        "is_stuck",
        "stuck_description",
        "press_home",
        "surface_wait_s",
        "timeline",
        "scene_name",
        "cancel_task_ids",
        "new_tasks",
    ],
    "additionalProperties": False,
}
# 出力のキー。MIDDLE_OUTPUT_SCHEMA の properties (と required) と同じキーを同じ順に並べる
_OUTPUT_KEYS: tuple[str, ...] = (
    "place_name",
    "current_tile",
    "new_connections",
    "new_observations",
    "is_stuck",
    "stuck_description",
    "press_home",
    "surface_wait_s",
    "timeline",
    "scene_name",
    "cancel_task_ids",
    "new_tasks",
)
# new_connections の要素のキー (スキーマの items の properties と同じ順)
_CONNECTION_KEYS: tuple[str, ...] = ("from", "to", "how", "from_tile", "to_tile")
# new_observations の要素のキー (スキーマの items の properties と同じ順)
_OBSERVATION_KEYS: tuple[str, ...] = ("kind", "place", "description", "tile")
# 座標の object のキー (_tile_or_null_schema の object の properties と同じ順)
_TILE_KEYS: tuple[str, ...] = ("x", "y")
# new_tasks の要素のキー (スキーマの items の properties と同じ順)
_NEW_TASK_KEYS: tuple[str, ...] = ("text", "destination")
# 行き先の object のキー (スキーマの destination の object の properties と同じ順)
_DESTINATION_KEYS: tuple[str, ...] = ("place", "tile")


def build_text_format() -> dict[str, object]:
    """要求の text.format の値
    {"type": "json_schema", "name": OUTPUT_FORMAT_NAME, "schema": MIDDLE_OUTPUT_SCHEMA, "strict": True} を返す。
    """
    return {
        "type": "json_schema",
        "name": OUTPUT_FORMAT_NAME,
        "schema": MIDDLE_OUTPUT_SCHEMA,
        "strict": True,
    }


def parse_middle_output(text: str) -> MiddleOutput:
    """出力の JSON の文 text を読み、MIDDLE_OUTPUT_SCHEMA に合うかを確かめて MiddleOutput にする。

    次のときは ValueError にする (スキーマに合わない出力):
        - JSON として読めない、JSON の object でない
        - キーが MIDDLE_OUTPUT_SCHEMA の properties と一致しない (足りない・余分なキーがある)
        - scene_name・place_name・stuck_description・timeline が文字列でない、is_stuck・press_home が真偽でない、
          surface_wait_s が数 (bool を除く) でないか有限でない (NaN・Infinity を含む)
        - surface_wait_s が SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S (両端を含む) の外
        - new_connections・new_observations が配列でない。要素が object でない、要素のキーがスキーマと一致しない
          (足りない・余分なキーがある)、要素の from・to・how・place・description が文字列でない
        - new_observations の要素の kind が ObservationKind の値 (exit・landmark・blocked。大文字と小文字を区別する)
          でない
        - current_tile と、new_connections の要素の from_tile・to_tile、new_observations の要素の tile が、null でも
          座標の object でもない。座標の object のキーが x と y に一致しない (足りない・余分なキーがある)、x か y が
          整数でない (bool と小数 (1.0 を含む) は整数でない)
        - cancel_task_ids が配列でない、要素が文字列でない (ID の形は確かめない)
        - new_tasks が配列でない。要素が object でない、要素のキーが text と destination に一致しない、text が文字列で
          ない、destination が null でも行き先の object でもない。行き先の object のキーが place と tile に一致しない、
          place が文字列でない、tile が null でも座標の object でもない (座標の object は上と同じ規則)
    scene_name と place_name は、空 (前後の空白を除くと空) なら None、それ以外は文字列のまま。new_connections と
    new_observations は、出力の順に並べた MapConnection と MapObservation のタプルにする (値は書かれたまま)。
    cancel_task_ids は出力の順の文字列のタプル、new_tasks は出力の順の tasks.task_queue.NewTask のタプル (text と
    place は書かれたまま。行き先は tasks.task_queue.TaskDestination、null は None) にする。
    座標は notes.map_document.TilePosition、null は None にする (current_tile は place_name が None でもそのまま)。
    ほかの値はそのまま (surface_wait_s は float、press_home は MiddleOutput.press_home) にする。
    """
    value = _json_object(text)
    _check_keys(value)
    scene_name = _string(value, "scene_name")
    place_name = _string(value, "place_name")
    return MiddleOutput(
        scene_name=scene_name if scene_name.strip() else None,
        is_stuck=_boolean(value, "is_stuck"),
        stuck_description=_string(value, "stuck_description"),
        press_home=_boolean(value, "press_home"),
        surface_wait_s=_surface_wait(value["surface_wait_s"]),
        timeline=_string(value, "timeline"),
        new_tasks=tuple(_new_task(item) for item in _array(value, "new_tasks")),
        cancel_task_ids=tuple(
            _cancel_task_id(item) for item in _array(value, "cancel_task_ids")
        ),
        place_name=place_name if place_name.strip() else None,
        current_tile=_tile(value["current_tile"], "'current_tile' in middle output"),
        new_connections=tuple(
            _connection(item) for item in _array(value, "new_connections")
        ),
        new_observations=tuple(
            _observation(item) for item in _array(value, "new_observations")
        ),
    )


# 以下の読み取りは、契約 (parse_middle_output) どおり型の誤りも ValueError にする。
# 型が合えば値を返し、合わなければ if の後で ValueError を出す


def _json_object(text: str) -> dict[str, object]:
    """text を JSON として読み、object ならその値を返す。読めないか object でなければ ValueError。"""
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ValueError(f"middle output is not JSON: {error}") from error
    if isinstance(value, dict):
        return value
    raise ValueError(f"middle output must be a JSON object: {value!r}")


def _check_keys(
    value: Mapping[str, object],
    expected_keys: tuple[str, ...] = _OUTPUT_KEYS,
    where: str = "middle output",
) -> None:
    """value のキーが expected_keys (既定は出力のキー _OUTPUT_KEYS。MIDDLE_OUTPUT_SCHEMA の properties と同じ) と
    一致しなければ ValueError。where は誤りの説明に入れる、確かめるものの名前。
    """
    expected = set(expected_keys)
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ValueError(
            f"{where} keys do not match the schema: missing {missing}, extra {extra}"
        )


def _array(value: Mapping[str, object], key: str) -> list[object]:
    """value[key] が配列 (list) ならその値を返す。それ以外は ValueError。"""
    item = value[key]
    if isinstance(item, list):
        return item
    raise ValueError(f"{key!r} in middle output must be an array: {item!r}")


def _element_object(item: object, where: str) -> dict[str, object]:
    """配列の要素 item が JSON の object ならその値を返す。それ以外は ValueError。where は誤りの説明に入れる。"""
    if isinstance(item, dict):
        return item
    raise ValueError(f"{where} must be a JSON object: {item!r}")


def _connection(item: object) -> MapConnection:
    """new_connections の要素 item を MapConnection にする。スキーマに合わなければ ValueError。"""
    where = "an element of new_connections"
    value = _element_object(item, where)
    _check_keys(value, _CONNECTION_KEYS, where)
    return MapConnection(
        from_place=_string(value, "from"),
        to_place=_string(value, "to"),
        how=_string(value, "how"),
        from_tile=_tile(value["from_tile"], f"'from_tile' in {where}"),
        to_tile=_tile(value["to_tile"], f"'to_tile' in {where}"),
    )


def _observation(item: object) -> MapObservation:
    """new_observations の要素 item を MapObservation にする。スキーマに合わなければ ValueError。"""
    where = "an element of new_observations"
    value = _element_object(item, where)
    _check_keys(value, _OBSERVATION_KEYS, where)
    return MapObservation(
        kind=_observation_kind(value["kind"]),
        place=_string(value, "place"),
        description=_string(value, "description"),
        tile=_tile(value["tile"], f"'tile' in {where}"),
    )


def _new_task(item: object) -> NewTask:
    """new_tasks の要素 item を NewTask にする。スキーマに合わなければ ValueError (parse_middle_output の規則)。"""
    where = "an element of new_tasks"
    value = _element_object(item, where)
    _check_keys(value, _NEW_TASK_KEYS, where)
    return NewTask(
        text=_string(value, "text"),
        destination=_destination(value["destination"]),
    )


def _destination(item: object) -> TaskDestination | None:
    """new_tasks の要素の destination の値 item が null なら None、行き先の object なら TaskDestination にする。

    object のキーが place と tile に一致しない、place が文字列でない、tile が null でも座標の object でもないとき、
    null でも object でもないときは ValueError。
    """
    if item is None:
        return None
    where = "'destination' in new_tasks"
    value = _element_object(item, where)
    _check_keys(value, _DESTINATION_KEYS, where)
    return TaskDestination(
        place=_string(value, "place"),
        tile=_tile(value["tile"], f"'tile' of {where}"),
    )


def _cancel_task_id(item: object) -> str:
    """cancel_task_ids の要素 item が文字列ならその値を返す。それ以外は ValueError (ID の形は確かめない)。"""
    if isinstance(item, str):
        return item
    raise ValueError(f"an element of cancel_task_ids must be a string: {item!r}")


def _tile(item: object, where: str) -> TilePosition | None:
    """座標の値 item が null なら None、{"x": 整数, "y": 整数} の object なら TilePosition にする。

    object のキーが x と y に一致しない (足りない・余分なキーがある) か、x か y が整数でない (bool と小数は整数でない)
    とき、null でも object でもないときは ValueError。where は誤りの説明に入れる、確かめる値の名前。
    """
    if item is None:
        return None
    if isinstance(item, dict):
        _check_keys(item, _TILE_KEYS, where)
        return TilePosition(
            x=_tile_integer(item, "x", where), y=_tile_integer(item, "y", where)
        )
    raise ValueError(f"{where} must be a JSON object or null: {item!r}")


def _tile_integer(value: Mapping[str, object], key: str, where: str) -> int:
    """座標の object value の value[key] が整数 (bool を除く) ならその値を返す。それ以外は ValueError。

    JSON の 1.0 は json.loads で float になるため、整数としない。
    """
    item = value[key]
    if isinstance(item, int) and not isinstance(item, bool):
        return item
    raise ValueError(f"{key!r} of {where} must be an integer: {item!r}")


def _observation_kind(item: object) -> ObservationKind:
    """観察の kind の値 item が ObservationKind の値の文字列なら、その ObservationKind を返す。それ以外は ValueError。"""
    if isinstance(item, str):
        # ObservationKind の値でなければ、ObservationKind(item) が ValueError を出す (大文字と小文字を区別する)
        return ObservationKind(item)
    raise ValueError(f"'kind' in new_observations must be a string: {item!r}")


def _string(value: Mapping[str, object], key: str) -> str:
    """value[key] が文字列ならその値を返す。それ以外は ValueError。"""
    item = value[key]
    if isinstance(item, str):
        return item
    raise ValueError(f"{key!r} in middle output must be a string: {item!r}")


def _boolean(value: Mapping[str, object], key: str) -> bool:
    """value[key] が真偽ならその値を返す。それ以外は ValueError。"""
    item = value[key]
    if isinstance(item, bool):
        return item
    raise ValueError(f"{key!r} in middle output must be a boolean: {item!r}")


def _surface_wait(item: object) -> float:
    """surface_wait_s の値 item が有限の数 (bool を除く) で範囲の中なら float にして返す。それ以外は ValueError。"""
    # 範囲を先に確かめる (float の範囲を超える int は、math.isfinite が OverflowError を出すため)
    if (
        isinstance(item, int | float)
        and not isinstance(item, bool)
        and SURFACE_WAIT_MIN_S <= item <= SURFACE_WAIT_MAX_S
        and math.isfinite(item)
    ):
        return float(item)
    raise ValueError(
        "'surface_wait_s' in middle output must be a finite number from "
        f"{SURFACE_WAIT_MIN_S} to {SURFACE_WAIT_MAX_S}: {item!r}"
    )
