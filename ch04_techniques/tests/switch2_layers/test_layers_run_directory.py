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
    MIDDLE_MAP_RECORDS_FILE,
    MIDDLE_REQUESTS_FILE,
    MIDDLE_SYSTEM_CALLS_FILE,
    ORCHESTRATOR_INSTRUCTION_FILE,
    SURFACE_REQUESTS_LOG_FILE,
    TASKS_LOG_FILE,
    RunPaths,
    create_empty_notes,
    create_run_directory,
    frame_file_name,
    initial_common_controls,
    open_run_directory,
    run_directory_name,
    session_directory_name,
    write_initial_instruction,
    write_initial_notes,
)
from switch2_layers.surface.box_targets import (
    DEFAULT_BOX_TARGETS,
    initial_box_targets_text,
    parse_box_targets,
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


def test_create_run_directory_creates_progress_directory_without_state(tmp_path):
    paths = create_run_directory(tmp_path, STARTED_AT)
    assert paths.progress_dir == paths.root / "progress"
    assert paths.progress_state == paths.root / "progress" / "state.json"
    assert paths.progress_dir.is_dir()
    # まだフレームが無いため、値のファイルは作らない
    assert not paths.progress_state.exists()


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


def test_surface_requests_log_path():
    root = Path("/runs/20261115-142233")
    assert SURFACE_REQUESTS_LOG_FILE == "surface_requests.jsonl"
    assert RunPaths(root).surface_requests_log == root / "surface_requests.jsonl"


def test_middle_requests_path():
    root = Path("/runs/20261115-142233")
    assert MIDDLE_REQUESTS_FILE == "requests.jsonl"
    assert RunPaths(root).middle_requests == root / "middle" / "requests.jsonl"


def test_middle_map_records_path():
    root = Path("/runs/20261115-142233")
    assert MIDDLE_MAP_RECORDS_FILE == "map_records.jsonl"
    assert RunPaths(root).middle_map_records == root / "middle" / "map_records.jsonl"


def test_system_calls_path():
    root = Path("/runs/20261115-142233")
    assert MIDDLE_SYSTEM_CALLS_FILE == "system_calls.jsonl"
    assert RunPaths(root).system_calls == root / "middle" / "system_calls.jsonl"


def test_safety_log_path():
    root = Path("/runs/20261115-142233")
    assert RunPaths(root).safety_log == root / "safety.jsonl"


def test_tasks_log_path():
    root = Path("/runs/20261115-142233")
    assert TASKS_LOG_FILE == "tasks.jsonl"
    assert RunPaths(root).tasks_log == root / "tasks.jsonl"


def test_orchestrator_paths():
    root = Path("/runs/20261115-142233")
    paths = RunPaths(root)
    assert paths.orchestrator_dir == root / "orchestrator"
    assert paths.orchestrator_sessions_log == root / "orchestrator" / "sessions.jsonl"
    assert paths.orchestrator_turns_log == root / "orchestrator" / "turns.jsonl"
    assert paths.orchestrator_session_dir(1) == root / "orchestrator" / "session-001"
    assert paths.orchestrator_session_stream(12) == (
        root / "orchestrator" / "session-012" / "stream.jsonl"
    )
    assert paths.claude_skills_dir == root / ".claude" / "skills"
    assert paths.orchestrator_monitor_log == root / "orchestrator" / "monitor.jsonl"


def test_orchestrator_instruction_path():
    root = Path("/runs/20261115-142233")
    assert ORCHESTRATOR_INSTRUCTION_FILE == "instruction.md"
    assert RunPaths(root).orchestrator_instruction == (
        root / "orchestrator" / "instruction.md"
    )


def test_create_empty_notes_creates_only_an_empty_notes_directory(tmp_path):
    # Orchestrator 型の run は、write_initial_notes の初期ファイルを書かずに空の notes/ で始める
    paths = create_run_directory(tmp_path, STARTED_AT)
    create_empty_notes(paths)
    assert paths.notes_dir.is_dir()
    assert list(paths.notes_dir.iterdir()) == []


def test_write_initial_instruction_creates_the_orchestrator_directory(tmp_path):
    paths = create_run_directory(tmp_path, STARTED_AT)
    text = "RPG のソフトを起動して冒険を始めてください\n"
    write_initial_instruction(paths, text)
    assert paths.orchestrator_dir.is_dir()
    assert paths.orchestrator_instruction.read_text(encoding="utf-8") == text


def test_session_directory_name_uses_three_digits():
    assert session_directory_name(1) == "session-001"
    assert session_directory_name(999) == "session-999"
    assert session_directory_name(1000) == "session-1000"
    with pytest.raises(ValueError):
        session_directory_name(0)


def test_create_run_directory_does_not_create_orchestrator_directories(tmp_path):
    # orchestrator/ と .claude/skills/ は、Orchestrator 型の主ループが作る
    paths = create_run_directory(tmp_path, STARTED_AT)
    assert not paths.orchestrator_dir.exists()
    assert not (paths.root / ".claude").exists()


def test_frame_file_name_uses_nine_digit_milliseconds():
    assert frame_file_name(12.345) == "000012345.jpg"
    assert frame_file_name(0.0) == "000000000.jpg"
    assert frame_file_name(123456.789) == "123456789.jpg"
    with pytest.raises(ValueError):
        frame_file_name(-0.5)


def test_numbered_frame_file_name_for_second_and_later_contents():
    assert frame_file_name(12.345, 1) == "000012345.jpg"
    assert frame_file_name(12.345, 2) == "000012345-2.jpg"
    assert frame_file_name(0.1, 13) == "000000100-13.jpg"
    with pytest.raises(ValueError):
        frame_file_name(1.0, 0)


def test_numbered_surface_frame_path():
    root = Path("/runs/20261115-142233")
    paths = RunPaths(root)
    assert paths.surface_frame(2.0, 3) == (
        root / "frames" / "surface" / "000002000-3.jpg"
    )


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


def test_write_initial_notes_writes_default_box_targets(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    write_initial_notes(RunPaths(root), BUTTONS_WITHOUT_HOME)
    text = (root / "notes" / "common" / "box_targets.md").read_text(encoding="utf-8")
    assert text == initial_box_targets_text()
    assert parse_box_targets(text) == DEFAULT_BOX_TARGETS


@pytest.mark.parametrize(
    "text", [INITIAL_COMMON_TIPS, INITIAL_STRATEGY, INITIAL_ADVICE]
)
def test_other_initial_markdown_has_only_a_heading(text):
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("# ")
