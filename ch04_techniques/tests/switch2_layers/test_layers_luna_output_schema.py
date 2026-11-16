import json

import pytest

from switch2_layers.middle.luna_output_schema import (
    MIDDLE_OUTPUT_SCHEMA,
    OUTPUT_FORMAT_NAME,
    build_text_format,
    parse_middle_output,
)
from switch2_layers.middle.middle_types import (
    MapObservation,
    MiddleOutput,
    ObservationKind,
)
from switch2_layers.notes.map_document import MapConnection, TilePosition

# 出力のキー。場所 (place_name・current_tile・new_connections・new_observations) は、短期タスクリスト (tasks) より前に
# 書かせる。press_home は詰まり (is_stuck・stuck_description) の直後に書かせる
OUTPUT_KEYS = [
    "scene_name",
    "place_name",
    "current_tile",
    "new_connections",
    "new_observations",
    "is_stuck",
    "stuck_description",
    "press_home",
    "surface_wait_s",
    "timeline",
    "tasks",
]
VALID_OUTPUT = {
    "scene_name": "フィールド",
    "place_name": "家の 1 階",
    "current_tile": {"x": 7, "y": 1},
    "new_connections": [
        {
            "from": "自分の部屋 (2 階)",
            "to": "家の 1 階",
            "how": "右上の階段を下に",
            "from_tile": {"x": 7, "y": 0},
            "to_tile": {"x": 7, "y": 1},
        }
    ],
    "new_observations": [
        {
            "kind": "exit",
            "place": "家の 1 階",
            "description": "左下の赤いマット",
            "tile": {"x": 1, "y": 2},
        },
        {
            "kind": "landmark",
            "place": "家の 1 階",
            "description": "中央のテーブル",
            "tile": None,
        },
        {
            "kind": "blocked",
            "place": "家の 1 階",
            "description": "下側の壁",
            "tile": {"x": -1, "y": 3},
        },
    ],
    "is_stuck": True,
    "stuck_description": "t=100.000 から 12 秒間、壁に向かって歩き続けている",
    "press_home": False,
    "surface_wait_s": 0.5,
    "timeline": "## 操作のまとめ\n- 上を押し続けた",
    "tasks": "- 右の扉に入る\n- B を試す\n",
}
VALID_PARSED = MiddleOutput(
    scene_name="フィールド",
    is_stuck=True,
    stuck_description="t=100.000 から 12 秒間、壁に向かって歩き続けている",
    surface_wait_s=0.5,
    timeline="## 操作のまとめ\n- 上を押し続けた",
    tasks="- 右の扉に入る\n- B を試す\n",
    place_name="家の 1 階",
    current_tile=TilePosition(7, 1),
    new_connections=(
        MapConnection(
            from_place="自分の部屋 (2 階)",
            to_place="家の 1 階",
            how="右上の階段を下に",
            from_tile=TilePosition(7, 0),
            to_tile=TilePosition(7, 1),
        ),
    ),
    new_observations=(
        MapObservation(
            kind=ObservationKind.EXIT,
            place="家の 1 階",
            description="左下の赤いマット",
            tile=TilePosition(1, 2),
        ),
        MapObservation(
            kind=ObservationKind.LANDMARK,
            place="家の 1 階",
            description="中央のテーブル",
            tile=None,
        ),
        MapObservation(
            kind=ObservationKind.BLOCKED,
            place="家の 1 階",
            description="下側の壁",
            tile=TilePosition(-1, 3),
        ),
    ),
    press_home=False,
)
# 座標か null の値のスキーマ (説明の注釈を除いたもの)。object と null の union は anyOf で書く
TILE_OR_NULL = {
    "anyOf": [
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        {"type": "null"},
    ]
}


def without_descriptions(node: object) -> object:
    """スキーマから description (説明の注釈) を、入れ子の中も含めて除いたもの (型と制約だけを比べる)。

    properties の中の名前 (例: new_observations の要素の description 項目) は注釈ではないので残す。
    """
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == "properties":
                result[key] = {
                    name: without_descriptions(prop) for name, prop in value.items()
                }
            elif key != "description":
                result[key] = without_descriptions(value)
        return result
    if isinstance(node, list):
        return [without_descriptions(item) for item in node]
    return node


# --- スキーマ ---


def test_schema_has_eleven_required_fields_without_additional_properties():
    assert without_descriptions(MIDDLE_OUTPUT_SCHEMA) == {
        "type": "object",
        "properties": {
            "scene_name": {"type": "string"},
            "place_name": {"type": "string"},
            "current_tile": TILE_OR_NULL,
            "new_connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "how": {"type": "string"},
                        "from_tile": TILE_OR_NULL,
                        "to_tile": TILE_OR_NULL,
                    },
                    "required": ["from", "to", "how", "from_tile", "to_tile"],
                    "additionalProperties": False,
                },
            },
            "new_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["exit", "landmark", "blocked"],
                        },
                        "place": {"type": "string"},
                        "description": {"type": "string"},
                        "tile": TILE_OR_NULL,
                    },
                    "required": ["kind", "place", "description", "tile"],
                    "additionalProperties": False,
                },
            },
            "is_stuck": {"type": "boolean"},
            "stuck_description": {"type": "string"},
            "press_home": {"type": "boolean"},
            "surface_wait_s": {"type": "number", "minimum": 0.2, "maximum": 2.0},
            "timeline": {"type": "string"},
            "tasks": {"type": "string"},
        },
        "required": OUTPUT_KEYS,
        "additionalProperties": False,
    }
    # 出力はスキーマのキーの順に書かれる
    properties = MIDDLE_OUTPUT_SCHEMA["properties"]
    assert list(properties) == OUTPUT_KEYS
    assert list(properties["new_connections"]["items"]["properties"]) == [
        "from",
        "to",
        "how",
        "from_tile",
        "to_tile",
    ]
    assert list(properties["new_observations"]["items"]["properties"]) == [
        "kind",
        "place",
        "description",
        "tile",
    ]
    assert json.loads(json.dumps(MIDDLE_OUTPUT_SCHEMA)) == MIDDLE_OUTPUT_SCHEMA


def test_map_fields_have_descriptions():
    properties = MIDDLE_OUTPUT_SCHEMA["properties"]
    for key in ["place_name", "current_tile", "new_connections", "new_observations"]:
        assert properties[key]["description"], key
    connection = properties["new_connections"]["items"]["properties"]
    observation = properties["new_observations"]["items"]["properties"]
    for item in [connection["from_tile"], connection["to_tile"], observation["tile"]]:
        assert item["description"]


def test_observation_kinds_match_the_schema_enum():
    assert [kind.value for kind in ObservationKind] == ["exit", "landmark", "blocked"]
    items = MIDDLE_OUTPUT_SCHEMA["properties"]["new_observations"]["items"]
    assert items["properties"]["kind"]["enum"] == [
        kind.value for kind in ObservationKind
    ]


def test_text_format_is_strict_json_schema():
    assert OUTPUT_FORMAT_NAME == "middle_output"
    assert build_text_format() == {
        "type": "json_schema",
        "name": "middle_output",
        "schema": MIDDLE_OUTPUT_SCHEMA,
        "strict": True,
    }


# --- 出力の読み取り ---


def output_text(**changes: object) -> str:
    return json.dumps({**VALID_OUTPUT, **changes}, ensure_ascii=False)


def assert_valid_output_parses() -> None:
    """VALID_OUTPUT が読めることを先に確かめる (誤りのテストが、別の理由の ValueError で通らないように)。"""
    assert parse_middle_output(output_text()) == VALID_PARSED


def test_parse_valid_output():
    assert_valid_output_parses()


def test_press_home_field_has_a_description():
    assert MIDDLE_OUTPUT_SCHEMA["properties"]["press_home"]["description"]


@pytest.mark.parametrize("press_home", [True, False])
def test_press_home_is_read(press_home):
    assert parse_middle_output(output_text(press_home=press_home)).press_home is (
        press_home
    )


@pytest.mark.parametrize("scene_name", ["", "   ", "\n"])
def test_blank_scene_name_means_undecided(scene_name):
    assert parse_middle_output(output_text(scene_name=scene_name)).scene_name is None


@pytest.mark.parametrize("wait", [0.2, 2.0, 2, 1])
def test_wait_at_the_boundaries_is_accepted(wait):
    result = parse_middle_output(output_text(surface_wait_s=wait))
    assert result.surface_wait_s == float(wait)
    assert isinstance(result.surface_wait_s, float)


@pytest.mark.parametrize("wait", [0.19, 2.01, 0, -1, 10, 10**400])
def test_wait_out_of_range_is_an_error(wait):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(surface_wait_s=wait))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_wait_is_an_error(literal):
    assert_valid_output_parses()
    text = output_text().replace(
        '"surface_wait_s": 0.5', f'"surface_wait_s": {literal}'
    )
    assert literal in text
    with pytest.raises(ValueError):
        parse_middle_output(text)


@pytest.mark.parametrize(
    "changes",
    [
        {"scene_name": None},
        {"scene_name": 1},
        {"is_stuck": "true"},
        {"is_stuck": 1},
        {"stuck_description": None},
        {"surface_wait_s": "0.5"},
        {"surface_wait_s": True},
        {"timeline": ["a"]},
        {"tasks": None},
        {"place_name": None},
        {"place_name": 1},
        {"new_connections": None},
        {"new_connections": {}},
        {"new_observations": "exit"},
        {"press_home": "true"},
        {"press_home": 1},
        {"press_home": None},
    ],
)
def test_wrong_types_are_errors(changes):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(**changes))


@pytest.mark.parametrize("missing", OUTPUT_KEYS)
def test_missing_key_is_an_error(missing):
    assert_valid_output_parses()
    value = {key: item for key, item in VALID_OUTPUT.items() if key != missing}
    with pytest.raises(ValueError):
        parse_middle_output(json.dumps(value, ensure_ascii=False))


def test_extra_key_is_an_error():
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(map_note="町の北に橋"))


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '"text"', "null", "{"])
def test_text_that_is_not_a_json_object_is_an_error(text):
    with pytest.raises(ValueError):
        parse_middle_output(text)


# --- マップの項目 (place_name・new_connections・new_observations) ---


def test_empty_map_fields():
    result = parse_middle_output(
        output_text(
            place_name="", current_tile=None, new_connections=[], new_observations=[]
        )
    )
    assert result.place_name is None
    assert result.current_tile is None
    assert result.new_connections == ()
    assert result.new_observations == ()
    # ほかの項目はそのまま
    assert result.scene_name == "フィールド"
    assert result.tasks == "- 右の扉に入る\n- B を試す\n"


@pytest.mark.parametrize("place_name", ["", "   ", "\n"])
def test_blank_place_name_means_no_place(place_name):
    result = parse_middle_output(output_text(place_name=place_name))
    assert result.place_name is None
    # 場所が無くても、つながりと観察は読む
    assert result.new_connections == VALID_PARSED.new_connections
    assert result.new_observations == VALID_PARSED.new_observations


def test_items_keep_their_order():
    connections = [
        {
            "from": "町",
            "to": "草原",
            "how": "北の出口",
            "from_tile": None,
            "to_tile": None,
        },
        {
            "from": "草原",
            "to": "洞窟",
            "how": "右上の穴",
            "from_tile": None,
            "to_tile": None,
        },
    ]
    result = parse_middle_output(output_text(new_connections=connections))
    assert [item.to_place for item in result.new_connections] == ["草原", "洞窟"]


@pytest.mark.parametrize("kind", ["door", "", "EXIT", "Exit", None, 1, ["exit"]])
def test_wrong_observation_kind_is_an_error(kind):
    assert_valid_output_parses()
    observations = [
        {"kind": kind, "place": "家の 1 階", "description": "右の扉", "tile": None}
    ]
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_observations=observations))


def wrong_connections() -> list[object]:
    good = {
        "from": "町",
        "to": "草原",
        "how": "北の出口",
        "from_tile": {"x": 3, "y": 0},
        "to_tile": None,
    }
    without_tiles = {"from": "町", "to": "草原", "how": "北の出口"}
    return [
        ["町から草原"],
        [None],
        [{"from": "町", "to": "草原"}],
        [{"to": "草原", "how": "北"}],
        [{**good, "note": "暗転する"}],
        [{**good, "from": None}],
        [{**good, "to": 1}],
        [{**good, "how": ["北"]}],
        [good, "extra"],
        # from_tile と to_tile も required
        [without_tiles],
        [{**without_tiles, "from_tile": None}],
        [{**without_tiles, "to_tile": None}],
        [{**good, "from_tile": {"x": 3}}],
        [{**good, "to_tile": {"x": 1.0, "y": 2}}],
    ]


@pytest.mark.parametrize("connections", wrong_connections())
def test_wrong_connection_items_are_errors(connections):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_connections=connections))


def wrong_observations() -> list[object]:
    good = {"kind": "exit", "place": "町", "description": "北の出口", "tile": None}
    return [
        ["exit"],
        [{"kind": "exit", "place": "町"}],
        [{"place": "町", "description": "北の出口"}],
        [{**good, "seen_t": 1.0}],
        [{**good, "place": None}],
        [{**good, "description": 3}],
        [good, None],
        # tile も required
        [{"kind": "exit", "place": "町", "description": "北の出口"}],
        [{**good, "tile": {"x": True, "y": 0}}],
        [{**good, "tile": "3,0"}],
    ]


@pytest.mark.parametrize("observations", wrong_observations())
def test_wrong_observation_items_are_errors(observations):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_observations=observations))


# --- 座標の項目 (current_tile・from_tile・to_tile・tile) ---


def test_tiles_are_read_as_positions_or_none():
    assert_valid_output_parses()
    result = parse_middle_output(output_text(current_tile={"x": -4, "y": 0}))
    assert result.current_tile == TilePosition(-4, 0)
    assert parse_middle_output(output_text(current_tile=None)).current_tile is None
    # 要素の座標は、出力の順のまま
    assert [item.tile for item in VALID_PARSED.new_observations] == [
        TilePosition(1, 2),
        None,
        TilePosition(-1, 3),
    ]


def test_current_tile_is_kept_when_place_name_is_blank():
    # 出力の値のまま読む (middle/state.json と記録に書くときに、place_name が無ければ null にする)
    result = parse_middle_output(output_text(place_name=" "))
    assert result.place_name is None
    assert result.current_tile == TilePosition(7, 1)


def wrong_tiles() -> list[object]:
    return [
        {"x": 1.0, "y": 2},
        {"x": 1, "y": 2.5},
        {"x": True, "y": 2},
        {"x": 1, "y": False},
        {"x": "1", "y": 2},
        {"x": 1},
        {"y": 2},
        {},
        {"x": 1, "y": 2, "z": 0},
        [1, 2],
        "1,2",
        1,
        True,
    ]


@pytest.mark.parametrize("tile", wrong_tiles())
def test_wrong_current_tile_is_an_error(tile):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(current_tile=tile))


@pytest.mark.parametrize("key", ["from_tile", "to_tile"])
@pytest.mark.parametrize("tile", wrong_tiles())
def test_wrong_connection_tile_is_an_error(key, tile):
    assert_valid_output_parses()
    connections = [{**VALID_OUTPUT["new_connections"][0], key: tile}]
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_connections=connections))


@pytest.mark.parametrize("tile", wrong_tiles())
def test_wrong_observation_tile_is_an_error(tile):
    assert_valid_output_parses()
    observations = [{**VALID_OUTPUT["new_observations"][0], "tile": tile}]
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_observations=observations))


def test_float_tile_literal_is_an_error():
    # JSON の 1.0 は小数点を含む数なので、座標の整数として読まない
    assert_valid_output_parses()
    text = output_text().replace(
        '"current_tile": {"x": 7,', '"current_tile": {"x": 7.0,'
    )
    assert '"x": 7.0' in text
    with pytest.raises(ValueError):
        parse_middle_output(text)
