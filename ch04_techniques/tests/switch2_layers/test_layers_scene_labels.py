import json

import pytest

from switch2_layers.notes.scene_labels import (
    SceneLabel,
    add_scene_label,
    find_scene_id,
    next_scene_id,
    normalize_scene_name,
    read_scene_labels,
    resolve_scene,
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


# --- 名前から場面 ID を引く ---


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


def test_resolve_scene_returns_new_scene_without_changing_labels():
    labels = [TOWN]
    label, is_new = resolve_scene(labels, " 草原 ", 30.0)
    assert (label, is_new) == (FIELD, True)
    # 一覧は書き換えない
    assert labels == [TOWN]
    assert resolve_scene([], "町", 1.5) == (TOWN, True)


def test_resolve_scene_returns_existing_scene():
    assert resolve_scene([TOWN, FIELD], " 町 ", 99.0) == (TOWN, False)


@pytest.mark.parametrize("name", ["", "   ", "\n"])
def test_resolve_scene_rejects_blank_name(name):
    with pytest.raises(ValueError):
        resolve_scene([TOWN], name, 1.0)


# --- 一覧に足す ---


def test_add_scene_label_appends_and_writes(tmp_path):
    path = tmp_path / "scene_labels.json"
    path.write_text('{"scenes": []}', encoding="utf-8")
    assert add_scene_label(path, TOWN) is True
    assert add_scene_label(path, FIELD) is True
    assert read_scene_labels(path) == [TOWN, FIELD]


def test_add_scene_label_does_not_add_same_name_twice(tmp_path):
    path = tmp_path / "scene_labels.json"
    write_scene_labels(path, [TOWN])
    before = path.read_bytes()
    assert add_scene_label(path, SceneLabel("scene-002", " 町 ", 5.0)) is False
    assert path.read_bytes() == before


def test_add_scene_label_creates_missing_file(tmp_path):
    path = tmp_path / "scene_labels.json"
    assert add_scene_label(path, TOWN) is True
    assert read_scene_labels(path) == [TOWN]


def test_resolve_then_add_gives_the_same_id_as_resolving_again(tmp_path):
    # 足す前に同じ名前を引き直しても、同じ新しい ID になる (書き込みに失敗した後の再試行と同じ)
    path = tmp_path / "scene_labels.json"
    write_scene_labels(path, [TOWN])
    first, _ = resolve_scene(read_scene_labels(path), "草原", 30.0)
    again, is_new = resolve_scene(read_scene_labels(path), "草原", 40.0)
    assert is_new is True
    assert again.id == first.id == "scene-002"
    add_scene_label(path, first)
    assert resolve_scene(read_scene_labels(path), "草原", 50.0) == (FIELD, False)
