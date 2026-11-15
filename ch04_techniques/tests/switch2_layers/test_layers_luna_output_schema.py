import json

import pytest

from switch2_layers.middle.luna_output_schema import (
    MIDDLE_OUTPUT_SCHEMA,
    OUTPUT_FORMAT_NAME,
    build_text_format,
    parse_middle_output,
)
from switch2_layers.middle.middle_types import MiddleOutput

OUTPUT_KEYS = [
    "scene_name",
    "is_stuck",
    "stuck_description",
    "surface_wait_s",
    "timeline",
    "tasks",
]
VALID_OUTPUT = {
    "scene_name": "フィールド",
    "is_stuck": True,
    "stuck_description": "t=100.000 から 12 秒間、壁に向かって歩き続けている",
    "surface_wait_s": 0.5,
    "timeline": "## 操作のまとめ\n- 上を押し続けた",
    "tasks": "- 右の扉に入る\n- B を試す\n",
}


def without_descriptions(schema: dict) -> dict:
    """スキーマの properties から description を除いたもの (型と制約だけを比べる)。"""
    properties = {
        name: {key: value for key, value in prop.items() if key != "description"}
        for name, prop in schema["properties"].items()
    }
    return {**schema, "properties": properties}


# --- スキーマ ---


def test_schema_has_six_required_fields_without_additional_properties():
    assert without_descriptions(MIDDLE_OUTPUT_SCHEMA) == {
        "type": "object",
        "properties": {
            "scene_name": {"type": "string"},
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


def test_parse_valid_output():
    assert parse_middle_output(output_text()) == MiddleOutput(
        scene_name="フィールド",
        is_stuck=True,
        stuck_description="t=100.000 から 12 秒間、壁に向かって歩き続けている",
        surface_wait_s=0.5,
        timeline="## 操作のまとめ\n- 上を押し続けた",
        tasks="- 右の扉に入る\n- B を試す\n",
    )


@pytest.mark.parametrize("scene_name", ["", "   ", "\n"])
def test_blank_scene_name_means_undecided(scene_name):
    assert parse_middle_output(output_text(scene_name=scene_name)).scene_name is None


@pytest.mark.parametrize("wait", [0.2, 2.0, 2, 1])
def test_wait_at_the_boundaries_is_accepted(wait):
    result = parse_middle_output(output_text(surface_wait_s=wait))
    assert result.surface_wait_s == float(wait)
    assert isinstance(result.surface_wait_s, float)


@pytest.mark.parametrize("wait", [0.19, 2.01, 0, -1, 10])
def test_wait_out_of_range_is_an_error(wait):
    with pytest.raises(ValueError):
        parse_middle_output(output_text(surface_wait_s=wait))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_wait_is_an_error(literal):
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
    ],
)
def test_wrong_types_are_errors(changes):
    with pytest.raises(ValueError):
        parse_middle_output(output_text(**changes))


@pytest.mark.parametrize("missing", OUTPUT_KEYS)
def test_missing_key_is_an_error(missing):
    value = {key: item for key, item in VALID_OUTPUT.items() if key != missing}
    with pytest.raises(ValueError):
        parse_middle_output(json.dumps(value, ensure_ascii=False))


def test_extra_key_is_an_error():
    with pytest.raises(ValueError):
        parse_middle_output(output_text(map_note="町の北に橋"))


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '"text"', "null", "{"])
def test_text_that_is_not_a_json_object_is_an_error(text):
    with pytest.raises(ValueError):
        parse_middle_output(text)
