import os
from pathlib import Path

import pytest

import switch2_layers.orchestrator as orchestrator_package
from switch2_layers.orchestrator.skill_copy import (
    DEFAULT_SWITCH_SKILLS_DIR,
    MISSING_KEYS_REASON,
    NO_FRONTMATTER_REASON,
    SHELL_COMMAND_REASON,
    UNREADABLE_REASON,
    GameSkillCopyResult,
    SkippedGameSkill,
    check_game_skill_text,
    copy_game_skills,
    copy_skill_directories,
    copy_switch_skills,
    game_skills_source_dir,
    has_skill_shell_command,
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


def write_skill_text(directory: Path, name: str, text: str) -> Path:
    """directory/<name>/SKILL.md に text をそのまま書いて、スキルのディレクトリーを返す。"""
    skill = directory / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    return skill


def skill_text(
    name: str, body: str = "会話の枠が出たら A を押す。\n", extra: str = ""
) -> str:
    """frontmatter が name と description (と extra の行) の SKILL.md の中身。"""
    return f"---\nname: {name}\ndescription: 会話を進める手順\n{extra}---\n\n{body}"


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


def test_skill_directory_that_cannot_be_read_is_skipped_and_others_are_copied(
    tmp_path,
):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    source = tmp_path / "package-skills"
    bad = write_skill(source, "switch-bad")
    write_skill(source, "switch-good")
    (bad / "SKILL.md").chmod(0)
    try:
        copied = copy_skill_directories(source, paths.claude_skills_dir, "switch-")
    finally:
        (bad / "SKILL.md").chmod(0o644)
    assert copied == ("switch-good",)
    assert (paths.claude_skills_dir / "switch-good" / "SKILL.md").is_file()
    # 写しかけのディレクトリーは残さない
    assert not (paths.claude_skills_dir / "switch-bad").exists()


# --- ゲームの内容のスキルの中身の確かめ ---


def test_skill_with_only_name_and_description_passes():
    assert check_game_skill_text("game-dialog", skill_text("game-dialog")) is None


def test_quoted_name_and_windows_line_endings_pass():
    text = (
        '---\r\nname: "game-dialog"\r\ndescription: 会話を進める\r\n---\r\n\r\n本文\r\n'
    )
    assert check_game_skill_text("game-dialog", text) is None


@pytest.mark.parametrize(
    "extra",
    [
        "allowed-tools: Bash\n",
        "hooks: x\n",
        "shell: bash\n",
        "context: fork\n",
        "model: claude-opus-5-5\n",
        "disable-model-invocation: true\n",
        "Name: game-dialog\n",
    ],
)
def test_skill_with_other_frontmatter_keys_is_rejected(extra):
    # スキルの frontmatter の欄 (https://code.claude.com/docs/en/skills の Frontmatter reference) には、
    # 権限やツールを足すもの (allowed-tools・hooks など) があるため、name と description のほかは許さない
    key = extra.split(":", 1)[0]
    reason = check_game_skill_text(
        "game-dialog", skill_text("game-dialog", extra=extra)
    )
    assert reason is not None
    assert key in reason


def test_skill_whose_name_differs_from_the_directory_is_rejected():
    # 本体の操作のスキル (switch-*) などの名前を名乗らせない
    reason = check_game_skill_text("game-dialog", skill_text("switch-select-user"))
    assert reason is not None
    assert "switch-select-user" in reason
    assert "game-dialog" in reason


@pytest.mark.parametrize(
    "text",
    [
        "会話の枠が出たら A を押す。\n",
        "---\nname: game-dialog\ndescription: d\n",
        "\n---\nname: game-dialog\ndescription: d\n---\n本文\n",
    ],
)
def test_skill_without_closed_frontmatter_is_rejected(text):
    assert check_game_skill_text("game-dialog", text) == NO_FRONTMATTER_REASON


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: game-dialog\ndescription: |\n  複数行の説明\n",
        "name: game-dialog\ndescription: d\n# コメント\n",
        "name: game-dialog\n  allowed-tools: Bash\ndescription: d\n",
        "name: game-dialog\ndescription: d\n- Bash\n",
        "{name: game-dialog, allowed-tools: Bash}\n",
    ],
)
def test_frontmatter_lines_that_are_not_one_line_key_values_are_rejected(frontmatter):
    text = f"---\n{frontmatter}---\n本文\n"
    assert check_game_skill_text("game-dialog", text) is not None


def test_duplicated_name_is_rejected():
    text = (
        "---\nname: game-dialog\ndescription: d\nname: switch-select-user\n---\n本文\n"
    )
    assert check_game_skill_text("game-dialog", text) is not None


@pytest.mark.parametrize(
    ("frontmatter", "missing"),
    [
        ("name: game-dialog\n", "description"),
        ("description: d\n", "name"),
        ("name: game-dialog\ndescription:   \n", "description"),
        ("", "name, description"),
    ],
)
def test_missing_name_or_description_is_rejected(frontmatter, missing):
    text = f"---\n{frontmatter}---\n本文\n"
    assert check_game_skill_text("game-dialog", text) == MISSING_KEYS_REASON.format(
        keys=missing
    )


@pytest.mark.parametrize(
    "body",
    [
        # インラインの形 !`<コマンド>` (https://code.claude.com/docs/en/skills の dynamic context injection)
        "!`git status`\n",
        "結果: !`cat notes/strategy.md`\n",
        # 行の途中の ! は実行されないが、ここでは位置に依らず写さない
        "KEY=!`id`\n",
        # 複数行の形 (```! で始まるコードブロック)
        "```!\nnode --version\n```\n",
        "  ```!\nls\n```\n",
        "~~~!\nls\n~~~\n",
    ],
)
def test_skill_with_shell_command_syntax_is_rejected(body):
    assert has_skill_shell_command(body) is True
    reason = check_game_skill_text("game-dialog", skill_text("game-dialog", body=body))
    assert reason == SHELL_COMMAND_REASON


@pytest.mark.parametrize(
    "body",
    [
        "A を押す!\n",
        '```json\n{"actions": []}\n```\n',
        "`notes/strategy.md` を読む\n",
    ],
)
def test_ordinary_text_is_not_shell_command_syntax(body):
    assert has_skill_shell_command(body) is False


# --- ゲームの内容のスキルを写す ---


def test_game_skill_with_name_and_description_is_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(notes_skills, "game-dialog", skill_text("game-dialog"))
    write_skill_text(notes_skills, "game-shop", skill_text("game-shop"))
    result = copy_game_skills(paths)
    assert result == GameSkillCopyResult(
        copied=("game-dialog", "game-shop"), skipped=()
    )
    assert tree(paths.claude_skills_dir / "game-dialog") == {
        "SKILL.md": skill_text("game-dialog").encode("utf-8")
    }


def test_files_other_than_skill_md_are_not_copied(tmp_path):
    # notes/ は Claude が読めるため、参考のファイルは notes/ から読む
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    skill = write_skill_text(
        paths.root / "notes" / "skills", "game-dialog", skill_text("game-dialog")
    )
    (skill / "reference.md").write_text("参考\n", encoding="utf-8")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.sh").write_text("echo x\n", encoding="utf-8")
    assert copy_game_skills(paths).copied == ("game-dialog",)
    assert sorted(tree(paths.claude_skills_dir / "game-dialog")) == ["SKILL.md"]


def test_rejected_skills_are_not_copied_and_are_reported(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(notes_skills, "game-good", skill_text("game-good"))
    write_skill_text(
        notes_skills,
        "game-tools",
        skill_text("game-tools", extra="allowed-tools: Bash\n"),
    )
    write_skill_text(notes_skills, "game-renamed", skill_text("switch-select-user"))
    write_skill_text(
        notes_skills, "game-shell", skill_text("game-shell", body="!`id`\n")
    )
    result = copy_game_skills(paths)
    assert result.copied == ("game-good",)
    assert [skipped.name for skipped in result.skipped] == [
        "game-renamed",
        "game-shell",
        "game-tools",
    ]
    for skipped in result.skipped:
        text = (notes_skills / skipped.name / "SKILL.md").read_text(encoding="utf-8")
        assert skipped == SkippedGameSkill(
            name=skipped.name, reason=check_game_skill_text(skipped.name, text)
        )
    assert sorted(path.name for path in paths.claude_skills_dir.iterdir()) == [
        "game-good"
    ]


def test_previous_copy_stays_when_the_new_version_is_rejected(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(notes_skills, "game-dialog", skill_text("game-dialog"))
    assert copy_game_skills(paths).copied == ("game-dialog",)
    write_skill_text(
        notes_skills,
        "game-dialog",
        skill_text("game-dialog", extra="allowed-tools: Bash\n"),
    )
    result = copy_game_skills(paths)
    assert result.copied == ()
    assert [skipped.name for skipped in result.skipped] == ["game-dialog"]
    copied = paths.claude_skills_dir / "game-dialog" / "SKILL.md"
    assert copied.read_text(encoding="utf-8") == skill_text("game-dialog")


def test_skill_md_that_is_not_utf8_is_reported(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    skill = paths.root / "notes" / "skills" / "game-binary"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"---\nname: game-binary\n\xff\xfe\n---\n")
    result = copy_game_skills(paths)
    assert result == GameSkillCopyResult(
        copied=(),
        skipped=(SkippedGameSkill(name="game-binary", reason=UNREADABLE_REASON),),
    )


def test_names_not_starting_with_game_and_directories_without_skill_are_ignored(
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
    assert copy_game_skills(paths) == GameSkillCopyResult(copied=(), skipped=())
    assert list(paths.claude_skills_dir.iterdir()) == []


def test_symlinked_skill_directory_is_ignored(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    outside = write_skill(tmp_path / "outside", "game-outside")
    notes_skills = paths.root / "notes" / "skills"
    notes_skills.mkdir()
    os.symlink(outside, notes_skills / "game-linked")
    assert copy_game_skills(paths) == GameSkillCopyResult(copied=(), skipped=())
    assert not (paths.claude_skills_dir / "game-linked").exists()


def test_symlinked_skill_file_is_ignored(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    secret = tmp_path / "secret.md"
    secret.write_text(skill_text("game-linked-file"), encoding="utf-8")
    skill = paths.root / "notes" / "skills" / "game-linked-file"
    skill.mkdir(parents=True)
    os.symlink(secret, skill / "SKILL.md")
    assert copy_game_skills(paths) == GameSkillCopyResult(copied=(), skipped=())
    assert not (paths.claude_skills_dir / "game-linked-file").exists()


def test_copy_replaces_a_skill_and_leaves_only_skill_md(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(
        notes_skills, "game-dialog", skill_text("game-dialog", body="first\n")
    )
    assert copy_game_skills(paths).copied == ("game-dialog",)
    target = paths.claude_skills_dir / "game-dialog"
    # 前の版の、SKILL.md 以外のファイル
    (target / "old.md").write_text("old\n", encoding="utf-8")
    write_skill_text(
        notes_skills, "game-dialog", skill_text("game-dialog", body="second\n")
    )
    assert copy_game_skills(paths).copied == ("game-dialog",)
    assert tree(target) == {
        "SKILL.md": skill_text("game-dialog", body="second\n").encode("utf-8")
    }


def test_unchanged_skill_is_not_copied_again(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    write_skill_text(
        paths.root / "notes" / "skills", "game-dialog", skill_text("game-dialog")
    )
    assert copy_game_skills(paths).copied == ("game-dialog",)
    assert copy_game_skills(paths) == GameSkillCopyResult(copied=(), skipped=())


def test_skill_removed_from_notes_stays_in_claude_skills(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    skill = write_skill_text(
        paths.root / "notes" / "skills", "game-dialog", skill_text("game-dialog")
    )
    copy_game_skills(paths)
    (skill / "SKILL.md").unlink()
    skill.rmdir()
    assert copy_game_skills(paths).copied == ()
    assert (paths.claude_skills_dir / "game-dialog" / "SKILL.md").is_file()


def test_missing_notes_skills_directory_copies_nothing(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    assert copy_game_skills(paths) == GameSkillCopyResult(copied=(), skipped=())


def test_game_skill_that_cannot_be_read_is_skipped_and_others_are_copied(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    bad = write_skill_text(notes_skills, "game-bad", skill_text("game-bad"))
    write_skill_text(notes_skills, "game-good", skill_text("game-good"))
    (bad / "SKILL.md").chmod(0)
    try:
        result = copy_game_skills(paths)
    finally:
        (bad / "SKILL.md").chmod(0o644)
    assert result == GameSkillCopyResult(copied=("game-good",), skipped=())
    assert not (paths.claude_skills_dir / "game-bad").exists()
