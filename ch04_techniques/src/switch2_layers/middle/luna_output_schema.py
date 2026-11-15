"""gpt-6-luna の出力の JSON Schema (Structured Outputs) と、出力の JSON の文を MiddleOutput にする読み取り。I/O を扱わない。

出力の形 (すべて required。additionalProperties は false。キーの順に書かせる):
    {"scene_name": 文字列, "is_stuck": 真偽, "stuck_description": 文字列,
     "surface_wait_s": 数 (SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S), "timeline": 文字列, "tasks": 文字列}
scene_name が空 (空白だけを含む) なら、場面を決められなかったものとして MiddleOutput の scene_name を None にする。
tasks は Markdown の箇条書き。
要求の text.format には {"type": "json_schema", "name": OUTPUT_FORMAT_NAME, "schema": MIDDLE_OUTPUT_SCHEMA,
"strict": true} を入れる (build_text_format)。
"""

import json
import math
from collections.abc import Mapping

from switch2_layers.middle.middle_types import MiddleOutput
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
    "is_stuck",
    "stuck_description",
    "surface_wait_s",
    "timeline",
    "tasks",
)


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
        - scene_name・stuck_description・timeline・tasks が文字列でない、is_stuck が真偽でない、
          surface_wait_s が数 (bool を除く) でないか有限でない (NaN・Infinity を含む)
        - surface_wait_s が SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S (両端を含む) の外
    scene_name が空 (前後の空白を除くと空) なら None、それ以外は文字列のまま。ほかの値はそのまま
    (surface_wait_s は float) にする。
    """
    value = _json_object(text)
    _check_keys(value)
    scene_name = _string(value, "scene_name")
    return MiddleOutput(
        scene_name=scene_name if scene_name.strip() else None,
        is_stuck=_boolean(value, "is_stuck"),
        stuck_description=_string(value, "stuck_description"),
        surface_wait_s=_surface_wait(value["surface_wait_s"]),
        timeline=_string(value, "timeline"),
        tasks=_string(value, "tasks"),
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


def _check_keys(value: Mapping[str, object]) -> None:
    """value のキーが出力のキー (_OUTPUT_KEYS。MIDDLE_OUTPUT_SCHEMA の properties と同じ) と一致しなければ ValueError。"""
    expected = set(_OUTPUT_KEYS)
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ValueError(
            f"middle output keys do not match the schema: missing {missing}, extra {extra}"
        )


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
