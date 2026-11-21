"""codex_player のテストと、CodexPlayer で動かした run_agent_until のテスト (本物の codex は起動しない)。"""

import signal
from pathlib import Path

from harness_fakes import (
    FakeClock,
    FakeDaemon,
    FakeProcessStarter,
    FakeSleep,
    ProcessPlan,
    codex_message_line,
    codex_thread_line,
    codex_turn_completed_line,
    make_paths,
)

from switch2_harness.agent_launcher import (
    CLAUDE_PLAYER,
    RESUME_PROMPT,
    LaunchOptions,
    RunOutcome,
    build_claude_args,
    build_claude_env,
    first_prompt,
    run_agent_until,
)
from switch2_harness.codex_command import build_codex_env, build_codex_exec_args
from switch2_harness.codex_player import CodexPlayer
from switch2_harness.event_log import CommandEvent
from switch2_harness.features import BASE_AGENT_COMMANDS

GAME = "ゲーム A"
START_TIME = 1000.0
CODEX_HOME = Path("/home/player/.cache/switch2-harness/codex-home/play-abc")
INSTRUCTIONS = "# 指示\n"
BASE_ENV = {"PATH": "/usr/bin", "OPENAI_API_KEY": "secret", "CLAUDECODE": "1"}


def make_player() -> CodexPlayer:
    return CodexPlayer(CODEX_HOME, INSTRUCTIONS)


def test_claude_player_uses_the_claude_functions(tmp_path):
    paths = make_paths(tmp_path / "play")
    options = LaunchOptions(effort="high")
    assert CLAUDE_PLAYER.name == "claude"
    assert CLAUDE_PLAYER.build_args(
        "p", BASE_AGENT_COMMANDS, paths, options, "s1", ("notes.md",)
    ) == build_claude_args(
        "p",
        BASE_AGENT_COMMANDS,
        paths.instructions_file,
        options,
        "s1",
        writable_files=("notes.md",),
    )
    assert CLAUDE_PLAYER.build_env(BASE_ENV, paths.root) == build_claude_env(
        BASE_ENV, paths.root
    )


def test_codex_player_builds_exec_args(tmp_path):
    paths = make_paths(tmp_path / "play")
    player = make_player()
    assert player.name == "codex"
    args = player.build_args(
        "始めて",
        BASE_AGENT_COMMANDS,
        paths,
        LaunchOptions(model="m", effort="low"),
        None,
        (),
    )
    assert args == build_codex_exec_args("始めて", "m", "low", INSTRUCTIONS)
    resumed = player.build_args(
        "続けて", BASE_AGENT_COMMANDS, paths, LaunchOptions(model="m"), "t1", ()
    )
    assert resumed == build_codex_exec_args("続けて", "m", None, INSTRUCTIONS, "t1")


def test_codex_player_uses_the_codex_default_model_without_a_model(tmp_path):
    paths = make_paths(tmp_path / "play")
    args = make_player().build_args("p", (), paths, LaunchOptions(model=None), None, ())
    assert args[args.index("-m") + 1] == "gpt-6.2-sol"


def test_codex_player_env_and_log_reading(tmp_path):
    root = tmp_path / "play"
    player = make_player()
    assert player.build_env(BASE_ENV, root) == build_codex_env(
        BASE_ENV, CODEX_HOME, root
    )
    lines = [
        codex_thread_line("t1"),
        codex_message_line("a"),
        codex_turn_completed_line(5),
    ]
    assert player.resume_id(lines) == "t1"
    assert player.result_subtype(lines) is None
    assert player.spent_usd(lines) is None


# --- run_agent_until を CodexPlayer で動かす ---


def run_codex(tmp_path: Path, plans: list[ProcessPlan], minutes: float = 1.0):
    clock = FakeClock(START_TIME)
    paths = make_paths(tmp_path / "play")
    starter = FakeProcessStarter(clock, plans)
    daemon = FakeDaemon(clock)
    events: list[CommandEvent] = []
    outcome = run_agent_until(
        minutes,
        paths,
        GAME,
        BASE_AGENT_COMMANDS,
        LaunchOptions(model="gpt-6.2-sol", effort="low"),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=FakeSleep(clock),
        send=daemon,
        record=events.append,
        base_env=BASE_ENV,
        writable_files=("notes.md",),
        agent=make_player(),
    )
    return outcome, paths, starter, daemon, events


def test_run_starts_codex_in_the_workspace(tmp_path):
    outcome, paths, starter, daemon, events = run_codex(
        tmp_path, [ProcessPlan(runs_for_s=None)]
    )
    assert outcome == RunOutcome(reason="deadline", launches=1)
    [launch] = starter.launches
    assert launch.args == build_codex_exec_args(
        first_prompt(GAME), "gpt-6.2-sol", "low", INSTRUCTIONS
    )
    assert launch.cwd == paths.root
    assert launch.env == build_codex_env(BASE_ENV, CODEX_HOME, paths.root)
    assert launch.stdout_path == paths.agent_log_file
    assert launch.stderr_path == paths.agent_stderr_file
    # 期限で止めるときは Claude Code と同じく SIGINT から送る
    assert starter.processes[0].calls[0] == ("signal", signal.SIGINT)
    assert daemon.sent == [["up", "ALL"]]
    assert [event.reply for event in events[:2]] == [
        "launched codex launch=1",
        "stopped reason=deadline launches=1",
    ]


def test_run_resumes_codex_with_the_last_thread_id(tmp_path):
    first = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(
            "WARNING: not json",
            codex_thread_line("t1"),
            codex_message_line("進みます"),
            codex_turn_completed_line(100),
        ),
    )
    outcome, _, starter, _, events = run_codex(
        tmp_path, [first, ProcessPlan(runs_for_s=None)]
    )
    assert outcome == RunOutcome(reason="deadline", launches=2)
    second = starter.launches[1]
    assert second.args == build_codex_exec_args(
        RESUME_PROMPT, "gpt-6.2-sol", "low", INSTRUCTIONS, "t1"
    )
    resume = events[1]
    assert resume.args == ("resume", "t1")
    assert resume.reply == "launched codex launch=2 resume=t1"


def test_run_stops_codex_without_a_thread_id(tmp_path):
    plan = ProcessPlan(runs_for_s=5.0, output_lines=("Reading additional input",))
    outcome, _, starter, _, _ = run_codex(tmp_path, [plan])
    assert outcome == RunOutcome(reason="no_session", launches=1)
    assert len(starter.launches) == 1


def test_run_resumes_codex_after_a_failed_turn(tmp_path):
    # 予算の概念が無いので、ターンが失敗して終わっても起動し直す
    failed = ProcessPlan(
        runs_for_s=5.0,
        output_lines=(
            codex_thread_line("t1"),
            '{"type": "turn.failed", "error": {"message": "x"}}',
        ),
    )
    outcome, _, starter, _, _ = run_codex(
        tmp_path, [failed, ProcessPlan(runs_for_s=None)]
    )
    assert outcome == RunOutcome(reason="deadline", launches=2)
    assert starter.launches[1].args[2] == "resume"
