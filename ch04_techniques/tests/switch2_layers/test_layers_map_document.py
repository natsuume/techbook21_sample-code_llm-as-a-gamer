import copy
import json
from pathlib import Path

import pytest

from switch2_layers.notes.map_document import (
    TILE_CHARACTERS,
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    MapDocument,
    MapLayout,
    MapPlace,
    TileMark,
    TilePosition,
    map_document_to_json,
    parse_map_document,
    read_map_document,
    tile_position_to_json,
)
from switch2_layers.notes.note_paths import MAP_JSON_NOTE

# issue の I/O 契約の例に、場所とつながりを 1 つずつ足したもの
VALID_MAP = {
    "places": [
        {
            "name": "家の 1 階",
            "aliases": ["1 階の部屋"],
            "description": "自分の部屋から階段で下りた先の部屋",
            "exits": ["左下の赤いマット (未確認)"],
            "blocked": ["下側の壁 (下を押し続けても動かない)"],
            "state": "訪問済み。出口は未通過",
            "changes_on_entry": False,
        },
        {
            "name": "自分の部屋 (2 階)",
            "aliases": [],
            "description": "",
            "exits": [],
            "blocked": [],
            "state": "訪問済み",
            "changes_on_entry": True,
        },
    ],
    "connections": [
        {
            "from": "自分の部屋 (2 階)",
            "to": "家の 1 階",
            "how": "部屋の右上の階段を上に",
        },
        {"from": "家の 1 階", "to": "自分の部屋 (2 階)", "how": "右上の階段"},
    ],
}
VALID_DOCUMENT = MapDocument(
    places=(
        MapPlace(
            name="家の 1 階",
            aliases=("1 階の部屋",),
            description="自分の部屋から階段で下りた先の部屋",
            exits=("左下の赤いマット (未確認)",),
            blocked=("下側の壁 (下を押し続けても動かない)",),
            state="訪問済み。出口は未通過",
            changes_on_entry=False,
        ),
        MapPlace(
            name="自分の部屋 (2 階)",
            aliases=(),
            description="",
            exits=(),
            blocked=(),
            state="訪問済み",
            changes_on_entry=True,
        ),
    ),
    connections=(
        MapConnection(
            from_place="自分の部屋 (2 階)",
            to_place="家の 1 階",
            how="部屋の右上の階段を上に",
        ),
        MapConnection(
            from_place="家の 1 階", to_place="自分の部屋 (2 階)", how="右上の階段"
        ),
    ),
)
PLACE_KEYS = [
    "name",
    "aliases",
    "description",
    "exits",
    "blocked",
    "state",
    "changes_on_entry",
]
CONNECTION_KEYS = ["from", "to", "how"]


def valid_map() -> dict:
    """VALID_MAP の深いコピー (テストの中で書き換えるため)。"""
    return copy.deepcopy(VALID_MAP)


# --- 形の確かめ ---


def test_parse_valid_map():
    assert MAP_JSON_NOTE == "map/map.json"
    assert parse_map_document(valid_map()) == VALID_DOCUMENT


def test_empty_places_and_connections_are_valid():
    assert parse_map_document({"places": [], "connections": []}) == MapDocument(
        places=(), connections=()
    )


def test_other_top_level_keys_are_ignored():
    value = {**valid_map(), "version": 2, "note": "deep のメモ"}
    assert parse_map_document(value) == VALID_DOCUMENT


def test_other_keys_in_elements_are_ignored():
    # issue は最上位のほかのキーを無視すると決めている。要素の object のほかのキーも同じく無視する
    value = valid_map()
    value["places"][0]["visited_t"] = 12.5
    value["connections"][0]["note"] = "暗転する"
    assert parse_map_document(value) == VALID_DOCUMENT


def test_values_are_kept_as_written():
    value = valid_map()
    value["places"][0]["name"] = " 家の 1 階 "
    value["connections"][0]["how"] = "  階段  "
    document = parse_map_document(value)
    assert document.places[0].name == " 家の 1 階 "
    assert document.connections[0].how == "  階段  "


def broken_top_level() -> list[object]:
    return [
        None,
        [],
        "map",
        {"places": []},
        {"connections": []},
        {"places": {}, "connections": []},
        {"places": [], "connections": None},
    ]


@pytest.mark.parametrize("value", broken_top_level())
def test_top_level_that_does_not_match_is_an_error(value):
    with pytest.raises(ValueError):
        parse_map_document(value)


def with_place(**changes: object) -> dict:
    value = valid_map()
    value["places"][1].update(changes)
    return value


def without_place_key(key: str) -> dict:
    value = valid_map()
    del value["places"][1][key]
    return value


def with_connection(**changes: object) -> dict:
    value = valid_map()
    value["connections"][1].update(changes)
    return value


def without_connection_key(key: str) -> dict:
    value = valid_map()
    del value["connections"][1][key]
    return value


def broken_elements() -> list[dict]:
    """形に合わない要素を 1 つだけ含む map.json の値。"""
    values = [
        {**valid_map(), "places": [*VALID_MAP["places"], "家"]},
        {**valid_map(), "connections": [*VALID_MAP["connections"], ["a", "b"]]},
        with_place(name=""),
        with_place(name="   "),
        with_place(name=None),
        with_place(name=1),
        with_place(aliases="1 階の部屋"),
        with_place(aliases=["ok", 1]),
        with_place(description=None),
        with_place(exits=[None]),
        with_place(blocked="壁"),
        with_place(state=0),
        with_place(changes_on_entry="false"),
        with_place(changes_on_entry=0),
        with_place(changes_on_entry=None),
        with_connection(**{"from": ""}),
        with_connection(to="  "),
        with_connection(**{"from": None}),
        with_connection(to=3),
        with_connection(how=None),
    ]
    values += [without_place_key(key) for key in PLACE_KEYS]
    values += [without_connection_key(key) for key in CONNECTION_KEYS]
    return values


@pytest.mark.parametrize("value", broken_elements())
def test_one_element_that_does_not_match_makes_the_whole_map_an_error(value):
    with pytest.raises(ValueError):
        parse_map_document(value)


# --- 辞書に戻す (middle の入力に渡す形) ---


def test_map_document_to_json_round_trip():
    value = map_document_to_json(VALID_DOCUMENT)
    # layout を最上位の最初のキーにする
    assert value == {"layout": "free", **VALID_MAP}
    assert list(value) == ["layout", "places", "connections"]
    assert all(list(place) == PLACE_KEYS for place in value["places"])
    assert all(list(item) == CONNECTION_KEYS for item in value["connections"])
    assert isinstance(value["places"][0]["aliases"], list)
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value
    assert parse_map_document(value) == VALID_DOCUMENT


def test_map_document_to_json_of_empty_map():
    assert map_document_to_json(MapDocument(places=(), connections=())) == {
        "layout": "free",
        "places": [],
        "connections": [],
    }


# --- notes/map/map.json の読み取り ---


def make_notes(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    (notes / "map").mkdir(parents=True)
    return notes


def test_read_valid_map(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "map" / "map.json").write_text(
        json.dumps(VALID_MAP, ensure_ascii=False), encoding="utf-8"
    )
    assert read_map_document(notes) == VALID_DOCUMENT


def test_missing_map_is_no_map(tmp_path):
    notes = make_notes(tmp_path)
    assert read_map_document(notes) is None
    # map/ も無い
    assert read_map_document(tmp_path / "other-notes") is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{broken",
        b"\xff\xfe not utf-8",
        b"null",
        b"[]",
        json.dumps({"places": [{"name": "家"}], "connections": []}).encode(),
    ],
    ids=["empty", "broken-json", "not-utf8", "null", "array", "shape"],
)
def test_unreadable_map_is_no_map(tmp_path, data):
    notes = make_notes(tmp_path)
    (notes / "map" / "map.json").write_bytes(data)
    assert read_map_document(notes) is None


def test_map_that_is_a_directory_is_no_map(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "map" / "map.json").mkdir()
    assert read_map_document(notes) is None


def test_map_symlinked_to_outside_of_notes_is_no_map(tmp_path):
    notes = make_notes(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(VALID_MAP, ensure_ascii=False), encoding="utf-8")
    (notes / "map" / "map.json").symlink_to(outside)
    assert read_map_document(notes) is None


def test_map_directory_symlinked_to_outside_of_notes_is_no_map(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    outside = tmp_path / "outside-map"
    outside.mkdir()
    (outside / "map.json").write_text(
        json.dumps(VALID_MAP, ensure_ascii=False), encoding="utf-8"
    )
    (notes / "map").symlink_to(outside)
    assert read_map_document(notes) is None


def test_map_symlinked_inside_notes_is_read(tmp_path):
    # notes/ の中を指すシンボリックリンクは、ほかの文書と同じく、その先の文書として読む
    notes = make_notes(tmp_path)
    (notes / "map" / "map-real.json").write_text(
        json.dumps(VALID_MAP, ensure_ascii=False), encoding="utf-8"
    )
    (notes / "map" / "map.json").symlink_to(notes / "map" / "map-real.json")
    assert read_map_document(notes) == VALID_DOCUMENT


# --- マップの型 (layout) ---


def test_map_without_layout_is_read_as_free():
    document = parse_map_document(valid_map())
    assert isinstance(document, MapDocument)
    assert document.layout is MapLayout.FREE
    assert [layout.value for layout in MapLayout] == ["grid", "free"]


def test_free_layout_is_read_as_free():
    assert parse_map_document({"layout": "free", **valid_map()}) == VALID_DOCUMENT


@pytest.mark.parametrize(
    "layout",
    [None, 1, True, ["grid"], {"type": "grid"}, "", "GRID", "Free", " grid", "hex"],
)
def test_layout_that_is_not_grid_or_free_is_an_error(layout):
    # 文字列でない、または "grid"・"free" 以外 (大文字と小文字を区別する) の layout は形に合わない
    with pytest.raises(ValueError):
        parse_map_document({"layout": layout, **valid_map()})


def test_unknown_layout_is_no_map(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "map" / "map.json").write_text(
        json.dumps({"layout": "hex", **VALID_MAP}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert read_map_document(notes) is None


# --- マス目型 (grid) ---

# issue の I/O 契約の例 (grid) に、図の無い場所と座標の無いつながりを 1 つずつ足したもの
VALID_GRID_MAP = {
    "layout": "grid",
    "places": [
        {
            "name": "家の 1 階",
            "aliases": ["1 階の部屋"],
            "description": "自分の部屋から階段で下りた先の部屋",
            "state": "訪問済み。出口は未通過",
            "changes_on_entry": False,
            "origin": {"x": 0, "y": 0},
            "tiles": ["#########", "#.......#", "#E......#"],
            "exits": [{"x": 1, "y": 2, "description": "左下の赤いマット"}],
            "blocked": [{"x": 4, "y": 1, "description": "机"}],
            "landmarks": [{"x": 6, "y": 1, "description": "テレビ"}],
        },
        {
            "name": "自分の部屋 (2 階)",
            "aliases": [],
            "description": "",
            "state": "訪問済み",
            "changes_on_entry": True,
            "origin": {"x": -2, "y": -1},
            "tiles": [],
            "exits": [],
            "blocked": [],
            "landmarks": [],
        },
    ],
    "connections": [
        {
            "from": "自分の部屋 (2 階)",
            "to": "家の 1 階",
            "how": "部屋の右上の階段を上に",
            "from_tile": {"x": 7, "y": 1},
            "to_tile": {"x": 7, "y": 1},
        },
        {
            "from": "家の 1 階",
            "to": "町",
            "how": "左下の赤いマット",
            "from_tile": None,
            "to_tile": None,
        },
    ],
}
VALID_GRID_DOCUMENT = GridMapDocument(
    places=(
        GridMapPlace(
            name="家の 1 階",
            aliases=("1 階の部屋",),
            description="自分の部屋から階段で下りた先の部屋",
            state="訪問済み。出口は未通過",
            changes_on_entry=False,
            origin=TilePosition(0, 0),
            tiles=("#########", "#.......#", "#E......#"),
            exits=(TileMark(TilePosition(1, 2), "左下の赤いマット"),),
            blocked=(TileMark(TilePosition(4, 1), "机"),),
            landmarks=(TileMark(TilePosition(6, 1), "テレビ"),),
        ),
        GridMapPlace(
            name="自分の部屋 (2 階)",
            aliases=(),
            description="",
            state="訪問済み",
            changes_on_entry=True,
            origin=TilePosition(-2, -1),
            tiles=(),
            exits=(),
            blocked=(),
            landmarks=(),
        ),
    ),
    connections=(
        MapConnection(
            from_place="自分の部屋 (2 階)",
            to_place="家の 1 階",
            how="部屋の右上の階段を上に",
            from_tile=TilePosition(7, 1),
            to_tile=TilePosition(7, 1),
        ),
        MapConnection(from_place="家の 1 階", to_place="町", how="左下の赤いマット"),
    ),
)
GRID_PLACE_KEYS = [
    "name",
    "aliases",
    "description",
    "state",
    "changes_on_entry",
    "origin",
    "tiles",
    "exits",
    "blocked",
    "landmarks",
]
GRID_CONNECTION_KEYS = ["from", "to", "how", "from_tile", "to_tile"]


def valid_grid_map() -> dict:
    """VALID_GRID_MAP の深いコピー (テストの中で書き換えるため)。"""
    return copy.deepcopy(VALID_GRID_MAP)


def assert_valid_grid_map_parses() -> None:
    """VALID_GRID_MAP が読めることを先に確かめる (誤りのテストが、別の理由の ValueError で通らないように)。"""
    assert parse_map_document(valid_grid_map()) == VALID_GRID_DOCUMENT


def test_parse_valid_grid_map():
    document = parse_map_document(valid_grid_map())
    assert isinstance(document, GridMapDocument)
    assert document.layout is MapLayout.GRID
    assert document == VALID_GRID_DOCUMENT


def test_tile_characters():
    assert TILE_CHARACTERS == frozenset({"#", ".", "E", "?"})


def test_grid_map_with_negative_origin_and_marks_outside_the_tiles():
    # origin は負でもよい。exits・blocked・landmarks の座標は tiles の範囲の外でもよい
    value = valid_grid_map()
    place = value["places"][0]
    place["origin"] = {"x": -3, "y": -2}
    place["exits"].append({"x": 20, "y": -9, "description": "画面の外の通り道"})
    place["landmarks"].append({"x": -100, "y": 100, "description": "遠くの塔"})
    document = parse_map_document(value)
    assert document.places[0].origin == TilePosition(-3, -2)
    assert document.places[0].exits[-1] == TileMark(
        TilePosition(20, -9), "画面の外の通り道"
    )
    assert document.places[0].landmarks[-1] == TileMark(
        TilePosition(-100, 100), "遠くの塔"
    )


@pytest.mark.parametrize(
    "tiles",
    [[], ["?"], ["#.E?", "?E.#"], ["", ""]],
    ids=["empty", "one-tile", "all-characters", "empty-rows"],
)
def test_grid_tiles_that_match(tiles):
    # 空の配列は、まだ図を描けない場所。各行の文字数がそろっていれば、文字数が 0 の行も形に合う
    value = valid_grid_map()
    value["places"][0]["tiles"] = tiles
    assert parse_map_document(value).places[0].tiles == tuple(tiles)


def test_other_keys_of_grid_elements_and_coordinates_are_ignored():
    value = valid_grid_map()
    value["places"][0]["origin"]["z"] = 3
    value["places"][0]["exits"][0]["seen_t"] = 1.5
    value["places"][0]["note"] = "deep のメモ"
    value["connections"][0]["from_tile"]["floor"] = 2
    value["connections"][0]["note"] = "暗転する"
    assert parse_map_document(value) == VALID_GRID_DOCUMENT


def with_grid_place(**changes: object) -> dict:
    value = valid_grid_map()
    value["places"][0].update(changes)
    return value


def without_grid_place_key(key: str) -> dict:
    value = valid_grid_map()
    del value["places"][0][key]
    return value


def with_grid_connection(**changes: object) -> dict:
    value = valid_grid_map()
    value["connections"][0].update(changes)
    return value


def without_grid_connection_key(key: str) -> dict:
    value = valid_grid_map()
    del value["connections"][0][key]
    return value


def broken_grid_elements() -> list[dict]:
    """形に合わない要素を 1 つだけ含む、layout が grid の map.json の値。"""
    mark = {"x": 1, "y": 2, "description": "左下の赤いマット"}
    values = [
        # tiles: 行の長さの違い・知らない文字・型
        with_grid_place(tiles=["#########", "#.......", "#E......#"]),
        with_grid_place(tiles=["", "#"]),
        with_grid_place(tiles=["#X#"]),
        with_grid_place(tiles=["#e#"]),
        with_grid_place(tiles=["# #"]),
        with_grid_place(tiles="#########"),
        with_grid_place(tiles=["###", 1]),
        with_grid_place(tiles=None),
        # origin: 小数点を含む数・真偽・文字列・キーの欠け・object でない
        with_grid_place(origin={"x": 1.0, "y": 0}),
        with_grid_place(origin={"x": 0, "y": 0.5}),
        with_grid_place(origin={"x": True, "y": 0}),
        with_grid_place(origin={"x": 0, "y": False}),
        with_grid_place(origin={"x": "0", "y": 0}),
        with_grid_place(origin={"x": 0}),
        with_grid_place(origin={"y": 0}),
        with_grid_place(origin=[0, 0]),
        with_grid_place(origin=None),
        # exits・blocked・landmarks の要素
        with_grid_place(exits=["左下の赤いマット"]),
        with_grid_place(exits=[{"x": 1, "y": 2}]),
        with_grid_place(exits=[{**mark, "description": None}]),
        with_grid_place(exits=[{**mark, "x": 1.0}]),
        with_grid_place(exits=[{**mark, "y": True}]),
        with_grid_place(blocked=[{"y": 1, "description": "机"}]),
        with_grid_place(blocked=[None]),
        with_grid_place(landmarks=[{"x": 6, "description": "テレビ"}]),
        with_grid_place(landmarks="テレビ"),
        # free と同じキーの型
        with_grid_place(name="  "),
        with_grid_place(aliases=[1]),
        with_grid_place(changes_on_entry="false"),
        # つながりの from_tile・to_tile
        with_grid_connection(from_tile={"x": 7}),
        with_grid_connection(from_tile={"x": 7.0, "y": 1}),
        with_grid_connection(from_tile={"x": False, "y": 1}),
        with_grid_connection(to_tile="7,1"),
        with_grid_connection(to_tile=[7, 1]),
        with_grid_connection(to_tile=True),
        with_grid_connection(to_tile=1),
        with_grid_connection(**{"from": ""}),
    ]
    values += [without_grid_place_key(key) for key in GRID_PLACE_KEYS]
    values += [without_grid_connection_key(key) for key in GRID_CONNECTION_KEYS]
    return values


@pytest.mark.parametrize("value", broken_grid_elements())
def test_one_grid_element_that_does_not_match_makes_the_whole_map_an_error(value):
    assert_valid_grid_map_parses()
    with pytest.raises(ValueError):
        parse_map_document(value)


def test_grid_layout_with_a_free_place_is_an_error():
    # layout が grid なら、すべての場所を grid の形で確かめる (free の場所が混ざると形に合わない)
    assert_valid_grid_map_parses()
    value = valid_grid_map()
    value["places"].append(VALID_MAP["places"][1])
    with pytest.raises(ValueError):
        parse_map_document(value)


def test_grid_layout_with_a_free_connection_is_an_error():
    assert_valid_grid_map_parses()
    value = valid_grid_map()
    value["connections"].append(VALID_MAP["connections"][1])
    with pytest.raises(ValueError):
        parse_map_document(value)


def test_free_layout_with_a_grid_place_is_an_error():
    # layout が free なら、すべての場所を free の形で確かめる (grid の exits は文字列の配列でない)
    value = {"layout": "free", **valid_map()}
    value["places"].append(VALID_GRID_MAP["places"][0])
    with pytest.raises(ValueError):
        parse_map_document(value)


def test_grid_map_without_layout_is_an_error():
    # layout が無い map.json は free として読むため、grid の場所は形に合わない
    value = valid_grid_map()
    del value["layout"]
    with pytest.raises(ValueError):
        parse_map_document(value)


def test_free_connection_ignores_tile_keys():
    # free のつながりの from_tile・to_tile は、ほかのキーと同じく無視する
    value = {"layout": "free", **valid_map()}
    value["connections"][0]["from_tile"] = {"x": 7, "y": 1}
    value["connections"][0]["to_tile"] = None
    document = parse_map_document(value)
    assert document == VALID_DOCUMENT
    assert document.connections[0].from_tile is None


def test_grid_map_document_to_json_round_trip():
    value = map_document_to_json(VALID_GRID_DOCUMENT)
    assert value == VALID_GRID_MAP
    assert list(value) == ["layout", "places", "connections"]
    assert all(list(place) == GRID_PLACE_KEYS for place in value["places"])
    assert all(list(item) == GRID_CONNECTION_KEYS for item in value["connections"])
    place = value["places"][0]
    assert list(place["origin"]) == ["x", "y"]
    for key in ["exits", "blocked", "landmarks"]:
        assert all(list(mark) == ["x", "y", "description"] for mark in place[key])
    assert isinstance(place["tiles"], list)
    assert list(value["connections"][0]["from_tile"]) == ["x", "y"]
    assert value["connections"][1]["to_tile"] is None
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value
    assert parse_map_document(value) == VALID_GRID_DOCUMENT


def test_grid_map_document_to_json_of_empty_map():
    assert map_document_to_json(GridMapDocument(places=(), connections=())) == {
        "layout": "grid",
        "places": [],
        "connections": [],
    }


def test_tile_position_to_json():
    value = tile_position_to_json(TilePosition(-1, 2))
    assert value == {"x": -1, "y": 2}
    assert list(value) == ["x", "y"]


def test_read_valid_grid_map(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "map" / "map.json").write_text(
        json.dumps(VALID_GRID_MAP, ensure_ascii=False), encoding="utf-8"
    )
    assert read_map_document(notes) == VALID_GRID_DOCUMENT


def test_grid_map_with_float_coordinate_is_no_map(tmp_path):
    # JSON の 1.0 は小数点を含む数なので、座標の整数として読まない
    assert_valid_grid_map_parses()
    notes = make_notes(tmp_path)
    text = json.dumps(VALID_GRID_MAP, ensure_ascii=False).replace(
        '"origin": {"x": 0, "y": 0}', '"origin": {"x": 0.0, "y": 0}'
    )
    assert '"x": 0.0' in text
    (notes / "map" / "map.json").write_text(text, encoding="utf-8")
    assert read_map_document(notes) is None
