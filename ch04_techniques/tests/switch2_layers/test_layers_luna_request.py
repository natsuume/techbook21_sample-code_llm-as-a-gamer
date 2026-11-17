import base64
import dataclasses
import json

import pytest

from switch2_layers.middle.luna_instructions import (
    LUNA_DEVELOPER_INSTRUCTIONS,
    TIMELINE_SECTION_TITLES,
)
from switch2_layers.middle.luna_output_schema import MIDDLE_OUTPUT_SCHEMA
from switch2_layers.middle.luna_request import (
    NO_MAP_TEXT,
    USER_SECTION_TITLES,
    build_developer_message,
    build_luna_payload,
    build_user_message,
    build_user_sections,
    describe_map_document,
    describe_place_names,
    describe_previous_state,
    describe_progress,
    describe_scene_labels,
    frame_label,
    jpeg_data_url,
)
from switch2_layers.middle.middle_types import MiddleFrame, MiddleInput, MiddleState
from switch2_layers.middle.record_summary import (
    summarize_operation_rows,
    summarize_surface_rows,
)
from switch2_layers.middle.route_section import (
    ROUTE_NO_MAP_TEXT,
    ROUTE_SECTION_TITLE,
    describe_route_section,
)
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    MapDocument,
    MapPlace,
    TileMark,
    TilePosition,
    map_document_to_json,
)
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import NO_PROGRESS_TEXT
from switch2_layers.safety.safety_rules import SAFETY_RULES
from switch2_layers.tasks.task_queue import TaskDestination, TaskView

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
    tasks_rows_offset=50,
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
MAP_DOCUMENT = MapDocument(
    places=(
        MapPlace(
            name="家の 1 階",
            aliases=("1 階の部屋",),
            description="自分の部屋から階段で下りた先の部屋",
            exits=("左下の赤いマット (未確認)",),
            blocked=("下側の壁",),
            state="訪問済み",
            changes_on_entry=False,
        ),
    ),
    connections=(
        MapConnection(
            from_place="自分の部屋 (2 階)", to_place="家の 1 階", how="右上の階段"
        ),
    ),
)
MAP_JSON = {
    "layout": "free",
    "places": [
        {
            "name": "家の 1 階",
            "aliases": ["1 階の部屋"],
            "description": "自分の部屋から階段で下りた先の部屋",
            "exits": ["左下の赤いマット (未確認)"],
            "blocked": ["下側の壁"],
            "state": "訪問済み",
            "changes_on_entry": False,
        }
    ],
    "connections": [
        {"from": "自分の部屋 (2 階)", "to": "家の 1 階", "how": "右上の階段"}
    ],
}
PLACE_NAMES = ("家の 1 階", "1 階の部屋", "自分の部屋 (2 階)")
PROGRESS = ProgressState(t=15.2, static_s=12.4, no_novel_s=85.0, window_s=300.0)
# 現在の場面のキュー (行き先がマスまで・場所だけ・無い、取り消しの要求がある・無い)
TASK_QUEUE = (
    TaskView(
        id="task-003",
        text="宿屋に入っている",
        scene_id="scene-001",
        added_t=1.0,
        active_s=12.5,
        cancel_requested_t=14.0,
        destination=TaskDestination(place="町", tile=TilePosition(3, 4)),
    ),
    TaskView(
        id="task-004",
        text="店の前にいる",
        scene_id="scene-001",
        added_t=2.0,
        active_s=3.0,
        cancel_requested_t=None,
        destination=TaskDestination(place="町", tile=None),
    ),
    TaskView(
        id="task-005",
        text="会話の枠が閉じている",
        scene_id="scene-001",
        added_t=3.0,
        active_s=0.25,
        cancel_requested_t=None,
        destination=None,
    ),
)
# 前回以降のタスクの出来事 (tasks.jsonl の行。added の行は入力に入らない)
TASK_EVENTS = (
    {
        "t": 12.345,
        "event": "done",
        "scene_id": "scene-001",
        "id": "task-002",
        "text": "宿屋の前にいる",
        "reason": None,
        "active_s": 8.0,
        "destination": None,
    },
    {
        "t": 13.0,
        "event": "dropped",
        "scene_id": "scene-001",
        "id": None,
        "text": "宿屋に入っている",
        "reason": "duplicate",
        "active_s": None,
        "destination": None,
    },
    {
        "t": 14.0,
        "event": "cancel_requested",
        "scene_id": "scene-001",
        "id": "task-003",
        "text": "宿屋に入っている",
        "reason": None,
        "active_s": 11.0,
        "destination": None,
    },
    {
        "t": 14.5,
        "event": "canceled",
        "scene_id": "scene-001",
        "id": "task-001",
        "text": "町の北の出口にいる",
        "reason": None,
        "active_s": 30.5,
        "destination": None,
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
    task_queue=TASK_QUEUE,
    task_events=TASK_EVENTS,
    surface_rows_offset=300,
    operations_rows_offset=90,
    tasks_rows_offset=700,
    reasons=("cancel_requested", "stuck"),
    current_controls="\n# 操作説明 (共通)\n\n- A: unknown\n- B: ジャンプ\n",
    place_names=PLACE_NAMES,
    map_document=MAP_DOCUMENT,
    progress=PROGRESS,
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
    task_queue=(),
    task_events=(),
    surface_rows_offset=0,
    operations_rows_offset=0,
    tasks_rows_offset=0,
    reasons=("interval",),
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
    assert len(sections) == 17
    assert content[:17] == [{"type": "input_text", "text": text} for text in sections]
    assert content[17:] == [
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
        "場所の名前の一覧",
        "マップ",
        "現在地からの経路 (コードの計算)",
        "前回の middle の出力",
        "ゲームの性質",
        "大方針",
        "現在の場面のタスクのキュー",
        "前回以降のタスクの出来事",
        "呼ばれた理由",
        "現在の場面の操作説明",
        "前回以降の surface の記録",
        "前回以降の操作の記録",
        "場面転換の印",
        "画面の停滞 (コードの計測)",
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
    assert section_body(sections, "場所の名前の一覧") == describe_place_names(
        PLACE_NAMES
    )
    # マップ (notes/map/map.json) の全体を JSON で渡す
    assert json.loads(section_body(sections, "マップ")) == MAP_JSON
    # マップの直後に、現在地からの経路の節
    assert section_body(sections, ROUTE_SECTION_TITLE) == describe_route_section(
        MAP_DOCUMENT, PREVIOUS_STATE
    )
    assert section_body(sections, "前回の middle の出力") == describe_previous_state(
        PREVIOUS_STATE
    )
    # 前後の空白を除く
    assert section_body(sections, "大方針") == "# 大方針\n\n北の城へ向かう"
    assert section_body(sections, "現在の場面のタスクのキュー") == TASK_QUEUE_TEXT
    assert section_body(sections, "前回以降のタスクの出来事") == TASK_EVENTS_TEXT
    assert section_body(sections, "呼ばれた理由") == lines_of(
        [
            "- cancel_requested: surface が実行中のタスクの取り消しを求めた",
            "- stuck: 現在の場面の最も古いタスクが、詰まりとみなす秒数を超えてキューに残っている",
        ]
    )
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
    # コードが画面のハッシュから測った停滞の値を、surface と同じ文で渡す
    assert section_body(sections, "画面の停滞 (コードの計測)") == (
        "計測した時刻: t=15.200\n"
        "画面が変わっていない: 12.4 秒\n"
        "直近 300 秒に見た画面だけが出ている: 85.0 秒"
    )
    assert section_body(sections, "フレーム") == (
        "一定間隔で保存したフレーム 2 枚 (古い順)。各画像の直前に時刻のラベルがある"
    )


# FULL_INPUT のタスクのキューの節の中身 (キューの順。秒数は active_s)
TASK_QUEUE_TEXT = lines_of(
    [
        "- task-003: 宿屋に入っている (行き先: 町 (3, 4)、キューに入ってから 12.5 秒、取り消しの要求あり)",
        "- task-004: 店の前にいる (行き先: 町、キューに入ってから 3 秒)",
        "- task-005: 会話の枠が閉じている (行き先: なし、キューに入ってから 0.25 秒)",
    ]
)
# FULL_INPUT のタスクの出来事の節の中身 (書かれた順。時刻は surface の記録と同じ t=<小数第 3 位まで>)
TASK_EVENTS_TEXT = lines_of(
    [
        "- t=12.345 完了 task-002 「宿屋の前にいる」",
        "- t=13.000 足せなかった (同じ文がある) 「宿屋に入っている」",
        "- t=14.000 取り消しの要求 task-003 「宿屋に入っている」",
        "- t=14.500 取り消し task-001 「町の北の出口にいる」",
    ]
)


def test_previous_state_section_has_no_first_task_lines():
    lines = section_body(build_user_sections(FULL_INPUT), "前回の middle の出力").split(
        "\n"
    )
    assert lines[4:6] == ["surface の待ち時間: 0.4 秒", "系列情報:"]
    assert not any(line.startswith("最初のタスク") for line in lines)


def test_task_sections_are_right_after_strategy():
    index = USER_SECTION_TITLES.index("大方針")
    assert USER_SECTION_TITLES[index + 1 : index + 5] == (
        "現在の場面のタスクのキュー",
        "前回以降のタスクの出来事",
        "呼ばれた理由",
        "現在の場面の操作説明",
    )


@pytest.mark.parametrize(
    ("reason", "dropped_text"),
    [("empty", "空の文"), ("duplicate", "同じ文がある"), ("limit", "上限")],
)
def test_dropped_event_has_the_reason_in_words(reason, dropped_text):
    row = {
        **TASK_EVENTS[1],
        "reason": reason,
        "text": "" if reason == "empty" else "店の前にいる",
    }
    sections = build_user_sections(dataclasses.replace(FULL_INPUT, task_events=(row,)))
    text = "" if reason == "empty" else "店の前にいる"
    assert section_body(sections, "前回以降のタスクの出来事") == (
        f"- t=13.000 足せなかった ({dropped_text}) 「{text}」"
    )


def test_every_call_reason_has_a_line_in_order():
    # 理由は MIDDLE_CALL_REASON_ORDER の順に 1 行ずつ書く
    reasons = ("scene_change", "queue_empty", "stuck", "cancel_requested")
    sections = build_user_sections(dataclasses.replace(FULL_INPUT, reasons=reasons))
    assert section_body(sections, "呼ばれた理由") == lines_of(
        [
            "- queue_empty: 現在の場面のキューが、完了か取り消しで空になった",
            "- cancel_requested: surface が実行中のタスクの取り消しを求めた",
            "- stuck: 現在の場面の最も古いタスクが、詰まりとみなす秒数を超えてキューに残っている",
            "- scene_change: surface が場面転換を検知した",
        ]
    )


def test_section_contents_of_empty_input():
    sections = build_user_sections(EMPTY_INPUT)
    assert section_body(sections, "現在の時刻") == "t=0.000"
    assert section_body(sections, "場面ラベルの一覧") == "(無い)"
    assert section_body(sections, "場所の名前の一覧") == "(無い)"
    assert section_body(sections, "マップ") == "マップなし"
    assert section_body(sections, ROUTE_SECTION_TITLE) == ROUTE_NO_MAP_TEXT
    assert section_body(sections, "前回の middle の出力") == "(無い)"
    assert section_body(sections, "ゲームの性質") == "(無い)"
    assert section_body(sections, "大方針") == "(無い)"
    assert section_body(sections, "現在の場面のタスクのキュー") == "(無い)"
    assert section_body(sections, "前回以降のタスクの出来事") == "(無い)"
    assert section_body(sections, "呼ばれた理由") == (
        "- interval: 前回から一定の間隔がたった"
    )
    assert section_body(sections, "現在の場面の操作説明") == "(無い)"
    assert section_body(sections, "前回以降の surface の記録") == "(記録なし)"
    assert section_body(sections, "前回以降の操作の記録") == "(記録なし)"
    assert section_body(sections, "場面転換の印") == (
        "surface は、前回の middle の実行以降に場面転換を検知していない"
    )
    assert section_body(sections, "画面の停滞 (コードの計測)") == NO_PROGRESS_TEXT
    assert section_body(sections, "フレーム") == "フレームは無い"
    content = build_user_message(EMPTY_INPUT)["content"]
    assert len(content) == 17
    assert all(part["type"] == "input_text" for part in content)


# --- 部品 ---


def test_jpeg_data_url():
    assert jpeg_data_url(b"\xff\xd8abc") == "data:image/jpeg;base64,/9hhYmM="


def test_frame_label_uses_milliseconds():
    assert frame_label(12.3456) == "フレーム t=12.346"
    assert frame_label(3.0) == "フレーム t=3.000"


def test_describe_scene_labels():
    labels = (SceneLabel("scene-001", "町", 3.0), SceneLabel("scene-002", "戦闘", 9.5))
    # 名前の後ろの括弧に場面 ID を書かない (middle が名前と一緒に書き返さないため)
    assert describe_scene_labels(labels) == lines_of(
        [
            "- 町",
            "  場面 ID: scene-001、初めて名前を付けた時刻: t=3.000",
            "- 戦闘",
            "  場面 ID: scene-002、初めて名前を付けた時刻: t=9.500",
        ]
    )
    assert describe_scene_labels(()) == "(無い)"


def test_describe_place_names():
    assert describe_place_names(PLACE_NAMES) == lines_of(
        ["- 家の 1 階", "- 1 階の部屋", "- 自分の部屋 (2 階)"]
    )
    assert describe_place_names(()) == "(無い)"


def test_describe_map_document():
    assert NO_MAP_TEXT == "マップなし"
    assert describe_map_document(None) == "マップなし"
    text = describe_map_document(MAP_DOCUMENT)
    assert json.loads(text) == map_document_to_json(MAP_DOCUMENT)
    assert json.loads(text) == MAP_JSON
    # 日本語をエスケープしない
    assert "家の 1 階" in text
    # 空のマップも JSON で渡す (マップなしとは区別する)
    empty = describe_map_document(MapDocument(places=(), connections=()))
    assert json.loads(empty) == {"layout": "free", "places": [], "connections": []}
    # layout を最上位の最初のキーにする
    assert list(json.loads(text)) == ["layout", "places", "connections"]


def test_describe_previous_state():
    assert describe_previous_state(PREVIOUS_STATE) == lines_of(
        [
            "作成時刻: t=10.600 (入力の時刻 t=10.000)",
            "場面: 町",
            "場面 ID: scene-001",
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


def test_describe_progress():
    assert describe_progress(PROGRESS) == (
        "計測した時刻: t=15.200\n"
        "画面が変わっていない: 12.4 秒\n"
        "直近 300 秒に見た画面だけが出ている: 85.0 秒"
    )
    assert describe_progress(None) == NO_PROGRESS_TEXT


# --- 変わらない指示 ---


def test_instructions_cover_every_output_field_and_timeline_section():
    properties = MIDDLE_OUTPUT_SCHEMA["properties"]
    new_task = properties["new_tasks"]["items"]["properties"]
    item_keys = [
        *properties["new_connections"]["items"]["properties"],
        *properties["new_observations"]["items"]["properties"],
        *new_task,
        *new_task["destination"]["anyOf"][0]["properties"],
    ]
    for key in [*properties, *item_keys]:
        assert key in LUNA_DEVELOPER_INSTRUCTIONS, key
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


def test_instructions_explain_when_to_press_home():
    # press_home を真にするのは、ゲームが操作を受け付けないなど本体の画面に出る必要があるときだけ。
    # 真にすると HOME を押して復帰の段階に移る
    assert "press_home" in LUNA_DEVELOPER_INSTRUCTIONS
    assert "ゲームが操作を受け付けない" in LUNA_DEVELOPER_INSTRUCTIONS
    assert "復帰" in LUNA_DEVELOPER_INSTRUCTIONS


def test_instructions_include_the_safety_rules():
    # 変わらない指示の中に入れる (毎回同じ文字列のまま、キャッシュから読む)
    assert SAFETY_RULES in LUNA_DEVELOPER_INSTRUCTIONS


def test_instructions_cover_the_map():
    # マップの 3 つの出力の項目と、観察の種類の書き方
    for phrase in [
        "place_name",
        "new_connections",
        "new_observations",
        "exit",
        "landmark",
        "blocked",
    ]:
        assert phrase in LUNA_DEVELOPER_INSTRUCTIONS, phrase
    # 場所の名前の一覧とマップを入力として説明し、マップを使って現在地から行き先までの手順をタスクにする
    for phrase in ["場所の名前の一覧", "マップ", "現在地"]:
        assert phrase in LUNA_DEVELOPER_INSTRUCTIONS, phrase


def test_instructions_use_the_measured_progress_for_is_stuck():
    instructions = LUNA_DEVELOPER_INSTRUCTIONS
    stuck_section = instructions.split("# 詰まり (is_stuck と stuck_description)", 1)[1]
    stuck_section = stuck_section.split("\n# ", 1)[0]
    # is_stuck を決めるときに、コードの計測の節を使う
    assert "画面の停滞 (コードの計測)" in stuck_section
    assert "is_stuck" in stuck_section
    # 出力の形は変えない
    assert set(MIDDLE_OUTPUT_SCHEMA["properties"]) >= {"is_stuck", "stuck_description"}


# 待ち時間の節の、タスクの進み具合の箇条の書き出し (箇条の文は、下のテストで語句を確かめる)
SURFACE_WAIT_PROGRESS_PREFIX = "- タスクが進んでいるか: "
SURFACE_WAIT_SECTION_BODY = lines_of(
    [
        (
            "surface が応答を受け取ってから次の要求を送るまでの待ち秒です。0.2〜2.0 の範囲で、"
            "次の 3 つを見て決めます。場面の種類 (フィールド・会話・メニューなど) だけでは決めません。"
        ),
        "",
        (
            "- 素早い反応が要るか: 動く敵や障害物を避ける、タイミングを合わせて押す、制限時間があるなど、"
            "画面の変化に遅れずに操作を変える必要があるときは短くします (0.2〜0.5)。文字送り・メニュー・"
            "ロード・演出のように、待っても困らないときは長くします (1.0〜2.0)"
        ),
        (
            "- 押し続けが長すぎないか: surface の押し続けは次の判断まで続くため、待ち時間がそのまま"
            "押す長さになります。操作の記録で押し続けが続いていて、フレームで目標 (出口・入口・"
            "話しかける相手など) を通り過ぎることが続いている、壁に当たり続けている、目標の前後を"
            "行き来しているときは、待ち時間を前回より短くします"
        ),
        SURFACE_WAIT_PROGRESS_PREFIX,
        "",
        "迷ったときは前回の値を引き継ぎます。",
    ]
)


def test_instructions_decide_surface_wait_by_reaction_holding_and_progress():
    # 見出しの次の行から次の見出し「# 出力」の前までが、待ち時間の節の本文
    section = LUNA_DEVELOPER_INSTRUCTIONS.split("\n# 待ち時間 (surface_wait_s)\n", 1)[
        1
    ].split("\n# 出力\n", 1)[0]
    # 既にある文は、この順にそのまま残す (ゲームの性質を見る文は、これらの文を変えずに足す)
    position = 0
    for text in SURFACE_WAIT_SECTION_BODY.split("\n"):
        if not text:
            continue
        found = section.find(text, position)
        assert found >= 0, text
        position = found + len(text)
    # タスクの進み具合は、キューの最も古いタスクが長く残っていることを手がかりにする
    progress_start = section.index(SURFACE_WAIT_PROGRESS_PREFIX)
    progress_line = section[progress_start:].split("\n", 1)[0]
    assert "最も古いタスク" in progress_line
    assert "最初のタスク" not in section
    # 場面の種類ごとの固定の表は使わない
    assert "0.5〜1.0: フィールドの移動、パズルなど" not in LUNA_DEVELOPER_INSTRUCTIONS
    # surface の押し続けには秒数が無い (次の判断まで続く)
    assert "0.3 秒か 1.0 秒" not in LUNA_DEVELOPER_INSTRUCTIONS


# --- ゲームの性質 ---

GAME_TEXT = "\n# ゲームの性質\n\n## ジャンル\nRPG (推測)\n\n"


def test_game_section_is_right_before_strategy():
    index = USER_SECTION_TITLES.index("ゲームの性質")
    assert USER_SECTION_TITLES[index - 1] == "前回の middle の出力"
    assert USER_SECTION_TITLES[index + 1] == "大方針"
    sections = build_user_sections(dataclasses.replace(FULL_INPUT, game=GAME_TEXT))
    assert sections[index].startswith("## ゲームの性質\n")
    assert sections[index + 1].startswith("## 大方針\n")


def test_game_section_has_the_stripped_game_document():
    sections = build_user_sections(dataclasses.replace(FULL_INPUT, game=GAME_TEXT))
    # 前後の空白を除く (大方針と同じ)
    assert (
        section_body(sections, "ゲームの性質")
        == "# ゲームの性質\n\n## ジャンル\nRPG (推測)"
    )


def test_game_section_of_missing_or_blank_document_is_empty_text():
    # 無い・読めない文書は入力で空文字列になる。空白だけの文書も、ほかの節と同じく「(無い)」
    for text in ["", "  \n\t\n"]:
        sections = build_user_sections(dataclasses.replace(FULL_INPUT, game=text))
        assert section_body(sections, "ゲームの性質") == "(無い)"


def test_developer_message_does_not_depend_on_the_game_document():
    with_game = build_luna_payload(dataclasses.replace(FULL_INPUT, game=GAME_TEXT))
    without_game = build_luna_payload(FULL_INPUT)
    assert with_game["input"][0] == without_game["input"][0]
    assert with_game["input"][0] == build_developer_message()
    assert "RPG (推測)" not in LUNA_DEVELOPER_INSTRUCTIONS


def instructions_section(title: str) -> str:
    """変わらない指示の「# <title>」の見出しの次の行から、次の「# 」の見出しの前までを返す。"""
    return LUNA_DEVELOPER_INSTRUCTIONS.split(f"\n# {title}\n", 1)[1].split("\n# ", 1)[0]


def test_instructions_list_the_game_section_in_the_input():
    lines = instructions_section("入力").split("\n")
    starts: list[int] = []
    for prefix in ["- 前回の middle の出力: ", "- ゲームの性質: ", "- 大方針: "]:
        found = [i for i, text in enumerate(lines) if text.startswith(prefix)]
        assert len(found) == 1, prefix
        starts.append(found[0])
    # user メッセージの節の順どおり、前回の middle の出力の後、大方針の前に並べる
    assert starts == sorted(starts)


def test_instructions_decide_surface_wait_with_the_game_document():
    section = instructions_section("待ち時間 (surface_wait_s)")
    for phrase in ["ゲームの性質", "リアルタイムに進む部分", "ターン制の部分"]:
        assert phrase in section, phrase


# --- マス目型 (grid) のマップと、現在地からの経路の節 ---

GRID_MAP_DOCUMENT = GridMapDocument(
    places=(
        GridMapPlace(
            name="家の 1 階",
            aliases=("1 階の部屋",),
            description="自分の部屋から階段で下りた先の部屋",
            state="訪問済み",
            changes_on_entry=False,
            origin=TilePosition(0, 0),
            tiles=("#########", "#.......#", "#E......#"),
            exits=(TileMark(TilePosition(1, 2), "左下の赤いマット"),),
            blocked=(TileMark(TilePosition(4, 1), "机"),),
            landmarks=(TileMark(TilePosition(6, 1), "テレビ"),),
        ),
    ),
    connections=(
        MapConnection(
            from_place="家の 1 階",
            to_place="町",
            how="左下のマットから下へ",
            from_tile=TilePosition(1, 2),
            to_tile=None,
        ),
    ),
)
GRID_INPUT = dataclasses.replace(
    FULL_INPUT,
    map_document=GRID_MAP_DOCUMENT,
    previous_state=dataclasses.replace(
        PREVIOUS_STATE, place_name="家の 1 階", current_tile=TilePosition(7, 1)
    ),
)


def test_grid_map_and_route_sections():
    sections = build_user_sections(GRID_INPUT)
    assert len(sections) == 17
    map_json = json.loads(section_body(sections, "マップ"))
    assert map_json == map_document_to_json(GRID_MAP_DOCUMENT)
    assert map_json["layout"] == "grid"
    # 前回の出力の現在地 (家の 1 階の (7, 1)) から、出口までの経路をコードで計算して書く。続けて場所をまたぐ経路
    # (町はマップの場所に無いため、行ける場所は無い)
    assert section_body(sections, ROUTE_SECTION_TITLE) == lines_of(
        [
            "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 家の 1 階 のマス (x=7, y=1)",
            "- 出口「左下の赤いマット」(x=1, y=2)。行き先: 町。手順: 下に 1 マス → 左に 6 マス (合計 7 マス)",
            "場所をまたぐ経路: 行ける場所は無い",
        ]
    )
    # 経路の節はマップの節の直後
    titles = [text.split("\n", 1)[0] for text in sections]
    assert titles.index(f"## {ROUTE_SECTION_TITLE}") == titles.index("## マップ") + 1


def test_developer_message_does_not_depend_on_the_grid_map():
    grid = build_luna_payload(GRID_INPUT)
    empty = build_luna_payload(EMPTY_INPUT)
    assert json.dumps(grid["input"][0], ensure_ascii=False) == json.dumps(
        empty["input"][0], ensure_ascii=False
    )
    # 経路の計算の結果は developer メッセージに入らない
    assert "左に 6 マス" not in LUNA_DEVELOPER_INSTRUCTIONS


def test_instructions_cover_the_grid_map_and_the_route_section():
    for phrase in [
        # マップの型と、grid の読み方 (origin・tiles・文字の意味・座標の向き)
        "layout",
        "grid",
        "free",
        "origin",
        "tiles",
        "landmarks",
        # 座標の書き方
        "current_tile",
        "from_tile",
        "to_tile",
        "null",
        # 経路の節
        ROUTE_SECTION_TITLE,
    ]:
        assert phrase in LUNA_DEVELOPER_INSTRUCTIONS, phrase


def test_instructions_describe_exits_avoided_by_the_route():
    # 経路は、目標でない出口のマスとつながりの出たマスを途中で通らない
    assert (
        "目標でない出口のマスとつながりの出たマス (tiles の E と、exits の座標と、その場所から出る connections の "
        "from_tile のマス) は、乗ると場所を移るため、途中で通りません"
    ) in LUNA_DEVELOPER_INSTRUCTIONS
