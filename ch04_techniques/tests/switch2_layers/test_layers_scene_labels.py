import json

import pytest

from switch2_layers.notes.scene_labels import (
    SceneLabel,
    find_or_add_scene,
    find_scene_id,
    next_scene_id,
    normalize_scene_name,
    read_scene_labels,
    scene_labels_to_json,
    write_scene_labels,
)

TOWN = SceneLabel(id="scene-001", name="町", first_seen_t=1.5)
FIELD = SceneLabel(id="scene-002", name="草原", first_seen_t=30.0)


# --- 一覧の読み書き ---


def test_read_missing_file_is_empty(tmp_path):
    assert read_scene_labels(tmp_path / "scene_labels.json") == []


@pytest.mark.parametrize(
    "text",
    ["{broken", "[]", '{"scenes": {}}', '{"other": []}', ""],
)
def test_read_broken_file_is_empty(tmp_path, text):
    path = tmp_path / "scene_labels.json"
    path.write_text(text, encoding="utf-8")
    assert read_scene_labels(path) == []


def test_read_skips_invalid_elements(tmp_path):
    path = tmp_path / "scene_labels.json"
    path.write_text(
        json.dumps(
            {
                "scenes": [
                    {"id": "scene-001", "name": "町", "first_seen_t": 1.5},
                    {"id": "../x", "name": "外", "first_seen_t": 2.0},
                    {"id": "scene-003", "name": 3, "first_seen_t": 2.0},
                    {"id": "scene-004", "name": "a", "first_seen_t": True},
                    {"id": "scene-005", "name": "b"},
                    "scene-006",
                    {"id": "scene-002", "name": "草原", "first_seen_t": 30},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert read_scene_labels(path) == [TOWN, SceneLabel("scene-002", "草原", 30)]


def test_write_then_read_round_trip(tmp_path):
    path = tmp_path / "scene_labels.json"
    write_scene_labels(path, [TOWN, FIELD])
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "scenes": [
            {"id": "scene-001", "name": "町", "first_seen_t": 1.5},
            {"id": "scene-002", "name": "草原", "first_seen_t": 30.0},
        ]
    }
    assert read_scene_labels(path) == [TOWN, FIELD]


def test_scene_labels_to_json_of_empty_list():
    assert scene_labels_to_json([]) == {"scenes": []}


# --- 名前から場面 ID を引く・無ければ足す ---


def test_normalize_scene_name():
    assert normalize_scene_name("  町 \n") == "町"


def test_find_scene_id():
    labels = [TOWN, FIELD]
    assert find_scene_id(labels, "草原") == "scene-002"
    assert find_scene_id(labels, " 町 ") == "scene-001"
    assert find_scene_id(labels, "洞窟") is None
    assert find_scene_id([], "町") is None


def test_next_scene_id():
    assert next_scene_id([]) == "scene-001"
    assert next_scene_id([TOWN, FIELD]) == "scene-003"
    assert next_scene_id([SceneLabel("scene-007", "x", 0.0), TOWN]) == "scene-008"
    assert next_scene_id([SceneLabel("scene-999", "x", 0.0)]) == "scene-1000"


def test_find_or_add_scene_adds_new_scene(tmp_path):
    path = tmp_path / "scene_labels.json"
    path.write_text('{"scenes": []}', encoding="utf-8")
    label, added = find_or_add_scene(path, " 町 ", 1.5)
    assert added is True
    assert label == TOWN
    assert read_scene_labels(path) == [TOWN]
    label, added = find_or_add_scene(path, "草原", 30.0)
    assert (label, added) == (FIELD, True)
    assert read_scene_labels(path) == [TOWN, FIELD]


def test_find_or_add_scene_returns_existing_scene_without_writing(tmp_path):
    path = tmp_path / "scene_labels.json"
    write_scene_labels(path, [TOWN])
    before = path.read_bytes()
    label, added = find_or_add_scene(path, "町", 99.0)
    assert (label, added) == (TOWN, False)
    assert path.read_bytes() == before


def test_find_or_add_scene_creates_missing_file(tmp_path):
    path = tmp_path / "scene_labels.json"
    label, added = find_or_add_scene(path, "町", 1.5)
    assert (label, added) == (TOWN, True)
    assert read_scene_labels(path) == [TOWN]


@pytest.mark.parametrize("name", ["", "   ", "\n"])
def test_find_or_add_scene_rejects_blank_name(tmp_path, name):
    path = tmp_path / "scene_labels.json"
    path.write_text('{"scenes": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        find_or_add_scene(path, name, 1.0)
    assert path.read_text(encoding="utf-8") == '{"scenes": []}'
