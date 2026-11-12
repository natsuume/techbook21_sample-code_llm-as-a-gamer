import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.advisors.claude_code_headless import (
    ADVISOR_SKILLS,
    TERMINATE_GRACE_S,
    ClaudeCodeHeadlessAdvisor,
    advisor_run_dir_name,
    build_advisor_prompt,
    build_allowed_tools,
    build_child_env,
    build_claude_command,
    image_file_name,
    start_daemon_thread,
    write_advisor_inputs,
)
from switch2_agent.decision_request import LabeledImage
from switch2_agent.safety import SAFETY_RULES

NOTES_DIR_RELATIVE = "game_notes/zelda"
RUNS_DIR_RELATIVE = "runs/20261112-120000"
EXPECTED_ALLOWED_TOOLS = [
    "Read(/game_notes/zelda/**)",
    "Read(/runs/20261112-120000/**)",
    "Edit(/game_notes/zelda/controls.md)",
    "Edit(/game_notes/zelda/tips.md)",
    "Edit(/game_notes/zelda/progress.md)",
    "Edit(/game_notes/zelda/strategy.md)",
    "Skill(record-controls)",
    "Skill(record-tips)",
    "Skill(record-progress)",
    "Skill(record-strategy)",
]
CLAUDE_OUTPUT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "戦略を更新した",
    "permission_denials": [
        {"tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {"command": "ls"}}
    ],
}
WAIT_TIMEOUT = 2.0  # テストがスレッドの進みを待つ上限 (秒)


def sample_context(
    strategy: str = "右へ進む", log_text: str = "-1.00 秒: 押下 A"
) -> AdvisorContext:
    return AdvisorContext(
        game="zelda",
        images=[
            LabeledImage("過去の場面 1/1 (3.00 秒前)", b"jpeg-one", 1.0),
            LabeledImage("直近 1/1 (0.10 秒前)", b"jpeg-two", 3.9),
        ],
        current_strategy=strategy,
        operation_log_text=log_text,
        trigger_probability=0.87,
        trigger_time_text="12:34:56",
    )


def option_value(cmd: list[str], option: str) -> str:
    """cmd の option の次の引数を返す。option がちょうど 1 回だけ現れることも確かめる。"""
    assert cmd.count(option) == 1, option
    return cmd[cmd.index(option) + 1]


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakePipe:
    """子プロセスの出力のパイプの代わり。close が呼ばれたかを closed に記録する。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeProcess:
    """subprocess.Popen の代わりの子プロセス。呼び出しを calls に記録する。

    最初の communicate は、clock を duration 進めてから、次のどれかになる:
        - block が真なら、terminate (terminate_works が真のとき) か kill が呼ばれるまで待ってから出力を返す
        - timeout_first が真なら TimeoutExpired を送出する
        - communicate_error があれば、それを送出する
        - それ以外は (stdout, stderr) を返し、returncode を exit_code にする
    2 回目以降の communicate は、止められていれば出力を返し、止められていなければ TimeoutExpired を送出する。
    pipes_stay_open が真なら、止められていても TimeoutExpired を送出する (子孫のプロセスがパイプを持ち続ける場合)。
    wait は、止められていれば returncode を返し、止められていなければすぐ TimeoutExpired を送出する。
    stdout と stderr は出力のパイプの代わり (FakePipe)。
    """

    def __init__(self, args, clock: FakeClock, settings: "FakeStartProcess"):
        self.args = args
        self.clock = clock
        self.settings = settings
        self.calls: list = []
        self.returncode: int | None = None
        self.stopped = threading.Event()
        self.communicate_count = 0
        self.stdout = FakePipe()
        self.stderr = FakePipe()

    def communicate(self, timeout=None):
        self.calls.append(("communicate", timeout))
        self.communicate_count += 1
        if self.communicate_count == 1:
            self.clock.now += self.settings.duration
            if self.settings.block:
                self.stopped.wait(WAIT_TIMEOUT)
            elif self.settings.timeout_first:
                raise subprocess.TimeoutExpired(self.args, timeout)
            elif self.settings.communicate_error is not None:
                raise self.settings.communicate_error
            else:
                self.returncode = self.settings.exit_code
        elif not self.stopped.is_set() or self.settings.pipes_stay_open:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.settings.stdout, self.settings.stderr

    def terminate(self):
        self.calls.append("terminate")
        if self.settings.terminate_works:
            self.returncode = -15
            self.stopped.set()

    def kill(self):
        self.calls.append("kill")
        self.returncode = -9
        self.stopped.set()

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if not self.stopped.is_set():
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode


class FakeStartProcess:
    """subprocess.Popen の代わりに FakeProcess を作る。引数は Popen と同じキーワードだけを受け付ける。"""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.calls: list[tuple[list[str], dict]] = []
        self.processes: list[FakeProcess] = []
        self.started = threading.Event()
        self.error: BaseException | None = None
        self.duration = 12.5
        self.stdout = json.dumps(CLAUDE_OUTPUT, ensure_ascii=False)
        self.stderr = ""
        self.exit_code = 0
        self.block = False
        self.timeout_first = False
        self.communicate_error: Exception | None = None
        self.terminate_works = True
        self.pipes_stay_open = False

    def __call__(
        self, args, *, cwd, env, stdin, stdout, stderr, text, encoding, errors
    ):
        self.calls.append(
            (
                list(args),
                {
                    "cwd": cwd,
                    "env": env,
                    "stdin": stdin,
                    "stdout": stdout,
                    "stderr": stderr,
                    "text": text,
                    "encoding": encoding,
                    "errors": errors,
                },
            )
        )
        if self.error is not None:
            raise self.error
        process = FakeProcess(list(args), self.clock, self)
        self.processes.append(process)
        self.started.set()
        return process


class Recorder:
    def __init__(self):
        self.events: list[dict] = []
        self.held_jobs: list = []

    def log(self, event: dict) -> None:
        self.events.append(event)

    def hold(self, job) -> None:
        """実行の本体を呼ばずに保留する start_thread。"""
        self.held_jobs.append(job)


def make_advisor(tmp_path: Path, start_thread=None, **kwargs):
    """tmp_path の下を workdir にした advisor と、偽の start_process・記録・実行のディレクトリーを返す。

    start_thread を省略すると、実行の本体をその場で呼ぶ。
    """
    workdir = tmp_path / "ch04_techniques"
    notes_dir = workdir / "game_notes" / "zelda"
    notes_dir.mkdir(parents=True)
    runs_dir = workdir / "runs" / "20261112-120000"
    clock = FakeClock(100.0)
    start_process = FakeStartProcess(clock)
    recorder = Recorder()
    advisor = ClaudeCodeHeadlessAdvisor(
        workdir,
        notes_dir,
        runs_dir,
        "zelda",
        start_process=start_process,
        log=recorder.log,
        start_thread=start_thread if start_thread is not None else (lambda job: job()),
        clock=clock,
        **kwargs,
    )
    return advisor, start_process, recorder, runs_dir


def read_result(runs_dir: Path, index: int = 1) -> dict:
    path = runs_dir / advisor_run_dir_name(index) / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def wait_until(condition, message: str) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail(message)
        time.sleep(0.01)


# --- 名前 ---


def test_run_dir_and_image_file_names():
    assert advisor_run_dir_name(1) == "advisor-001"
    assert advisor_run_dir_name(12) == "advisor-012"
    assert image_file_name(1) == "frame-01.jpg"
    assert image_file_name(10) == "frame-10.jpg"


# --- build_claude_command ---


def test_command_starts_with_claude_print_and_prompt():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE)
    assert cmd[:3] == ["claude", "-p", "プロンプト"]


def test_command_uses_json_output_and_dont_ask_mode():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE)
    assert option_value(cmd, "--output-format") == "json"
    assert option_value(cmd, "--permission-mode") == "dontAsk"


def test_command_reads_only_project_settings_and_disables_hooks():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE)
    assert option_value(cmd, "--setting-sources") == "project"
    assert json.loads(option_value(cmd, "--settings")) == {"disableAllHooks": True}
    assert "--bare" not in cmd


def test_command_limits_turns():
    assert (
        option_value(
            build_claude_command("p", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE),
            "--max-turns",
        )
        == "30"
    )
    cmd = build_claude_command("p", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, max_turns=10)
    assert option_value(cmd, "--max-turns") == "10"


def test_command_allowed_tools_is_one_comma_joined_string():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE)
    assert option_value(cmd, "--allowedTools").split(",") == EXPECTED_ALLOWED_TOOLS


def test_command_allows_writing_only_the_four_note_files():
    rules = option_value(
        build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE),
        "--allowedTools",
    ).split(",")
    write_rules = [rule for rule in rules if rule.startswith(("Edit", "Write"))]
    assert write_rules == [
        "Edit(/game_notes/zelda/controls.md)",
        "Edit(/game_notes/zelda/tips.md)",
        "Edit(/game_notes/zelda/progress.md)",
        "Edit(/game_notes/zelda/strategy.md)",
    ]
    # ディレクトリー全体への書き込みは許さない
    assert "Edit(/game_notes/zelda/**)" not in rules
    assert not any(rule.startswith("Bash") for rule in rules)


def test_command_allows_reading_only_under_notes_dir_and_runs_dir():
    rules = option_value(
        build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE),
        "--allowedTools",
    ).split(",")
    read_rules = [rule for rule in rules if rule.startswith("Read")]
    assert read_rules == [
        "Read(/game_notes/zelda/**)",
        "Read(/runs/20261112-120000/**)",
    ]


def test_allowed_tools_include_reads_edit_and_four_skills():
    rules = build_allowed_tools("game_notes/mario", "runs/20261112-130000")
    assert rules == [
        "Read(/game_notes/mario/**)",
        "Read(/runs/20261112-130000/**)",
        "Edit(/game_notes/mario/controls.md)",
        "Edit(/game_notes/mario/tips.md)",
        "Edit(/game_notes/mario/progress.md)",
        "Edit(/game_notes/mario/strategy.md)",
        *[f"Skill({name})" for name in ADVISOR_SKILLS],
    ]
    # パスを限らない Read は含めない
    assert "Read" not in rules
    assert ADVISOR_SKILLS == (
        "record-controls",
        "record-tips",
        "record-progress",
        "record-strategy",
    )


# --- build_child_env ---


def test_child_env_removes_only_hidden_names():
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/player",
        "TYPESAFE_API_KEY": "test-key-not-a-secret",
        "OPENAI_API_KEY": "test-key-not-a-secret",
    }
    env = build_child_env(environ, ("TYPESAFE_API_KEY", "OPENAI_API_KEY"))
    assert env == {"PATH": "/usr/bin", "HOME": "/home/player"}
    # 元の辞書は変えない
    assert set(environ) == {"PATH", "HOME", "TYPESAFE_API_KEY", "OPENAI_API_KEY"}


def test_child_env_keeps_all_variables_without_hidden_names():
    environ = {"PATH": "/usr/bin", "HOME": "/home/player"}
    env = build_child_env(environ, ())
    assert env == environ
    assert env is not environ


# --- build_advisor_prompt ---


def test_prompt_points_to_context_notes_and_skills(tmp_path):
    context_path = tmp_path / "advisor-001" / "context.md"
    notes_dir = tmp_path / "game_notes" / "zelda"
    prompt = build_advisor_prompt(context_path, notes_dir, "zelda")
    assert str(context_path) in prompt
    assert str(notes_dir) in prompt
    assert "zelda" in prompt
    for skill in ADVISOR_SKILLS:
        assert skill in prompt
    assert SAFETY_RULES in prompt
    assert "3 行以内" in prompt
    assert not prompt.startswith("/")


# --- write_advisor_inputs ---


def test_write_advisor_inputs_writes_images_and_context(tmp_path):
    run_dir = tmp_path / "runs" / "advisor-001"
    context_path = write_advisor_inputs(run_dir, sample_context())
    assert context_path == run_dir / "context.md"
    assert (run_dir / "frame-01.jpg").read_bytes() == b"jpeg-one"
    assert (run_dir / "frame-02.jpg").read_bytes() == b"jpeg-two"
    text = context_path.read_text(encoding="utf-8")
    for expected in [
        "zelda",
        "frame-01.jpg",
        "過去の場面 1/1 (3.00 秒前)",
        "frame-02.jpg",
        "直近 1/1 (0.10 秒前)",
        "右へ進む",
        "-1.00 秒: 押下 A",
        "0.87",
        "12:34:56",
    ]:
        assert expected in text, expected
    assert text.index("frame-01.jpg") < text.index("frame-02.jpg")


def test_write_advisor_inputs_marks_empty_strategy_and_log(tmp_path):
    context_path = write_advisor_inputs(
        tmp_path / "advisor-001", sample_context(strategy="", log_text="")
    )
    text = context_path.read_text(encoding="utf-8")
    assert "(まだ書かれていない)" in text
    assert "(記録なし)" in text


# --- ClaudeCodeHeadlessAdvisor ---


def test_advisor_rejects_notes_dir_outside_workdir(tmp_path):
    workdir = tmp_path / "ch04_techniques"
    with pytest.raises(ValueError, match="notes_dir must be under workdir"):
        ClaudeCodeHeadlessAdvisor(
            workdir,
            tmp_path / "elsewhere",
            workdir / "runs",
            "zelda",
        )


def test_advisor_rejects_runs_dir_outside_workdir(tmp_path):
    workdir = tmp_path / "ch04_techniques"
    with pytest.raises(ValueError, match="runs_dir must be under workdir"):
        ClaudeCodeHeadlessAdvisor(
            workdir,
            workdir / "game_notes" / "zelda",
            tmp_path / "runs",
            "zelda",
        )


def test_request_update_returns_false_while_running(tmp_path):
    recorder_box = Recorder()
    advisor, _, _, _ = make_advisor(tmp_path, start_thread=recorder_box.hold)
    assert advisor.is_busy() is False
    assert advisor.request_update(sample_context()) is True
    assert advisor.is_busy() is True
    assert advisor.request_update(sample_context()) is False
    assert len(recorder_box.held_jobs) == 1
    # 保留した実行が終われば、次の要求を受け付ける
    recorder_box.held_jobs[0]()
    assert advisor.is_busy() is False
    assert advisor.request_update(sample_context()) is True
    assert len(recorder_box.held_jobs) == 2


def test_request_update_starts_claude_in_workdir_and_waits_with_timeout(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    assert len(start_process.calls) == 1
    cmd, kwargs = start_process.calls[0]
    assert kwargs == {
        "cwd": tmp_path / "ch04_techniques",
        # hidden_env_names を渡さなければ、環境変数をすべて渡す
        "env": dict(os.environ),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    assert cmd[:2] == ["claude", "-p"]
    assert option_value(cmd, "--allowedTools").split(",") == EXPECTED_ALLOWED_TOOLS
    assert start_process.processes[0].calls == [("communicate", 300.0)]


def test_request_update_passes_given_timeout(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path, timeout_s=42.0)
    advisor.request_update(sample_context())
    assert start_process.processes[0].calls == [("communicate", 42.0)]


def test_request_update_hides_api_keys_from_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("SWITCH2_AGENT_TEST_VARIABLE", "kept")
    advisor, start_process, _, _ = make_advisor(
        tmp_path, hidden_env_names=("TYPESAFE_API_KEY", "OPENAI_API_KEY")
    )
    advisor.request_update(sample_context())
    env = start_process.calls[0][1]["env"]
    assert "TYPESAFE_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["SWITCH2_AGENT_TEST_VARIABLE"] == "kept"
    assert env == build_child_env(os.environ, ("TYPESAFE_API_KEY", "OPENAI_API_KEY"))
    # 親のプロセスの環境変数は変えない
    assert os.environ["TYPESAFE_API_KEY"] == "test-key-not-a-secret"


def test_request_update_writes_inputs_in_numbered_run_dir(tmp_path):
    advisor, start_process, _, runs_dir = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    advisor.request_update(sample_context())
    for index in (1, 2):
        run_dir = runs_dir / advisor_run_dir_name(index)
        assert (run_dir / "context.md").is_file()
        assert (run_dir / "frame-01.jpg").read_bytes() == b"jpeg-one"
    prompt = start_process.calls[0][0][2]
    assert str(runs_dir / "advisor-001" / "context.md") in prompt


def test_request_update_writes_result_json_and_logs(tmp_path):
    advisor, _, recorder, runs_dir = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["returncode"] == 0
    assert result["duration_s"] == pytest.approx(12.5)
    assert result["timed_out"] is False
    assert result["error"] is None
    assert result["output"] == CLAUDE_OUTPUT
    assert result["stdout"] is None
    assert result["stderr"] == ""
    assert result["permission_denials"] == CLAUDE_OUTPUT["permission_denials"]
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event["event"] == "advisor"
    assert event["run_dir"] == str(runs_dir / "advisor-001")
    assert event["returncode"] == 0
    assert event["permission_denials"] == CLAUDE_OUTPUT["permission_denials"]
    assert event["result"] == "戦略を更新した"
    assert advisor.is_busy() is False


def test_request_update_records_non_json_output(tmp_path):
    advisor, start_process, _, runs_dir = make_advisor(tmp_path)
    start_process.stdout = "not json"
    start_process.exit_code = 1
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["returncode"] == 1
    assert result["output"] is None
    assert result["stdout"] == "not json"
    assert result["permission_denials"] == []


def test_request_update_terminates_claude_on_timeout(tmp_path):
    advisor, start_process, recorder, runs_dir = make_advisor(tmp_path)
    start_process.timeout_first = True
    start_process.stdout = "partial"
    start_process.stderr = "still running"
    advisor.request_update(sample_context())
    assert start_process.processes[0].calls == [
        ("communicate", 300.0),
        "terminate",
        ("communicate", TERMINATE_GRACE_S),
    ]
    result = read_result(runs_dir)
    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert result["error"] == "timed out after 300.0 seconds"
    assert result["stdout"] == "partial"
    assert result["stderr"] == "still running"
    assert len(recorder.events) == 1
    assert recorder.events[0]["timed_out"] is True
    assert advisor.is_busy() is False


def test_request_update_kills_claude_that_ignores_terminate_on_timeout(tmp_path):
    advisor, start_process, _, runs_dir = make_advisor(tmp_path)
    start_process.timeout_first = True
    start_process.terminate_works = False
    advisor.request_update(sample_context())
    assert start_process.processes[0].calls == [
        ("communicate", 300.0),
        "terminate",
        ("communicate", TERMINATE_GRACE_S),
        "kill",
        ("communicate", TERMINATE_GRACE_S),
    ]
    assert TERMINATE_GRACE_S == 5.0
    assert read_result(runs_dir)["timed_out"] is True
    assert advisor.is_busy() is False


def test_request_update_terminates_claude_when_waiting_fails(tmp_path):
    advisor, start_process, recorder, runs_dir = make_advisor(tmp_path)
    start_process.communicate_error = OverflowError("timeout value is too large")
    advisor.request_update(sample_context())
    assert start_process.processes[0].calls == [
        ("communicate", 300.0),
        "terminate",
        ("communicate", TERMINATE_GRACE_S),
    ]
    result = read_result(runs_dir)
    assert result["timed_out"] is False
    assert result["returncode"] is None
    assert "timeout value is too large" in result["error"]
    assert len(recorder.events) == 1
    assert "timeout value is too large" in recorder.events[0]["error"]
    assert advisor.process is None
    assert advisor.is_busy() is False


def test_request_update_kills_claude_that_ignores_terminate_when_waiting_fails(
    tmp_path,
):
    advisor, start_process, _, runs_dir = make_advisor(tmp_path)
    start_process.communicate_error = OverflowError("timeout value is too large")
    start_process.terminate_works = False
    advisor.request_update(sample_context())
    assert start_process.processes[0].calls == [
        ("communicate", 300.0),
        "terminate",
        ("communicate", TERMINATE_GRACE_S),
        "kill",
        ("communicate", TERMINATE_GRACE_S),
    ]
    assert "timeout value is too large" in read_result(runs_dir)["error"]
    assert advisor.process is None
    assert advisor.is_busy() is False


def test_request_update_stops_reading_when_pipes_stay_open_after_kill(tmp_path):
    advisor, start_process, recorder, runs_dir = make_advisor(tmp_path)
    start_process.timeout_first = True
    start_process.terminate_works = False
    start_process.pipes_stay_open = True
    advisor.request_update(sample_context())
    process = start_process.processes[0]
    assert process.calls == [
        ("communicate", 300.0),
        "terminate",
        ("communicate", TERMINATE_GRACE_S),
        "kill",
        ("communicate", TERMINATE_GRACE_S),
    ]
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    result = read_result(runs_dir)
    assert result["timed_out"] is True
    assert result["error"] == "timed out after 300.0 seconds"
    assert result["stdout"] is None
    assert result["stderr"] == ""
    assert len(recorder.events) == 1
    assert advisor.process is None
    assert advisor.is_busy() is False


def test_request_update_records_exception(tmp_path):
    advisor, start_process, recorder, runs_dir = make_advisor(tmp_path)
    start_process.error = FileNotFoundError("claude not found")
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["timed_out"] is False
    assert result["returncode"] is None
    assert "claude not found" in result["error"]
    assert len(recorder.events) == 1
    assert advisor.is_busy() is False
    # 失敗の後も、次の要求を受け付ける
    assert advisor.request_update(sample_context()) is True


# --- close ---


def test_close_without_running_advisor_returns():
    advisor = ClaudeCodeHeadlessAdvisor(
        Path("/work"), Path("/work/game_notes/zelda"), Path("/work/runs"), "zelda"
    )
    advisor.close()
    assert advisor.request_update(sample_context()) is False


def test_close_terminates_running_claude_and_waits_for_thread(tmp_path):
    advisor, start_process, _, runs_dir = make_advisor(
        tmp_path, start_thread=start_daemon_thread
    )
    start_process.block = True
    advisor.request_update(sample_context())
    assert start_process.started.wait(WAIT_TIMEOUT)
    advisor.close()
    process = start_process.processes[0]
    assert "terminate" in process.calls
    assert "kill" not in process.calls
    assert advisor.is_busy() is False
    result = read_result(runs_dir)
    assert result["returncode"] == -15
    assert result["error"] == "stopped by close"
    # close の後は要求を受け付けない
    assert advisor.request_update(sample_context()) is False


def test_close_kills_claude_that_ignores_terminate(tmp_path):
    advisor, start_process, _, _ = make_advisor(
        tmp_path, start_thread=start_daemon_thread
    )
    start_process.block = True
    start_process.terminate_works = False
    advisor.request_update(sample_context())
    assert start_process.started.wait(WAIT_TIMEOUT)
    advisor.close()
    process = start_process.processes[0]
    assert process.calls[1:] == ["terminate", ("wait", TERMINATE_GRACE_S), "kill"]
    assert advisor.is_busy() is False


def test_close_prevents_starting_claude_for_pending_request(tmp_path):
    recorder_box = Recorder()
    advisor, start_process, _, runs_dir = make_advisor(
        tmp_path, start_thread=recorder_box.hold
    )
    advisor.request_update(sample_context())
    closer = threading.Thread(target=advisor.close)
    closer.start()
    wait_until(lambda: advisor.closed, "close() did not mark the advisor closed")
    # close() の後に動き出した実行は、claude を起動しない
    recorder_box.held_jobs[0]()
    closer.join(WAIT_TIMEOUT)
    assert not closer.is_alive()
    assert start_process.calls == []
    assert read_result(runs_dir)["error"] == "stopped by close"
