"""MiddleInput から gpt-6-luna の要求の JSON (Responses API の本文) を組み立てる。I/O を扱わない。

要求の形:
    {
      "model": LUNA_MODEL,
      "reasoning": {"effort": REASONING_EFFORT},
      "text": {"format": luna_output_schema.build_text_format()},
      "prompt_cache_options": {"mode": "explicit"},
      "input": [
        {"role": "developer", "content": [
          {"type": "input_text", "text": luna_instructions.LUNA_DEVELOPER_INSTRUCTIONS,
           "prompt_cache_breakpoint": {"mode": "explicit"}}]},
        {"role": "user", "content": [
          {"type": "input_text", "text": <節 1>}, ..., {"type": "input_text", "text": <節 10>},
          {"type": "input_text", "text": frame_label(フレーム 1 の t)},
          {"type": "input_image", "image_url": jpeg_data_url(フレーム 1 の JPEG)},
          ...]}
      ]
    }
reasoning の effort は省くと medium になるため、none を明示する。
developer メッセージは入力によらず同じにし、その最後の (唯一の) 内容に明示のキャッシュの区切りを置く。
prompt_cache_options の mode を explicit にして、自動の区切りを置かせない。区切りより後ろの毎回変わる user メッセージ
(画像を含む) はキャッシュに書かれず、書き込みの割増がかからない。user メッセージには区切りを置かない。
キャッシュの寿命 (ttl) は指定しない (既定の 30 分)。

user メッセージの節 (この順。各節は「## <USER_SECTION_TITLES の見出し>」の行で始まり、次の行から中身を書く):
    1. 現在の時刻                       「t=<middle_input.t>」(frames.surface_frames.format_frame_label の形)
    2. 場面ラベルの一覧                 describe_scene_labels(middle_input.scene_labels)
    3. 前回の middle の出力             describe_previous_state(middle_input.previous_state)
    4. 大方針                           middle_input.strategy の前後の空白を除いたもの。空なら EMPTY_TEXT
    5. 現在の場面の短期タスクリスト     middle_input.current_tasks の前後の空白を除いたもの。空なら EMPTY_TEXT
    6. 現在の場面の操作説明             middle_input.current_controls の前後の空白を除いたもの。空なら EMPTY_TEXT
                                        (unknown のボタンを確かめるタスクを作るために渡す)
    7. 前回以降の surface の記録        record_summary.summarize_surface_rows(middle_input.surface_rows)
    8. 前回以降の操作の記録             record_summary.summarize_operation_rows(middle_input.operation_rows)
    9. 場面転換の印                     scene_change_reported が真なら SCENE_CHANGE_REPORTED_TEXT、偽なら
                                        SCENE_CHANGE_NOT_REPORTED_TEXT
    10. フレーム                        「一定間隔で保存したフレーム <枚数> 枚 (古い順)。各画像の直前に時刻のラベルがある」。
                                        0 枚なら NO_FRAMES_TEXT
節の後ろに、middle_input.frames の順 (古い順) に、時刻のラベル (frame_label) と画像 (input_image) を 1 組ずつ置く。
画像の detail は指定しない (既定の auto)。
"""

import base64
from collections.abc import Sequence

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.middle.luna_instructions import LUNA_DEVELOPER_INSTRUCTIONS
from switch2_layers.middle.luna_output_schema import build_text_format
from switch2_layers.middle.middle_types import MiddleInput, MiddleState
from switch2_layers.middle.record_summary import (
    summarize_operation_rows,
    summarize_surface_rows,
)
from switch2_layers.notes.map_document import MapDocument
from switch2_layers.notes.scene_labels import SceneLabel

LUNA_MODEL = "gpt-6-luna"
REASONING_EFFORT = "none"
# user メッセージの節の見出し (この順に置く)
USER_SECTION_TITLES: tuple[str, ...] = (
    "現在の時刻",
    "場面ラベルの一覧",
    "前回の middle の出力",
    "大方針",
    "現在の場面の短期タスクリスト",
    "現在の場面の操作説明",
    "前回以降の surface の記録",
    "前回以降の操作の記録",
    "場面転換の印",
    "フレーム",
)
EMPTY_TEXT = "(無い)"
SCENE_CHANGE_REPORTED_TEXT = "surface が、前回の middle の実行以降に場面転換を検知した"
SCENE_CHANGE_NOT_REPORTED_TEXT = (
    "surface は、前回の middle の実行以降に場面転換を検知していない"
)
NO_FRAMES_TEXT = "フレームは無い"
# マップ (notes/map/map.json) が無い・読めない・形に合わないときに、マップの節に書く文字列
NO_MAP_TEXT = "マップなし"


def jpeg_data_url(jpeg: bytes) -> str:
    """JPEG のバイト列を `data:image/jpeg;base64,<base64>` の data URL にする。

    base64 は標準のアルファベットで、= のパディングを付け、改行を入れない (base64.b64encode)。
    """
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def frame_label(t: float) -> str:
    """画像の直前に置く時刻のラベル「フレーム t=<t>」を返す (t の部分は frames.surface_frames.format_frame_label(t))。"""
    return f"フレーム {format_frame_label(t)}"


def describe_scene_labels(labels: Sequence[SceneLabel]) -> str:
    """場面ラベルの一覧を、1 件 1 行「- <name> (<id>。初めて名前を付けた時刻 t=<first_seen_t>)」の文にする。

    t は format_frame_label の形。一覧が空なら EMPTY_TEXT を返す。
    """
    if not labels:
        return EMPTY_TEXT
    return "\n".join(
        f"- {label.name} ({label.id}。初めて名前を付けた時刻 "
        f"{format_frame_label(label.first_seen_t)})"
        for label in labels
    )


def describe_place_names(names: Sequence[str]) -> str:
    """場所の名前の一覧を、1 件 1 行「- <名前>」の文にする (names の順。行は改行 1 つでつなぎ、末尾に改行を付けない)。

    一覧が空なら EMPTY_TEXT を返す。
    """
    raise NotImplementedError


def describe_map_document(document: MapDocument | None) -> str:
    """マップの節の中身を返す。

    document が None (マップなし) なら NO_MAP_TEXT。そうでなければ、マップの全体
    notes.map_document.map_document_to_json(document) を json.dumps(..., ensure_ascii=False) で JSON にした文字列。
    """
    raise NotImplementedError


def describe_previous_state(state: MiddleState | None) -> str:
    """前回の middle の出力を、次の行の文にする。state が None なら EMPTY_TEXT を返す。

        作成時刻: t=<generated_t> (入力の時刻 t=<based_on_t>)
        場面: <scene_name> (<scene_id>)        場面が決まっていなければ「場面: (決まっていない)」
        止まっているか: はい | いいえ
        詰まり方: <stuck_description>          空なら EMPTY_TEXT
        surface の待ち時間: <surface_wait_s> 秒        surface_wait_s は書式 g (例: 0.4)
        系列情報:
        <timeline>                             空なら EMPTY_TEXT
    t は format_frame_label の形。
    """
    if state is None:
        return EMPTY_TEXT
    if state.scene_id is None or state.scene_name is None:
        scene = "(決まっていない)"
    else:
        scene = f"{state.scene_name} ({state.scene_id})"
    return "\n".join(
        [
            (
                f"作成時刻: {format_frame_label(state.generated_t)} "
                f"(入力の時刻 {format_frame_label(state.based_on_t)})"
            ),
            f"場面: {scene}",
            f"止まっているか: {'はい' if state.is_stuck else 'いいえ'}",
            f"詰まり方: {state.stuck_description or EMPTY_TEXT}",
            f"surface の待ち時間: {state.surface_wait_s:g} 秒",
            "系列情報:",
            state.timeline or EMPTY_TEXT,
        ]
    )


def build_user_sections(middle_input: MiddleInput) -> list[str]:
    """user メッセージの 10 の節の文を、モジュールの docstring の順と形で返す (画像の組は含まない)。"""
    frame_count = len(middle_input.frames)
    if frame_count:
        frames_text = (
            f"一定間隔で保存したフレーム {frame_count} 枚 (古い順)。"
            "各画像の直前に時刻のラベルがある"
        )
    else:
        frames_text = NO_FRAMES_TEXT
    bodies = [
        format_frame_label(middle_input.t),
        describe_scene_labels(middle_input.scene_labels),
        describe_previous_state(middle_input.previous_state),
        _stripped_or_empty_text(middle_input.strategy),
        _stripped_or_empty_text(middle_input.current_tasks),
        _stripped_or_empty_text(middle_input.current_controls),
        summarize_surface_rows(middle_input.surface_rows),
        summarize_operation_rows(middle_input.operation_rows),
        SCENE_CHANGE_REPORTED_TEXT
        if middle_input.scene_change_reported
        else SCENE_CHANGE_NOT_REPORTED_TEXT,
        frames_text,
    ]
    return [
        f"## {title}\n{body}"
        for title, body in zip(USER_SECTION_TITLES, bodies, strict=True)
    ]


def _stripped_or_empty_text(text: str) -> str:
    """text の前後の空白を除いたものを返す。空になれば EMPTY_TEXT を返す。"""
    return text.strip() or EMPTY_TEXT


def build_developer_message() -> dict[str, object]:
    """developer メッセージ {"role": "developer", "content": [{"type": "input_text",
    "text": LUNA_DEVELOPER_INSTRUCTIONS, "prompt_cache_breakpoint": {"mode": "explicit"}}]} を返す。
    """
    return {
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": LUNA_DEVELOPER_INSTRUCTIONS,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }


def build_user_message(middle_input: MiddleInput) -> dict[str, object]:
    """user メッセージ {"role": "user", "content": [...]} を返す。

    content は、build_user_sections の各節の input_text、続けてフレームごとに frame_label の input_text と
    jpeg_data_url の input_image (古い順)。prompt_cache_breakpoint は置かない。
    """
    content: list[dict[str, object]] = [
        {"type": "input_text", "text": text}
        for text in build_user_sections(middle_input)
    ]
    for frame in middle_input.frames:
        content.append({"type": "input_text", "text": frame_label(frame.t)})
        content.append({"type": "input_image", "image_url": jpeg_data_url(frame.jpeg)})
    return {"role": "user", "content": content}


def build_luna_payload(middle_input: MiddleInput) -> dict[str, object]:
    """middle_input から、要求の本文 (モジュールの docstring の形) を作って返す。"""
    return {
        "model": LUNA_MODEL,
        "reasoning": {"effort": REASONING_EFFORT},
        "text": {"format": build_text_format()},
        "prompt_cache_options": {"mode": "explicit"},
        "input": [build_developer_message(), build_user_message(middle_input)],
    }
