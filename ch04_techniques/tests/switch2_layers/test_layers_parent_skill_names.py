import os
from pathlib import Path

from switch2_layers.orchestrator.parent_skill_names import find_parent_skill_names


def add_skill(directory: Path, name: str) -> Path:
    """directory/.claude/skills/<name>/SKILL.md を作って、そのパスを返す。"""
    skill = directory / ".claude" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")
    return skill


def make_tree(tmp_path: Path) -> Path:
    """tmp_path/repo/ch04/runs/<実行>/ を作り、実行ディレクトリーを返す。"""
    run_dir = tmp_path / "repo" / "ch04" / "runs" / "20261116-120000"
    run_dir.mkdir(parents=True)
    return run_dir


def outside_names(tmp_path: Path) -> set[str]:
    """tmp_path より上のディレクトリーにあるスキルの名前 (テストの環境に依る分)。"""
    return set(find_parent_skill_names(tmp_path / "probe"))


def test_names_from_every_parent_are_collected_once_in_name_order(tmp_path):
    run_dir = make_tree(tmp_path)
    add_skill(tmp_path / "repo" / "ch04", "record-tips")
    add_skill(tmp_path / "repo" / "ch04", "record-advice")
    add_skill(tmp_path / "repo", "record-tips")
    add_skill(tmp_path / "repo", "deploy")
    names = find_parent_skill_names(run_dir)
    assert isinstance(names, tuple)
    expected = sorted(
        outside_names(tmp_path) | {"deploy", "record-advice", "record-tips"}
    )
    assert list(names) == expected


def test_skills_of_the_run_directory_itself_are_not_collected(tmp_path):
    run_dir = make_tree(tmp_path)
    add_skill(run_dir, "switch-select-user")
    add_skill(run_dir, "game-dialog")
    assert set(find_parent_skill_names(run_dir)) == outside_names(tmp_path)


def test_directories_without_skill_file_and_plain_files_are_not_skills(tmp_path):
    run_dir = make_tree(tmp_path)
    skills = tmp_path / "repo" / ".claude" / "skills"
    (skills / "empty").mkdir(parents=True)
    (skills / "notes.md").write_text("not a skill\n", encoding="utf-8")
    (skills / "nested" / "inner").mkdir(parents=True)
    (skills / "nested" / "inner" / "SKILL.md").write_text("x\n", encoding="utf-8")
    assert set(find_parent_skill_names(run_dir)) == outside_names(tmp_path)


def test_symlinked_skill_file_counts_as_a_skill(tmp_path):
    # Claude Code はリンクの先の SKILL.md も読むため、名前を集めて off にする
    run_dir = make_tree(tmp_path)
    real = tmp_path / "elsewhere" / "SKILL.md"
    real.parent.mkdir()
    real.write_text("x\n", encoding="utf-8")
    linked = tmp_path / "repo" / ".claude" / "skills" / "linked"
    linked.mkdir(parents=True)
    os.symlink(real, linked / "SKILL.md")
    assert "linked" in find_parent_skill_names(run_dir)


def test_unreadable_skill_directory_is_skipped(tmp_path):
    run_dir = make_tree(tmp_path)
    add_skill(tmp_path / "repo" / "ch04", "record-tips")
    add_skill(tmp_path / "repo", "hidden")
    locked = tmp_path / "repo" / ".claude" / "skills"
    locked.chmod(0)
    try:
        names = find_parent_skill_names(run_dir)
    finally:
        locked.chmod(0o755)
    assert "record-tips" in names
