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
from switch2_layers.notes.map_document import MapConnection

# 出力のキー。場所 (place_name・new_connections・new_observations) は、短期タスクリスト (tasks) より前に書かせる
OUTPUT_KEYS = [
    "scene_name",
    "place_name",
    "new_connections",
    "new_observations",
    "is_stuck",
    "stuck_description",
    "surface_wait_s",
    "timeline",
    "tasks",
]
VALID_OUTPUT = {
    "scene_name": "フィールド",
    "place_name": "家の 1 階",
    "new_connections": [
        {"from": "自分の部屋 (2 階)", "to": "家の 1 階", "how": "右上の階段を下に"}
    ],
    "new_observations": [
        {"kind": "exit", "place": "家の 1 階", "description": "左下の赤いマット"},
        {"kind": "landmark", "place": "家の 1 階", "description": "中央のテーブル"},
        {"kind": "blocked", "place": "家の 1 階", "description": "下側の壁"},
    ],
    "is_stuck": True,
    "stuck_description": "t=100.000 から 12 秒間、壁に向かって歩き続けている",
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
    new_connections=(
        MapConnection(
            from_place="自分の部屋 (2 階)", to_place="家の 1 階", how="右上の階段を下に"
        ),
    ),
    new_observations=(
        MapObservation(
            kind=ObservationKind.EXIT, place="家の 1 階", description="左下の赤いマット"
        ),
        MapObservation(
            kind=ObservationKind.LANDMARK,
            place="家の 1 階",
            description="中央のテーブル",
        ),
        MapObservation(
            kind=ObservationKind.BLOCKED, place="家の 1 階", description="下側の壁"
        ),
    ),
)


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


def test_schema_has_nine_required_fields_without_additional_properties():
    assert without_descriptions(MIDDLE_OUTPUT_SCHEMA) == {
        "type": "object",
        "properties": {
            "scene_name": {"type": "string"},
            "place_name": {"type": "string"},
            "new_connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "how": {"type": "string"},
                    },
                    "required": ["from", "to", "how"],
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
                    },
                    "required": ["kind", "place", "description"],
                    "additionalProperties": False,
                },
            },
            "is_stuck": {"type": "boolean"},
            "stuck_description": {"type": "string"},
            "surface_wait_s": {"type": "number", "minimum": 0.2, "maximum": 2.0},
            "timeline": {"type": "string"},
            "tasks": {"type": "string"},
        },
        "required": OUTPUT_KEYS,
        "additionalProperties": False,
    }
    # 出力はスキーマのキーの順に書かれる
    assert list(MIDDLE_OUTPUT_SCHEMA["properties"]) == OUTPUT_KEYS
    assert json.loads(json.dumps(MIDDLE_OUTPUT_SCHEMA)) == MIDDLE_OUTPUT_SCHEMA


def test_map_fields_have_descriptions():
    properties = MIDDLE_OUTPUT_SCHEMA["properties"]
    for key in ["place_name", "new_connections", "new_observations"]:
        assert properties[key]["description"], key


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
        output_text(place_name="", new_connections=[], new_observations=[])
    )
    assert result.place_name is None
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
        {"from": "町", "to": "草原", "how": "北の出口"},
        {"from": "草原", "to": "洞窟", "how": "右上の穴"},
    ]
    result = parse_middle_output(output_text(new_connections=connections))
    assert [item.to_place for item in result.new_connections] == ["草原", "洞窟"]


@pytest.mark.parametrize("kind", ["door", "", "EXIT", "Exit", None, 1, ["exit"]])
def test_wrong_observation_kind_is_an_error(kind):
    assert_valid_output_parses()
    observations = [{"kind": kind, "place": "家の 1 階", "description": "右の扉"}]
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_observations=observations))


def wrong_connections() -> list[object]:
    good = {"from": "町", "to": "草原", "how": "北の出口"}
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
    ]


@pytest.mark.parametrize("connections", wrong_connections())
def test_wrong_connection_items_are_errors(connections):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_connections=connections))


def wrong_observations() -> list[object]:
    good = {"kind": "exit", "place": "町", "description": "北の出口"}
    return [
        ["exit"],
        [{"kind": "exit", "place": "町"}],
        [{"place": "町", "description": "北の出口"}],
        [{**good, "seen_t": 1.0}],
        [{**good, "place": None}],
        [{**good, "description": 3}],
        [good, None],
    ]


@pytest.mark.parametrize("observations", wrong_observations())
def test_wrong_observation_items_are_errors(observations):
    assert_valid_output_parses()
    with pytest.raises(ValueError):
        parse_middle_output(output_text(new_observations=observations))
