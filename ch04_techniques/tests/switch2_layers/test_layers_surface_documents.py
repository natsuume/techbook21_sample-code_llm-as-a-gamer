from pathlib import Path

import pytest

from switch2_layers.notes.surface_documents import is_blank, read_surface_documents
from switch2_layers.surface.surface_types import SurfaceDocuments

COMMON_CONTROLS = "# 操作説明 (共通)\n\n- A: unknown\n"
COMMON_TIPS = "# 攻略情報 (共通)\n"
ADVICE = "# 助言\n\n扉の前で止まっている\n"
SCENE_CONTROLS = "# 操作説明 (町)\n\n- A: 話しかける\n"
SCENE_TIPS = "# 攻略情報 (町)\n\n宿屋は北\n"
SCENE_TASKS = "- 宿屋に入る\n"


def make_notes(tmp_path: Path) -> Path:
    """共通の文書と助言だけがある notes/ を作る (パッケージのコードは使わない)。"""
    notes = tmp_path / "notes"
    (notes / "common").mkdir(parents=True)
    (notes / "common" / "controls.md").write_text(COMMON_CONTROLS, encoding="utf-8")
    (notes / "common" / "tips.md").write_text(COMMON_TIPS, encoding="utf-8")
    (notes / "advice.md").write_text(ADVICE, encoding="utf-8")
    return notes


def write_scene(notes: Path, scene_id: str, files: dict[str, str]) -> None:
    scene_dir = notes / "scenes" / scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (scene_dir / name).write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "text, expected", [("", True), (" \n\t", True), ("# x", False)]
)
def test_is_blank(text, expected):
    assert is_blank(text) is expected


def test_no_scene_uses_common_documents(tmp_path):
    notes = make_notes(tmp_path)
    assert read_surface_documents(notes, None) == SurfaceDocuments(
        tasks="",
        tips=COMMON_TIPS,
        controls=COMMON_CONTROLS,
        advice=ADVICE,
        scene_id=None,
        uses_common_controls=True,
        uses_common_tips=True,
    )


def test_scene_without_scene_documents_uses_common_documents(tmp_path):
    notes = make_notes(tmp_path)
    assert read_surface_documents(notes, "scene-001") == SurfaceDocuments(
        tasks="",
        tips=COMMON_TIPS,
        controls=COMMON_CONTROLS,
        advice=ADVICE,
        scene_id="scene-001",
        uses_common_controls=True,
        uses_common_tips=True,
    )


@pytest.mark.parametrize("empty_text", ["", "  \n\n"])
def test_empty_scene_documents_fall_back_to_common(tmp_path, empty_text):
    notes = make_notes(tmp_path)
    write_scene(
        notes,
        "scene-001",
        {"controls.md": empty_text, "tips.md": empty_text, "tasks.md": empty_text},
    )
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.tips == COMMON_TIPS
    assert documents.uses_common_controls is True
    assert documents.uses_common_tips is True
    # 短期タスクリストには共通の文書が無い
    assert documents.tasks == empty_text


def test_scene_documents_are_used_when_present(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(
        notes,
        "scene-001",
        {"controls.md": SCENE_CONTROLS, "tips.md": SCENE_TIPS, "tasks.md": SCENE_TASKS},
    )
    assert read_surface_documents(notes, "scene-001") == SurfaceDocuments(
        tasks=SCENE_TASKS,
        tips=SCENE_TIPS,
        controls=SCENE_CONTROLS,
        advice=ADVICE,
        scene_id="scene-001",
        uses_common_controls=False,
        uses_common_tips=False,
    )


def test_each_kind_falls_back_independently(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(notes, "scene-001", {"controls.md": SCENE_CONTROLS})
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == SCENE_CONTROLS
    assert documents.uses_common_controls is False
    assert documents.tips == COMMON_TIPS
    assert documents.uses_common_tips is True


def test_documents_of_another_scene_are_not_used(tmp_path):
    notes = make_notes(tmp_path)
    write_scene(
        notes, "scene-002", {"controls.md": SCENE_CONTROLS, "tasks.md": SCENE_TASKS}
    )
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.tasks == ""


def test_invalid_scene_id_is_treated_as_no_scene(tmp_path):
    notes = make_notes(tmp_path)
    documents = read_surface_documents(notes, "../common")
    assert documents.scene_id is None
    assert documents.uses_common_controls is True
    assert documents.tasks == ""


def test_missing_documents_are_empty(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    assert read_surface_documents(notes, None) == SurfaceDocuments(
        tasks="",
        tips="",
        controls="",
        advice="",
        scene_id=None,
        uses_common_controls=True,
        uses_common_tips=True,
    )


# --- notes/ の外を指す文書 ---

SECRET = "text outside of notes\n"


def write_secret(tmp_path: Path) -> Path:
    """notes/ の外の、surface に読ませたくないファイルを作って返す。"""
    secret = tmp_path / "secret.env"
    secret.write_text(SECRET, encoding="utf-8")
    return secret


def test_scene_document_symlinked_to_outside_falls_back_to_common(tmp_path):
    notes = make_notes(tmp_path)
    secret = write_secret(tmp_path)
    write_scene(notes, "scene-001", {"tips.md": SCENE_TIPS})
    (notes / "scenes" / "scene-001" / "controls.md").symlink_to(secret)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
    assert documents.uses_common_controls is True
    assert documents.tips == SCENE_TIPS


def test_documents_symlinked_to_outside_are_empty(tmp_path):
    notes = make_notes(tmp_path)
    secret = write_secret(tmp_path)
    (notes / "advice.md").unlink()
    (notes / "advice.md").symlink_to(secret)
    (notes / "common" / "tips.md").unlink()
    (notes / "common" / "tips.md").symlink_to(secret)
    write_scene(notes, "scene-001", {})
    (notes / "scenes" / "scene-001" / "tasks.md").symlink_to(secret)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.advice == ""
    assert documents.tips == ""
    assert documents.tasks == ""


def test_scene_directory_symlinked_to_outside_is_not_read(tmp_path):
    notes = make_notes(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "controls.md").write_text(SECRET, encoding="utf-8")
    (notes / "scenes").mkdir()
    (notes / "scenes" / "scene-001").symlink_to(outside)
    documents = read_surface_documents(notes, "scene-001")
    assert documents.controls == COMMON_CONTROLS
