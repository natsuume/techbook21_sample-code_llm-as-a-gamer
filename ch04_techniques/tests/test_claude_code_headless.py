import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from switch2_agent.advisor_roles import ADVISOR_ROLES, AdvisorRole
from switch2_agent.advisors import claude_code_headless
from switch2_agent.advisors.base import AdvisorContext
from switch2_agent.advisors.claude_code_headless import (
    FAILED_RUN_COOLDOWN_S,
    TERMINATE_GRACE_S,
    ClaudeCodeHeadlessAdvisor,
    advisor_run_dir_name,
    build_advisor_prompt,
    build_allowed_tools,
    build_child_env,
    build_claude_command,
    build_log_event,
    image_file_name,
    start_daemon_thread,
    write_advisor_inputs,
)
from switch2_agent.decision_request import LabeledImage
from switch2_agent.game_notes import NOTE_KINDS, NOTE_TITLES
from switch2_agent.safety import SAFETY_RULES

NOTES_DIR_RELATIVE = "game_notes/zelda"
RUNS_DIR_RELATIVE = "runs/20261112-120000"
ROLES_BY_NAME = {role.name: role for role in ADVISOR_ROLES}
# テストで使う役割 (目標・戦略の更新)
ROLE = ROLES_BY_NAME["strategy"]
EXPECTED_ALLOWED_TOOLS = [
    "Read(/game_notes/zelda/**)",
    "Read(/runs/20261112-120000/**)",
    "Edit(/game_notes/zelda/strategy.md)",
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


def make_advisor(tmp_path: Path, start_thread=None, role: AdvisorRole = ROLE, **kwargs):
    """tmp_path の下を workdir にした役割 role の advisor と、偽の start_process・記録・実行のディレクトリーを返す。

    start_thread を省略すると、実行の本体をその場で呼ぶ。同じ tmp_path で何度呼んでもよい
    (ディレクトリーは共通にする)。
    """
    workdir = tmp_path / "ch04_techniques"
    notes_dir = workdir / "game_notes" / "zelda"
    notes_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = workdir / "runs" / "20261112-120000"
    clock = FakeClock(100.0)
    start_process = FakeStartProcess(clock)
    recorder = Recorder()
    advisor = ClaudeCodeHeadlessAdvisor(
        workdir,
        notes_dir,
        runs_dir,
        "zelda",
        role,
        start_process=start_process,
        log=recorder.log,
        start_thread=start_thread if start_thread is not None else (lambda job: job()),
        clock=clock,
        **kwargs,
    )
    return advisor, start_process, recorder, runs_dir


def read_result(runs_dir: Path, index: int = 1, role: AdvisorRole = ROLE) -> dict:
    path = runs_dir / advisor_run_dir_name(role.name, index) / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def wait_until(condition, message: str) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not condition():
        if time.monotonic() >= deadline:
            pytest.fail(message)
        time.sleep(0.01)


# --- 名前 ---


def test_run_dir_names_have_role_name_and_three_digit_number():
    assert advisor_run_dir_name("strategy", 1) == "advisor-strategy-001"
    assert advisor_run_dir_name("tips", 12) == "advisor-tips-012"


def test_image_file_names():
    assert image_file_name(1) == "frame-01.jpg"
    assert image_file_name(10) == "frame-10.jpg"


# --- build_claude_command ---


def test_command_starts_with_claude_print_and_prompt():
    cmd = build_claude_command(
        "プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE
    )
    assert cmd[:3] == ["claude", "-p", "プロンプト"]


def test_command_uses_json_output_and_dont_ask_mode():
    cmd = build_claude_command(
        "プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE
    )
    assert option_value(cmd, "--output-format") == "json"
    assert option_value(cmd, "--permission-mode") == "dontAsk"


def test_command_reads_only_project_settings_and_disables_hooks():
    cmd = build_claude_command(
        "プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE
    )
    assert option_value(cmd, "--setting-sources") == "project"
    assert json.loads(option_value(cmd, "--settings")) == {"disableAllHooks": True}
    assert "--bare" not in cmd


def test_command_limits_turns():
    assert (
        option_value(
            build_claude_command("p", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE),
            "--max-turns",
        )
        == "30"
    )
    cmd = build_claude_command(
        "p", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE, max_turns=10
    )
    assert option_value(cmd, "--max-turns") == "10"


def test_command_allowed_tools_is_one_comma_joined_string():
    cmd = build_claude_command(
        "プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE
    )
    assert option_value(cmd, "--allowedTools").split(",") == EXPECTED_ALLOWED_TOOLS


def test_command_allows_writing_only_the_note_file_of_the_role():
    rules = option_value(
        build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE),
        "--allowedTools",
    ).split(",")
    write_rules = [rule for rule in rules if rule.startswith(("Edit", "Write"))]
    assert write_rules == ["Edit(/game_notes/zelda/strategy.md)"]
    # ほかの 3 ファイルとディレクトリー全体への書き込みは許さない
    for kind in ("controls", "tips", "advice"):
        assert f"Edit(/game_notes/zelda/{kind}.md)" not in rules
    assert "Edit(/game_notes/zelda/**)" not in rules
    assert not any(rule.startswith("Bash") for rule in rules)


def test_command_allows_reading_only_under_notes_dir_and_runs_dir():
    rules = option_value(
        build_claude_command("プロンプト", NOTES_DIR_RELATIVE, RUNS_DIR_RELATIVE, ROLE),
        "--allowedTools",
    ).split(",")
    read_rules = [rule for rule in rules if rule.startswith("Read")]
    assert read_rules == [
        "Read(/game_notes/zelda/**)",
        "Read(/runs/20261112-120000/**)",
    ]


@pytest.mark.parametrize(
    "role_name, note_file, skill",
    [
        ("controls", "controls.md", "record-controls"),
        ("strategy", "strategy.md", "record-strategy"),
        ("advice", "advice.md", "record-advice"),
        ("tips", "tips.md", "record-tips"),
    ],
)
def test_allowed_tools_have_reads_and_one_edit_and_one_skill_of_role(
    role_name, note_file, skill
):
    rules = build_allowed_tools(
        "game_notes/mario", "runs/20261112-130000", ROLES_BY_NAME[role_name]
    )
    assert rules == [
        "Read(/game_notes/mario/**)",
        "Read(/runs/20261112-130000/**)",
        f"Edit(/game_notes/mario/{note_file})",
        f"Skill({skill})",
    ]
    # パスを限らない Read は含めない
    assert "Read" not in rules


def test_skill_list_constant_is_removed():
    # 使える Skill は役割から決めるため、全役割の Skill の一覧の定数は無い
    assert not hasattr(claude_code_headless, "ADVISOR_SKILLS")


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


def sample_prompt(tmp_path: Path, role: AdvisorRole = ROLE) -> tuple[str, Path, Path]:
    """role の advisor への指示の文と、そこに渡した context.md のパスとゲームの情報のディレクトリーを返す。"""
    context_path = tmp_path / "advisor-strategy-001" / "context.md"
    notes_dir = tmp_path / "game_notes" / "zelda"
    prompt = build_advisor_prompt(context_path, notes_dir, "zelda", role)
    return prompt, context_path, notes_dir


def test_prompt_starts_with_role_title(tmp_path):
    prompt, _, _ = sample_prompt(tmp_path)
    assert prompt.splitlines()[0] == (
        "あなたは、Nintendo Switch 2 のゲーム「zelda」を遊ぶ速い層のために、"
        "目標・戦略の更新を受け持つ advisor である。"
    )
    assert not prompt.startswith("/")


def test_prompt_points_to_context_and_all_four_note_files_in_order(tmp_path):
    prompt, context_path, notes_dir = sample_prompt(tmp_path)
    assert str(context_path) in prompt
    lines = prompt.splitlines()
    positions = []
    for kind in NOTE_KINDS:
        index = next(
            i for i, line in enumerate(lines) if str(notes_dir / f"{kind}.md") in line
        )
        assert NOTE_TITLES[kind] in lines[index]
        positions.append(index)
    assert positions == sorted(positions)


def test_prompt_has_task_skill_and_the_only_writable_file_of_role(tmp_path):
    prompt, _, _ = sample_prompt(tmp_path)
    assert ROLE.task in prompt
    assert "record-strategy の Skill の手順で strategy.md を書き換える" in prompt
    assert (
        "書き換えてよいのは strategy.md だけで、ほかの 3 ファイルは読むだけにする"
        in prompt
    )
    # ほかの役割の Skill は書かない
    for other in ("record-controls", "record-tips", "record-advice"):
        assert other not in prompt


def test_prompt_has_safety_rules_and_short_answer(tmp_path):
    prompt, _, _ = sample_prompt(tmp_path)
    assert SAFETY_RULES in prompt
    assert "3 行以内" in prompt
    assert "短く保つ" in prompt


def test_prompt_of_another_role_uses_its_title_and_file(tmp_path):
    prompt, _, _ = sample_prompt(tmp_path, ROLES_BY_NAME["advice"])
    assert prompt.splitlines()[0].endswith(
        "問題の解決の助言を受け持つ advisor である。"
    )
    assert "record-advice の Skill の手順で advice.md を書き換える" in prompt
    assert (
        "書き換えてよいのは advice.md だけで、ほかの 3 ファイルは読むだけにする"
        in prompt
    )


# --- write_advisor_inputs ---


def test_write_advisor_inputs_writes_images_and_context(tmp_path):
    run_dir = tmp_path / "runs" / "advisor-strategy-001"
    context_path = write_advisor_inputs(run_dir, sample_context(), ROLE)
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
        tmp_path / "advisor-strategy-001",
        sample_context(strategy="", log_text=""),
        ROLE,
    )
    text = context_path.read_text(encoding="utf-8")
    assert "(まだ書かれていない)" in text
    assert "(記録なし)" in text


def test_write_advisor_inputs_writes_role_line(tmp_path):
    context_path = write_advisor_inputs(
        tmp_path / "advisor-tips-001", sample_context(), ROLES_BY_NAME["tips"]
    )
    lines = context_path.read_text(encoding="utf-8").splitlines()
    assert "- 役割: 攻略情報・Tips の更新" in lines


# --- ClaudeCodeHeadlessAdvisor ---


def test_advisor_rejects_notes_dir_outside_workdir(tmp_path):
    workdir = tmp_path / "ch04_techniques"
    with pytest.raises(ValueError, match="notes_dir must be under workdir"):
        ClaudeCodeHeadlessAdvisor(
            workdir,
            tmp_path / "elsewhere",
            workdir / "runs",
            "zelda",
            ROLE,
        )


def test_advisor_rejects_runs_dir_outside_workdir(tmp_path):
    workdir = tmp_path / "ch04_techniques"
    with pytest.raises(ValueError, match="runs_dir must be under workdir"):
        ClaudeCodeHeadlessAdvisor(
            workdir,
            workdir / "game_notes" / "zelda",
            tmp_path / "runs",
            "zelda",
            ROLE,
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
    for name in ("advisor-strategy-001", "advisor-strategy-002"):
        run_dir = runs_dir / name
        assert (run_dir / "context.md").is_file()
        assert (run_dir / "frame-01.jpg").read_bytes() == b"jpeg-one"
    prompt = start_process.calls[0][0][2]
    assert str(runs_dir / "advisor-strategy-001" / "context.md") in prompt


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
    assert event["run_dir"] == str(runs_dir / advisor_run_dir_name("strategy", 1))
    assert event["returncode"] == 0
    assert event["permission_denials"] == CLAUDE_OUTPUT["permission_denials"]
    assert event["result"] == "戦略を更新した"
    assert advisor.is_busy() is False


def test_request_update_logs_role_name(tmp_path):
    advisor, _, recorder, _ = make_advisor(tmp_path, role=ROLES_BY_NAME["controls"])
    advisor.request_update(sample_context())
    assert recorder.events[0]["role"] == "controls"


def test_log_event_has_role_name():
    record = {
        "returncode": 0,
        "duration_s": 1.5,
        "timed_out": False,
        "error": None,
        "output": CLAUDE_OUTPUT,
        "permission_denials": [],
    }
    event = build_log_event(
        Path("/work/runs/advisor-tips-001"), record, ROLES_BY_NAME["tips"]
    )
    assert event["role"] == "tips"
    assert event["event"] == "advisor"
    assert event["result"] == "戦略を更新した"


def test_advisors_of_two_roles_keep_their_own_busy_and_run_dirs(tmp_path):
    held = Recorder()
    strategy, _, _, runs_dir = make_advisor(tmp_path, start_thread=held.hold)
    tips, _, _, _ = make_advisor(
        tmp_path, start_thread=held.hold, role=ROLES_BY_NAME["tips"]
    )
    # 片方の実行中も、もう片方は要求を受け付ける (役割どうしは並列に動いてよい)
    assert strategy.request_update(sample_context()) is True
    assert strategy.is_busy() is True
    assert tips.is_busy() is False
    assert tips.request_update(sample_context()) is True
    for job in held.held_jobs:
        job()
    # 実行のディレクトリーは役割ごとの名前と連番で、同じ runs_dir でもぶつからない
    assert (runs_dir / "advisor-strategy-001" / "result.json").is_file()
    assert (runs_dir / "advisor-tips-001" / "result.json").is_file()


def test_request_update_uses_allowed_tools_and_prompt_of_its_role(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path, role=ROLES_BY_NAME["advice"])
    advisor.request_update(sample_context())
    cmd = start_process.calls[0][0]
    rules = option_value(cmd, "--allowedTools").split(",")
    assert rules[2:] == ["Edit(/game_notes/zelda/advice.md)", "Skill(record-advice)"]
    assert "問題の解決の助言を受け持つ advisor である。" in cmd[2]


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
    assert advisor.busy is False


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
    assert advisor.busy is False


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
    assert advisor.busy is False


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
    assert advisor.busy is False


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
    assert advisor.busy is False


def test_request_update_records_exception(tmp_path):
    advisor, start_process, recorder, runs_dir = make_advisor(tmp_path)
    start_process.error = FileNotFoundError("claude not found")
    advisor.request_update(sample_context())
    result = read_result(runs_dir)
    assert result["timed_out"] is False
    assert result["returncode"] is None
    assert "claude not found" in result["error"]
    assert len(recorder.events) == 1
    assert advisor.busy is False
    # 失敗の後、FAILED_RUN_COOLDOWN_S 秒の間は次の要求を受け付けず、その後は受け付ける
    assert FAILED_RUN_COOLDOWN_S == 60.0
    assert advisor.is_busy() is True
    assert advisor.request_update(sample_context()) is False
    advisor.clock.now += FAILED_RUN_COOLDOWN_S - 0.01
    assert advisor.request_update(sample_context()) is False
    advisor.clock.now += 0.01
    assert advisor.is_busy() is False
    assert advisor.request_update(sample_context()) is True
    assert len(start_process.calls) == 2


def test_failed_run_cooldown_starts_at_end_of_failed_run(tmp_path):
    # 待ちは、失敗した実行が終わった時刻から数える (実行に 300 秒かかったタイムアウトなら、その後から 60 秒)
    advisor, start_process, _, _ = make_advisor(tmp_path)
    start_process.timeout_first = True
    start_process.duration = 300.0
    started = advisor.clock.now
    advisor.request_update(sample_context())
    assert advisor.retry_after == started + 300.0 + FAILED_RUN_COOLDOWN_S
    assert advisor.is_busy() is True


def test_run_with_nonzero_exit_code_starts_cooldown(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path)
    start_process.exit_code = 1
    advisor.request_update(sample_context())
    assert advisor.busy is False
    assert advisor.is_busy() is True
    assert advisor.request_update(sample_context()) is False


def test_successful_run_does_not_start_cooldown(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path)
    advisor.request_update(sample_context())
    assert advisor.retry_after is None
    assert advisor.is_busy() is False
    assert advisor.request_update(sample_context()) is True
    assert len(start_process.calls) == 2


def test_successful_run_after_cooldown_clears_it(tmp_path):
    advisor, start_process, _, _ = make_advisor(tmp_path, failed_run_cooldown_s=5.0)
    start_process.exit_code = 1
    advisor.request_update(sample_context())
    assert advisor.retry_after is not None
    advisor.clock.now += 5.0
    start_process.exit_code = 0
    assert advisor.request_update(sample_context()) is True
    assert advisor.retry_after is None


# --- close ---


def test_close_without_running_advisor_returns():
    advisor = ClaudeCodeHeadlessAdvisor(
        Path("/work"), Path("/work/game_notes/zelda"), Path("/work/runs"), "zelda", ROLE
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
    assert advisor.busy is False
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
    assert advisor.busy is False


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
