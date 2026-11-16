import os

import pytest

from switch2_layers.deep.deep_types import DocumentKey, DocumentKind
from switch2_layers.notes.note_paths import (
    BOX_TARGETS_FILE,
    GAME_NOTE,
    MAP_JSON_NOTE,
    MAP_PLACES_NOTE,
    box_targets_note,
    common_note,
    deep_document_notes,
    document_key,
    is_scene_id,
    scene_note,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("scene-001", True),
        ("scene-999", True),
        ("scene-1000", True),
        ("scene-01", False),
        ("scene-abc", False),
        ("Scene-001", False),
        ("scene-001/..", False),
        ("../scene-001", False),
        ("scene-001 ", False),
        ("", False),
    ],
)
def test_is_scene_id(text, expected):
    assert is_scene_id(text) is expected


def test_common_note():
    assert common_note(DocumentKind.CONTROLS) == "common/controls.md"
    assert common_note(DocumentKind.TIPS) == "common/tips.md"
    with pytest.raises(ValueError):
        common_note(DocumentKind.STRATEGY)


def test_scene_note():
    assert scene_note("scene-001", "controls") == "scenes/scene-001/controls.md"
    assert scene_note("scene-012", "tips") == "scenes/scene-012/tips.md"
    assert scene_note("scene-012", "tasks") == "scenes/scene-012/tasks.md"
    with pytest.raises(ValueError):
        scene_note("../../etc", "tasks")


def test_document_key_keeps_scene_only_for_controls_and_tips():
    assert document_key(DocumentKind.CONTROLS, "scene-001") == DocumentKey(
        DocumentKind.CONTROLS, "scene-001"
    )
    assert document_key(DocumentKind.TIPS, None) == DocumentKey(DocumentKind.TIPS, None)
    for kind in [DocumentKind.STRATEGY, DocumentKind.ADVICE, DocumentKind.MAP]:
        assert document_key(kind, "scene-001") == DocumentKey(kind, None)


def test_document_key_rejects_invalid_scene_for_scene_documents():
    with pytest.raises(ValueError):
        document_key(DocumentKind.CONTROLS, "../x")


def test_deep_document_notes_for_scene_documents(tmp_path):
    key = DocumentKey(DocumentKind.CONTROLS, "scene-001")
    # 場面の文書と共通の文書の両方 (まだ無くてもパスを返す)
    assert deep_document_notes(tmp_path, key) == [
        "scenes/scene-001/controls.md",
        "common/controls.md",
    ]
    assert deep_document_notes(tmp_path, DocumentKey(DocumentKind.TIPS, None)) == [
        "common/tips.md"
    ]


def test_deep_document_notes_for_scene_independent_documents(tmp_path):
    assert deep_document_notes(tmp_path, DocumentKey(DocumentKind.STRATEGY, None)) == [
        "strategy.md"
    ]
    assert deep_document_notes(tmp_path, DocumentKey(DocumentKind.ADVICE, None)) == [
        "advice.md"
    ]


def test_deep_document_notes_for_map_are_map_json_and_places_md(tmp_path):
    assert MAP_JSON_NOTE == "map/map.json"
    assert MAP_PLACES_NOTE == "map/places.md"
    key = DocumentKey(DocumentKind.MAP, None)
    # まだ無くてもパスを返す (deep が Write で作る)。notes/map/ が無くても同じ
    assert deep_document_notes(tmp_path, key) == ["map/map.json", "map/places.md"]
    (tmp_path / "map").mkdir()
    assert deep_document_notes(tmp_path, key) == ["map/map.json", "map/places.md"]


def test_map_documents_do_not_depend_on_files_in_map_directory(tmp_path):
    # notes/map/ の下のほかのファイル (場所ごとのファイル・一時ファイル・隠しファイル) は対象にしない
    key = DocumentKey(DocumentKind.MAP, None)
    map_dir = tmp_path / "map"
    (map_dir / "town").mkdir(parents=True)
    (map_dir / "places.md").write_text("# 場所\n", encoding="utf-8")
    (map_dir / "map.json").write_text(
        '{"places": [], "connections": []}', encoding="utf-8"
    )
    (map_dir / ".places.md.x7k2q.tmp").write_text("書きかけ", encoding="utf-8")
    (map_dir / ".notes").write_text("x", encoding="utf-8")
    (map_dir / "town" / "house.md").write_text("# 家\n", encoding="utf-8")
    assert deep_document_notes(tmp_path, key) == ["map/map.json", "map/places.md"]


# --- box で取るもの ---


def test_box_targets_note():
    assert BOX_TARGETS_FILE == "box_targets.md"
    assert box_targets_note("scene-001") == "scenes/scene-001/box_targets.md"
    with pytest.raises(ValueError):
        box_targets_note("../x")


def test_scene_tips_job_also_targets_box_targets(tmp_path):
    key = DocumentKey(DocumentKind.TIPS, "scene-002")
    assert deep_document_notes(tmp_path, key) == [
        "scenes/scene-002/tips.md",
        "scenes/scene-002/box_targets.md",
        "common/tips.md",
    ]


def test_common_tips_and_scene_controls_jobs_do_not_target_box_targets(tmp_path):
    assert deep_document_notes(tmp_path, DocumentKey(DocumentKind.TIPS, None)) == [
        "common/tips.md"
    ]
    assert deep_document_notes(
        tmp_path, DocumentKey(DocumentKind.CONTROLS, "scene-002")
    ) == ["scenes/scene-002/controls.md", "common/controls.md"]


# --- map の文書とリンク ---


def test_map_documents_do_not_list_symlinks_in_map_directory(tmp_path):
    # notes/map/ の下の、外を指すリンクを対象に入れない (対象の文書は 2 つに固定)。
    # 対象の文書そのものがリンクなら、deep のジョブが起動の前に拒む (deep.claude_code_runner)
    notes = tmp_path / "notes"
    map_dir = notes / "map"
    map_dir.mkdir(parents=True)
    (map_dir / "places.md").write_text("# 場所\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n", encoding="utf-8")
    os.symlink(outside / "secret.md", map_dir / "linked.md")
    os.symlink(outside, map_dir / "linked-dir")
    key = DocumentKey(DocumentKind.MAP, None)
    assert deep_document_notes(notes, key) == ["map/map.json", "map/places.md"]


# --- ゲームの性質 ---


def test_game_note_is_game_md():
    assert GAME_NOTE == "game.md"


def test_game_document_does_not_depend_on_the_scene():
    for scene_id in [None, "scene-001"]:
        assert document_key(DocumentKind.GAME, scene_id) == DocumentKey(
            DocumentKind.GAME, None
        )
    # 場面ごとの文書も共通の文書も無い
    with pytest.raises(ValueError):
        common_note(DocumentKind.GAME)


def test_deep_document_notes_for_game_is_only_game_md(tmp_path):
    key = DocumentKey(DocumentKind.GAME, None)
    # まだ無くてもパスを返す (deep が Write で作る)
    assert deep_document_notes(tmp_path, key) == ["game.md"]
    # マップの文書があっても、マップの 2 文書は対象にしない
    map_dir = tmp_path / "map"
    map_dir.mkdir()
    (map_dir / "map.json").write_text(
        '{"places": [], "connections": []}', encoding="utf-8"
    )
    (map_dir / "places.md").write_text("# 場所\n", encoding="utf-8")
    (tmp_path / "game.md").write_text("## ジャンル\nRPG\n", encoding="utf-8")
    notes = deep_document_notes(tmp_path, key)
    assert notes == [GAME_NOTE]
    assert MAP_JSON_NOTE not in notes
    assert MAP_PLACES_NOTE not in notes
