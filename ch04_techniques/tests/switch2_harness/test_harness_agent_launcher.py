import json
import math
import os
import signal
import sys
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
    CLAUDE_ENV_OVERRIDES,
    DEFAULT_MODEL,
    MAX_RESUMES,
    MIN_REMAINING_S,
    POLL_INTERVAL_S,
    RESUME_PROMPT,
    STOP_GRACE_S,
    TERMINATE_GRACE_S,
    LaunchOptions,
    RunOutcome,
    allowed_tools,
    build_claude_args,
    build_claude_env,
    first_prompt,
    last_session_id,
    read_last_session_id,
    run_agent_until,
    start_claude_process,
    stop_process,
    validate_minutes,
)
from switch2_harness.event_log import CommandEvent
from switch2_harness.features import BASE_AGENT_COMMANDS
from switch2_harness.workspace import WorkspacePaths

GAME = "ゲーム A"
START_TIME = 1000.0


def session_line(session_id: str) -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id})


def result_line(session_id: str) -> str:
    return json.dumps(
        {"type": "result", "session_id": session_id, "total_cost_usd": 0.1}
    )


# --- 定数とプロンプト ---


def test_constants():
    assert DEFAULT_MODEL == "claude-opus-5-5"
    assert RESUME_PROMPT == "プレイを続けてください。"
    assert POLL_INTERVAL_S == 1.0
    assert STOP_GRACE_S == 20.0
    assert TERMINATE_GRACE_S == 10.0
    assert MIN_REMAINING_S == 30.0
    assert MAX_RESUMES == 20


def test_launch_options_defaults():
    assert LaunchOptions() == LaunchOptions(
        model="claude-opus-5-5", effort=None, max_budget_usd=None
    )


def test_first_prompt():
    assert first_prompt("ゼルダの伝説") == (
        "指示に従って、ゲーム「ゼルダの伝説」のプレイを始めてください。"
    )


def test_allowed_tools():
    assert allowed_tools(("look", "press")) == [
        "Bash(uv run switch2-harness look *)",
        "Bash(uv run switch2-harness press *)",
        "Read",
    ]


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
        "Read",
        "--disallowedTools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources",
        "project,local",
        "--append-system-prompt-file",
        "/w/CLAUDE.md",
        "--model",
        "claude-opus-5-5",
    ]


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
        "2.5",
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
    assert args[-4:] == ["--model", "m", "--max-budget-usd", "1.0"]
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
        "Read",
    ]


def test_claude_env_overrides():
    assert dict(CLAUDE_ENV_OVERRIDES) == {
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }


def test_build_claude_env_copies_and_adds():
    base = {"PATH": "/usr/bin", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}
    env = build_claude_env(base)
    assert env == {
        "PATH": "/usr/bin",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }
    assert base == {"PATH": "/usr/bin", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}


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
    assert read_last_session_id(path) == "s2"


def test_read_last_session_id_of_missing_file_is_none(tmp_path):
    assert read_last_session_id(tmp_path / "agent.jsonl") is None


# --- --minutes の検証 ---


@pytest.mark.parametrize("minutes", [0.5, 1.0, 30.0])
def test_validate_minutes_accepts_positive_numbers(minutes):
    assert validate_minutes(minutes) is None


@pytest.mark.parametrize("minutes", [0.0, -1.0, math.nan])
def test_validate_minutes_rejects_zero_negative_and_nan(minutes):
    with pytest.raises(ValueError) as error:
        validate_minutes(minutes)
    assert str(error.value).startswith("--minutes must be greater than 0: ")


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


def test_stop_process_ends_with_sigint():
    process = FakeAgentProcess(FakeClock(), ProcessPlan(runs_for_s=None))
    stop_process(process)
    assert process.calls == [("signal", signal.SIGINT), ("wait", 20.0)]


def test_stop_process_sends_sigterm_when_sigint_is_ignored():
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset({signal.SIGTERM}))
    process = FakeAgentProcess(FakeClock(), plan)
    stop_process(process)
    assert process.calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
        ("signal", signal.SIGTERM),
        ("wait", 10.0),
    ]


def test_stop_process_kills_when_sigterm_is_ignored():
    plan = ProcessPlan(runs_for_s=None, stops_on=frozenset())
    process = FakeAgentProcess(FakeClock(), plan)
    stop_process(process)
    assert process.calls == [
        ("signal", signal.SIGINT),
        ("wait", 20.0),
        ("signal", signal.SIGTERM),
        ("wait", 10.0),
        ("kill",),
        ("wait", None),
    ]


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
) -> RunOutcome:
    return run_agent_until(
        minutes,
        scenario.paths,
        GAME,
        BASE_AGENT_COMMANDS,
        options if options is not None else LaunchOptions(),
        start_process=scenario.starter,
        clock=scenario.clock,
        sleep=scenario.sleep,
        send=scenario.daemon,
        record=scenario.events.append,
        base_env={"PATH": "/usr/bin"},
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
        ("kill",),
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


def test_run_releases_buttons_on_keyboard_interrupt(tmp_path):
    scenario = make_scenario(
        tmp_path, [ProcessPlan(runs_for_s=None)], interrupt_at_call=3
    )
    with pytest.raises(KeyboardInterrupt):
        run_scenario(scenario, minutes=1.0)
    assert scenario.daemon.sent == [["up", "ALL"]]
    assert event_kinds(scenario.events) == ["start", "release"]


def test_run_records_release_error(tmp_path):
    scenario = make_scenario(
        tmp_path,
        [ProcessPlan(runs_for_s=None)],
        daemon_overrides={"up": "error bridge is recovering"},
    )
    outcome = run_scenario(scenario, minutes=1.0)
    assert outcome == RunOutcome(reason="deadline", launches=1)
    release = scenario.events[-1]
    assert release.args == ("release",)
    assert release.reply == "error bridge is recovering"
    assert release.ok is False
