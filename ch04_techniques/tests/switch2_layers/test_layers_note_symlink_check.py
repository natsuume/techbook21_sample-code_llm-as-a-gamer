import os
from pathlib import Path

import pytest

from switch2_layers.notes.note_symlink_check import find_symlink_in_note_paths

TIPS_NOTES = [
    "scenes/scene-001/tips.md",
    "scenes/scene-001/box_targets.md",
    "common/tips.md",
]


def make_notes(tmp_path: Path) -> Path:
    """common/ と scenes/scene-001/ を持つ notes/ と、notes/ の外のディレクトリーを作る (パッケージのコードは使わない)。"""
    notes = tmp_path / "run" / "notes"
    (notes / "common").mkdir(parents=True)
    (notes / "scenes" / "scene-001").mkdir(parents=True)
    (notes / "common" / "tips.md").write_text("# 攻略情報\n", encoding="utf-8")
    (notes / "scenes" / "scene-001" / "tips.md").write_text("# 町\n", encoding="utf-8")
    (notes / "strategy.md").write_text("# 大方針\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n", encoding="utf-8")
    return notes


def test_paths_without_links_are_none(tmp_path):
    notes = make_notes(tmp_path)
    assert find_symlink_in_note_paths(notes, TIPS_NOTES) is None
    assert find_symlink_in_note_paths(notes, ["strategy.md"]) is None
    assert find_symlink_in_note_paths(notes, []) is None


def test_missing_documents_and_directories_are_not_links(tmp_path):
    # まだ無い文書と、まだ無い途中のディレクトリーは、deep が Write で作る
    notes = make_notes(tmp_path)
    assert find_symlink_in_note_paths(notes, ["advice.md"]) is None
    assert (
        find_symlink_in_note_paths(
            notes, ["scenes/scene-009/controls.md", "common/controls.md"]
        )
        is None
    )
    assert (
        find_symlink_in_note_paths(tmp_path / "missing" / "notes", TIPS_NOTES) is None
    )


def test_notes_directory_that_is_a_link_is_found(tmp_path):
    notes = make_notes(tmp_path)
    real = notes.parent / "notes-real"
    os.rename(notes, real)
    os.symlink(real, notes)
    # notes/ の中を指すリンクでも、リンクとして返す
    assert find_symlink_in_note_paths(notes, ["strategy.md"]) == notes


def test_document_that_is_a_link_is_found(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "outside" / "secret.md", notes / "advice.md")
    assert find_symlink_in_note_paths(notes, ["advice.md"]) == notes / "advice.md"


def test_document_linked_inside_notes_is_found(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(notes / "strategy.md", notes / "advice.md")
    assert find_symlink_in_note_paths(notes, ["advice.md"]) == notes / "advice.md"


def test_broken_link_is_found(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "outside" / "missing.md", notes / "advice.md")
    assert find_symlink_in_note_paths(notes, ["advice.md"]) == notes / "advice.md"


def test_parent_directory_that_is_a_link_is_found(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "outside", notes / "scenes" / "scene-002")
    assert (
        find_symlink_in_note_paths(notes, ["scenes/scene-002/tips.md"])
        == notes / "scenes" / "scene-002"
    )


def test_upper_parent_is_found_before_lower_parts(tmp_path):
    notes = make_notes(tmp_path)
    real = tmp_path / "scenes-real"
    os.rename(notes / "scenes", real)
    os.symlink(real, notes / "scenes")
    # scenes/ がリンクなら、その下の要素 (scene-001・tips.md) より先に scenes/ を返す
    assert find_symlink_in_note_paths(notes, TIPS_NOTES) == notes / "scenes"


def test_first_link_in_the_order_of_documents_is_returned(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "common" / "tips.md").unlink()
    os.symlink(tmp_path / "outside" / "secret.md", notes / "common" / "tips.md")
    box_targets = notes / "scenes" / "scene-001" / "box_targets.md"
    os.symlink(tmp_path / "outside" / "secret.md", box_targets)
    assert find_symlink_in_note_paths(notes, TIPS_NOTES) == box_targets
    assert find_symlink_in_note_paths(notes, ["common/tips.md"]) == (
        notes / "common" / "tips.md"
    )


def test_links_outside_the_target_paths_are_ignored(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "outside" / "secret.md", notes / "advice.md")
    os.symlink(tmp_path / "outside", notes / "scenes" / "scene-002")
    # notes/ より上 (実行ディレクトリーとその親) は確かめない
    linked_run = tmp_path / "linked-run"
    os.symlink(notes.parent, linked_run)
    assert find_symlink_in_note_paths(notes, TIPS_NOTES) is None
    assert find_symlink_in_note_paths(linked_run / "notes", TIPS_NOTES) is None


@pytest.mark.parametrize(
    "note", ["", "/etc/passwd", "../run.jsonl", "scenes/../strategy.md", "./x.md"]
)
def test_invalid_document_paths_are_value_errors(tmp_path, note):
    notes = make_notes(tmp_path)
    with pytest.raises(ValueError):
        find_symlink_in_note_paths(notes, ["strategy.md", note])


def test_other_os_errors_are_raised(tmp_path):
    notes = make_notes(tmp_path)
    # 名前が長すぎる (ENAMETOOLONG) と、os.lstat が OSError を出す
    with pytest.raises(OSError):
        find_symlink_in_note_paths(notes, ["a" * 300 + ".md"])
