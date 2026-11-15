"""gpt-6-luna の出力の JSON Schema (Structured Outputs) と、出力の JSON の文を MiddleOutput にする読み取り。I/O を扱わない。

出力の形 (すべて required。additionalProperties は false。キーの順に書かせる):
    {"scene_name": 文字列, "place_name": 文字列,
     "new_connections": [{"from": 文字列, "to": 文字列, "how": 文字列}, ...],
     "new_observations": [{"kind": "exit" | "landmark" | "blocked", "place": 文字列, "description": 文字列}, ...],
     "is_stuck": 真偽, "stuck_description": 文字列,
     "surface_wait_s": 数 (SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S), "timeline": 文字列, "tasks": 文字列}
new_connections と new_observations の要素も、すべて required で additionalProperties は false。
場所の項目 (place_name・new_connections・new_observations) は、短期タスクリスト (tasks) より前に書かせる。
scene_name が空 (空白だけを含む) なら、場面を決められなかったものとして MiddleOutput の scene_name を None にする。
place_name も同じく、空なら場所を決められなかった (ゲームの世界の中の場所が映っていない場合を含む) ものとして None にする。
tasks は Markdown の箇条書き。
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
from switch2_layers.notes.map_document import MapConnection
from switch2_layers.surface.middle_state_reader import (
    SURFACE_WAIT_MAX_S,
    SURFACE_WAIT_MIN_S,
)

OUTPUT_FORMAT_NAME = "middle_output"

MIDDLE_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "scene_name": {
            "type": "string",
            "description": (
                "現在の場面ラベル。場面ラベルの一覧に当てはまるものがあれば、その名前をそのまま書く。"
                "当てはまるものが無いときだけ新しい名前を作る。決められなければ空文字列"
            ),
        },
        "place_name": {
            "type": "string",
            "description": (
                "現在地の場所の名前。場所の名前の一覧に当てはまるものがあれば、その名前をそのまま書く。"
                "当てはまるものが無いときだけ新しい名前を作る。ゲームの世界の中の場所が映っていない"
                " (メニュー・タイトル・戦闘など) か、決められなければ空文字列"
            ),
        },
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
                },
                "required": ["from", "to", "how"],
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
                },
                "required": ["kind", "place", "description"],
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
        "tasks": {
            "type": "string",
            "description": "現在の場面の短期タスクリスト (Markdown の箇条書き)",
        },
    },
    "required": [
        "scene_name",
        "place_name",
        "new_connections",
        "new_observations",
        "is_stuck",
        "stuck_description",
        "surface_wait_s",
        "timeline",
        "tasks",
    ],
    "additionalProperties": False,
}
# 出力のキー。MIDDLE_OUTPUT_SCHEMA の properties (と required) と同じキーを同じ順に並べる
_OUTPUT_KEYS: tuple[str, ...] = (
    "scene_name",
    "place_name",
    "new_connections",
    "new_observations",
    "is_stuck",
    "stuck_description",
    "surface_wait_s",
    "timeline",
    "tasks",
)
# new_connections の要素のキー (スキーマの items の properties と同じ順)
_CONNECTION_KEYS: tuple[str, ...] = ("from", "to", "how")
# new_observations の要素のキー (スキーマの items の properties と同じ順)
_OBSERVATION_KEYS: tuple[str, ...] = ("kind", "place", "description")


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
        - scene_name・place_name・stuck_description・timeline・tasks が文字列でない、is_stuck が真偽でない、
          surface_wait_s が数 (bool を除く) でないか有限でない (NaN・Infinity を含む)
        - surface_wait_s が SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S (両端を含む) の外
        - new_connections・new_observations が配列でない。要素が object でない、要素のキーがスキーマと一致しない
          (足りない・余分なキーがある)、要素の値が文字列でない
        - new_observations の要素の kind が ObservationKind の値 (exit・landmark・blocked。大文字と小文字を区別する)
          でない
    scene_name と place_name は、空 (前後の空白を除くと空) なら None、それ以外は文字列のまま。new_connections と
    new_observations は、出力の順に並べた MapConnection と MapObservation のタプルにする (値は書かれたまま)。
    ほかの値はそのまま (surface_wait_s は float) にする。
    """
    value = _json_object(text)
    _check_keys(value)
    scene_name = _string(value, "scene_name")
    place_name = _string(value, "place_name")
    return MiddleOutput(
        scene_name=scene_name if scene_name.strip() else None,
        is_stuck=_boolean(value, "is_stuck"),
        stuck_description=_string(value, "stuck_description"),
        surface_wait_s=_surface_wait(value["surface_wait_s"]),
        timeline=_string(value, "timeline"),
        tasks=_string(value, "tasks"),
        place_name=place_name if place_name.strip() else None,
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
    )


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
