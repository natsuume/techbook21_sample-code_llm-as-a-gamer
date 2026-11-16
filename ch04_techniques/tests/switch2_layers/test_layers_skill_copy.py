import os
from pathlib import Path

import pytest

import switch2_layers.orchestrator as orchestrator_package
from switch2_layers.orchestrator.skill_copy import (
    DEFAULT_SWITCH_SKILLS_DIR,
    INVALID_DESCRIPTION_REASON,
    INVALID_NAME_REASON,
    MALFORMED_FRONTMATTER_REASON,
    MISSING_KEYS_REASON,
    NO_FRONTMATTER_REASON,
    SHELL_COMMAND_REASON,
    UNREADABLE_REASON,
    UNUSUAL_LINE_BREAK_CHARACTERS,
    UNUSUAL_LINE_BREAK_REASON,
    GameSkillCopyResult,
    SkippedGameSkill,
    check_game_skill_bytes,
    check_game_skill_text,
    copy_game_skills,
    copy_skill_directories,
    copy_switch_skills,
    game_skills_source_dir,
    has_skill_shell_command,
    is_plain_skill_description,
    is_plain_skill_name,
    prepare_claude_skills_dir,
    read_skill_bytes,
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


@pytest.mark.parametrize(
    "body",
    [
        "手順: ```!\nls\n```\n",
        "x ```! ls\n",
        "x ~~~!\nls\n~~~\n",
        "x ````\t!\nls\n````\n",
    ],
)
def test_fenced_shell_syntax_in_the_middle_of_a_line_is_rejected(body):
    # 複数行の形も、インラインの形と同じく位置に依らず写さない
    assert has_skill_shell_command(body) is True
    reason = check_game_skill_text("game-dialog", skill_text("game-dialog", body=body))
    assert reason == SHELL_COMMAND_REASON


# --- 見慣れない行の区切り ---


def code_point(character: str) -> str:
    return f"U+{ord(character):04X}"


def test_unusual_line_break_characters_are_the_listed_ones():
    assert UNUSUAL_LINE_BREAK_CHARACTERS == (
        "\r",
        "\x0b",
        "\x0c",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        " ",
        " ",
        "\x00",
    )


@pytest.mark.parametrize(
    "character",
    ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " ", "\x00"],
)
def test_skill_with_an_unusual_line_break_is_rejected(character):
    # YAML などの読み手によっては行の区切りになり、frontmatter に欄を足せるため写さない
    text = f"---\nname: game-dialog\ndescription: d{character}allowed-tools: Write\n---\n\n本文\n"
    assert check_game_skill_text("game-dialog", text) == (
        UNUSUAL_LINE_BREAK_REASON.format(character=code_point(character))
    )


def test_unusual_line_break_in_the_body_is_rejected_too():
    text = skill_text("game-dialog", body="1 行目 2 行目\n")
    assert check_game_skill_text("game-dialog", text) == (
        UNUSUAL_LINE_BREAK_REASON.format(character="U+2028")
    )


def test_first_unusual_character_is_reported():
    text = skill_text("game-dialog", body="a\x0cb\x0bc\n")
    assert check_game_skill_text("game-dialog", text) == (
        UNUSUAL_LINE_BREAK_REASON.format(character="U+000C")
    )


def test_crlf_line_endings_are_not_unusual():
    text = skill_text("game-dialog").replace("\n", "\r\n")
    assert check_game_skill_text("game-dialog", text) is None


@pytest.mark.parametrize(
    "character",
    ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " ", "\x00"],
)
def test_skill_md_with_an_unusual_line_break_is_not_copied_and_is_reported(
    tmp_path, character
):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    write_skill_text(
        paths.root / "notes" / "skills",
        "game-dialog",
        f"---\nname: game-dialog\ndescription: d{character}x: y\n---\n\n本文\n",
    )
    result = copy_game_skills(paths)
    assert result == GameSkillCopyResult(
        copied=(),
        skipped=(
            SkippedGameSkill(
                name="game-dialog",
                reason=UNUSUAL_LINE_BREAK_REASON.format(
                    character=code_point(character)
                ),
            ),
        ),
    )
    assert not (paths.claude_skills_dir / "game-dialog").exists()


def test_skill_md_with_crlf_line_endings_is_copied_as_it_is(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    data = skill_text("game-dialog").replace("\n", "\r\n").encode("utf-8")
    skill = paths.root / "notes" / "skills" / "game-dialog"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(data)
    assert copy_game_skills(paths).copied == ("game-dialog",)
    assert (paths.claude_skills_dir / "game-dialog" / "SKILL.md").read_bytes() == data


# --- バイト列の確かめと読み方 ---


def test_bytes_that_are_not_utf8_are_rejected():
    assert check_game_skill_bytes("game-dialog", b"---\nname: \xff\n---\n") == (
        UNREADABLE_REASON
    )


def test_bytes_are_checked_as_their_utf8_text():
    good = skill_text("game-dialog")
    bad = skill_text("game-dialog", extra="allowed-tools: Bash\n")
    assert check_game_skill_bytes("game-dialog", good.encode("utf-8")) is None
    assert check_game_skill_bytes("game-dialog", bad.encode("utf-8")) == (
        check_game_skill_text("game-dialog", bad)
    )


def test_skill_bytes_are_read_from_a_regular_file(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_bytes(b"---\nname: game-dialog\n---\n")
    assert read_skill_bytes(path) == b"---\nname: game-dialog\n---\n"


def test_skill_bytes_are_not_read_through_a_symlink_or_from_a_directory(tmp_path):
    real = tmp_path / "real.md"
    real.write_bytes(b"x")
    os.symlink(real, tmp_path / "SKILL.md")
    with pytest.raises(OSError):
        read_skill_bytes(tmp_path / "SKILL.md")
    (tmp_path / "dir").mkdir()
    with pytest.raises(OSError):
        read_skill_bytes(tmp_path / "dir")


def test_checked_bytes_are_written_even_if_the_source_changes_after_reading(tmp_path):
    # 確かめた中身と写す中身を一致させる: SKILL.md は 1 回だけ読み、読んだバイト列をそのまま書く
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    skill = write_skill_text(
        paths.root / "notes" / "skills", "game-dialog", skill_text("game-dialog")
    )
    checked = (skill / "SKILL.md").read_bytes()
    reads: list[Path] = []

    def read_then_replace(path: Path) -> bytes:
        reads.append(path)
        data = path.read_bytes()
        # 読んだ後に、確かめを通らない中身に書き換えられた
        path.write_text(
            skill_text("game-dialog", extra="allowed-tools: Bash\n"), encoding="utf-8"
        )
        return data

    result = copy_game_skills(paths, read_bytes=read_then_replace)
    assert result == GameSkillCopyResult(copied=("game-dialog",), skipped=())
    assert reads == [skill / "SKILL.md"]
    assert (
        paths.claude_skills_dir / "game-dialog" / "SKILL.md"
    ).read_bytes() == checked


def test_each_skill_md_is_read_once_whether_it_is_copied_or_rejected(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(notes_skills, "game-good", skill_text("game-good"))
    write_skill_text(
        notes_skills, "game-tools", skill_text("game-tools", extra="hooks: x\n")
    )
    reads: list[str] = []

    def counting_read(path: Path) -> bytes:
        reads.append(path.parent.name)
        return path.read_bytes()

    result = copy_game_skills(paths, read_bytes=counting_read)
    assert result.copied == ("game-good",)
    assert [skipped.name for skipped in result.skipped] == ["game-tools"]
    assert sorted(reads) == ["game-good", "game-tools"]


def test_skill_whose_read_fails_is_skipped_without_a_report(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    notes_skills = paths.root / "notes" / "skills"
    write_skill_text(notes_skills, "game-bad", skill_text("game-bad"))
    write_skill_text(notes_skills, "game-good", skill_text("game-good"))

    def failing_read(path: Path) -> bytes:
        if path.parent.name == "game-bad":
            raise PermissionError(13, "Permission denied", str(path))
        return path.read_bytes()

    result = copy_game_skills(paths, read_bytes=failing_read)
    assert result == GameSkillCopyResult(copied=("game-good",), skipped=())
    assert not (paths.claude_skills_dir / "game-bad").exists()


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


# --- frontmatter の値の形 ---
# Claude Code は frontmatter を YAML として読む。YAML で 1 つの文字列として読めない値のスキルを写すと、Claude Code が
# スキルを読めず、その理由も Claude に届かないため、そのような値は写さずに理由を返す


def frontmatter_text(name: str = "game-dialog", description: str = "d") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n本文\n"


@pytest.mark.parametrize(
    "description",
    [
        # 値の途中の ": " (YAML は値の区切りとして読む。タブも同じ)
        "会話を進める: A を押す",
        "会話を進める:\tA を押す",
        # "#" の前の空白 (YAML はコメントの始まりとして読む)
        "会話の枠 #メモ",
        "会話の枠\t#メモ",
        # ":" で終わる
        "会話を進める:",
        # YAML の指示子で始まる
        "- 会話を進める",
        "? 会話",
        ": 会話",
        ", 会話",
        "[会話]",
        "{会話}",
        "#会話",
        "&会話",
        "*会話",
        "!会話",
        "|会話",
        ">会話",
        "'会話を進める'",
        "%会話",
        "@会話",
        "`会話`",
        # YAML が文字列でない値として読む語 (大文字と小文字を区別しない)
        "true",
        "False",
        "YES",
        "no",
        "On",
        "off",
        "null",
        "NULL",
        "~",
        # 数
        "123",
        "1_000",
        "1.5",
        "1e3",
        "0x1F",
        "0o17",
        "0b101",
        ".5",
        ".inf",
        ".NaN",
        "1:30",
        # " で囲んだ中に " か \ がある
        '"会話を "進める""',
        '"会話を\\進める"',
        # 閉じていない "
        '"会話を進める',
    ],
)
def test_description_values_that_yaml_reads_differently_are_rejected(description):
    assert is_plain_skill_description(description) is False
    reason = check_game_skill_text(
        "game-dialog", frontmatter_text(description=description)
    )
    assert reason == INVALID_DESCRIPTION_REASON


@pytest.mark.parametrize(
    "description",
    [
        # " で囲めば、: や # を含んでも 1 つの文字列として読める
        '"会話を進める: A を押す"',
        '"会話の枠 #メモ"',
        '"true"',
        '"123"',
        '"- 項目"',
        # 引用符の無い、ふつうの文
        "会話の枠が出たら A を押す",
        "A を押す (会話を進める)",
        "100 歩進む",
        "会話:進める",
        "メモ#1",
        "truely",
    ],
)
def test_description_values_read_as_one_string_pass(description):
    assert is_plain_skill_description(description) is True
    assert (
        check_game_skill_text("game-dialog", frontmatter_text(description=description))
        is None
    )


@pytest.mark.parametrize(
    "name",
    ["game-Dialog", "game-a_b", "game-", "game--x", "game-x-", "game-会話", "game-a.b"],
)
def test_names_that_are_not_plain_game_names_are_rejected(name):
    # ディレクトリーの名前と同じでも、game- で始まり英小文字・数字・ハイフンだけの名前でなければ写さない
    assert is_plain_skill_name(name) is False
    assert check_game_skill_text(name, frontmatter_text(name=name)) == (
        INVALID_NAME_REASON.format(name=name)
    )


@pytest.mark.parametrize(
    "value", ["game-dialog-2", '"game-dialog-2"', "'game-dialog-2'"]
)
def test_name_may_be_plain_or_quoted(value):
    assert is_plain_skill_name(value) is True
    assert check_game_skill_text("game-dialog-2", frontmatter_text(name=value)) is None


def test_key_without_a_space_before_the_value_is_rejected():
    # YAML は `name:game-dialog` を欄と値ではなく 1 つの文字列として読む
    text = "---\nname:game-dialog\ndescription: d\n---\n本文\n"
    assert check_game_skill_text("game-dialog", text) == (
        MALFORMED_FRONTMATTER_REASON.format(line="name:game-dialog")
    )


def test_quoted_empty_description_is_missing():
    assert check_game_skill_text("game-dialog", frontmatter_text(description='""')) == (
        MISSING_KEYS_REASON.format(keys="description")
    )


def test_reasons_tell_how_to_write_the_frontmatter():
    assert 'description: "' in INVALID_DESCRIPTION_REASON
    assert "name: game-" in INVALID_NAME_REASON


def test_skill_with_a_description_yaml_reads_differently_is_reported(tmp_path):
    paths = make_run(tmp_path)
    prepare_claude_skills_dir(paths)
    write_skill_text(
        paths.root / "notes" / "skills",
        "game-dialog",
        frontmatter_text(description="会話を進める: A を押す"),
    )
    assert copy_game_skills(paths) == GameSkillCopyResult(
        copied=(),
        skipped=(
            SkippedGameSkill(name="game-dialog", reason=INVALID_DESCRIPTION_REASON),
        ),
    )
    assert not (paths.claude_skills_dir / "game-dialog").exists()
