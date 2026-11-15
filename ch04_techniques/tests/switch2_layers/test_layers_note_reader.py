import os
from pathlib import Path

import pytest

from switch2_layers.notes.note_reader import (
    read_note_bytes,
    read_note_text,
    resolve_note_file,
)

STRATEGY = "# 大方針\n\n町を出る\n"
SECRET = "text outside of notes\n"


def make_notes(tmp_path: Path) -> Path:
    """strategy.md と scenes/scene-001/ を持つ notes/ と、notes/ の外の秘密のファイルを作る (パッケージのコードは使わない)。"""
    notes = tmp_path / "run" / "notes"
    (notes / "scenes" / "scene-001").mkdir(parents=True)
    (notes / "strategy.md").write_text(STRATEGY, encoding="utf-8")
    (tmp_path / "secret.env").write_text(SECRET, encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "tips.md").write_text(SECRET, encoding="utf-8")
    return notes


def test_regular_file_inside_notes_is_read(tmp_path):
    notes = make_notes(tmp_path)
    assert resolve_note_file(notes, "strategy.md") == (notes / "strategy.md").resolve()
    assert read_note_bytes(notes, "strategy.md") == STRATEGY.encode()
    assert read_note_text(notes, "strategy.md") == STRATEGY


@pytest.mark.parametrize(
    "note",
    ["missing.md", "scenes", "scenes/scene-001", "", "../secret.env", "/etc/passwd"],
)
def test_missing_directory_and_outside_paths_are_missing_documents(tmp_path, note):
    notes = make_notes(tmp_path)
    assert resolve_note_file(notes, note) is None
    assert read_note_bytes(notes, note) is None
    assert read_note_text(notes, note) == ""


def test_symlink_to_outside_file_is_a_missing_document(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "secret.env", notes / "advice.md")
    assert resolve_note_file(notes, "advice.md") is None
    assert read_note_bytes(notes, "advice.md") is None
    assert read_note_text(notes, "advice.md") == ""


def test_symlinked_directory_to_outside_is_a_missing_document(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(tmp_path / "outside", notes / "scenes" / "scene-002")
    assert read_note_bytes(notes, "scenes/scene-002/tips.md") is None


def test_symlink_inside_notes_reads_the_target(tmp_path):
    notes = make_notes(tmp_path)
    os.symlink(notes / "strategy.md", notes / "alias.md")
    assert resolve_note_file(notes, "alias.md") == (notes / "strategy.md").resolve()
    assert read_note_text(notes, "alias.md") == STRATEGY


def test_os_error_while_checking_the_path_is_a_missing_document(tmp_path):
    notes = make_notes(tmp_path)
    too_long = "a" * 300 + ".md"
    assert resolve_note_file(notes, too_long) is None
    assert read_note_bytes(notes, too_long) is None
    os.symlink("loop.md", notes / "loop.md")
    assert read_note_bytes(notes, "loop.md") is None


def test_runtime_error_while_resolving_is_a_missing_document(tmp_path, monkeypatch):
    # Python 3.12 の Path.resolve は、シンボリックリンクの循環で RuntimeError を出す
    notes = make_notes(tmp_path)

    def loop_error(self, strict=False):
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr(Path, "resolve", loop_error)
    assert resolve_note_file(notes, "strategy.md") is None
    assert read_note_text(notes, "strategy.md") == ""


def test_text_that_is_not_utf8_is_empty(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "advice.md").write_bytes(b"\xff\xfe")
    assert read_note_bytes(notes, "advice.md") == b"\xff\xfe"
    assert read_note_text(notes, "advice.md") == ""
