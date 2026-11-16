import os
from pathlib import Path

import switch2_layers.orchestrator as orchestrator_package
from switch2_layers.orchestrator.skill_copy import (
    DEFAULT_SWITCH_SKILLS_DIR,
    copy_game_skills,
    copy_skill_directories,
    copy_switch_skills,
    game_skills_source_dir,
    prepare_claude_skills_dir,
)
from switch2_layers.run_directory import RunPaths


def make_run(tmp_path: Path) -> RunPaths:
    root = tmp_path / "runs" / "20261116-120000"
    (root / "notes").mkdir(parents=True)
    return RunPaths(root)


def write_skill(directory: Path, name: str, body: str = "body\n") -> Path:
    """directory/<name>/SKILL.md を作って、スキルのディレクトリーを返す。"""
    skill = directory / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\n{body}", encoding="utf-8"
    )
    return skill


def tree(directory: Path) -> dict[str, bytes]:
    """directory の下のファイルの、相対パス → 中身。"""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# --- .claude/skills/ の用意 ---


def test_prepare_creates_claude_skills_directory(tmp_path):
    paths = make_run(tmp_path)
    created = prepare_claude_skills_dir(paths)
    assert created == paths.claude_skills_dir
    assert (paths.root / ".claude" / "skills").is_dir()
    # 2 回目は何もしない
    assert prepare_claude_skills_dir(paths) == paths.claude_skills_dir


def test_default_switch_skills_directory_is_in_the_package():
    package_dir = Path(orchestrator_package.__file__).resolve().parent
    assert DEFAULT_SWITCH_SKILLS_DIR == package_dir / "skills"


def test_game_skills_are_read_from_notes_skills(tmp_path):
    paths = make_run(tmp_path)
    assert game_skills_source_dir(paths) == paths.root / "notes" / "skills"


# --- 本体の操作のスキル ---


def test_switch_skills_are_copied_with_their_files(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    source = tmp_path / "package-skills"
    select_user = write_skill(source, "switch-select-user")
    (select_user / "reference.md").write_text("more\n", encoding="utf-8")
    write_skill(source, "switch-system-dialog")
    copied = copy_switch_skills(source, paths)
    assert copied == ("switch-select-user", "switch-system-dialog")
    target = paths.claude_skills_dir
    assert tree(target / "switch-select-user") == tree(select_user)
    assert (target / "switch-system-dialog" / "SKILL.md").is_file()


def test_only_switch_skills_are_copied_from_the_package(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    source = tmp_path / "package-skills"
    write_skill(source, "switch-launch-software")
    write_skill(source, "record-tips")
    write_skill(source, "game-dialog")
    (source / "switch-empty").mkdir()
    (source / "switch-file.md").write_text("x\n", encoding="utf-8")
    assert copy_switch_skills(source, paths) == ("switch-launch-software",)
    assert sorted(path.name for path in paths.claude_skills_dir.iterdir()) == [
        "switch-launch-software"
    ]


def test_missing_switch_skills_directory_copies_nothing(tmp_path):
    # 本体の操作のスキルがまだ無い状態でも、主ループは .claude/skills/ を作ってから起動する
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    assert copy_switch_skills(tmp_path / "missing", paths) == ()
    assert paths.claude_skills_dir.is_dir()
    assert list(paths.claude_skills_dir.iterdir()) == []


# --- ゲームの内容のスキル ---


def test_game_skills_written_under_notes_are_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill(notes_skills, "game-dialog")
    write_skill(notes_skills, "game-shop")
    assert copy_game_skills(paths) == ("game-dialog", "game-shop")
    assert tree(paths.claude_skills_dir / "game-dialog") == tree(
        notes_skills / "game-dialog"
    )


def test_names_not_starting_with_game_and_directories_without_skill_are_not_copied(
    tmp_path,
):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill(notes_skills, "switch-fake")
    write_skill(notes_skills, "dialog")
    write_skill(notes_skills, "Game-upper")
    (notes_skills / "game-empty").mkdir()
    (notes_skills / "game-file.md").write_text("x\n", encoding="utf-8")
    sub = notes_skills / "game-nested" / "inner"
    sub.mkdir(parents=True)
    (sub / "SKILL.md").write_text("x\n", encoding="utf-8")
    assert copy_game_skills(paths) == ()
    assert list(paths.claude_skills_dir.iterdir()) == []


def test_symlinked_skill_directory_is_not_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    outside = write_skill(tmp_path / "outside", "game-outside")
    notes_skills = paths.root / "notes" / "skills"
    notes_skills.mkdir()
    os.symlink(outside, notes_skills / "game-linked")
    assert copy_game_skills(paths) == ()
    assert not (paths.claude_skills_dir / "game-linked").exists()


def test_symlinked_skill_file_is_not_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    secret = tmp_path / "secret.md"
    secret.write_text("secret\n", encoding="utf-8")
    skill = paths.root / "notes" / "skills" / "game-linked-file"
    skill.mkdir(parents=True)
    os.symlink(secret, skill / "SKILL.md")
    assert copy_game_skills(paths) == ()
    assert not (paths.claude_skills_dir / "game-linked-file").exists()


def test_symlinked_file_inside_a_skill_is_left_out(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    secret = tmp_path / "secret.md"
    secret.write_text("secret\n", encoding="utf-8")
    skill = write_skill(paths.root / "notes" / "skills", "game-dialog")
    os.symlink(secret, skill / "secret.md")
    assert copy_game_skills(paths) == ("game-dialog",)
    target = paths.claude_skills_dir / "game-dialog"
    assert (target / "SKILL.md").is_file()
    assert not (target / "secret.md").exists()
    assert not (target / "secret.md").is_symlink()


def test_copy_replaces_a_skill_with_the_same_name(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    skill = write_skill(notes_skills, "game-dialog", "first\n")
    (skill / "old.md").write_text("old\n", encoding="utf-8")
    assert copy_game_skills(paths) == ("game-dialog",)
    (skill / "old.md").unlink()
    write_skill(notes_skills, "game-dialog", "second\n")
    assert copy_game_skills(paths) == ("game-dialog",)
    target = paths.claude_skills_dir / "game-dialog"
    assert tree(target) == tree(skill)
    assert "second" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert not (target / "old.md").exists()


def test_unchanged_skill_is_not_copied_again(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    write_skill(paths.root / "notes" / "skills", "game-dialog")
    assert copy_game_skills(paths) == ("game-dialog",)
    assert copy_game_skills(paths) == ()


def test_skill_removed_from_notes_stays_in_claude_skills(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    skill = write_skill(paths.root / "notes" / "skills", "game-dialog")
    copy_game_skills(paths)
    (skill / "SKILL.md").unlink()
    skill.rmdir()
    assert copy_game_skills(paths) == ()
    assert (paths.claude_skills_dir / "game-dialog" / "SKILL.md").is_file()


def test_missing_notes_skills_directory_copies_nothing(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    assert copy_game_skills(paths) == ()


def test_skill_that_cannot_be_read_is_skipped_and_others_are_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    bad = write_skill(notes_skills, "game-bad")
    write_skill(notes_skills, "game-good")
    (bad / "SKILL.md").chmod(0)
    try:
        copied = copy_skill_directories(notes_skills, paths.claude_skills_dir, "game-")
    finally:
        (bad / "SKILL.md").chmod(0o644)
    assert copied == ("game-good",)
    assert (paths.claude_skills_dir / "game-good" / "SKILL.md").is_file()
    # 写しかけのディレクトリーは残さない
    assert not (paths.claude_skills_dir / "game-bad").exists()
