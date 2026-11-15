import json
from datetime import datetime
from pathlib import Path

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.run_directory import (
    COMMON_CONTROLS_HEADING,
    INITIAL_ADVICE,
    INITIAL_COMMON_TIPS,
    INITIAL_STRATEGY,
    RunPaths,
    create_run_directory,
    frame_file_name,
    initial_common_controls,
    open_run_directory,
    run_directory_name,
    write_initial_notes,
)

STARTED_AT = datetime(2026, 11, 15, 14, 22, 33)
BUTTONS_WITHOUT_HOME = frozenset(BUTTON_ORDER) - {"HOME"}


# --- 実行ディレクトリーの作成と名前の重複 ---


def test_run_directory_name():
    assert run_directory_name(STARTED_AT) == "20261115-142233"


def test_create_run_directory_creates_parent_and_subdirectories(tmp_path):
    runs_dir = tmp_path / "nested" / "runs"
    paths = create_run_directory(runs_dir, STARTED_AT)
    assert paths.root == runs_dir / "20261115-142233"
    assert paths.root.is_dir()
    assert (paths.root / "frames" / "interval").is_dir()
    assert (paths.root / "frames" / "surface").is_dir()
    assert (paths.root / "middle").is_dir()
    assert (paths.root / "deep").is_dir()


def test_create_run_directory_adds_suffix_for_duplicate_names(tmp_path):
    first = create_run_directory(tmp_path, STARTED_AT)
    second = create_run_directory(tmp_path, STARTED_AT)
    third = create_run_directory(tmp_path, STARTED_AT)
    assert first.root.name == "20261115-142233"
    assert second.root.name == "20261115-142233-2"
    assert third.root.name == "20261115-142233-3"
    assert len({first.root, second.root, third.root}) == 3


def test_create_run_directory_skips_existing_file_with_same_name(tmp_path):
    (tmp_path / "20261115-142233").write_text("not a directory", encoding="utf-8")
    paths = create_run_directory(tmp_path, STARTED_AT)
    assert paths.root.name == "20261115-142233-2"
    assert paths.root.is_dir()


def test_create_run_directory_does_not_reuse_existing_suffix(tmp_path):
    (tmp_path / "20261115-142233").mkdir()
    (tmp_path / "20261115-142233-2").mkdir()
    paths = create_run_directory(tmp_path, STARTED_AT)
    assert paths.root.name == "20261115-142233-3"


def test_open_run_directory(tmp_path):
    run_dir = tmp_path / "20261115-142233"
    run_dir.mkdir()
    assert open_run_directory(run_dir) == RunPaths(run_dir)
    with pytest.raises(FileNotFoundError):
        open_run_directory(tmp_path / "missing")
    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        open_run_directory(file_path)


# --- パスの集約 ---


def test_run_paths_follow_the_layout():
    root = Path("/runs/20261115-142233")
    paths = RunPaths(root)
    assert paths.run_log == root / "run.jsonl"
    assert paths.frames_log == root / "frames.jsonl"
    assert paths.interval_frames_dir == root / "frames" / "interval"
    assert paths.surface_frames_dir == root / "frames" / "surface"
    assert paths.surface_log == root / "surface.jsonl"
    assert paths.operations_log == root / "operations.jsonl"
    assert paths.notes_dir == root / "notes"
    assert paths.middle_dir == root / "middle"
    assert paths.middle_state == root / "middle" / "state.json"
    assert paths.middle_calls == root / "middle" / "calls.jsonl"
    assert paths.deep_dir == root / "deep"


def test_frame_file_name_uses_nine_digit_milliseconds():
    assert frame_file_name(12.345) == "000012345.jpg"
    assert frame_file_name(0.0) == "000000000.jpg"
    assert frame_file_name(123456.789) == "123456789.jpg"
    with pytest.raises(ValueError):
        frame_file_name(-0.5)


def test_frame_paths_and_relative_paths():
    root = Path("/runs/20261115-142233")
    paths = RunPaths(root)
    assert paths.interval_frame(1.5) == root / "frames" / "interval" / "000001500.jpg"
    assert paths.surface_frame(2.0) == root / "frames" / "surface" / "000002000.jpg"
    assert paths.relative(paths.surface_frame(2.0)) == "frames/surface/000002000.jpg"
    assert paths.relative(root / "deep" / "controls-001") == "deep/controls-001"
    with pytest.raises(ValueError):
        paths.relative(Path("/elsewhere/a.jpg"))


# --- 起動時の notes/ ---


def test_initial_common_controls_lists_allowed_buttons_as_unknown():
    text = initial_common_controls(BUTTONS_WITHOUT_HOME)
    lines = text.splitlines()
    assert lines[0] == COMMON_CONTROLS_HEADING
    assert lines[1] == ""
    expected = [f"- {b}: unknown" for b in BUTTON_ORDER if b != "HOME"]
    assert lines[2:] == expected
    assert len(expected) == 16
    assert text.endswith("\n")


def test_initial_common_controls_with_home():
    text = initial_common_controls(frozenset(BUTTON_ORDER))
    assert "- HOME: unknown" in text.splitlines()
    assert len(text.splitlines()) == 2 + 17


def test_initial_common_controls_example_in_docstring():
    assert initial_common_controls({"B", "A"}) == (
        "# 操作説明 (共通)\n\n- A: unknown\n- B: unknown\n"
    )


def test_write_initial_notes(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    paths = RunPaths(root)
    write_initial_notes(paths, BUTTONS_WITHOUT_HOME)
    notes = root / "notes"
    assert (notes / "common" / "controls.md").read_text(
        encoding="utf-8"
    ) == initial_common_controls(BUTTONS_WITHOUT_HOME)
    assert "HOME" not in (notes / "common" / "controls.md").read_text(encoding="utf-8")
    assert (notes / "common" / "tips.md").read_text(
        encoding="utf-8"
    ) == INITIAL_COMMON_TIPS
    assert (notes / "strategy.md").read_text(encoding="utf-8") == INITIAL_STRATEGY
    assert (notes / "advice.md").read_text(encoding="utf-8") == INITIAL_ADVICE
    assert json.loads((notes / "scene_labels.json").read_text(encoding="utf-8")) == {
        "scenes": []
    }
    assert (notes / "map").is_dir()
    assert list((notes / "map").iterdir()) == []
    assert (notes / "scenes").is_dir()


@pytest.mark.parametrize(
    "text", [INITIAL_COMMON_TIPS, INITIAL_STRATEGY, INITIAL_ADVICE]
)
def test_other_initial_markdown_has_only_a_heading(text):
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("# ")
