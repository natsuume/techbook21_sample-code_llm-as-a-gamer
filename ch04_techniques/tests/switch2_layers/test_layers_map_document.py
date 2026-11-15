import copy
import json
from pathlib import Path

import pytest

from switch2_layers.notes.map_document import (
    MapConnection,
    MapDocument,
    MapPlace,
    map_document_to_json,
    parse_map_document,
    read_map_document,
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
    assert value == VALID_MAP
    assert list(value) == ["places", "connections"]
    assert all(list(place) == PLACE_KEYS for place in value["places"])
    assert all(list(item) == CONNECTION_KEYS for item in value["connections"])
    assert isinstance(value["places"][0]["aliases"], list)
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value
    assert parse_map_document(value) == VALID_DOCUMENT


def test_map_document_to_json_of_empty_map():
    assert map_document_to_json(MapDocument(places=(), connections=())) == {
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
