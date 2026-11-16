import dataclasses
import json
from pathlib import Path

from switch2_layers.middle.map_records import (
    MAP_RECORD_KEYS,
    build_map_record,
    read_recorded_place_names,
)
from switch2_layers.middle.middle_types import (
    MapObservation,
    MiddleFrame,
    MiddleInput,
    MiddleOutput,
    ObservationKind,
)
from switch2_layers.notes.map_document import MapConnection, TilePosition

FRAMES = (
    MiddleFrame(t=6.0, file="frames/interval/000006000.jpg", jpeg=b"a"),
    MiddleFrame(t=9.2, file="frames/interval/000009200.jpg", jpeg=b"b"),
    MiddleFrame(t=15.25, file="frames/interval/000015250.jpg", jpeg=b"c"),
)
INPUT = MiddleInput(
    t=16.0,
    frames=FRAMES,
    surface_rows=(),
    operation_rows=(),
    scene_change_reported=False,
    scene_labels=(),
    previous_state=None,
    strategy="",
    current_tasks="",
    surface_rows_offset=0,
    operations_rows_offset=0,
)
CONNECTIONS = (
    MapConnection(
        from_place="自分の部屋 (2 階)",
        to_place="家の 1 階",
        how="右上の階段を下に",
        from_tile=TilePosition(7, 0),
        to_tile=TilePosition(-7, 1),
    ),
    MapConnection(from_place="家の 1 階", to_place="町", how=" 左下のマットから下へ "),
)
OBSERVATIONS = (
    MapObservation(
        kind=ObservationKind.EXIT,
        place="家の 1 階",
        description="左下の赤いマット",
        tile=TilePosition(1, 2),
    ),
    MapObservation(
        kind=ObservationKind.BLOCKED, place="家の 1 階", description="下側の壁"
    ),
    MapObservation(kind=ObservationKind.LANDMARK, place="町", description="噴水"),
)
OUTPUT = MiddleOutput(
    scene_name="フィールド",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.5,
    timeline="",
    tasks="- 町へ出る\n",
    place_name="家の 1 階",
    current_tile=TilePosition(7, 1),
    new_connections=CONNECTIONS,
    new_observations=OBSERVATIONS,
)


def map_output(**changes: object) -> MiddleOutput:
    return dataclasses.replace(OUTPUT, **changes)


# --- 行の組み立て ---


def test_row_has_latest_sent_frame_time_scene_place_and_items():
    row = build_map_record(INPUT, "scene-002", OUTPUT)
    assert row == {
        "t": 15.25,
        "scene_id": "scene-002",
        "place_name": "家の 1 階",
        "current_tile": {"x": 7, "y": 1},
        "connections": [
            {
                "from": "自分の部屋 (2 階)",
                "to": "家の 1 階",
                "how": "右上の階段を下に",
                "from_tile": {"x": 7, "y": 0},
                "to_tile": {"x": -7, "y": 1},
            },
            # 値は書かれたまま。座標の無いつながりは null
            {
                "from": "家の 1 階",
                "to": "町",
                "how": " 左下のマットから下へ ",
                "from_tile": None,
                "to_tile": None,
            },
        ],
        "observations": [
            {
                "kind": "exit",
                "place": "家の 1 階",
                "description": "左下の赤いマット",
                "tile": {"x": 1, "y": 2},
            },
            {
                "kind": "blocked",
                "place": "家の 1 階",
                "description": "下側の壁",
                "tile": None,
            },
            {"kind": "landmark", "place": "町", "description": "噴水", "tile": None},
        ],
    }
    assert MAP_RECORD_KEYS == (
        "t",
        "scene_id",
        "place_name",
        "current_tile",
        "connections",
        "observations",
    )
    assert list(row) == list(MAP_RECORD_KEYS)
    assert list(row["current_tile"]) == ["x", "y"]
    assert all(
        list(item) == ["from", "to", "how", "from_tile", "to_tile"]
        for item in row["connections"]
    )
    assert list(row["connections"][0]["from_tile"]) == ["x", "y"]
    assert all(
        list(item) == ["kind", "place", "description", "tile"]
        for item in row["observations"]
    )
    assert json.loads(json.dumps(row, ensure_ascii=False)) == row


def test_time_is_the_latest_of_the_sent_frames():
    one_frame = dataclasses.replace(INPUT, frames=FRAMES[:1])
    assert build_map_record(one_frame, None, OUTPUT)["t"] == 6.0


def test_scene_without_id_is_null():
    assert build_map_record(INPUT, None, OUTPUT)["scene_id"] is None


def test_place_name_is_stripped():
    row = build_map_record(INPUT, "scene-001", map_output(place_name="  家の 1 階 "))
    assert row["place_name"] == "家の 1 階"


def test_only_place_name_is_recorded():
    row = build_map_record(
        INPUT, "scene-001", map_output(new_connections=(), new_observations=())
    )
    assert row == {
        "t": 15.25,
        "scene_id": "scene-001",
        "place_name": "家の 1 階",
        "current_tile": {"x": 7, "y": 1},
        "connections": [],
        "observations": [],
    }


def test_place_name_without_current_tile_is_recorded_with_null_tile():
    row = build_map_record(INPUT, "scene-001", map_output(current_tile=None))
    assert row["place_name"] == "家の 1 階"
    assert row["current_tile"] is None


def test_current_tile_without_place_name_is_null():
    # middle/state.json と同じく、place_name が null なら current_tile も null にする
    for place_name in [None, "", "   "]:
        row = build_map_record(INPUT, "scene-001", map_output(place_name=place_name))
        assert row is not None
        assert row["place_name"] is None
        assert row["current_tile"] is None


def test_current_tile_alone_is_not_recorded():
    # current_tile は記録するかの判断に使わない (place_name が null で、つながりと観察が空なら記録しない)
    output = map_output(
        place_name=None,
        current_tile=TilePosition(7, 1),
        new_connections=(),
        new_observations=(),
    )
    assert build_map_record(INPUT, "scene-001", output) is None


def test_place_name_without_place_is_null_when_items_exist():
    for place_name in [None, "", "   "]:
        row = build_map_record(
            INPUT,
            "scene-001",
            map_output(place_name=place_name, new_observations=()),
        )
        assert row is not None
        assert row["place_name"] is None
        assert len(row["connections"]) == 2
        assert row["observations"] == []


def test_only_observations_are_recorded():
    row = build_map_record(INPUT, None, map_output(place_name=None, new_connections=()))
    assert row is not None
    assert row["place_name"] is None
    assert row["connections"] == []
    assert len(row["observations"]) == 3


def test_nothing_new_is_not_recorded():
    assert build_map_record(INPUT, "scene-001", map_output()) is not None
    for place_name in [None, "", "  \n"]:
        output = map_output(
            place_name=place_name, new_connections=(), new_observations=()
        )
        assert build_map_record(INPUT, "scene-001", output) is None


def test_call_without_sent_frames_is_not_recorded():
    assert build_map_record(INPUT, "scene-001", OUTPUT) is not None
    no_frames = dataclasses.replace(INPUT, frames=())
    assert build_map_record(no_frames, "scene-001", OUTPUT) is None


def test_output_without_map_fields_is_not_recorded():
    # フェイクの middle のように、マップの項目を持たない出力 (既定値) は記録しない
    assert build_map_record(INPUT, "scene-001", OUTPUT) is not None
    plain = MiddleOutput(
        scene_name="フィールド",
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        tasks="",
    )
    assert build_map_record(INPUT, "scene-001", plain) is None


# --- 記録の場所の名前の読み取り ---


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


def row_line(place_name: object, **extra: object) -> str:
    row = {
        "t": 1.0,
        "scene_id": None,
        "place_name": place_name,
        "connections": [],
        "observations": [],
        **extra,
    }
    return json.dumps(row, ensure_ascii=False) + "\n"


def test_recorded_place_names_in_written_order(tmp_path):
    path = tmp_path / "map_records.jsonl"
    write_lines(
        path,
        [
            row_line("家の 1 階"),
            row_line(None),
            row_line(" 町 "),
            row_line("家の 1 階"),
            row_line(""),
            row_line("   "),
            row_line(3),
            json.dumps({"t": 2.0}) + "\n",
            "{broken\n",
            "\n",
            row_line("草原"),
            # 書きかけの最後の行は読まない
            '{"t": 9.0, "place_name": "洞窟"',
        ],
    )
    # 重複は除かず、前後の空白を除く
    assert read_recorded_place_names(path) == ["家の 1 階", "町", "家の 1 階", "草原"]


def test_missing_or_unreadable_records_give_no_names(tmp_path):
    path = tmp_path / "map_records.jsonl"
    assert read_recorded_place_names(path) == []
    # 読めない (ディレクトリーになっている) ときも、例外を出さずに空のリスト
    path.mkdir()
    assert read_recorded_place_names(path) == []
