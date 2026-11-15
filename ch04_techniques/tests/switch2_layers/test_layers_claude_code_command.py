import json
from pathlib import Path

import pytest

from switch2_layers.deep.claude_code_command import (
    CLAUDE_EXECUTABLE,
    DEEP_EFFORT,
    DEEP_MODEL,
    DEEP_PERMISSION_MODE,
    DEEP_SETTINGS,
    DEEP_TOOLS,
    DISALLOWED_TOOLS,
    HIDDEN_ENV_NAMES,
    ClaudeCodeOptions,
    RulePathError,
    build_allowed_tools,
    build_child_env,
    build_claude_command,
    deep_settings_json,
    edit_rule,
    read_rule,
    rule_path,
    validate_document_note,
)

RUN_DIR = Path("/home/user/ch04/runs/20261115-120000")
RUN = "/home/user/ch04/runs/20261115-120000"
TIPS_NOTES = [
    "scenes/scene-001/tips.md",
    "scenes/scene-001/box_targets.md",
    "common/tips.md",
]
PROMPT = "あなたは deep 層である。"
# 使えない状態で起動するツール (コマンドの実行・Web・サブエージェント・skill。MCP のツールは mcp__ で始まる)
FORBIDDEN_TOOLS = ["Bash", "WebSearch", "WebFetch", "Agent", "Skill"]
UNSAFE_NOTES = [
    "",  # 空
    "/etc/passwd",  # 絶対パス
    "../run.jsonl",  # notes/ の外
    "scenes/../../run.jsonl",
    "./advice.md",
    "scenes//tips.md",  # 空の要素
    "scenes/",
    "my notes.md",  # 空白
    "a,b.md",  # カンマ (ルールの区切り)
    "tips(1).md",  # 括弧
    "*.md",  # gitignore の特殊文字
    "scenes/**",
    "tips[1].md",
    "!advice.md",
    "#advice.md",
    "a\\b.md",
]


def option_value(command: list[str], option: str) -> str:
    """command の option の直後の値を返す (option は 1 回だけ現れる)。"""
    assert command.count(option) == 1, option
    return command[command.index(option) + 1]


# --- 定数 ---


def test_model_effort_and_executable_follow_the_issue():
    assert CLAUDE_EXECUTABLE == "claude"
    assert DEEP_MODEL == "claude-opus-5-5"
    assert DEEP_EFFORT == "high"
    assert DEEP_PERMISSION_MODE == "dontAsk"
    assert ClaudeCodeOptions() == ClaudeCodeOptions(
        executable="claude",
        model="claude-opus-5-5",
        effort="high",
        hidden_env_names=("TYPESAFE_API_KEY", "OPENAI_API_KEY"),
    )


def test_only_read_glob_grep_edit_and_write_are_available():
    assert DEEP_TOOLS == ("Read", "Glob", "Grep", "Edit", "Write")
    for tool in [*FORBIDDEN_TOOLS, "NotebookEdit", "PowerShell", "Monitor"]:
        assert tool not in DEEP_TOOLS


def test_disallowed_tools_remove_bash_web_subagents_skills_and_mcp():
    assert set(DISALLOWED_TOOLS) == {*FORBIDDEN_TOOLS, "mcp__*"}
    # ツール名だけのルール (そのツールを Claude から見えなくする)。使える 5 つのツールは拒まない
    for rule in DISALLOWED_TOOLS:
        assert "(" not in rule
        assert rule not in DEEP_TOOLS


def test_hidden_env_names_are_the_other_layers_api_keys():
    assert set(HIDDEN_ENV_NAMES) == {"TYPESAFE_API_KEY", "OPENAI_API_KEY"}


def test_deep_settings_disable_hooks_memory_connectors_and_outside_access():
    assert DEEP_SETTINGS["disableAllHooks"] is True
    assert DEEP_SETTINGS["autoMemoryEnabled"] is False
    assert DEEP_SETTINGS["disableClaudeAiConnectors"] is True
    permissions = DEEP_SETTINGS["permissions"]
    assert isinstance(permissions, dict)
    assert permissions["blockReadsOutsideWorkingDirectories"] is True
    assert permissions["blockWritesOutsideWorkingDirectories"] is True
    # 許すルールは --allowedTools だけで渡す
    assert "allow" not in permissions


def test_deep_settings_json_is_compact_json_of_the_settings():
    text = deep_settings_json()
    assert json.loads(text) == DEEP_SETTINGS
    assert " " not in text


# --- パス ---


def test_rule_path_of_absolute_path():
    assert rule_path(RUN_DIR) == RUN


@pytest.mark.parametrize(
    "path",
    [
        Path("runs/x"),  # 絶対パスでない
        Path("/home/user/my runs/x"),  # 空白
        Path("/home/user/runs,x"),  # カンマ (ルールの区切り)
        Path("/home/user/runs(x)"),  # 括弧
        Path("/home/user/runs*"),  # gitignore の特殊文字
        Path("/home/user/runs[1]"),
        Path("/home/user/runs!"),
        Path("/home/user/runs$x"),  # シェルの特殊文字
        Path("/home/user/runs;x"),
    ],
)
def test_rule_path_rejects_paths_that_break_rules(path):
    with pytest.raises(RulePathError):
        rule_path(path)


def test_rule_path_error_is_a_value_error():
    assert issubclass(RulePathError, ValueError)


@pytest.mark.parametrize(
    "note",
    ["advice.md", "strategy.md", "common/controls.md", *TIPS_NOTES, "map/town-2.md"],
)
def test_validate_document_note_accepts_document_paths(note):
    assert validate_document_note(note) == note


@pytest.mark.parametrize("note", UNSAFE_NOTES)
def test_validate_document_note_rejects_paths_outside_notes_or_breaking_rules(note):
    with pytest.raises(RulePathError):
        validate_document_note(note)


# --- 許すルール ---


def test_read_rule_allows_reading_inside_the_run_directory():
    assert read_rule(RUN_DIR) == f"Read(/{RUN}/**)"
    assert read_rule(RUN_DIR) == "Read(//home/user/ch04/runs/20261115-120000/**)"


def test_edit_rule_allows_writing_one_document_in_notes():
    assert edit_rule(RUN_DIR, "scenes/scene-001/tips.md") == (
        "Edit(//home/user/ch04/runs/20261115-120000/notes/scenes/scene-001/tips.md)"
    )
    assert edit_rule(RUN_DIR, "advice.md") == f"Edit(/{RUN}/notes/advice.md)"


@pytest.mark.parametrize("note", UNSAFE_NOTES)
def test_edit_rule_rejects_unsafe_documents(note):
    with pytest.raises(RulePathError):
        edit_rule(RUN_DIR, note)


def test_rules_reject_unsafe_run_directories():
    for unsafe in [Path("runs/x"), Path("/home/user/my runs/x")]:
        with pytest.raises(RulePathError):
            read_rule(unsafe)
        with pytest.raises(RulePathError):
            edit_rule(unsafe, "advice.md")


def test_allowed_tools_are_read_in_run_dir_and_edit_of_each_document():
    assert build_allowed_tools(RUN_DIR, TIPS_NOTES) == [
        f"Read(/{RUN}/**)",
        f"Edit(/{RUN}/notes/scenes/scene-001/tips.md)",
        f"Edit(/{RUN}/notes/scenes/scene-001/box_targets.md)",
        f"Edit(/{RUN}/notes/common/tips.md)",
    ]
    assert build_allowed_tools(RUN_DIR, ["strategy.md"]) == [
        f"Read(/{RUN}/**)",
        f"Edit(/{RUN}/notes/strategy.md)",
    ]
    assert build_allowed_tools(RUN_DIR, []) == [f"Read(/{RUN}/**)"]


def test_allowed_tools_scope_every_rule_to_a_path():
    rules = build_allowed_tools(RUN_DIR, TIPS_NOTES)
    # パスを限らないルール・ほかのツールのルールを含めない
    for unscoped in ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Bash(*)", "*"]:
        assert unscoped not in rules
    for rule in rules:
        assert rule.startswith(("Read(//", "Edit(//")), rule
    # Write・Glob・Grep のパスのルールは照合されないため書かない (Edit が Write に、Read が Glob と Grep に効く)
    assert not any(
        rule.startswith(("Write(", "Glob(", "Grep(", "Bash(")) for rule in rules
    )
    # 読み取りのルールは実行ディレクトリーの 1 つだけ
    assert [rule for rule in rules if rule.startswith("Read(")] == [read_rule(RUN_DIR)]
    # 書き込みのルールは対象の文書のファイルそのもの (ワイルドカードを含まない)
    edit_rules = [rule for rule in rules if rule.startswith("Edit(")]
    assert edit_rules == [edit_rule(RUN_DIR, note) for note in TIPS_NOTES]
    for rule in edit_rules:
        assert "*" not in rule
        assert rule.startswith(f"Edit(/{RUN}/notes/")


def test_allowed_tools_reject_unsafe_documents():
    with pytest.raises(RulePathError):
        build_allowed_tools(RUN_DIR, ["advice.md", "../run.jsonl"])


# --- コマンド ---


def test_build_claude_command():
    command = build_claude_command(PROMPT, RUN_DIR, TIPS_NOTES, ClaudeCodeOptions())
    assert command == [
        "claude",
        "-p",
        PROMPT,
        "--model",
        "claude-opus-5-5",
        "--effort",
        "high",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--settings",
        deep_settings_json(),
        "--tools",
        "Read,Glob,Grep,Edit,Write",
        "--allowedTools",
        ",".join(build_allowed_tools(RUN_DIR, TIPS_NOTES)),
        "--disallowedTools",
        "Bash,WebSearch,WebFetch,Agent,Skill,mcp__*",
    ]


def test_build_claude_command_limits_the_tools_to_five():
    command = build_claude_command(PROMPT, RUN_DIR, TIPS_NOTES, ClaudeCodeOptions())
    assert option_value(command, "--tools").split(",") == [
        "Read",
        "Glob",
        "Grep",
        "Edit",
        "Write",
    ]
    disallowed = option_value(command, "--disallowedTools").split(",")
    for tool in [*FORBIDDEN_TOOLS, "mcp__*"]:
        assert tool in disallowed
    allowed = option_value(command, "--allowedTools").split(",")
    assert allowed == build_allowed_tools(RUN_DIR, TIPS_NOTES)


def test_build_claude_command_loads_no_mcp_plugins_skills_or_user_settings():
    command = build_claude_command(PROMPT, RUN_DIR, TIPS_NOTES, ClaudeCodeOptions())
    # ユーザー・プロジェクトの設定ファイル・CLAUDE.md・hooks・plugins・MCP サーバー・skills を読まない
    for flag in [
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ]:
        assert command.count(flag) == 1, flag
    # MCP サーバー・plugin・エージェント・ディレクトリーを足す引数を渡さない
    for forbidden in [
        "--mcp-config",
        "--plugin-dir",
        "--plugin-url",
        "--agents",
        "--agent",
        "--add-dir",
        "--setting-sources",
    ]:
        assert forbidden not in command
    settings = json.loads(option_value(command, "--settings"))
    assert settings["disableAllHooks"] is True
    assert settings["disableClaudeAiConnectors"] is True


def test_build_claude_command_never_skips_permissions():
    command = build_claude_command(PROMPT, RUN_DIR, TIPS_NOTES, ClaudeCodeOptions())
    for forbidden in [
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--bare",
    ]:
        assert forbidden not in command
    assert option_value(command, "--permission-mode") == "dontAsk"
    for mode in ["bypassPermissions", "acceptEdits", "auto", "default", "manual"]:
        assert mode not in command


def test_build_claude_command_uses_options():
    options = ClaudeCodeOptions(
        executable="/opt/fake-claude", model="claude-sonnet-5", effort="low"
    )
    command = build_claude_command(PROMPT, RUN_DIR, ["advice.md"], options)
    assert command[0] == "/opt/fake-claude"
    assert command[1:3] == ["-p", PROMPT]
    assert option_value(command, "--model") == "claude-sonnet-5"
    assert option_value(command, "--effort") == "low"
    assert option_value(command, "--allowedTools") == (
        f"Read(/{RUN}/**),Edit(/{RUN}/notes/advice.md)"
    )


@pytest.mark.parametrize("prompt", ["", "/review", "--help", "-p"])
def test_build_claude_command_rejects_prompts_read_as_commands_or_options(prompt):
    with pytest.raises(ValueError):
        build_claude_command(prompt, RUN_DIR, TIPS_NOTES, ClaudeCodeOptions())


def test_build_claude_command_rejects_unsafe_documents():
    with pytest.raises(RulePathError):
        build_claude_command(PROMPT, RUN_DIR, ["a,b.md"], ClaudeCodeOptions())


# --- 環境変数 ---


def test_child_env_removes_api_keys_of_other_layers():
    environ = {
        "TYPESAFE_API_KEY": "typesafe",
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
    }
    env = build_child_env(environ, HIDDEN_ENV_NAMES)
    assert env == {
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
    }
    # 元の辞書は変えない
    assert "TYPESAFE_API_KEY" in environ
