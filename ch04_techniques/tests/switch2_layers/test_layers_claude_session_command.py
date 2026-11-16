import inspect
import json
from pathlib import Path

import pytest

from switch2_layers.deep.claude_code_command import (
    HIDDEN_ENV_NAMES,
    PARENT_SESSION_ENV_NAMES,
    SETTING_OVERRIDE_ENV_NAMES,
    RulePathError,
)
from switch2_layers.orchestrator.claude_session_command import (
    AUTH_ENV_NAMES,
    AUTO_COMPACT_START_TOKENS,
    AUTOCOMPACT_MAX_TOKENS,
    BUNDLED_SKILLS_TO_HIDE,
    ROOT_ENTRY_ACCESS,
    SESSION_SETTINGS_BASE,
    ClaudeSessionOptions,
    RootEntryAccess,
    autocompact_arguments,
    build_allowed_rules,
    build_disallowed_rules,
    build_session_command,
    build_session_env,
    build_session_settings,
    session_settings_json,
)
from switch2_layers.run_directory import RunPaths

ROOT = Path("/home/user/runs/20261116-120000")
R = ROOT.as_posix()
INSTRUCTIONS = "あなたは画面だけを見てゲームを進める。"
# 実際に起動して確かめたとき (2026-11-16 と 2026-11-17、Claude Code 2.1.325) の、親のディレクトリーのスキルの名前
PARENT_SKILLS = ("record-tips", "record-advice", "record-strategy", "record-controls")
# 2026-11-17 に起動して確かめた --settings の値 (実行ディレクトリーのパスに依らない)。
# disableSkillShellExecution は https://code.claude.com/docs/en/skills (dynamic context injection) と
# https://code.claude.com/docs/en/settings-reference (disableSkillShellExecution) による
VERIFIED_SETTINGS_JSON = (
    '{"disableAllHooks":true,"autoMemoryEnabled":false,"disableClaudeAiConnectors":true,'
    '"disableBundledSkills":true,"disableSkillShellExecution":true,"syncClaudeAiSkills":false,'
    '"permissions":'
    '{"blockReadsOutsideWorkingDirectories":true,"blockWritesOutsideWorkingDirectories":true},'
    '"pluginConfigs":{"cc-plugin-agents-md@builtin":{"options":{"instructionFiles":"managed-only"}}},'
    '"skillOverrides":{"design":"off","doctor":"off","record-advice":"off","record-controls":"off",'
    '"record-strategy":"off","record-tips":"off"},"crossSessionInbound":"refuse"}'
)
# そのときの許すルールと拒むルール (実行ディレクトリーを ROOT に置き換えたもの)
VERIFIED_ALLOWED_RULES = [
    f"Read(/{R}/frames/**)",
    f"Read(/{R}/notes/**)",
    f"Read(/{R}/.claude/skills/**)",
    f"Edit(/{R}/notes/**)",
    "Skill",
]
VERIFIED_DISALLOWED_RULES = [
    "Bash",
    "PowerShell",
    "WebSearch",
    "WebFetch",
    "Agent",
    "mcp__*",
    f"Read(/{R}/run.jsonl)",
    f"Read(/{R}/frames.jsonl)",
    f"Read(/{R}/surface.jsonl)",
    f"Read(/{R}/surface_requests.jsonl)",
    f"Read(/{R}/safety.jsonl)",
    f"Read(/{R}/operations.jsonl)",
    f"Read(/{R}/middle/**)",
    f"Read(/{R}/deep/**)",
    f"Read(/{R}/progress/**)",
    f"Read(/{R}/orchestrator/**)",
]


def verified_command(executable: str = "claude", effort: str = "medium") -> list[str]:
    """実際に起動して確かめた引数の列 (実行ディレクトリーを ROOT、指示を INSTRUCTIONS に置き換えたもの)。"""
    return [
        executable,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5-5",
        "--effort",
        effort,
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--settings",
        VERIFIED_SETTINGS_JSON,
        "--tools",
        "Read,Glob,Grep,Write,Edit,Skill",
        "--allowedTools",
        ",".join(VERIFIED_ALLOWED_RULES),
        "--disallowedTools",
        ",".join(VERIFIED_DISALLOWED_RULES),
        "--append-system-prompt",
        INSTRUCTIONS,
    ]


# --- コマンド ---


def test_command_is_the_verified_launch():
    command = build_session_command(
        RunPaths(ROOT), INSTRUCTIONS, PARENT_SKILLS, ClaudeSessionOptions(), 120_000
    )
    assert command == verified_command()


def test_command_uses_executable_model_and_effort_from_options():
    options = ClaudeSessionOptions(
        executable="/opt/claude/bin/claude", model="claude-test", effort="high"
    )
    command = build_session_command(
        RunPaths(ROOT), INSTRUCTIONS, PARENT_SKILLS, options, 120_000
    )
    assert command[0] == "/opt/claude/bin/claude"
    assert command[command.index("--model") + 1] == "claude-test"
    assert command[command.index("--effort") + 1] == "high"


def test_default_options():
    options = ClaudeSessionOptions()
    assert options.executable == "claude"
    assert options.model == "claude-opus-5-5"
    assert options.effort == "medium"
    assert options.hidden_env_names == HIDDEN_ENV_NAMES


@pytest.mark.parametrize(
    "flag",
    [
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--restricted",
        "--safe-mode",
        "--bare",
        "--add-dir",
        "--mcp-config",
        "--plugin-dir",
        "--plugin-url",
        "--agents",
        "--agent",
        "bypassPermissions",
    ],
)
def test_command_does_not_use_flags_that_weaken_the_isolation(flag):
    command = build_session_command(
        RunPaths(ROOT), INSTRUCTIONS, PARENT_SKILLS, ClaudeSessionOptions(), 120_000
    )
    assert flag not in command


@pytest.mark.parametrize("instructions", ["", "-p", "--verbose"])
def test_instructions_that_could_be_read_as_options_are_rejected(instructions):
    with pytest.raises(ValueError):
        build_session_command(
            RunPaths(ROOT), instructions, PARENT_SKILLS, ClaudeSessionOptions(), 120_000
        )


def test_command_adds_autocompact_when_rotation_comes_after_the_automatic_summary():
    command = build_session_command(
        RunPaths(ROOT), INSTRUCTIONS, PARENT_SKILLS, ClaudeSessionOptions(), 980_000
    )
    assert command[:-2] == verified_command()
    assert command[-2:] == ["--autocompact", "1000000"]


# --- 許すルールと拒むルール ---


def test_allowed_rules_are_the_verified_rules():
    assert build_allowed_rules(RunPaths(ROOT)) == VERIFIED_ALLOWED_RULES


def test_disallowed_rules_are_the_verified_rules():
    assert build_disallowed_rules(RunPaths(ROOT)) == VERIFIED_DISALLOWED_RULES


def test_every_hidden_root_entry_has_a_read_rule():
    rules = build_disallowed_rules(RunPaths(ROOT))
    for name, access in ROOT_ENTRY_ACCESS.items():
        file_rule = f"Read(/{R}/{name})"
        directory_rule = f"Read(/{R}/{name}/**)"
        if access is RootEntryAccess.HIDDEN_FILE:
            assert file_rule in rules
            assert directory_rule not in rules
        elif access is RootEntryAccess.HIDDEN_DIRECTORY:
            assert directory_rule in rules
            assert file_rule not in rules
        else:
            assert file_rule not in rules
            assert directory_rule not in rules


def run_paths_root_entries(root: Path) -> set[str]:
    """RunPaths の property と、番号や時刻を受け取るパスのメソッドが返すパスの、root からの最初の要素の集合。"""
    paths = RunPaths(root)
    entries: set[str] = set()
    for name, member in inspect.getmembers(RunPaths):
        if isinstance(member, property):
            entries.add(getattr(paths, name).relative_to(root).parts[0])
    for path in (
        paths.interval_frame(1.0),
        paths.surface_frame(1.0, 2),
        paths.orchestrator_session_dir(1),
        paths.orchestrator_session_stream(1),
    ):
        entries.add(path.relative_to(root).parts[0])
    return entries


def test_every_root_entry_of_run_paths_is_decided_readable_or_hidden():
    # RunPaths に実行ディレクトリーの直下の項目を足したら、ROOT_ENTRY_ACCESS で読ませるか拒むかを決める
    entries = run_paths_root_entries(ROOT)
    undecided = entries - set(ROOT_ENTRY_ACCESS)
    assert not undecided, (
        f"decide whether the orchestrator may read {sorted(undecided)}"
    )
    assert set(ROOT_ENTRY_ACCESS) == entries


def test_readable_root_entries_are_frames_notes_and_skills():
    readable = {
        name
        for name, access in ROOT_ENTRY_ACCESS.items()
        if access is RootEntryAccess.READABLE
    }
    assert readable == {"frames", "notes", ".claude"}


@pytest.mark.parametrize(
    "root",
    [
        Path("runs/20261116-120000"),
        Path("/home/user/my runs/20261116-120000"),
        Path("/home/user/runs,1/20261116-120000"),
    ],
)
def test_rules_reject_run_directories_that_cannot_be_embedded(root):
    with pytest.raises(RulePathError):
        build_allowed_rules(RunPaths(root))
    with pytest.raises(RulePathError):
        build_disallowed_rules(RunPaths(root))


# --- --settings ---


def test_settings_are_the_verified_settings():
    assert session_settings_json(PARENT_SKILLS) == VERIFIED_SETTINGS_JSON


def test_settings_keep_the_base_keys_in_order_and_add_overrides_and_inbound():
    settings = build_session_settings(["record-tips", "record-advice"])
    assert list(settings) == [
        *SESSION_SETTINGS_BASE,
        "skillOverrides",
        "crossSessionInbound",
    ]
    for key, value in SESSION_SETTINGS_BASE.items():
        assert settings[key] == value
    assert settings["disableAllHooks"] is True
    assert settings["autoMemoryEnabled"] is False
    assert settings["crossSessionInbound"] == "refuse"


def test_settings_stop_skills_from_running_shell_commands():
    # スキルの本文の !`...` と ```! のブロックを実行させない
    # (https://code.claude.com/docs/en/settings-reference の disableSkillShellExecution)
    assert SESSION_SETTINGS_BASE["disableSkillShellExecution"] is True
    assert build_session_settings([])["disableSkillShellExecution"] is True


def test_skill_overrides_turn_off_parent_and_bundled_skills_in_name_order():
    settings = build_session_settings(["zeta", "record-tips", "doctor", "alpha"])
    overrides = settings["skillOverrides"]
    assert isinstance(overrides, dict)
    assert list(overrides) == ["alpha", "design", "doctor", "record-tips", "zeta"]
    assert set(overrides.values()) == {"off"}


def test_skill_overrides_without_parent_skills_hide_only_bundled_skills():
    settings = build_session_settings([])
    assert settings["skillOverrides"] == {
        name: "off" for name in BUNDLED_SKILLS_TO_HIDE
    }


def test_settings_json_has_no_spaces_and_keeps_non_ascii():
    text = session_settings_json(["記録"])
    assert text == json.dumps(
        build_session_settings(["記録"]), ensure_ascii=False, separators=(",", ":")
    )
    assert "記録" in text


# --- --autocompact ---


@pytest.mark.parametrize("rotate_tokens", [1, 120_000, AUTO_COMPACT_START_TOKENS - 1])
def test_no_autocompact_when_rotation_comes_before_the_automatic_summary(
    rotate_tokens,
):
    assert autocompact_arguments(rotate_tokens) == []


@pytest.mark.parametrize(
    "rotate_tokens", [AUTO_COMPACT_START_TOKENS, AUTOCOMPACT_MAX_TOKENS - 1]
)
def test_autocompact_is_raised_above_rotation(rotate_tokens):
    assert autocompact_arguments(rotate_tokens) == [
        "--autocompact",
        str(AUTOCOMPACT_MAX_TOKENS),
    ]


@pytest.mark.parametrize("rotate_tokens", [0, -1, AUTOCOMPACT_MAX_TOKENS, 2_000_000])
def test_rotate_tokens_that_cannot_be_used_are_rejected(rotate_tokens):
    with pytest.raises(ValueError):
        autocompact_arguments(rotate_tokens)


# --- 環境変数 ---


def test_env_drops_deep_hidden_variables_and_auth_variables():
    environ = {
        "TYPESAFE_API_KEY": "typesafe",
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "ANTHROPIC_AUTH_TOKEN": "token",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "parent",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/run/user/1000/cc-socks/1.sock",
        "CLAUDE_CODE_MESSAGING_TOKEN": "secret",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "CLAUDE_CONFIG_DIR": "/home/user/.claude",
        "HOME": "/home/user",
        "PATH": "/usr/bin",
    }
    before = dict(environ)
    env = build_session_env(environ, HIDDEN_ENV_NAMES)
    assert env == {
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "CLAUDE_CONFIG_DIR": "/home/user/.claude",
        "HOME": "/home/user",
        "PATH": "/usr/bin",
    }
    assert environ == before


def test_env_drops_every_listed_name():
    names = {
        *HIDDEN_ENV_NAMES,
        *PARENT_SESSION_ENV_NAMES,
        *SETTING_OVERRIDE_ENV_NAMES,
        *AUTH_ENV_NAMES,
    }
    environ = {name: "x" for name in names} | {"PATH": "/usr/bin"}
    assert build_session_env(environ, HIDDEN_ENV_NAMES) == {"PATH": "/usr/bin"}


def test_env_drops_the_given_hidden_names():
    environ = {"MY_SECRET": "x", "OPENAI_API_KEY": "kept", "PATH": "/usr/bin"}
    env = build_session_env(environ, ("MY_SECRET",))
    assert env == {"OPENAI_API_KEY": "kept", "PATH": "/usr/bin"}
