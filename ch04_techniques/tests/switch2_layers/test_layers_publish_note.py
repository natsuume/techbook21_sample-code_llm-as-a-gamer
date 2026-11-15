import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from switch2_layers.notes.publish_note import (
    EXIT_CONFLICT,
    EXIT_INVALID,
    EXIT_PUBLISHED,
    NO_NOTE_SHA256,
    NotePathError,
    PublishResult,
    PublishStatus,
    publish_note,
    resolve_note_path,
)

ADVICE = "# 助言\n"
EDITED = "# 助言\n\n詰まりの原因を書いた\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_run(tmp_path: Path) -> Path:
    """notes/advice.md だけを持つ実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    run_dir = tmp_path / "run"
    (run_dir / "notes" / "common").mkdir(parents=True)
    (run_dir / "notes" / "map").mkdir()
    (run_dir / "notes" / "advice.md").write_text(ADVICE, encoding="utf-8")
    return run_dir


def edited_file(tmp_path: Path, text: str = EDITED) -> Path:
    path = tmp_path / "work" / "advice.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_exit_codes_follow_the_issue():
    assert (EXIT_PUBLISHED, EXIT_INVALID, EXIT_CONFLICT) == (0, 2, 3)
    assert NO_NOTE_SHA256 == "none"


def test_result_exit_code_and_stdout_json():
    published = PublishResult(PublishStatus.PUBLISHED, sha256="a" * 64)
    conflict = PublishResult(PublishStatus.CONFLICT, current_sha256="none")
    invalid = PublishResult(PublishStatus.INVALID, message="outside notes")
    assert published.exit_code() == 0
    assert published.stdout_json() == {"status": "published", "sha256": "a" * 64}
    assert conflict.exit_code() == 3
    assert conflict.stdout_json() == {"status": "conflict", "current_sha256": "none"}
    assert invalid.exit_code() == 2
    assert invalid.stdout_json() is None


# --- 成功 ---


def test_publish_replaces_note_when_hash_matches(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(run_dir, "advice.md", sha(ADVICE), edited_file(tmp_path))
    assert result.status is PublishStatus.PUBLISHED
    assert result.sha256 == sha(EDITED)
    assert result.exit_code() == 0
    assert (run_dir / "notes" / "advice.md").read_text(encoding="utf-8") == EDITED
    # ロックのファイルは notes/ に作る
    assert (run_dir / "notes" / ".publish.lock").exists()


def test_publish_accepts_uppercase_hash(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(
        run_dir, "advice.md", sha(ADVICE).upper(), edited_file(tmp_path)
    )
    assert result.status is PublishStatus.PUBLISHED


def test_publish_creates_missing_note_with_base_none(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(
        run_dir, "scenes/scene-002/controls.md", "none", edited_file(tmp_path)
    )
    assert result.status is PublishStatus.PUBLISHED
    target = run_dir / "notes" / "scenes" / "scene-002" / "controls.md"
    assert target.read_text(encoding="utf-8") == EDITED


def test_publish_leaves_no_temp_files(tmp_path):
    run_dir = make_run(tmp_path)
    publish_note(run_dir, "advice.md", sha(ADVICE), edited_file(tmp_path))
    names = sorted(p.name for p in (run_dir / "notes").iterdir())
    assert names == [".publish.lock", "advice.md", "common", "map"]


# --- 衝突 ---


def test_conflict_when_hash_differs(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(
        run_dir, "advice.md", sha("old version"), edited_file(tmp_path)
    )
    assert result.status is PublishStatus.CONFLICT
    assert result.current_sha256 == sha(ADVICE)
    assert result.exit_code() == 3
    assert (run_dir / "notes" / "advice.md").read_text(encoding="utf-8") == ADVICE


def test_conflict_when_note_exists_but_base_is_none(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(run_dir, "advice.md", "none", edited_file(tmp_path))
    assert result.status is PublishStatus.CONFLICT
    assert result.current_sha256 == sha(ADVICE)


def test_conflict_when_note_does_not_exist_but_base_is_a_hash(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(
        run_dir, "scenes/scene-001/tips.md", sha(ADVICE), edited_file(tmp_path)
    )
    assert result.status is PublishStatus.CONFLICT
    assert result.current_sha256 == "none"
    assert not (run_dir / "notes" / "scenes").exists()


def test_only_one_of_concurrent_publishes_with_same_base_succeeds(tmp_path):
    run_dir = make_run(tmp_path)
    sources = [edited_file(tmp_path / str(i), f"v{i}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [
            pool.submit(publish_note, run_dir, "advice.md", sha(ADVICE), source)
            for source in sources
        ]
        # result() はスレッドの例外を、テストの本体でそのまま出す
        results: list[PublishResult] = [future.result() for future in futures]
    statuses = [result.status for result in results]
    assert statuses.count(PublishStatus.PUBLISHED) == 1
    assert statuses.count(PublishStatus.CONFLICT) == 7


# --- notes/ の外のパスと引数の誤り ---


@pytest.mark.parametrize(
    "note",
    [
        "",
        "../run.jsonl",
        "common/../../run.jsonl",
        "scenes/../../outside.md",
        ".publish.lock",
        "map",
        "common",
    ],
)
def test_invalid_note_paths_change_nothing(tmp_path, note):
    run_dir = make_run(tmp_path)
    (run_dir / "run.jsonl").write_text("{}\n", encoding="utf-8")
    result = publish_note(run_dir, note, "none", edited_file(tmp_path))
    assert result.status is PublishStatus.INVALID
    assert result.exit_code() == 2
    assert result.stdout_json() is None
    assert (run_dir / "run.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert not (tmp_path / "outside.md").exists()


def test_absolute_note_path_is_invalid(tmp_path):
    run_dir = make_run(tmp_path)
    target = run_dir / "notes" / "advice.md"
    result = publish_note(run_dir, str(target), sha(ADVICE), edited_file(tmp_path))
    assert result.status is PublishStatus.INVALID
    assert target.read_text(encoding="utf-8") == ADVICE


def test_symlink_to_outside_of_notes_is_invalid(tmp_path):
    run_dir = make_run(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    os.symlink(outside, run_dir / "notes" / "link.md")
    result = publish_note(run_dir, "link.md", sha("outside\n"), edited_file(tmp_path))
    assert result.status is PublishStatus.INVALID
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_symlinked_directory_to_outside_of_notes_is_invalid(tmp_path):
    run_dir = make_run(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    os.symlink(outside_dir, run_dir / "notes" / "scenes")
    result = publish_note(
        run_dir, "scenes/scene-001/tips.md", "none", edited_file(tmp_path)
    )
    assert result.status is PublishStatus.INVALID
    assert list(outside_dir.iterdir()) == []


def test_symlink_inside_notes_replaces_the_link_target(tmp_path):
    run_dir = make_run(tmp_path)
    notes = run_dir / "notes"
    os.symlink(notes / "advice.md", notes / "alias.md")
    result = publish_note(run_dir, "alias.md", sha(ADVICE), edited_file(tmp_path))
    assert result.status is PublishStatus.PUBLISHED
    assert (notes / "advice.md").read_text(encoding="utf-8") == EDITED
    assert (notes / "alias.md").is_symlink()


def test_missing_source_file_is_invalid(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(run_dir, "advice.md", sha(ADVICE), tmp_path / "missing.md")
    assert result.status is PublishStatus.INVALID
    assert (run_dir / "notes" / "advice.md").read_text(encoding="utf-8") == ADVICE


def test_directory_source_is_invalid(tmp_path):
    run_dir = make_run(tmp_path)
    result = publish_note(run_dir, "advice.md", sha(ADVICE), tmp_path)
    assert result.status is PublishStatus.INVALID


def test_missing_notes_directory_is_invalid(tmp_path):
    result = publish_note(
        tmp_path / "missing", "advice.md", "none", edited_file(tmp_path)
    )
    assert result.status is PublishStatus.INVALID


# --- resolve_note_path ---


def test_resolve_note_path_inside_notes(tmp_path):
    notes = make_run(tmp_path) / "notes"
    assert resolve_note_path(notes, "advice.md") == (notes / "advice.md").resolve()
    assert (
        resolve_note_path(notes, "scenes/scene-001/tasks.md")
        == (notes / "scenes" / "scene-001" / "tasks.md").resolve()
    )


@pytest.mark.parametrize("note", ["", "../x.md", "/etc/passwd", ".publish.lock", "map"])
def test_resolve_note_path_rejects_outside_paths(tmp_path, note):
    notes = make_run(tmp_path) / "notes"
    with pytest.raises(NotePathError):
        resolve_note_path(notes, note)
