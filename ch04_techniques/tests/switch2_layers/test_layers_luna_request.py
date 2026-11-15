import base64
import dataclasses
import json

from switch2_layers.middle.luna_instructions import (
    LUNA_DEVELOPER_INSTRUCTIONS,
    TIMELINE_SECTION_TITLES,
)
from switch2_layers.middle.luna_output_schema import MIDDLE_OUTPUT_SCHEMA
from switch2_layers.middle.luna_request import (
    USER_SECTION_TITLES,
    build_developer_message,
    build_luna_payload,
    build_user_message,
    build_user_sections,
    describe_previous_state,
    describe_scene_labels,
    frame_label,
    jpeg_data_url,
)
from switch2_layers.middle.middle_types import MiddleFrame, MiddleInput, MiddleState
from switch2_layers.middle.record_summary import (
    summarize_operation_rows,
    summarize_surface_rows,
)
from switch2_layers.notes.scene_labels import SceneLabel

JPEG_1 = b"\xff\xd8\xff\xe0frame-1"
JPEG_2 = b"\xff\xd8\xff\xe0frame-2"


def lines_of(lines: list[str]) -> str:
    """行を改行 1 つでつないだ文 (末尾に改行を付けない) を返す。"""
    return "\n".join(lines)


PREVIOUS_STATE = MiddleState(
    generated_t=10.6,
    based_on_t=10.0,
    scene_id="scene-001",
    scene_name="町",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.4,
    timeline="## 操作のまとめ\n- 上を押した",
    surface_rows_offset=120,
    operations_rows_offset=40,
)
SURFACE_ROWS = (
    {"t_received": 12.0, "scene_changed": True, "error": None},
    {"t_received": 13.0, "error": "RuntimeError: model failed"},
)
OPERATION_ROWS = (
    {
        "t": 12.5,
        "source": "apply",
        "held": ["A"],
        "taps": [],
        "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
        "ok": True,
        "error": None,
    },
)
FULL_INPUT = MiddleInput(
    t=15.25,
    frames=(
        MiddleFrame(t=6.0, file="frames/interval/000006000.jpg", jpeg=JPEG_1),
        MiddleFrame(t=15.2, file="frames/interval/000015200.jpg", jpeg=JPEG_2),
    ),
    surface_rows=SURFACE_ROWS,
    operation_rows=OPERATION_ROWS,
    scene_change_reported=True,
    scene_labels=(
        SceneLabel("scene-001", "町", 3.0),
        SceneLabel("scene-002", "戦闘", 9.5),
    ),
    previous_state=PREVIOUS_STATE,
    strategy="\n# 大方針\n\n北の城へ向かう\n\n",
    current_tasks="- 宿屋に入る\n",
    surface_rows_offset=300,
    operations_rows_offset=90,
    current_controls="\n# 操作説明 (共通)\n\n- A: unknown\n- B: ジャンプ\n",
)
EMPTY_INPUT = MiddleInput(
    t=0.0,
    frames=(),
    surface_rows=(),
    operation_rows=(),
    scene_change_reported=False,
    scene_labels=(),
    previous_state=None,
    strategy="  \n",
    current_tasks="",
    surface_rows_offset=0,
    operations_rows_offset=0,
    current_controls=" \n",
)


# --- 要求の全体 ---


def test_payload_top_level_fields():
    payload = build_luna_payload(FULL_INPUT)
    assert set(payload) == {
        "model",
        "reasoning",
        "text",
        "prompt_cache_options",
        "input",
    }
    assert payload["model"] == "gpt-6-luna"
    # effort を省くと medium になるため、none を明示する
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["text"] == {
        "format": {
            "type": "json_schema",
            "name": "middle_output",
            "schema": MIDDLE_OUTPUT_SCHEMA,
            "strict": True,
        }
    }
    # 自動の区切りを置かず、明示の区切りだけをキャッシュに使う
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert [message["role"] for message in payload["input"]] == ["developer", "user"]
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_developer_message_has_fixed_instructions_and_cache_breakpoint():
    expected = {
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": LUNA_DEVELOPER_INSTRUCTIONS,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }
    assert build_developer_message() == expected
    assert build_luna_payload(FULL_INPUT)["input"][0] == expected


def test_developer_message_and_schema_do_not_depend_on_input():
    full = build_luna_payload(FULL_INPUT)
    empty = build_luna_payload(EMPTY_INPUT)
    assert json.dumps(full["input"][0], ensure_ascii=False) == json.dumps(
        empty["input"][0], ensure_ascii=False
    )
    assert json.dumps(full["text"], ensure_ascii=False) == json.dumps(
        empty["text"], ensure_ascii=False
    )
    # 入力の値は developer メッセージに入らない
    assert "北の城" not in LUNA_DEVELOPER_INSTRUCTIONS
    assert "t=15.250" not in LUNA_DEVELOPER_INSTRUCTIONS


def test_user_message_has_no_cache_breakpoint():
    user = build_luna_payload(FULL_INPUT)["input"][1]
    assert user["role"] == "user"
    for part in user["content"]:
        assert "prompt_cache_breakpoint" not in part


# --- user メッセージ ---


def test_user_content_is_sections_then_labeled_images():
    user = build_user_message(FULL_INPUT)
    assert user == build_luna_payload(FULL_INPUT)["input"][1]
    content = user["content"]
    sections = build_user_sections(FULL_INPUT)
    assert len(sections) == 10
    assert content[:10] == [{"type": "input_text", "text": text} for text in sections]
    assert content[10:] == [
        {"type": "input_text", "text": "フレーム t=6.000"},
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,"
            + base64.b64encode(JPEG_1).decode("ascii"),
        },
        {"type": "input_text", "text": "フレーム t=15.200"},
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,"
            + base64.b64encode(JPEG_2).decode("ascii"),
        },
    ]


def test_four_frames_are_sent_in_order():
    frames = tuple(
        MiddleFrame(t=float(i), file=f"frames/interval/{i:09d}.jpg", jpeg=bytes([i]))
        for i in range(1, 5)
    )
    content = build_user_message(dataclasses.replace(FULL_INPUT, frames=frames))[
        "content"
    ]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == 4
    assert [
        base64.b64decode(part["image_url"].split(",", 1)[1]) for part in images
    ] == [bytes([1]), bytes([2]), bytes([3]), bytes([4])]
    # 各画像の直前に時刻のラベル
    for index, part in enumerate(content):
        if part["type"] == "input_image":
            assert content[index - 1]["text"].startswith("フレーム t=")


def test_section_titles_and_order():
    assert USER_SECTION_TITLES == (
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
    sections = build_user_sections(FULL_INPUT)
    for title, text in zip(USER_SECTION_TITLES, sections, strict=True):
        assert text.startswith(f"## {title}\n")


def section_body(sections: list[str], title: str) -> str:
    """見出しの行を除いた節の中身を返す。"""
    text = sections[USER_SECTION_TITLES.index(title)]
    return text.split("\n", 1)[1]


def test_section_contents_of_full_input():
    sections = build_user_sections(FULL_INPUT)
    assert section_body(sections, "現在の時刻") == "t=15.250"
    assert section_body(sections, "場面ラベルの一覧") == describe_scene_labels(
        FULL_INPUT.scene_labels
    )
    assert section_body(sections, "前回の middle の出力") == describe_previous_state(
        PREVIOUS_STATE
    )
    # 前後の空白を除く
    assert section_body(sections, "大方針") == "# 大方針\n\n北の城へ向かう"
    assert section_body(sections, "現在の場面の短期タスクリスト") == "- 宿屋に入る"
    # unknown のボタンを確かめるタスクを作るために、操作説明を渡す (前後の空白を除く)
    assert section_body(sections, "現在の場面の操作説明") == (
        "# 操作説明 (共通)\n\n- A: unknown\n- B: ジャンプ"
    )
    assert section_body(sections, "前回以降の surface の記録") == (
        summarize_surface_rows(SURFACE_ROWS)
    )
    assert section_body(sections, "前回以降の操作の記録") == (
        summarize_operation_rows(OPERATION_ROWS)
    )
    assert section_body(sections, "場面転換の印") == (
        "surface が、前回の middle の実行以降に場面転換を検知した"
    )
    assert section_body(sections, "フレーム") == (
        "一定間隔で保存したフレーム 2 枚 (古い順)。各画像の直前に時刻のラベルがある"
    )


def test_section_contents_of_empty_input():
    sections = build_user_sections(EMPTY_INPUT)
    assert section_body(sections, "現在の時刻") == "t=0.000"
    assert section_body(sections, "場面ラベルの一覧") == "(無い)"
    assert section_body(sections, "前回の middle の出力") == "(無い)"
    assert section_body(sections, "大方針") == "(無い)"
    assert section_body(sections, "現在の場面の短期タスクリスト") == "(無い)"
    assert section_body(sections, "現在の場面の操作説明") == "(無い)"
    assert section_body(sections, "前回以降の surface の記録") == "(記録なし)"
    assert section_body(sections, "前回以降の操作の記録") == "(記録なし)"
    assert section_body(sections, "場面転換の印") == (
        "surface は、前回の middle の実行以降に場面転換を検知していない"
    )
    assert section_body(sections, "フレーム") == "フレームは無い"
    content = build_user_message(EMPTY_INPUT)["content"]
    assert len(content) == 10
    assert all(part["type"] == "input_text" for part in content)


# --- 部品 ---


def test_jpeg_data_url():
    assert jpeg_data_url(b"\xff\xd8abc") == "data:image/jpeg;base64,/9hhYmM="


def test_frame_label_uses_milliseconds():
    assert frame_label(12.3456) == "フレーム t=12.346"
    assert frame_label(3.0) == "フレーム t=3.000"


def test_describe_scene_labels():
    labels = (SceneLabel("scene-001", "町", 3.0), SceneLabel("scene-002", "戦闘", 9.5))
    assert describe_scene_labels(labels) == lines_of(
        [
            "- 町 (scene-001。初めて名前を付けた時刻 t=3.000)",
            "- 戦闘 (scene-002。初めて名前を付けた時刻 t=9.500)",
        ]
    )
    assert describe_scene_labels(()) == "(無い)"


def test_describe_previous_state():
    assert describe_previous_state(PREVIOUS_STATE) == lines_of(
        [
            "作成時刻: t=10.600 (入力の時刻 t=10.000)",
            "場面: 町 (scene-001)",
            "止まっているか: いいえ",
            "詰まり方: (無い)",
            "surface の待ち時間: 0.4 秒",
            "系列情報:",
            "## 操作のまとめ",
            "- 上を押した",
        ]
    )


def test_describe_previous_state_without_scene_and_timeline():
    state = dataclasses.replace(
        PREVIOUS_STATE,
        scene_id=None,
        scene_name=None,
        is_stuck=True,
        stuck_description="壁に向かって歩き続けている",
        surface_wait_s=1.25,
        timeline="",
    )
    assert describe_previous_state(state) == lines_of(
        [
            "作成時刻: t=10.600 (入力の時刻 t=10.000)",
            "場面: (決まっていない)",
            "止まっているか: はい",
            "詰まり方: 壁に向かって歩き続けている",
            "surface の待ち時間: 1.25 秒",
            "系列情報:",
            "(無い)",
        ]
    )
    assert describe_previous_state(None) == "(無い)"


# --- 変わらない指示 ---


def test_instructions_cover_every_output_field_and_timeline_section():
    for key in MIDDLE_OUTPUT_SCHEMA["properties"]:
        assert key in LUNA_DEVELOPER_INSTRUCTIONS
    assert TIMELINE_SECTION_TITLES == (
        "操作のまとめ",
        "画面の変化",
        "場面の移り変わり",
        "詰まり方",
    )
    for title in TIMELINE_SECTION_TITLES:
        assert f"## {title}" in LUNA_DEVELOPER_INSTRUCTIONS
    # 待ち時間の範囲
    assert "0.2〜2.0" in LUNA_DEVELOPER_INSTRUCTIONS
    # 操作説明の unknown のボタンと、画面に出た複合ボタンの操作を確かめるタスクを優先させる
    assert "unknown" in LUNA_DEVELOPER_INSTRUCTIONS
    assert "複合ボタン" in LUNA_DEVELOPER_INSTRUCTIONS
    # user メッセージの節の見出しをすべて説明する
    for title in USER_SECTION_TITLES:
        assert title in LUNA_DEVELOPER_INSTRUCTIONS
