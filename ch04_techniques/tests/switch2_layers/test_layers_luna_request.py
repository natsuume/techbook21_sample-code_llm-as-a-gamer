import base64
import dataclasses
import json

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
from switch2_layers.safety.safety_rules import SAFETY_RULES

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
    first_task="宿屋に入る",
    first_task_since_t=4.75,
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
    place_names=PLACE_NAMES,
    map_document=MAP_DOCUMENT,
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
    assert len(sections) == 14
    assert content[:14] == [{"type": "input_text", "text": text} for text in sections]
    assert content[14:] == [
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
        PREVIOUS_STATE, FULL_INPUT.t
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


def test_previous_state_section_has_first_task_and_its_time_from_input_time():
    # 最初のタスクの文と、その文になった時刻を載せる。経過秒は今回の入力の時刻 (t=15.25) から数える
    # (前回の出力の作成時刻 t=10.6 や、前回の入力の時刻 t=10.0 からではない)
    lines = section_body(build_user_sections(FULL_INPUT), "前回の middle の出力").split(
        "\n"
    )
    assert lines[4:8] == [
        "surface の待ち時間: 0.4 秒",
        "最初のタスク: 宿屋に入る",
        "最初のタスクが今の文になった時刻: t=4.750 (入力の時刻まで 10.5 秒)",
        "系列情報:",
    ]


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
    assert section_body(sections, "現在の場面の短期タスクリスト") == "(無い)"
    assert section_body(sections, "現在の場面の操作説明") == "(無い)"
    assert section_body(sections, "前回以降の surface の記録") == "(記録なし)"
    assert section_body(sections, "前回以降の操作の記録") == "(記録なし)"
    assert section_body(sections, "場面転換の印") == (
        "surface は、前回の middle の実行以降に場面転換を検知していない"
    )
    assert section_body(sections, "フレーム") == "フレームは無い"
    content = build_user_message(EMPTY_INPUT)["content"]
    assert len(content) == 14
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
    assert describe_previous_state(PREVIOUS_STATE, 15.25) == lines_of(
        [
            "作成時刻: t=10.600 (入力の時刻 t=10.000)",
            "場面: 町 (scene-001)",
            "止まっているか: いいえ",
            "詰まり方: (無い)",
            "surface の待ち時間: 0.4 秒",
            "最初のタスク: 宿屋に入る",
            "最初のタスクが今の文になった時刻: t=4.750 (入力の時刻まで 10.5 秒)",
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
        first_task="",
        first_task_since_t=None,
    )
    assert describe_previous_state(state, 15.25) == lines_of(
        [
            "作成時刻: t=10.600 (入力の時刻 t=10.000)",
            "場面: (決まっていない)",
            "止まっているか: はい",
            "詰まり方: 壁に向かって歩き続けている",
            "surface の待ち時間: 1.25 秒",
            "最初のタスク: (無い)",
            "最初のタスクが今の文になった時刻: (無い)",
            "系列情報:",
            "(無い)",
        ]
    )
    assert describe_previous_state(None, 15.25) == "(無い)"


def first_task_line(first_task: str) -> str:
    """describe_previous_state の「最初のタスク」の行 (6 行目) を返す。"""
    state = dataclasses.replace(PREVIOUS_STATE, first_task=first_task)
    return describe_previous_state(state, 15.25).split("\n")[5]


def test_first_task_line_shows_the_text_or_empty_text():
    assert first_task_line("宿屋に入る") == "最初のタスク: 宿屋に入る"
    assert first_task_line("") == "最初のタスク: (無い)"


def first_task_time_line(first_task_since_t: float | None, now_t: float) -> str:
    """describe_previous_state の「最初のタスクが今の文になった時刻」の行 (7 行目) を返す。"""
    state = dataclasses.replace(PREVIOUS_STATE, first_task_since_t=first_task_since_t)
    return describe_previous_state(state, now_t).split("\n")[6]


def test_first_task_time_is_counted_to_input_time_with_one_decimal():
    assert first_task_time_line(605.0, 640.2) == (
        "最初のタスクが今の文になった時刻: t=605.000 (入力の時刻まで 35.2 秒)"
    )
    assert first_task_time_line(3.0, 33.0) == (
        "最初のタスクが今の文になった時刻: t=3.000 (入力の時刻まで 30.0 秒)"
    )


def test_first_task_time_after_input_time_counts_as_zero():
    # 入力の時刻より後の時刻 (経過が負) は 0.0 秒にする
    assert first_task_time_line(20.0, 15.25) == (
        "最初のタスクが今の文になった時刻: t=20.000 (入力の時刻まで 0.0 秒)"
    )
    assert first_task_time_line(15.25, 15.25) == (
        "最初のタスクが今の文になった時刻: t=15.250 (入力の時刻まで 0.0 秒)"
    )


def test_first_task_time_without_record_is_empty_text():
    assert (
        first_task_time_line(None, 15.25) == "最初のタスクが今の文になった時刻: (無い)"
    )


# --- 変わらない指示 ---


def test_instructions_cover_every_output_field_and_timeline_section():
    properties = MIDDLE_OUTPUT_SCHEMA["properties"]
    item_keys = [
        *properties["new_connections"]["items"]["properties"],
        *properties["new_observations"]["items"]["properties"],
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


def test_instructions_describe_first_task_and_its_time_in_previous_output():
    assert (
        "\n- 前回の middle の出力: 前回あなたが書いた場面・詰まり・待ち時間・系列情報と、"
        "それを作った時刻。最初のタスクの文と、その文になった時刻と、そこから入力の時刻までの"
        "秒数も含みます\n"
    ) in LUNA_DEVELOPER_INSTRUCTIONS
    assert (
        "前回あなたが書いた場面・詰まり・待ち時間・系列情報と、それを作った時刻\n"
        not in LUNA_DEVELOPER_INSTRUCTIONS
    )


def test_instructions_weigh_first_task_progress_for_stuck():
    # 例の箇条の直後、短い停止の箇条の前に、最初のタスクの進み具合を重視する 2 つの箇条を置く
    assert (
        "  - 壁にぶつかり続けている、同じ場所を回り続けている\n"
        "- 最初のタスクが進んでいるかを、画面の動きより重視します。前回の middle の出力の"
        "「最初のタスクが今の文になった時刻」から、最初のタスクが同じ文のまま何秒続いているかを"
        "見ます。目安として 30 秒以上続いていれば、画面や主人公が動いていても is_stuck を true に"
        "します。ただし、会話の文字送り・演出・ロードを待つこと自体が最初のタスクで、その表示が"
        "進んでいるときは除きます\n"
        "- 詰まりを解くために最初のタスクを書き換えたときも、詰まりが解けたことをフレームで"
        "確かめるまでは is_stuck を true のままにします\n"
        "- 会話の文字送り・演出・ロードによる短い停止は"
    ) in LUNA_DEVELOPER_INSTRUCTIONS
    assert (
        "いつから何秒間続いているか (最初のタスクが同じ文のまま続いている秒数を含む)、"
        in LUNA_DEVELOPER_INSTRUCTIONS
    )


def test_instructions_keep_first_task_text_unchanged():
    assert (
        "\n- 前回のタスクリストのうち、終わったものは消し、続いているものは前回の文を一字一句"
        "そのまま引き継ぎます。最初のタスクの文が変わると、タスクが進んだとみなされます。"
        "最初のタスクの文を変えるのは、そのタスクを終えたときと、詰まりを解くためにやり方を"
        "変えるときと、確かめていないボタンを試すタスクを先頭に入れるときだけにします\n"
    ) in LUNA_DEVELOPER_INSTRUCTIONS
    assert "続いているものは引き継ぎます" not in LUNA_DEVELOPER_INSTRUCTIONS


def test_instructions_keep_first_task_short_enough_to_finish():
    assert (
        "\n- 各タスクは、画面で達成を確かめられる具体的な行動にします (例: 「- 右の扉に入る」"
        "「- メニューを開いて所持品を確かめる」)。最初のタスクは、20 秒ほどで終えられる大きさに"
        "します。遠くへの移動のように長くかかることは、途中の目印までの手順に分けます\n"
    ) in LUNA_DEVELOPER_INSTRUCTIONS


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
            "- 押し続けが長すぎないか: surface の押し続けは、選んだ秒数 (0.3 秒か 1.0 秒) がたつか、"
            "次の判断で押さないと決めたときに離れます。待ち時間が短いほど、押し続けの途中で次の判断が入り、"
            "早く止められます。操作の記録で押し続けが続いているのに、フレームで"
            "目標 (出口・入口・話しかける相手など) を通り過ぎている、壁に当たり続けている、目標の前後を"
            "行き来しているときは、前回より短くします"
        ),
        (
            "- タスクが進んでいるか: 最初のタスクが同じ文のまま長く続き、位置を細かく合わせる必要が"
            "あるときは短くします。タスクが順に進んでいるときは、前回の値を引き継ぎます"
        ),
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
    # 場面の種類ごとの固定の表は使わない
    assert "0.5〜1.0: フィールドの移動、パズルなど" not in LUNA_DEVELOPER_INSTRUCTIONS


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


def test_instructions_keep_damage_and_game_over_out_of_tasks():
    section = instructions_section("短期タスクリスト (tasks)")
    assert "ダメージとゲームオーバーの条件" in section


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
    assert len(sections) == 14
    map_json = json.loads(section_body(sections, "マップ"))
    assert map_json == map_document_to_json(GRID_MAP_DOCUMENT)
    assert map_json["layout"] == "grid"
    # 前回の出力の現在地 (家の 1 階の (7, 1)) から、出口までの経路をコードで計算して書く
    assert section_body(sections, ROUTE_SECTION_TITLE) == lines_of(
        [
            "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 家の 1 階 のマス (x=7, y=1)",
            "- 出口「左下の赤いマット」(x=1, y=2)。行き先: 町。手順: 下に 1 マス → 左に 6 マス (合計 7 マス)",
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
        # 経路の節の使い方 (マス目型では、短期タスクに手順を書く)
        ROUTE_SECTION_TITLE,
        "上に 3 マス",
    ]:
        assert phrase in LUNA_DEVELOPER_INSTRUCTIONS, phrase
