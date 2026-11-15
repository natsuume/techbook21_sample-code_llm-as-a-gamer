"""gpt-6-luna の出力の JSON Schema (Structured Outputs) と、出力の JSON の文を MiddleOutput にする読み取り。I/O を扱わない。

出力の形 (すべて required。additionalProperties は false。キーの順に書かせる):
    {"scene_name": 文字列, "is_stuck": 真偽, "stuck_description": 文字列,
     "surface_wait_s": 数 (SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S), "timeline": 文字列, "tasks": 文字列}
scene_name が空 (空白だけを含む) なら、場面を決められなかったものとして MiddleOutput の scene_name を None にする。
tasks は Markdown の箇条書き。
要求の text.format には {"type": "json_schema", "name": OUTPUT_FORMAT_NAME, "schema": MIDDLE_OUTPUT_SCHEMA,
"strict": true} を入れる (build_text_format)。
"""

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


def build_text_format() -> dict[str, object]:
    """要求の text.format の値
    {"type": "json_schema", "name": OUTPUT_FORMAT_NAME, "schema": MIDDLE_OUTPUT_SCHEMA, "strict": True} を返す。
    """
    raise NotImplementedError


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
    raise NotImplementedError
