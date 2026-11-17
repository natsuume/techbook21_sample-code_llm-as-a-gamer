import inspect
import itertools
import json
import math
import os
import signal
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from harness_fakes import (
    FakeAgentProcess,
    FakeClock,
    FakeDaemon,
    FakeProcessStarter,
    FakeSleep,
    ProcessPlan,
    make_paths,
)

from switch2_harness.agent_launcher import (
    API_KEY_ENV_SUFFIX,
    BUDGET_EXHAUSTED_SUBTYPE,
    CLAUDE_ENV_OVERRIDES,
    CLAUDE_SETTINGS_JSON,
    DEFAULT_MODEL,
    KEPT_API_KEY_ENV_PREFIX,
    KEPT_CLAUDE_ENV_NAMES,
    KEPT_CLAUDE_ENV_PREFIX,
    MAX_CLEANUP_ATTEMPTS,
    MAX_RESUMES,
    MIN_REMAINING_S,
    POLL_INTERVAL_S,
    RELEASE_RETRY_INTERVAL_S,
    RELEASE_TIMEOUT_S,
    RESUME_PROMPT,
    SKIP_AUTH_ENV_PREFIX,
    SKIP_AUTH_ENV_SUFFIX,
    STOP_GRACE_S,
    TERMINATE_GRACE_S,
    LaunchOptions,
    RunOutcome,
    agent_log_size,
    allowed_tools,
    build_claude_args,
    build_claude_env,
    first_prompt,
    format_budget_usd,
    is_kept_env_name,
    last_result_subtype,
    last_session_id,
    max_total_cost_usd,
    read_agent_log_lines,
    read_last_result_subtype,
    read_last_session_id,
    read_max_total_cost_usd,
    run_agent_until,
    start_claude_process,
    stop_process,
    stop_reason_after_exit,
    tool_names,
    validate_max_budget_usd,
    validate_minutes,
)
from switch2_harness.daemon_client import SendCommand
from switch2_harness.event_log import CommandEvent
from switch2_harness.features import BASE_AGENT_COMMANDS
from switch2_harness.process_tree import (
    ProcessEntry,
    kill_sessions,
    read_descendants,
)
from switch2_harness.signal_guard import (
    RunTerminated,
    defer_interrupts,
    ignore_interrupts,
)
from switch2_harness.workspace import WorkspacePaths
from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

GAME = "ゲーム A"
START_TIME = 1000.0


def session_line(session_id: str) -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id})


def result_line(
    session_id: str, subtype: str = "success", total_cost_usd: float = 0.1
) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "session_id": session_id,
            "total_cost_usd": total_cost_usd,
        }
    )


# 日本語の文字の途中 (3 バイトの文字の 2 バイト目まで) で切れた行。強制終了したプロセスの出力に現れる
BROKEN_LINE = '{"type": "assistant", "message": {"text": "日本'.encode()[:-1]


def write_log_bytes(path: Path, *lines: bytes) -> None:
    path.write_bytes(b"".join(line + b"\n" for line in lines))


# --- 定数とプロンプト ---


def test_constants():
    assert DEFAULT_MODEL == "claude-opus-5-5"
    assert RESUME_PROMPT == "プレイを続けてください。"
    assert POLL_INTERVAL_S == 1.0
    assert STOP_GRACE_S == 20.0
    assert TERMINATE_GRACE_S == 10.0
    assert MIN_REMAINING_S == 30.0
    assert MAX_RESUMES == 20
    assert RELEASE_TIMEOUT_S == 10.0
    assert BUDGET_EXHAUSTED_SUBTYPE == "error_max_budget_usd"
    assert KEPT_CLAUDE_ENV_NAMES == ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN")
    assert KEPT_CLAUDE_ENV_PREFIX == "CLAUDE_CODE_USE_"
    assert SKIP_AUTH_ENV_PREFIX == "CLAUDE_CODE_SKIP_"
    assert SKIP_AUTH_ENV_SUFFIX == "_AUTH"
    assert KEPT_API_KEY_ENV_PREFIX == "ANTHROPIC_"
    assert API_KEY_ENV_SUFFIX == "_API_KEY"


def test_claude_settings_json_blocks_reads_outside_the_workspace():
    assert CLAUDE_SETTINGS_JSON == json.dumps(
        {"permissions": {"blockReadsOutsideWorkingDirectories": True}}
    )
    assert json.loads(CLAUDE_SETTINGS_JSON) == {
        "permissions": {"blockReadsOutsideWorkingDirectories": True}
    }


def test_launch_options_defaults():
    assert LaunchOptions() == LaunchOptions(
        model="claude-opus-5-5", effort=None, max_budget_usd=None
    )


def test_first_prompt():
    assert first_prompt("ゼルダの伝説") == (
        "指示に従って、ゲーム「ゼルダの伝説」のプレイを始めてください。"
    )


def test_allowed_tools_lists_only_harness_commands():
    assert allowed_tools(("look", "press")) == [
        "Bash(uv run switch2-harness look *)",
        "Bash(uv run switch2-harness press *)",
    ]


def test_allowed_tools_adds_edit_rules_for_writable_files():
    assert allowed_tools(("look",), ("notes.md", "map.md")) == [
        "Bash(uv run switch2-harness look *)",
        "Edit(./notes.md)",
        "Edit(./map.md)",
    ]


def test_tool_names_without_writable_files():
    assert tool_names(()) == "Bash,Read"


def test_tool_names_with_writable_files_adds_write_and_edit():
    assert tool_names(("notes.md",)) == "Bash,Read,Write,Edit"


@pytest.mark.parametrize(
    ("budget", "text"),
    [
        (2.5, "2.5000"),
        (1.0, "1.0000"),
        (1.0 - 0.4, "0.6000"),
        (1.0 - 0.7, "0.3000"),
        (12.0, "12.0000"),
    ],
)
def test_format_budget_usd(budget, text):
    assert format_budget_usd(budget) == text


# --- 起動引数と環境変数 ---


def test_build_claude_args_in_order():
    args = build_claude_args(
        "プロンプト",
        BASE_AGENT_COMMANDS,
        Path("/w/CLAUDE.md"),
        LaunchOptions(),
    )
    assert args == [
        "claude",
        "-p",
        "プロンプト",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Bash,Read",
        "--allowedTools",
        "Bash(uv run switch2-harness look *)",
        "Bash(uv run switch2-harness press *)",
        "Bash(uv run switch2-harness status *)",
        "Bash(uv run switch2-harness finish *)",
        "--disallowedTools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--restricted",
        "--settings",
        '{"permissions": {"blockReadsOutsideWorkingDirectories": true}}',
        "--append-system-prompt-file",
        "/w/CLAUDE.md",
        "--model",
        "claude-opus-5-5",
    ]
    assert "--setting-sources" not in args
    assert "Read" not in args


def test_build_claude_args_with_options_and_resume():
    args = build_claude_args(
        "P",
        ("look",),
        Path("/w/CLAUDE.md"),
        LaunchOptions(model="m", effort="high", max_budget_usd=2.5),
        resume_session_id="abc",
    )
    assert args[:3] == ["claude", "-p", "P"]
    assert args[-8:] == [
        "--model",
        "m",
        "--effort",
        "high",
        "--max-budget-usd",
        "2.5000",
        "--resume",
        "abc",
    ]


def test_build_claude_args_adds_only_given_options():
    args = build_claude_args(
        "P",
        ("look",),
        Path("/w/CLAUDE.md"),
        LaunchOptions(model="m", max_budget_usd=1.0),
    )
    assert args[-4:] == ["--model", "m", "--max-budget-usd", "1.0000"]
    assert "--effort" not in args
    assert "--resume" not in args


def test_build_claude_args_puts_feature_commands_in_allowed_tools():
    args = build_claude_args(
        "P",
        ("look", "press", "status", "finish", "history"),
        Path("/w/CLAUDE.md"),
        LaunchOptions(),
    )
    start = args.index("--allowedTools") + 1
    end = args.index("--disallowedTools")
    assert args[start:end] == [
        "Bash(uv run switch2-harness look *)",
        "Bash(uv run switch2-harness press *)",
        "Bash(uv run switch2-harness status *)",
        "Bash(uv run switch2-harness finish *)",
        "Bash(uv run switch2-harness history *)",
    ]


def test_build_claude_args_with_writable_files_allows_write_and_edit():
    args = build_claude_args(
        "P",
        BASE_AGENT_COMMANDS,
        Path("/w/CLAUDE.md"),
        LaunchOptions(),
        writable_files=("notes.md",),
    )
    assert args[args.index("--tools") + 1] == "Bash,Read,Write,Edit"
    start = args.index("--allowedTools") + 1
    end = args.index("--disallowedTools")
    assert args[start:end] == [
        "Bash(uv run switch2-harness look *)",
        "Bash(uv run switch2-harness press *)",
        "Bash(uv run switch2-harness status *)",
        "Bash(uv run switch2-harness finish *)",
        "Edit(./notes.md)",
    ]
    assert "Read" not in args


def test_claude_env_overrides():
    assert dict(CLAUDE_ENV_OVERRIDES) == {
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }


# build_claude_env に渡す作業ディレクトリー
WORKSPACE = Path("/plays/one")


def test_build_claude_env_copies_and_adds():
    base = {"PATH": "/usr/bin", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}
    env = build_claude_env(base, WORKSPACE)
    assert env == {
        "PATH": "/usr/bin",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "SWITCH2_HARNESS_WORKSPACE": "/plays/one",
    }
    assert base == {"PATH": "/usr/bin", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}


def test_build_claude_env_pins_the_workspace():
    env = build_claude_env(
        {"PATH": "/usr/bin", PINNED_WORKSPACE_ENV: "/plays/other"}, WORKSPACE
    )
    assert env[PINNED_WORKSPACE_ENV] == "/plays/one"


# run を起動した親の Claude Code のセッションの環境にある変数
PARENT_SESSION_ENV = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/claude-messaging.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "secret-token",
    "CLAUDE_CODE_SESSION_ID": "parent-session",
    "CLAUDE_EFFORT": "high",
    "CLAUDE_PID": "1234",
}
OVERRIDES = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "SWITCH2_HARNESS_WORKSPACE": "/plays/one",
}


def test_build_claude_env_removes_parent_session_variables():
    env = build_claude_env({"PATH": "/usr/bin", **PARENT_SESSION_ENV}, WORKSPACE)
    assert env == {"PATH": "/usr/bin", **OVERRIDES}


def test_build_claude_env_keeps_claude_code_auth_and_config():
    kept = {
        "CLAUDE_CONFIG_DIR": "/home/u/.claude-eval",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "ANTHROPIC_API_KEY": "anthropic-key",
    }
    assert build_claude_env(kept, WORKSPACE) == {**kept, **OVERRIDES}


def test_build_claude_env_removes_other_api_keys():
    env = build_claude_env(
        {
            "OPENAI_API_KEY": "openai-key",
            "GEMINI_API_KEY": "gemini-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
            "PATH": "/usr/bin",
        },
        WORKSPACE,
    )
    assert env == {
        "ANTHROPIC_API_KEY": "anthropic-key",
        "PATH": "/usr/bin",
        **OVERRIDES,
    }


def test_build_claude_env_keeps_unrelated_variables():
    unrelated = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "ja_JP.UTF-8",
        "MY_CLAUDE_NOTE": "x",
        "API_KEY_FILE": "/etc/key",
        "OPENAI_API_KEY_FILE": "/etc/openai",
        "claude_lowercase": "y",
    }
    assert build_claude_env(unrelated, WORKSPACE) == {**unrelated, **OVERRIDES}


def test_build_claude_env_does_not_change_base():
    base = {"PATH": "/usr/bin", "OPENAI_API_KEY": "o", **PARENT_SESSION_ENV}
    copy = dict(base)
    build_claude_env(base, WORKSPACE)
    assert base == copy


def test_build_claude_env_keeps_provider_selection_and_auth_variables():
    provider = {
        "CLAUDE_CODE_USE_FOUNDRY": "1",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1",
        "ANTHROPIC_FOUNDRY_API_KEY": "foundry-key",
        "ANTHROPIC_BASE_URL": "https://gateway.example",
        "AWS_REGION": "us-east-1",
        "GOOGLE_CLOUD_PROJECT": "project",
    }
    assert build_claude_env(provider, WORKSPACE) == {**provider, **OVERRIDES}


def test_build_claude_env_removes_unknown_skip_variables_and_other_keys():
    env = build_claude_env(
        {
            "CLAUDE_CODE_SKIP_FOO": "1",
            "CLAUDE_CODE_SKIP_AUTH_LATER": "1",
            "OPENAI_API_KEY": "openai-key",
            "PATH": "/usr/bin",
        },
        WORKSPACE,
    )
    assert env == {"PATH": "/usr/bin", **OVERRIDES}


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("CLAUDE_CONFIG_DIR", True),
        ("CLAUDE_CODE_OAUTH_TOKEN", True),
        ("CLAUDE_CODE_USE_BEDROCK", True),
        ("CLAUDE_CODE_USE_FOUNDRY", True),
        ("CLAUDE_CODE_SKIP_VERTEX_AUTH", True),
        ("CLAUDE_CODE_SKIP_FOO", False),
        ("CLAUDECODE", False),
        ("CLAUDE_EFFORT", False),
        ("ANTHROPIC_API_KEY", True),
        ("ANTHROPIC_FOUNDRY_API_KEY", True),
        ("ANTHROPIC_MODEL", True),
        ("OPENAI_API_KEY", False),
        ("GEMINI_API_KEY", False),
        ("PATH", True),
    ],
)
def test_is_kept_env_name(name, kept):
    assert is_kept_env_name(name) is kept


# --- session_id の読み取り ---


def test_last_session_id_returns_the_last_one():
    lines = [
        session_line("old"),
        json.dumps({"type": "assistant", "message": {}}),
        "not json",
        "",
        json.dumps(["session_id", "x"]),
        json.dumps({"type": "result", "session_id": "abc"}),
        json.dumps({"type": "result", "session_id": 123}),
        '{"type": "assistant", "session_id": "cut',
    ]
    assert last_session_id(lines) == "abc"


def test_last_session_id_without_session_is_none():
    assert last_session_id([]) is None
    assert last_session_id(["not json", json.dumps({"type": "x"})]) is None


def test_read_last_session_id_from_file(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text(session_line("s1") + "\n" + result_line("s2") + "\n", "utf-8")
    assert read_last_session_id(path, 0) == "s2"


def test_read_last_session_id_of_missing_file_is_none(tmp_path):
    assert read_last_session_id(tmp_path / "agent.jsonl", 0) is None


def test_read_last_session_id_reads_only_after_offset(tmp_path):
    path = tmp_path / "agent.jsonl"
    earlier = (session_line("前の run") + "\n").encode()
    write_log_bytes(path, earlier.rstrip(b"\n"), session_line("today").encode())
    assert read_last_session_id(path, len(earlier)) == "today"


def test_read_last_session_id_after_offset_without_session_is_none(tmp_path):
    path = tmp_path / "agent.jsonl"
    write_log_bytes(path, session_line("old").encode())
    assert read_last_session_id(path, path.stat().st_size) is None


def test_read_last_session_id_skips_a_line_cut_in_a_character(tmp_path):
    path = tmp_path / "agent.jsonl"
    write_log_bytes(path, BROKEN_LINE, session_line("s1").encode())
    assert read_last_session_id(path, 0) == "s1"


# --- agent.jsonl の大きさと行の読み込み ---


def test_agent_log_size(tmp_path):
    path = tmp_path / "agent.jsonl"
    assert agent_log_size(path) == 0
    path.write_bytes("日本\n".encode())
    assert agent_log_size(path) == 7


def test_read_agent_log_lines_from_offset(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_bytes("前の行\n今回の行 1\n今回の行 2\n".encode())
    offset = len("前の行\n".encode())
    assert read_agent_log_lines(path, offset) == ["今回の行 1", "今回の行 2"]
    assert read_agent_log_lines(path, 0) == ["前の行", "今回の行 1", "今回の行 2"]


def test_read_agent_log_lines_of_missing_file_is_empty(tmp_path):
    assert read_agent_log_lines(tmp_path / "agent.jsonl", 0) == []


def test_read_agent_log_lines_replaces_broken_bytes(tmp_path):
    path = tmp_path / "agent.jsonl"
    write_log_bytes(path, BROKEN_LINE, session_line("s1").encode())
    first, second = read_agent_log_lines(path, 0)
    assert first.endswith("日\N{REPLACEMENT CHARACTER}")
    assert second == session_line("s1")


def test_read_agent_log_lines_splits_only_on_newline(tmp_path):
    path = tmp_path / "agent.jsonl"
    # stream-json の文字列の中には U+2028 (行区切り) がエスケープされずに入ることがある
    line = '{"type": "system", "session_id": "s1", "text": "a\N{LINE SEPARATOR}b"}'
    path.write_bytes((line + "\n").encode())
    assert read_agent_log_lines(path, 0) == [line]
    assert read_last_session_id(path, 0) == "s1"


# --- 最後の result の subtype の読み取り ---


def test_last_result_subtype_returns_the_last_result_subtype():
    lines = [
        session_line("s1"),
        result_line("s1", subtype="success"),
        result_line("s1", subtype="error_max_budget_usd"),
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "result", "subtype": 5}),
        json.dumps({"type": "result"}),
        "not json",
        "",
        json.dumps(["result", "success"]),
        '{"type": "result", "subtype": "succ',
    ]
    assert last_result_subtype(lines) == "error_max_budget_usd"


def test_last_result_subtype_ignores_subtype_of_other_messages():
    lines = [result_line("s1", subtype="success"), session_line("s1")]
    assert last_result_subtype(lines) == "success"


def test_last_result_subtype_without_result_is_none():
    assert last_result_subtype([]) is None
    assert last_result_subtype([session_line("s1"), "not json"]) is None


def test_read_last_result_subtype_from_file(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text(
        result_line("s1") + "\n" + result_line("s1", "error_max_budget_usd") + "\n",
        "utf-8",
    )
    assert read_last_result_subtype(path, 0) == "error_max_budget_usd"


def test_read_last_result_subtype_of_missing_file_is_none(tmp_path):
    assert read_last_result_subtype(tmp_path / "agent.jsonl", 0) is None


def test_read_last_result_subtype_reads_only_after_offset(tmp_path):
    path = tmp_path / "agent.jsonl"
    earlier = (result_line("old", "error_max_budget_usd") + "\n").encode()
    path.write_bytes(earlier + (result_line("new", "success") + "\n").encode())
    assert read_last_result_subtype(path, len(earlier)) == "success"
    assert read_last_result_subtype(path, path.stat().st_size) is None


def test_read_last_result_subtype_skips_a_line_cut_in_a_character(tmp_path):
    path = tmp_path / "agent.jsonl"
    write_log_bytes(path, BROKEN_LINE, result_line("s1", "success").encode())
    assert read_last_result_subtype(path, 0) == "success"


# --- result の total_cost_usd の最大の値の読み取り ---


def test_max_total_cost_usd_returns_the_largest_number():
    lines = [
        result_line("s1", total_cost_usd=0.25),
        json.dumps({"type": "result", "total_cost_usd": 1}),
        json.dumps({"type": "result", "total_cost_usd": True}),
        json.dumps({"type": "result", "total_cost_usd": "0.7"}),
        json.dumps({"type": "result", "total_cost_usd": None}),
        json.dumps({"type": "result"}),
        json.dumps({"type": "assistant", "total_cost_usd": 9.9}),
        "not json",
        json.dumps([{"type": "result", "total_cost_usd": 3.0}]),
        '{"type": "result", "total_cost_usd": 4.',
    ]
    cost = max_total_cost_usd(lines)
    assert cost == 1.0
    assert isinstance(cost, float)


def test_max_total_cost_usd_keeps_the_cost_before_a_crash():
    # クラッシュで終わった result は total_cost_usd が 0 になることがある
    lines = [
        result_line("s1", total_cost_usd=0.4),
        result_line("s1", subtype="error_during_execution", total_cost_usd=0.0),
    ]
    assert max_total_cost_usd(lines) == pytest.approx(0.4)


def test_max_total_cost_usd_without_result_is_none():
    assert max_total_cost_usd([]) is None
    assert max_total_cost_usd([session_line("s1")]) is None


def test_read_max_total_cost_usd_reads_only_after_offset(tmp_path):
    path = tmp_path / "agent.jsonl"
    earlier = (result_line("old", total_cost_usd=0.9) + "\n").encode()
    path.write_bytes(earlier + (result_line("new", total_cost_usd=0.2) + "\n").encode())
    assert read_max_total_cost_usd(path, 0) == pytest.approx(0.9)
    assert read_max_total_cost_usd(path, len(earlier)) == pytest.approx(0.2)
    assert read_max_total_cost_usd(path, path.stat().st_size) is None


def test_read_max_total_cost_usd_of_missing_file_is_none(tmp_path):
    assert read_max_total_cost_usd(tmp_path / "agent.jsonl", 0) is None


def test_read_max_total_cost_usd_skips_a_line_cut_in_a_character(tmp_path):
    path = tmp_path / "agent.jsonl"
    write_log_bytes(path, BROKEN_LINE, result_line("s1", total_cost_usd=0.4).encode())
    assert read_max_total_cost_usd(path, 0) == pytest.approx(0.4)


# --- --minutes の検証 ---


@pytest.mark.parametrize("minutes", [0.5, 1.0, 30.0])
def test_validate_minutes_accepts_positive_numbers(minutes):
    assert validate_minutes(minutes) is None


@pytest.mark.parametrize("minutes", [0.0, -1.0, math.nan])
def test_validate_minutes_rejects_zero_negative_and_nan(minutes):
    with pytest.raises(ValueError) as error:
        validate_minutes(minutes)
    assert str(error.value).startswith("--minutes must be greater than 0: ")


# --- --max-budget-usd の検証 ---


@pytest.mark.parametrize("budget", [None, 0.01, 1.0, 25.5])
def test_validate_max_budget_usd_accepts_none_and_positive_finite(budget):
    assert validate_max_budget_usd(budget) is None


@pytest.mark.parametrize(
    ("budget", "shown"),
    [(0.0, "0.0"), (-1.0, "-1.0"), (math.nan, "nan"), (math.inf, "inf")],
)
def test_validate_max_budget_usd_rejects_non_positive_and_non_finite(budget, shown):
    with pytest.raises(ValueError) as error:
        validate_max_budget_usd(budget)
    assert str(error.value) == (
        f"--max-budget-usd must be a positive finite number: {shown}"
    )


def test_validate_max_budget_usd_rejects_a_budget_that_rounds_to_zero():
    # 0.00004 は --max-budget-usd に渡す文字列が "0.0000" になる
    with pytest.raises(ValueError) as error:
        validate_max_budget_usd(0.00004)
    assert str(error.value).startswith(
        "--max-budget-usd must be a positive finite number: "
    )


def test_validate_max_budget_usd_accepts_the_smallest_rounded_budget():
    assert validate_max_budget_usd(0.0001) is None


# --- 終わったプロセスを起動し直すかの判定 ---


@pytest.mark.parametrize(
    ("remaining_s", "subtype", "budget_left", "resumes", "session_id", "reason"),
    [
        (60.0, "success", 0.5, 0, "s1", None),
        (60.0, None, None, 0, "s1", None),
        (29.9, "success", 0.5, 0, "s1", "deadline"),
        (29.9, "error_max_budget_usd", 0.0, 20, None, "deadline"),
        (60.0, "error_max_budget_usd", 0.5, 0, "s1", "budget_exhausted"),
        (60.0, "success", 0.0, 0, "s1", "budget_exhausted"),
        (60.0, "success", -0.2, 0, "s1", "budget_exhausted"),
        (60.0, "success", 0.00004, 0, "s1", "budget_exhausted"),
        (60.0, "success", 0.0001, 0, "s1", None),
        (60.0, "success", 0.0, 20, None, "budget_exhausted"),
        (60.0, "success", None, 20, "s1", "resumes_exhausted"),
        (60.0, "success", None, 20, None, "resumes_exhausted"),
        (60.0, "success", None, 19, None, "no_session"),
    ],
)
def test_stop_reason_after_exit(
    remaining_s, subtype, budget_left, resumes, session_id, reason
):
    assert (
        stop_reason_after_exit(
            remaining_s=remaining_s,
            last_subtype=subtype,
            remaining_budget_usd=budget_left,
            resumes=resumes,
            session_id=session_id,
        )
        == reason
    )


# --- プロセスの起動と停止 ---


def test_start_claude_process_appends_output_and_uses_cwd_and_env(tmp_path):
    cwd = tmp_path / "w"
    cwd.mkdir()
    stdout_path = tmp_path / "out.jsonl"
    stderr_path = tmp_path / "err.log"
    stdout_path.write_text("old\n", encoding="utf-8")
    script = (
        "import os, sys; print(os.getcwd()); print(os.environ['HARNESS_TEST']); "
        "print('err', file=sys.stderr)"
    )
    env = {"HARNESS_TEST": "1", "PATH": os.environ.get("PATH", "")}
    process = start_claude_process(
        [sys.executable, "-c", script], cwd, env, stdout_path, stderr_path
    )
    assert process.wait(timeout=30) == 0
    assert stdout_path.read_text(encoding="utf-8") == f"old\n{cwd}\n1\n"
    assert stderr_path.read_text(encoding="utf-8") == "err\n"


def test_start_claude_process_starts_a_new_process_group(tmp_path):
    process = start_claude_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        {"PATH": os.environ.get("PATH", "")},
        tmp_path / "out.jsonl",
        tmp_path / "err.log",
    )
    try:
        assert os.getpgid(process.pid) == process.pid
        assert os.getsid(process.pid) != os.getsid(0)
        process.signal_group(signal.SIGTERM)
        assert process.wait(timeout=10) == -signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            process.signal_group(0)
    finally:
        if process.poll() is None:
            process.signal_group(signal.SIGKILL)
            process.wait(timeout=10)


class DescendantRecorder:
    """stop_process の snapshot_descendants と stop_sessions の fake。

    呼ばれたことを process.calls に足し、プロセスへの操作との順序を確かめられるようにする。
    """

    def __init__(
        self, process: FakeAgentProcess, descendants: list[ProcessEntry]
    ) -> None:
        self.process = process
        self.descendants = descendants

    def snapshot(self, root_pid: int) -> list[ProcessEntry]:
        self.process.calls.append(("snapshot", root_pid))
        return list(self.descendants)

    def stop_sessions(self, sessions: set[int], sig: int) -> None:
        self.process.calls.append(("stop_sessions", frozenset(sessions), sig))


def stop_with_recorder(
    process: FakeAgentProcess, descendants: list[ProcessEntry] | None = None
) -> None:
    recorder = DescendantRecorder(process, descendants or [])
    stop_process(
        process,
        snapshot_descendants=recorder.snapshot,
        stop_sessions=recorder.stop_sessions,
    )


def descendant(pid: int, ppid: int, sid: int) -> ProcessEntry:
    return ProcessEntry(pid=pid, state="S", ppid=ppid, pgid=pid, sid=sid, start_time=9)


def test_stop_process_ends_with_sigint_without_snapshotting_descendants():
    process = FakeAgentProcess(FakeClock(), ProcessPlan(runs_for_s=None))
    stop_with_recorder(process, [descendant(501, process.pid, 501)])
    assert process.calls == [("signal", signal.SIGINT), ("wait", 20.0)]


def test_stop_process_does_not_fail_when_the_group_is_gone():
    process = FakeAgentProcess(FakeClock(), ProcessPlan(runs_for_s=None))
    process.exit_now()
    stop_with_recorder(process)
    assert process.calls == [("wait", 20.0)]


def test_stop_process_sends_sigterm_when_sigint_is_ignored():
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset({signal.SIGTERM}))
    process = FakeAgentProcess(FakeClock(), plan)
    stop_with_recorder(process, [descendant(501, process.pid, 501)])
    assert process.calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
        ("signal", signal.SIGTERM),
        ("wait", 10.0),
    ]


def test_stop_process_stops_descendant_sessions_after_sigkill():
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset())
    process = FakeAgentProcess(FakeClock(), plan)
    # Bash ツールのコマンド (別のセッション) 2 つと、その子 (同じセッション)
    descendants = [
        descendant(501, process.pid, 501),
        descendant(502, 501, 501),
        descendant(601, process.pid, 601),
    ]
    stop_with_recorder(process, descendants)
    assert process.calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
        ("signal", signal.SIGTERM),
        ("wait", 10.0),
        ("snapshot", process.pid),
        ("signal", signal.SIGKILL),
        ("wait", None),
        ("stop_sessions", frozenset({process.pid, 501, 601}), signal.SIGKILL),
    ]


def test_stop_process_stops_claude_session_without_descendants():
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset())
    process = FakeAgentProcess(FakeClock(), plan)
    stop_with_recorder(process, [])
    assert process.calls[-1] == (
        "stop_sessions",
        frozenset({process.pid}),
        signal.SIGKILL,
    )


def test_stop_process_defaults_to_process_tree_functions():
    defaults = inspect.signature(stop_process).parameters
    assert defaults["snapshot_descendants"].default is read_descendants
    assert defaults["stop_sessions"].default is kill_sessions


# --- run_agent_until の流れ ---


@dataclass
class Scenario:
    clock: FakeClock
    paths: WorkspacePaths
    starter: FakeProcessStarter
    daemon: FakeDaemon
    sleep: FakeSleep
    events: list[CommandEvent]


def make_scenario(
    tmp_path: Path,
    plans: list[ProcessPlan],
    daemon_overrides: dict[str, str] | None = None,
    interrupt_at_call: int | None = None,
) -> Scenario:
    clock = FakeClock(START_TIME)
    return Scenario(
        clock=clock,
        paths=make_paths(tmp_path),
        starter=FakeProcessStarter(clock, plans),
        daemon=FakeDaemon(clock, daemon_overrides),
        sleep=FakeSleep(clock, interrupt_at_call=interrupt_at_call),
        events=[],
    )


def run_scenario(
    scenario: Scenario,
    minutes: float = 1.0,
    options: LaunchOptions | None = None,
    send: SendCommand | None = None,
    sleep: Callable[[float], None] | None = None,
    shield: Callable[[], AbstractContextManager[None]] = ignore_interrupts,
    defer: Callable[[], AbstractContextManager[None]] = defer_interrupts,
    writable_files: Sequence[str] = (),
) -> RunOutcome:
    """scenario の fake で run_agent_until を呼ぶ。send と sleep は渡したときだけ置き換える。"""
    return run_agent_until(
        minutes,
        scenario.paths,
        GAME,
        BASE_AGENT_COMMANDS,
        options if options is not None else LaunchOptions(),
        start_process=scenario.starter,
        clock=scenario.clock,
        deadline_clock=scenario.clock,
        sleep=sleep if sleep is not None else scenario.sleep,
        send=send if send is not None else scenario.daemon,
        record=scenario.events.append,
        base_env={"PATH": "/usr/bin"},
        writable_files=writable_files,
        shield=shield,
        defer=defer,
    )


def event_kinds(events: list[CommandEvent]) -> list[str]:
    return [event.args[0] for event in events]


def test_run_stops_at_deadline_with_sigint(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=1)
    assert scenario.starter.processes[0].calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
    ]
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert scenario.sleep.calls
    assert all(seconds == POLL_INTERVAL_S for seconds in scenario.sleep.calls)
    assert event_kinds(scenario.events) == ["start", "stop", "release"]
    assert scenario.events[1].time >= START_TIME + 60.0


def test_run_kills_at_deadline_when_signals_are_ignored(tmp_path):
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset())
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(scenario, minutes=0.5)
    assert outcome == RunOutcome(reason="deadline", launches=1)
    assert scenario.starter.processes[0].calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
        ("signal", signal.SIGTERM),
        ("wait", 10.0),
        ("signal", signal.SIGKILL),
        ("wait", None),
    ]
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_records_events(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    run_scenario(scenario, minutes=1.0)
    start, stop, release = scenario.events
    assert start == CommandEvent(
        time=START_TIME,
        command="run",
        args=("start",),
        reply="launched claude launch=1",
        ok=True,
        elapsed_s=0.0,
        frame=None,
    )
    assert stop.command == "run"
    assert stop.args == ("stop", "deadline")
    assert stop.reply == "stopped reason=deadline launches=1"
    assert stop.ok is True
    assert release.command == "run"
    assert release.args == ("release",)
    assert release.reply == "ok held=none"
    assert release.ok is True
    assert release.elapsed_s == pytest.approx(0.5)
    assert release.frame is None


def test_run_starts_claude_in_the_workspace(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    options = LaunchOptions(model="m", effort="high", max_budget_usd=2.5)
    run_scenario(scenario, options=options)
    [launch] = scenario.starter.launches
    assert launch.args == build_claude_args(
        first_prompt(GAME),
        BASE_AGENT_COMMANDS,
        scenario.paths.instructions_file,
        options,
    )
    assert launch.args[1:3] == [
        "-p",
        "指示に従って、ゲーム「ゲーム A」のプレイを始めてください。",
    ]
    assert "--resume" not in launch.args
    assert launch.cwd == scenario.paths.root
    assert launch.env == {
        "PATH": "/usr/bin",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "SWITCH2_HARNESS_WORKSPACE": str(scenario.paths.root),
    }
    assert launch.stdout_path == scenario.paths.agent_log_file
    assert launch.stderr_path == scenario.paths.agent_stderr_file


def test_run_resumes_with_the_last_session_id(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0, output_lines=(session_line("old"), result_line("abc"))
    )
    second = ProcessPlan(runs_for_s=None)
    scenario = make_scenario(tmp_path, [first, second])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=2)
    _, second_launch = scenario.starter.launches
    assert second_launch.args == build_claude_args(
        RESUME_PROMPT,
        BASE_AGENT_COMMANDS,
        scenario.paths.instructions_file,
        LaunchOptions(),
        resume_session_id="abc",
    )
    assert second_launch.args[1:3] == ["-p", "プレイを続けてください。"]
    assert second_launch.args[-2:] == ["--resume", "abc"]
    assert scenario.starter.processes[0].calls == []
    assert scenario.starter.processes[1].calls[0] == ("signal", signal.SIGINT)
    assert event_kinds(scenario.events) == ["start", "resume", "stop", "release"]
    resume = scenario.events[1]
    assert resume.args == ("resume", "abc")
    assert resume.reply == "launched claude launch=2 resume=abc"
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_passes_writable_files_on_launch_and_resume(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0, output_lines=(session_line("old"), result_line("abc"))
    )
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0, writable_files=("notes.md",))
    assert outcome == RunOutcome(reason="deadline", launches=2)
    first_launch, second_launch = scenario.starter.launches
    assert first_launch.args == build_claude_args(
        first_prompt(GAME),
        BASE_AGENT_COMMANDS,
        scenario.paths.instructions_file,
        LaunchOptions(),
        writable_files=("notes.md",),
    )
    assert second_launch.args == build_claude_args(
        RESUME_PROMPT,
        BASE_AGENT_COMMANDS,
        scenario.paths.instructions_file,
        LaunchOptions(),
        resume_session_id="abc",
        writable_files=("notes.md",),
    )
    for launch in (first_launch, second_launch):
        assert launch.args[launch.args.index("--tools") + 1] == "Bash,Read,Write,Edit"
        assert "Edit(./notes.md)" in launch.args


def test_run_resumes_when_exactly_min_remaining_is_left(tmp_path):
    first = ProcessPlan(runs_for_s=30.0, output_lines=(session_line("s"),))
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=2)


def test_run_stops_without_session_id(tmp_path):
    plan = ProcessPlan(runs_for_s=10.0, output_lines=("not json",))
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="no_session", launches=1)
    assert len(scenario.starter.launches) == 1
    assert event_kinds(scenario.events) == ["start", "stop", "release"]
    assert scenario.events[1].args == ("stop", "no_session")
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_stops_when_resumes_are_exhausted(tmp_path):
    plan = ProcessPlan(runs_for_s=1.0, output_lines=(session_line("s"),))
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(scenario, minutes=60.0)
    assert outcome == RunOutcome(reason="resumes_exhausted", launches=21)
    assert len(scenario.starter.launches) == 1 + MAX_RESUMES
    assert all(
        launch.args[-2:] == ["--resume", "s"]
        for launch in scenario.starter.launches[1:]
    )
    kinds = event_kinds(scenario.events)
    assert kinds == ["start"] + ["resume"] * 20 + ["stop", "release"]
    assert scenario.events[-2].args == ("stop", "resumes_exhausted")
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_does_not_resume_with_less_than_min_remaining(tmp_path):
    plan = ProcessPlan(runs_for_s=40.0, output_lines=(session_line("s"),))
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=1)
    assert len(scenario.starter.launches) == 1
    assert scenario.starter.processes[0].calls == []
    assert scenario.events[1].args == ("stop", "deadline")
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_stops_when_budget_is_exhausted(tmp_path):
    plan = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(
            session_line("s1"),
            result_line("s1", subtype="error_max_budget_usd"),
        ),
    )
    scenario = make_scenario(tmp_path, [plan, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="budget_exhausted", launches=1)
    assert len(scenario.starter.launches) == 1
    assert scenario.starter.processes[0].calls == []
    assert event_kinds(scenario.events) == ["start", "stop", "release"]
    stop = scenario.events[1]
    assert stop.args == ("stop", "budget_exhausted")
    assert stop.reply == "stopped reason=budget_exhausted launches=1"
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_checks_budget_before_session_id(tmp_path):
    budget_result = json.dumps({"type": "result", "subtype": "error_max_budget_usd"})
    plan = ProcessPlan(runs_for_s=10.0, output_lines=(budget_result,))
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="budget_exhausted", launches=1)


def test_run_resumes_after_a_successful_result(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", subtype="success")),
    )
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=2)
    assert scenario.starter.launches[1].args[-2:] == ["--resume", "s1"]


def test_run_looks_at_the_last_result_for_the_budget(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", subtype="success")),
    )
    second = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(result_line("s1", subtype="error_max_budget_usd"),),
    )
    scenario = make_scenario(tmp_path, [first, second])
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="budget_exhausted", launches=2)
    assert scenario.daemon.sent == [["up", "ALL"]]


# --- run 全体の予算 ---


def budget_arg(args: list[str]) -> str:
    return args[args.index("--max-budget-usd") + 1]


def test_run_passes_the_remaining_budget_when_resuming(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", total_cost_usd=0.4)),
    )
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    options = LaunchOptions(max_budget_usd=1.0)
    outcome = run_scenario(scenario, minutes=1.0, options=options)
    assert outcome == RunOutcome(reason="deadline", launches=2)
    first_launch, second_launch = scenario.starter.launches
    assert budget_arg(first_launch.args) == "1.0000"
    assert budget_arg(second_launch.args) == "0.6000"
    assert second_launch.args == build_claude_args(
        RESUME_PROMPT,
        BASE_AGENT_COMMANDS,
        scenario.paths.instructions_file,
        LaunchOptions(max_budget_usd=0.6),
        resume_session_id="s1",
    )


def test_run_stops_when_the_run_budget_is_used_up(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", total_cost_usd=0.4)),
    )
    second = ProcessPlan(
        runs_for_s=10.0, output_lines=(result_line("s1", total_cost_usd=1.0),)
    )
    scenario = make_scenario(tmp_path, [first, second, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="budget_exhausted", launches=2)
    assert len(scenario.starter.launches) == 2
    assert scenario.events[-2].args == ("stop", "budget_exhausted")
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_checks_the_run_budget_before_session_id(tmp_path):
    used_up = json.dumps(
        {"type": "result", "subtype": "success", "total_cost_usd": 1.5}
    )
    plan = ProcessPlan(runs_for_s=10.0, output_lines=(used_up,))
    scenario = make_scenario(tmp_path, [plan])
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="budget_exhausted", launches=1)


def test_run_keeps_the_whole_budget_without_result(tmp_path):
    first = ProcessPlan(runs_for_s=10.0, output_lines=(session_line("s1"),))
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    run_scenario(scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0))
    assert budget_arg(scenario.starter.launches[1].args) == "1.0000"


def test_run_without_budget_does_not_pass_max_budget_usd(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", total_cost_usd=5.0)),
    )
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(scenario, minutes=1.0, options=LaunchOptions())
    assert outcome == RunOutcome(reason="deadline", launches=2)
    for launch in scenario.starter.launches:
        assert "--max-budget-usd" not in launch.args


def test_run_stops_when_the_rounded_remaining_budget_is_zero(tmp_path):
    # 残り 0.00004 USD は format_budget_usd で "0.0000" になるため、起動し直さない
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", total_cost_usd=0.99996)),
    )
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="budget_exhausted", launches=1)
    assert len(scenario.starter.launches) == 1
    assert scenario.events[-2].args == ("stop", "budget_exhausted")
    assert scenario.daemon.sent == [["up", "ALL"]]


# --- 後片付けを割り込みから守る ---


@dataclass
class ShieldSnapshot:
    """fake の shield に入ったときと出たときの、プロセスの操作・送信・記録の様子。"""

    moment: str
    process_calls: list[tuple[object, ...]]
    sent: list[list[str]]
    event_kinds: list[str]


def recording_shield(
    scenario: Scenario, snapshots: list[ShieldSnapshot]
) -> Callable[[], AbstractContextManager[None]]:
    """入ったときと出たときに scenario の様子を snapshots に記録する、fake の shield を返す。"""

    def take(moment: str) -> ShieldSnapshot:
        calls = (
            scenario.starter.processes[-1].calls if scenario.starter.processes else []
        )
        return ShieldSnapshot(
            moment=moment,
            process_calls=list(calls),
            sent=[list(args) for args in scenario.daemon.sent],
            event_kinds=event_kinds(scenario.events),
        )

    @contextmanager
    def shield() -> Iterator[None]:
        snapshots.append(take("enter"))
        try:
            yield
        finally:
            snapshots.append(take("exit"))

    return shield


def test_run_cleans_up_inside_the_shield_at_deadline(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    snapshots: list[ShieldSnapshot] = []
    run_scenario(scenario, minutes=1.0, shield=recording_shield(scenario, snapshots))
    stopped = [("signal", signal.SIGINT), ("wait", 20.0)]
    assert snapshots == [
        ShieldSnapshot("enter", [], [], ["start"]),
        ShieldSnapshot("exit", stopped, [["up", "ALL"]], ["start", "stop", "release"]),
    ]


def test_run_cleans_up_inside_the_shield_on_keyboard_interrupt(tmp_path):
    scenario = make_scenario(
        tmp_path, [ProcessPlan(runs_for_s=None)], interrupt_at_call=3
    )
    snapshots: list[ShieldSnapshot] = []
    with pytest.raises(KeyboardInterrupt):
        run_scenario(
            scenario, minutes=1.0, shield=recording_shield(scenario, snapshots)
        )
    stopped = [("signal", signal.SIGINT), ("wait", 20.0)]
    assert snapshots == [
        ShieldSnapshot("enter", [], [], ["start"]),
        ShieldSnapshot("exit", stopped, [["up", "ALL"]], ["start", "release"]),
    ]


def test_run_releases_buttons_even_if_stopping_fails(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])

    def failing_signal(sig: int) -> None:
        raise OSError("cannot signal the process")

    def start_with_failing_signal(
        args: list[str], cwd: Path, env: dict[str, str], stdout: Path, stderr: Path
    ) -> FakeAgentProcess:
        process = scenario.starter(args, cwd, env, stdout, stderr)
        process.signal_group = failing_signal  # シグナルを送ると失敗するプロセスにする
        return process

    with pytest.raises(OSError):
        run_agent_until(
            1.0,
            scenario.paths,
            GAME,
            BASE_AGENT_COMMANDS,
            LaunchOptions(),
            start_process=start_with_failing_signal,
            clock=scenario.clock,
            deadline_clock=scenario.clock,
            sleep=scenario.sleep,
            send=scenario.daemon,
            record=scenario.events.append,
            base_env={},
        )
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "release"]


def test_run_releases_buttons_when_the_first_launch_fails(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])

    def failing_start(
        args: list[str], cwd: Path, env: dict[str, str], stdout: Path, stderr: Path
    ) -> FakeAgentProcess:
        raise FileNotFoundError("claude")

    with pytest.raises(FileNotFoundError):
        run_agent_until(
            1.0,
            scenario.paths,
            GAME,
            BASE_AGENT_COMMANDS,
            LaunchOptions(),
            start_process=failing_start,
            clock=scenario.clock,
            deadline_clock=scenario.clock,
            sleep=scenario.sleep,
            send=scenario.daemon,
            record=scenario.events.append,
            base_env={},
        )
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["release"]


def test_run_does_not_launch_inside_the_shield(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    launches_at_enter: list[int] = []

    @contextmanager
    def shield() -> Iterator[None]:
        launches_at_enter.append(len(scenario.starter.launches))
        yield
        assert len(scenario.starter.launches) == launches_at_enter[-1]

    run_scenario(scenario, minutes=1.0, shield=shield)
    assert launches_at_enter == [1]


# --- 前の run の出力を判定に使わない ---


def test_run_ignores_the_previous_run_session_and_budget_mark(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=10.0)])
    previous_run = [
        session_line("previous"),
        result_line("previous", "error_max_budget_usd", total_cost_usd=0.9),
    ]
    scenario.paths.agent_log_file.write_text(
        "\n".join(previous_run) + "\n", encoding="utf-8"
    )
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="no_session", launches=1)
    assert scenario.events[1].args == ("stop", "no_session")


def test_run_uses_this_run_session_and_cost(tmp_path):
    first = ProcessPlan(runs_for_s=10.0, output_lines=(session_line("today"),))
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    previous_run = [
        session_line("previous"),
        result_line("previous", "success", total_cost_usd=0.9),
    ]
    scenario.paths.agent_log_file.write_text(
        "\n".join(previous_run) + "\n", encoding="utf-8"
    )
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="deadline", launches=2)
    second_launch = scenario.starter.launches[1]
    assert second_launch.args[-2:] == ["--resume", "today"]
    assert budget_arg(second_launch.args) == "1.0000"


def test_run_stops_running_process_before_release_on_keyboard_interrupt(tmp_path):
    scenario = make_scenario(
        tmp_path, [ProcessPlan(runs_for_s=None)], interrupt_at_call=3
    )
    calls_when_sent: list[list[tuple[object, ...]]] = []

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        calls_when_sent.append(list(scenario.starter.processes[0].calls))
        return scenario.daemon(args, timeout_s=timeout_s)

    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0, send=send)
    stopped = [("signal", signal.SIGINT), ("wait", 20.0)]
    assert scenario.starter.processes[0].calls == stopped
    assert calls_when_sent == [stopped]  # up ALL を送る前に止め終わっている
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "release"]


def test_run_does_not_stop_finished_process_on_keyboard_interrupt(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            # 待っている間に claude が自分で終わり、続けて Ctrl-C が届いた
            scenario.starter.processes[0].exit_now()
            raise KeyboardInterrupt
        scenario.clock.advance(seconds)

    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0, sleep=sleep)
    assert scenario.starter.processes[0].calls == []
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "release"]


def test_run_records_release_error(tmp_path):
    scenario = make_scenario(
        tmp_path,
        [ProcessPlan(runs_for_s=None)],
        daemon_overrides={"up": "error bridge is recovering"},
    )
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(
        reason="deadline", launches=1, release_reply="error bridge is recovering"
    )
    release = scenario.events[-1]
    assert release.args == ("release",)
    assert release.reply == "error bridge is recovering"
    assert release.ok is False
    # 接続の拒否ではない error は送り直さない
    assert scenario.daemon.sent == [["up", "ALL"]]


REFUSED_REPLY = (
    "error cannot connect to daemon: [Errno 11] Resource temporarily unavailable"
)


def test_run_resends_release_while_the_daemon_refuses_the_connection(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    replies = iter([REFUSED_REPLY, REFUSED_REPLY])
    release_timeouts: list[float | None] = []

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        if args == ["up", "ALL"]:
            release_timeouts.append(timeout_s)
            scenario.clock.advance(0.01)
            return next(replies, "ok held=none")
        return scenario.daemon(args, timeout_s=timeout_s)

    outcome = run_scenario(scenario, minutes=1.0, send=send)
    assert outcome.release_reply == "ok held=none"
    assert len(release_timeouts) == 3
    # 送り直すほど、各送信の待ち時間の上限は RELEASE_TIMEOUT_S の残りに縮む
    assert release_timeouts[0] == RELEASE_TIMEOUT_S
    assert all(
        later < earlier for earlier, later in itertools.pairwise(release_timeouts)
    )
    release = scenario.events[-1]
    assert release.args == ("release",)
    assert release.ok is True


def test_run_gives_up_resending_release_after_the_timeout(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    release_count = 0

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        nonlocal release_count
        if args == ["up", "ALL"]:
            release_count += 1
            scenario.clock.advance(0.01)
            return REFUSED_REPLY
        return scenario.daemon(args, timeout_s=timeout_s)

    outcome = run_scenario(scenario, minutes=1.0, send=send)
    assert outcome.release_reply == REFUSED_REPLY
    # 0.2 秒おきに送り直し、RELEASE_TIMEOUT_S (10 秒) の中に収まる回数で諦める
    assert 2 <= release_count <= int(RELEASE_TIMEOUT_S / RELEASE_RETRY_INTERVAL_S) + 1
    assert scenario.events[-1].ok is False


# --- 後片付けの up ALL の待ち時間 ---


def test_run_sends_release_with_the_timeout(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    run_scenario(scenario, minutes=1.0)
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert scenario.daemon.timeouts == [RELEASE_TIMEOUT_S]


def test_run_keeps_the_spent_budget_after_a_crashed_result(tmp_path):
    # クラッシュで終わった result の total_cost_usd 0.0 で、使った 0.4 が戻らないこと
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(session_line("s1"), result_line("s1", total_cost_usd=0.4)),
    )
    crashed = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(
            result_line("s1", subtype="error_during_execution", total_cost_usd=0.0),
        ),
    )
    scenario = make_scenario(tmp_path, [first, crashed, ProcessPlan(runs_for_s=None)])
    outcome = run_scenario(
        scenario, minutes=1.0, options=LaunchOptions(max_budget_usd=1.0)
    )
    assert outcome == RunOutcome(reason="deadline", launches=3)
    budgets = [budget_arg(launch.args) for launch in scenario.starter.launches]
    assert budgets == ["1.0000", "0.6000", "0.6000"]


# --- エージェント用のサブコマンドの作業ディレクトリーの固定 ---


def test_run_pins_agent_commands_to_the_workspace(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    run_scenario(scenario, minutes=1.0)
    [launch] = scenario.starter.launches
    assert launch.env[PINNED_WORKSPACE_ENV] == str(scenario.paths.root)


# --- 起動の直後に届いたシグナル ---


def interrupting_defer(
    interrupt_on_use: int,
) -> Callable[[], AbstractContextManager[None]]:
    """interrupt_on_use 回目に使われたとき、抜ける時点で KeyboardInterrupt を送出する fake の defer を返す。

    defer_interrupts が遅らせた Ctrl-C を、抜けるときに送り直した状況の代わり。
    """
    uses: list[int] = []

    @contextmanager
    def defer() -> Iterator[None]:
        uses.append(1)
        yield
        if len(uses) == interrupt_on_use:
            raise KeyboardInterrupt

    return defer


def test_run_stops_a_claude_interrupted_right_after_the_first_launch(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0, defer=interrupting_defer(1))
    [process] = scenario.starter.processes
    assert process.calls == [("signal", signal.SIGINT), ("wait", 20.0)]
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["release"]


def test_run_stops_a_claude_interrupted_right_after_resuming(tmp_path):
    first = ProcessPlan(runs_for_s=10.0, output_lines=(session_line("s1"),))
    scenario = make_scenario(tmp_path, [first, ProcessPlan(runs_for_s=None)])
    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0, defer=interrupting_defer(2))
    first_process, second_process = scenario.starter.processes
    assert first_process.calls == []
    assert second_process.calls == [("signal", signal.SIGINT), ("wait", 20.0)]
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "release"]


# --- 子孫を控えるときの失敗 ---


@pytest.mark.parametrize(
    "error",
    [PermissionError("cannot read /proc"), ValueError("unexpected /proc stat line")],
)
def test_stop_process_sends_sigkill_even_if_snapshotting_fails(error):
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset())
    process = FakeAgentProcess(FakeClock(), plan)
    stopped_sessions: list[set[int]] = []

    def failing_snapshot(root_pid: int) -> list[ProcessEntry]:
        raise error

    def stop_sessions(sessions: set[int], sig: int) -> None:
        stopped_sessions.append(set(sessions))

    stop_process(
        process, snapshot_descendants=failing_snapshot, stop_sessions=stop_sessions
    )
    assert process.calls[-2:] == [("signal", signal.SIGKILL), ("wait", None)]
    # 子孫を控えられなくても、claude 自身のセッションは止める
    assert stopped_sessions == [{process.pid}]


# --- 期限は deadline_clock で計り、記録の時刻は clock で取る ---


@dataclass
class TwoClockRun:
    wall: FakeClock
    mono: FakeClock
    starter: FakeProcessStarter
    sleep_calls: list[float]
    events: list[CommandEvent]
    outcome: RunOutcome


def run_with_two_clocks(tmp_path: Path, jump_wall_at_sleep: int | None) -> TwoClockRun:
    """記録の時計 (clock、wall) と期限の時計 (deadline_clock、mono) を分けて run を動かす。

    sleep は期限の時計だけを進める。jump_wall_at_sleep を渡すと、その回の sleep で記録の時計を 1 時間進める。
    """
    wall = FakeClock(START_TIME)
    mono = FakeClock(50.0)
    starter = FakeProcessStarter(mono, [ProcessPlan(runs_for_s=None)])
    sleep_calls: list[float] = []
    events: list[CommandEvent] = []

    def sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) > 10_000:
            raise AssertionError("sleep was called too many times")
        if len(sleep_calls) == jump_wall_at_sleep:
            wall.advance(3600.0)
        mono.advance(seconds)

    outcome = run_agent_until(
        1.0,
        make_paths(tmp_path),
        GAME,
        BASE_AGENT_COMMANDS,
        LaunchOptions(),
        start_process=starter,
        clock=wall,
        deadline_clock=mono,
        sleep=sleep,
        send=FakeDaemon(mono),
        record=events.append,
        base_env={},
    )
    return TwoClockRun(wall, mono, starter, sleep_calls, events, outcome)


def test_run_does_not_stop_early_when_the_record_clock_jumps(tmp_path):
    run = run_with_two_clocks(tmp_path, jump_wall_at_sleep=10)
    assert run.outcome == RunOutcome(reason="deadline", launches=1)
    # 期限の時計で 60 秒 (POLL_INTERVAL_S ごとに 60 回) たってから止まる
    assert len(run.sleep_calls) == 60
    start, stop, release = run.events
    # 記録の時刻は記録の時計
    assert start.time == START_TIME
    assert stop.time == START_TIME + 3600.0
    assert release.time == START_TIME + 3600.0
    # up ALL にかかった時間は期限の時計で計る (FakeDaemon が期限の時計を 0.5 秒進める)
    assert release.elapsed_s == pytest.approx(0.5)


def test_run_stops_at_the_deadline_clock_without_the_record_clock(tmp_path):
    run = run_with_two_clocks(tmp_path, jump_wall_at_sleep=None)
    assert run.outcome == RunOutcome(reason="deadline", launches=1)
    assert len(run.sleep_calls) == 60
    assert run.wall.now == START_TIME
    assert {event.time for event in run.events} == {START_TIME}
    assert run.starter.processes[0].calls[0] == ("signal", signal.SIGINT)


# --- 後片付けの shield に入る前に届いた割り込み ---


def shield_failing_on_entry(
    error: BaseException, failures: int = 1
) -> tuple[Callable[[], AbstractContextManager[None]], list[int]]:
    """入るときに、最初の failures 回だけ error を送出する fake の shield と、入った回数の記録を返す。

    shield が割り込みを無視に切り替える前に Ctrl-C・SIGTERM が届いた状況の代わり。
    """
    entries: list[int] = []

    @contextmanager
    def shield() -> Iterator[None]:
        entries.append(1)
        if len(entries) <= failures:
            raise error
        yield

    return shield, entries


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt(), RunTerminated(signal.SIGTERM)],
    ids=["sigint", "sigterm"],
)
def test_run_cleans_up_after_an_interrupt_before_the_shield(tmp_path, error):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    shield, entries = shield_failing_on_entry(error)
    with pytest.raises(type(error)) as raised:
        run_scenario(scenario, minutes=1.0, shield=shield)
    assert raised.value is error
    assert len(entries) == 2
    assert scenario.starter.processes[0].calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
    ]
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "stop", "release"]


def test_run_without_interrupts_enters_the_shield_once_and_returns(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    shield, entries = shield_failing_on_entry(KeyboardInterrupt(), failures=0)
    outcome = run_scenario(scenario, minutes=1.0, shield=shield)
    assert outcome == RunOutcome(reason="deadline", launches=1)
    assert len(entries) == 1
    assert scenario.daemon.sent == [["up", "ALL"]]


def test_run_does_not_clean_up_twice_after_an_interrupt_on_leaving_the_shield(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    exits: list[int] = []

    @contextmanager
    def shield() -> Iterator[None]:
        yield
        exits.append(1)
        if len(exits) == 1:
            # 後片付けが済み、shield が元のハンドラーに戻した直後に Ctrl-C が届いた
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0, shield=shield)
    assert exits == [1]
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "stop", "release"]


def test_run_gives_up_cleaning_up_after_max_attempts(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    error = KeyboardInterrupt()
    shield, entries = shield_failing_on_entry(error, failures=MAX_CLEANUP_ATTEMPTS)
    with pytest.raises(KeyboardInterrupt) as raised:
        run_scenario(scenario, minutes=1.0, shield=shield)
    assert raised.value is error
    assert len(entries) == MAX_CLEANUP_ATTEMPTS
    assert scenario.daemon.sent == []


def test_run_reraises_the_first_interrupt(tmp_path):
    scenario = make_scenario(tmp_path, [ProcessPlan(runs_for_s=None)])
    errors = [RunTerminated(signal.SIGHUP), KeyboardInterrupt()]
    entries: list[int] = []

    @contextmanager
    def shield() -> Iterator[None]:
        entries.append(1)
        if len(entries) <= len(errors):
            raise errors[len(entries) - 1]
        yield

    with pytest.raises(RunTerminated) as raised:
        run_scenario(scenario, minutes=1.0, shield=shield)
    assert raised.value is errors[0]
    assert len(entries) == 3
    assert scenario.daemon.sent == [["up", "ALL"]]
