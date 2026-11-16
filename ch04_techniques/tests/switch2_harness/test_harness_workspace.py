import json
import os

import pytest

from switch2_harness.harness_config import HarnessConfig
from switch2_harness.workspace import (
    NotAWorkspaceError,
    WorkspaceNotEmptyError,
    create_workspace,
    require_workspace,
    workspace_paths,
)

CONFIG = HarnessConfig(
    game="G",
    goal="X",
    features=(),
    created_at="2026-11-16T21:00:05+09:00",
)


def test_workspace_paths_builds_every_path(tmp_path):
    root = tmp_path / "play"
    paths = workspace_paths(root)
    assert paths.root == root
    assert paths.harness_file == root / "harness.json"
    assert paths.instructions_file == root / "CLAUDE.md"
    assert paths.records_dir == root / "records"
    assert paths.frames_dir == root / "frames"
    assert paths.events_file == root / "records" / "events.jsonl"
    assert paths.agent_log_file == root / "records" / "agent.jsonl"
    assert paths.agent_stderr_file == root / "records" / "agent-stderr.log"


def test_workspace_paths_makes_relative_path_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = workspace_paths("plays/../plays/one")
    assert paths.root == tmp_path / "plays" / "one"
    assert paths.root.is_absolute()


def test_workspace_paths_does_not_touch_the_filesystem(tmp_path):
    workspace_paths(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_require_workspace_returns_paths_when_harness_json_exists(tmp_path):
    (tmp_path / "harness.json").write_text("{}", encoding="utf-8")
    assert require_workspace(tmp_path) == workspace_paths(tmp_path)


def test_require_workspace_rejects_directory_without_harness_json(tmp_path):
    with pytest.raises(NotAWorkspaceError) as error:
        require_workspace(tmp_path)
    assert str(error.value) == (
        f"not a harness workspace (harness.json not found): {tmp_path}"
    )


def test_require_workspace_message_uses_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()
    with pytest.raises(NotAWorkspaceError) as error:
        require_workspace("empty")
    assert str(error.value).endswith(f": {tmp_path / 'empty'}")


def test_create_workspace_creates_files_and_directories(tmp_path):
    paths = workspace_paths(tmp_path / "parent" / "play")
    create_workspace(paths, CONFIG, "# 指示\n")
    assert json.loads(paths.harness_file.read_text(encoding="utf-8")) == {
        "game": "G",
        "goal": "X",
        "features": [],
        "created_at": "2026-11-16T21:00:05+09:00",
    }
    assert paths.instructions_file.read_text(encoding="utf-8") == "# 指示\n"
    assert paths.records_dir.is_dir()
    assert paths.frames_dir.is_dir()
    assert os.listdir(paths.records_dir) == []
    assert os.listdir(paths.frames_dir) == []
    assert sorted(os.listdir(paths.root)) == [
        "CLAUDE.md",
        "frames",
        "harness.json",
        "records",
    ]


def test_create_workspace_accepts_existing_empty_directory(tmp_path):
    root = tmp_path / "play"
    root.mkdir()
    paths = workspace_paths(root)
    create_workspace(paths, CONFIG, "# 指示\n")
    assert paths.harness_file.is_file()


def test_create_workspace_rejects_non_empty_directory(tmp_path):
    root = tmp_path / "play"
    root.mkdir()
    (root / "memo.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(WorkspaceNotEmptyError) as error:
        create_workspace(workspace_paths(root), CONFIG, "# 指示\n")
    assert str(error.value) == f"directory is not empty: {root}"
    assert sorted(os.listdir(root)) == ["memo.txt"]
    assert (root / "memo.txt").read_text(encoding="utf-8") == "keep"


def test_create_workspace_rejects_existing_file(tmp_path):
    root = tmp_path / "play"
    root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(WorkspaceNotEmptyError):
        create_workspace(workspace_paths(root), CONFIG, "# 指示\n")
    assert root.read_text(encoding="utf-8") == "not a directory"
