import pytest

from switch2_layers.deep.deep_types import DocumentKey, DocumentKind
from switch2_layers.notes.note_paths import (
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


def test_deep_document_notes_for_map_lists_files(tmp_path):
    key = DocumentKey(DocumentKind.MAP, None)
    assert deep_document_notes(tmp_path, key) == []
    (tmp_path / "map" / "town").mkdir(parents=True)
    assert deep_document_notes(tmp_path, key) == []
    (tmp_path / "map" / "places.md").write_text("# 場所\n", encoding="utf-8")
    (tmp_path / "map" / "town" / "house.md").write_text("# 家\n", encoding="utf-8")
    assert deep_document_notes(tmp_path, key) == ["map/places.md", "map/town/house.md"]


def test_map_listing_skips_temporary_and_hidden_files(tmp_path):
    key = DocumentKey(DocumentKind.MAP, None)
    map_dir = tmp_path / "map"
    (map_dir / "town").mkdir(parents=True)
    (map_dir / ".hidden").mkdir()
    (map_dir / "places.md").write_text("# 場所\n", encoding="utf-8")
    # 置き換えの書き込みの途中 (や kill された後) に残る一時ファイル
    (map_dir / ".places.md.x7k2q.tmp").write_text("書きかけ", encoding="utf-8")
    (map_dir / "town" / ".house.md.a1b2c.tmp").write_text("書きかけ", encoding="utf-8")
    # `.` で始まる名前と、その下のファイル
    (map_dir / ".notes").write_text("x", encoding="utf-8")
    (map_dir / ".hidden" / "inside.md").write_text("x", encoding="utf-8")
    (map_dir / "town" / "house.md").write_text("# 家\n", encoding="utf-8")
    assert deep_document_notes(tmp_path, key) == ["map/places.md", "map/town/house.md"]
