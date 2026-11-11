import json
import subprocess
from pathlib import Path

import pytest

from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.advisors.claude_code_headless import (
    ADVISOR_SKILLS,
    ClaudeCodeHeadlessAdvisor,
    advisor_run_dir_name,
    build_advisor_prompt,
    build_allowed_tools,
    build_claude_command,
    image_file_name,
    write_advisor_inputs,
)
from switch2_agent.decision_request import LabeledImage
from switch2_agent.safety import SAFETY_RULES

NOTES_DIR_RELATIVE = "game_notes/zelda"
EXPECTED_ALLOWED_TOOLS = [
    "Read",
    "Edit(/game_notes/zelda/**)",
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


class FakeRunCommand:
    """subprocess.run の代わり。呼び出しを記録し、result (CompletedProcess) を返すか error を送出する。"""

    def __init__(self, clock: FakeClock, duration: float = 12.5):
        self.clock = clock
        self.duration = duration
        self.calls: list[tuple[list[str], dict]] = []
        self.stdout = json.dumps(CLAUDE_OUTPUT, ensure_ascii=False)
        self.returncode = 0
        self.error: BaseException | None = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        self.clock.now += self.duration
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=""
        )


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
    """tmp_path の下を workdir にした advisor と、偽の run_command・clock・記録を返す。

    start_thread を省略すると、実行の本体をその場で呼ぶ。
    """
    workdir = tmp_path / "ch04_techniques"
    notes_dir = workdir / "game_notes" / "zelda"
    notes_dir.mkdir(parents=True)
    runs_dir = workdir / "runs" / "20261112-120000"
    clock = FakeClock(100.0)
    run_command = FakeRunCommand(clock)
    recorder = Recorder()
    advisor = ClaudeCodeHeadlessAdvisor(
        workdir,
        notes_dir,
        runs_dir,
        "zelda",
        run_command=run_command,
        log=recorder.log,
        start_thread=start_thread if start_thread is not None else (lambda job: job()),
        clock=clock,
        **kwargs,
    )
    return advisor, run_command, recorder, runs_dir


def read_result(runs_dir: Path, index: int = 1) -> dict:
    path = runs_dir / advisor_run_dir_name(index) / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- 名前 ---


def test_run_dir_and_image_file_names():
    assert advisor_run_dir_name(1) == "advisor-001"
    assert advisor_run_dir_name(12) == "advisor-012"
    assert image_file_name(1) == "frame-01.jpg"
    assert image_file_name(10) == "frame-10.jpg"


# --- build_claude_command ---


def test_command_starts_with_claude_print_and_prompt():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE)
    assert cmd[:3] == ["claude", "-p", "プロンプト"]


def test_command_uses_json_output_and_dont_ask_mode():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE)
    assert option_value(cmd, "--output-format") == "json"
    assert option_value(cmd, "--permission-mode") == "dontAsk"


def test_command_reads_only_project_settings_and_disables_hooks():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE)
    assert option_value(cmd, "--setting-sources") == "project"
    assert json.loads(option_value(cmd, "--settings")) == {"disableAllHooks": True}
    assert "--bare" not in cmd


def test_command_limits_turns():
    assert (
        option_value(build_claude_command("p", NOTES_DIR_RELATIVE), "--max-turns")
        == "30"
    )
    cmd = build_claude_command("p", NOTES_DIR_RELATIVE, max_turns=10)
    assert option_value(cmd, "--max-turns") == "10"


def test_command_allowed_tools_is_one_comma_joined_string():
    cmd = build_claude_command("プロンプト", NOTES_DIR_RELATIVE)
    assert option_value(cmd, "--allowedTools").split(",") == EXPECTED_ALLOWED_TOOLS


def test_command_allows_writing_only_under_notes_dir():
    rules = option_value(
        build_claude_command("プロンプト", NOTES_DIR_RELATIVE), "--allowedTools"
    ).split(",")
    write_rules = [rule for rule in rules if rule.startswith(("Edit", "Write"))]
    assert write_rules == ["Edit(/game_notes/zelda/**)"]
    assert not any(rule.startswith("Bash") for rule in rules)


def test_allowed_tools_include_read_and_four_skills():
    rules = build_allowed_tools("game_notes/mario")
    assert rules == [
        "Read",
        "Edit(/game_notes/mario/**)",
        *[f"Skill({name})" for name in ADVISOR_SKILLS],
    ]
    assert ADVISOR_SKILLS == (
        "record-controls",
        "record-tips",
        "record-progress",
        "record-strategy",
    )


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
    with pytest.raises(ValueError):
        ClaudeCodeHeadlessAdvisor(
            tmp_path / "ch04_techniques",
            tmp_path / "elsewhere",
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


def test_request_update_runs_claude_in_workdir_with_timeout(tmp_path):
    advisor, run_command, _, _ = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    assert len(run_command.calls) == 1
    cmd, kwargs = run_command.calls[0]
    assert kwargs == {
        "cwd": tmp_path / "ch04_techniques",
        "capture_output": True,
        "text": True,
        "timeout": 300.0,
    }
    assert cmd[:2] == ["claude", "-p"]
    assert option_value(cmd, "--allowedTools").split(",") == EXPECTED_ALLOWED_TOOLS


def test_request_update_passes_given_timeout(tmp_path):
    advisor, run_command, _, _ = make_advisor(tmp_path, timeout_s=42.0)
    advisor.request_update(sample_context())
    assert run_command.calls[0][1]["timeout"] == 42.0


def test_request_update_writes_inputs_in_numbered_run_dir(tmp_path):
    advisor, run_command, _, runs_dir = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    advisor.request_update(sample_context())
    for index in (1, 2):
        run_dir = runs_dir / advisor_run_dir_name(index)
        assert (run_dir / "context.md").is_file()
        assert (run_dir / "frame-01.jpg").read_bytes() == b"jpeg-one"
    prompt = run_command.calls[0][0][2]
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
    assert result["permission_denials"] == CLAUDE_OUTPUT["permission_denials"]
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event["event"] == "advisor"
    assert event["returncode"] == 0
    assert event["permission_denials"] == CLAUDE_OUTPUT["permission_denials"]
    assert event["result"] == "戦略を更新した"
    assert advisor.is_busy() is False


def test_request_update_records_non_json_output(tmp_path):
    advisor, run_command, _, runs_dir = make_advisor(tmp_path)
    run_command.stdout = "not json"
    run_command.returncode = 1
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["returncode"] == 1
    assert result["output"] is None
    assert result["stdout"] == "not json"
    assert result["permission_denials"] == []


def test_request_update_records_timeout(tmp_path):
    advisor, run_command, recorder, runs_dir = make_advisor(tmp_path)
    run_command.error = subprocess.TimeoutExpired(
        ["claude"], 300.0, output=b"partial", stderr=b"still running"
    )
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert "timed out" in result["error"]
    assert len(recorder.events) == 1
    assert recorder.events[0]["timed_out"] is True
    assert advisor.is_busy() is False


def test_request_update_records_exception(tmp_path):
    advisor, run_command, recorder, runs_dir = make_advisor(tmp_path)
    run_command.error = FileNotFoundError("claude not found")
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["timed_out"] is False
    assert result["returncode"] is None
    assert "claude not found" in result["error"]
    assert len(recorder.events) == 1
    assert advisor.is_busy() is False
    # 失敗の後も、次の要求を受け付ける
    assert advisor.request_update(sample_context()) is True


def test_close_does_not_wait_for_running_advisor(tmp_path):
    recorder_box = Recorder()
    advisor, run_command, _, _ = make_advisor(tmp_path, start_thread=recorder_box.hold)
    advisor.request_update(sample_context())
    advisor.close()
    assert run_command.calls == []
