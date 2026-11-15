import io
import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from switch2_layers.deep import claude_code_runner
from switch2_layers.deep.claude_code_command import (
    ClaudeCodeOptions,
    build_claude_command,
)
from switch2_layers.deep.claude_code_runner import (
    KILLED_MESSAGE,
    STOPPED_MESSAGE,
    ClaudeCodeDeepRunner,
    symlink_refusal_message,
)
from switch2_layers.deep.deep_job_directory import create_job_directory
from switch2_layers.deep.deep_prompt import build_deep_prompt, read_prompt_context
from switch2_layers.deep.deep_scheduler import (
    DeepScheduler,
    DeepSchedulerParts,
    DeepSchedulerSettings,
    start_daemon_thread,
)
from switch2_layers.deep.deep_types import (
    DeepJob,
    DeepResult,
    DeepTrigger,
    DocumentKind,
)
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

# 偽物の子プロセスが、テストを止めないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
EOF = object()
START_T = 100.0  # 偽の時計の起動時の値

INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5-5"}
ASSISTANT = {
    "type": "assistant",
    "message": {"content": [{"type": "text", "text": "大方針を読む"}]},
}
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "大方針を更新した",
    "total_cost_usd": 0.42,
    "permission_denials": [],
}
ERROR_RESULT = {
    **RESULT,
    "subtype": "error_during_execution",
    "is_error": True,
    "result": "失敗した",
}


def line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


class FakeClock:
    def __init__(self, now: float = START_T):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStdout:
    """子プロセスの標準出力の偽物。行を 1 つずつ返し、EOF で終わる。読むたびに on_read(読んだ番号) を呼ぶ。"""

    def __init__(self, items: queue.Queue, on_read: Callable[[int], None]):
        self._items = items
        self._on_read = on_read
        self._count = 0
        self._done = False

    def readline(self) -> str:
        if self._done:
            return ""
        try:
            item = self._items.get(timeout=WAIT_LIMIT_S)
        except queue.Empty:
            item = EOF
        self._count += 1
        self._on_read(self._count)
        if item is EOF:
            self._done = True
            return ""
        return item

    def __iter__(self):
        return self

    def __next__(self) -> str:
        text = self.readline()
        if text == "":
            raise StopIteration
        return text

    def read(self) -> str:
        return "".join(iter(self.readline, ""))

    def close(self) -> None:
        self._done = True


class FakeClaudeProcess:
    """claude の子プロセスの偽物。

    wait_for_signal が False なら、lines を出してすぐ exit_code で終わる。True なら、lines を出した後、SIGTERM
    (ignore_sigterm なら無視する) か SIGKILL が届くまで終わらない。SIGTERM では 143、SIGKILL では -9 で終わる。
    """

    def __init__(
        self,
        args: list[str],
        kwargs: dict,
        pid: int,
        lines: list[str],
        exit_code: int,
        wait_for_signal: bool,
        ignore_sigterm: bool,
        stderr_text: str,
        on_read: Callable[[int], None],
    ):
        self.args = args
        self.kwargs = kwargs
        self.pid = pid
        self.returncode: int | None = None
        self.signals: list[int] = []
        self._ignore_sigterm = ignore_sigterm
        self._exit_code = exit_code
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        for text in lines:
            self._items.put(text)
        self.stdout = FakeStdout(self._items, on_read)
        self.stderr = io.StringIO(stderr_text)
        if not wait_for_signal:
            self.finish(exit_code)

    def finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._exit_code = code
        self._items.put(EOF)
        self._finished.set()

    def receive_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGKILL:
            self.finish(-signal.SIGKILL)
        elif sig == signal.SIGTERM and not self._ignore_sigterm:
            self.finish(143)

    def poll(self) -> int | None:
        if self._finished.is_set():
            self.returncode = self._exit_code
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        limit = WAIT_LIMIT_S if timeout is None else min(timeout, WAIT_LIMIT_S)
        if not self._finished.wait(limit):
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.finish(-signal.SIGKILL)
        self.returncode = self._exit_code
        return self.returncode


class FakeClaude:
    """start_process と signal_process_group の偽物。起動した子プロセスと、送ったシグナルを記録する。

    lines を読むたびに、偽の時計を START_T + 読んだ番号 にする (EOF を読んだら START_T + 10)。
    """

    def __init__(
        self,
        lines: list[str] = (),
        exit_code: int = 0,
        wait_for_signal: bool = False,
        ignore_sigterm: bool = False,
        stderr_text: str = "",
        start_error: Exception | None = None,
        clock: FakeClock | None = None,
    ):
        self.lines = list(lines)
        self.exit_code = exit_code
        self.wait_for_signal = wait_for_signal
        self.ignore_sigterm = ignore_sigterm
        self.stderr_text = stderr_text
        self.start_error = start_error
        self.clock = clock or FakeClock()
        self.processes: list[FakeClaudeProcess] = []
        self.signals: list[tuple[int, int]] = []
        self.started = threading.Event()

    def start(self, args: list[str], **kwargs) -> FakeClaudeProcess:
        if self.start_error is not None:
            raise self.start_error
        count = len(self.lines)

        def on_read(number: int) -> None:
            self.clock.now = START_T + (number if number <= count else 10)

        process = FakeClaudeProcess(
            args,
            kwargs,
            pid=40000 + len(self.processes),
            lines=self.lines,
            exit_code=self.exit_code,
            wait_for_signal=self.wait_for_signal,
            ignore_sigterm=self.ignore_sigterm,
            stderr_text=self.stderr_text,
            on_read=on_read,
        )
        self.processes.append(process)
        self.started.set()
        return process

    def signal_group(self, pgid: int, sig: int) -> None:
        self.signals.append((pgid, sig))
        for process in self.processes:
            if process.pid == pgid:
                process.receive_signal(sig)
                return
        raise ProcessLookupError(pgid)

    def runner(self, environ: dict[str, str] | None = None) -> ClaudeCodeDeepRunner:
        return ClaudeCodeDeepRunner(
            options=ClaudeCodeOptions(),
            start_process=self.start,
            signal_process_group=self.signal_group,
            environ=environ if environ is not None else {"PATH": "/usr/bin"},
            clock=self.clock,
        )


def make_run(tmp_path: Path) -> Path:
    """deep のジョブを用意できる実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "runs" / "20261115-120000"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "notes" / "map").mkdir()
    (root / "deep").mkdir()
    (root / "middle").mkdir()
    (root / "notes" / "strategy.md").write_text("# 大方針\n", encoding="utf-8")
    (root / "notes" / "common" / "tips.md").write_text("# 攻略情報\n", encoding="utf-8")
    return root


def deep_job(kind: DocumentKind, scene_id: str | None = None) -> DeepJob:
    return DeepJob(
        kind=kind,
        scene_id=scene_id,
        trigger=DeepTrigger.MANUAL,
        requested_t=10.0,
        previous_latest_frame_t=None,
        latest_frame_t=9.5,
    )


def prepare(
    root: Path, kind: DocumentKind = DocumentKind.STRATEGY, scene_id: str | None = None
) -> tuple[DeepJob, Path]:
    job = deep_job(kind, scene_id)
    return job, create_job_directory(RunPaths(root), job)


def make_scheduler_with_manual_threads(
    root: Path, runner: ClaudeCodeDeepRunner
) -> tuple[DeepScheduler, list[Callable[[], None]]]:
    """ジョブの本体を溜めておき、テストが呼ぶスケジューラーを作る。"""
    bodies: list[Callable[[], None]] = []
    clock = RunClock(FakeClock(), origin=START_T)
    scheduler = DeepScheduler(
        DeepSchedulerParts(
            paths=RunPaths(root),
            runner=runner,
            clock=clock,
            run_log=RunLog(root / "run.jsonl", clock),
            current_scene_id=lambda: None,
            latest_frame_t=lambda: None,
            start_job_thread=bodies.append,
        ),
        DeepSchedulerSettings(min_start_interval_s=15, auto_seconds=60, timeout_s=300),
    )
    return scheduler, bodies


def run_in_thread(
    runner: ClaudeCodeDeepRunner, job: DeepJob, job_dir: Path
) -> tuple[threading.Thread, list[DeepResult]]:
    results: list[DeepResult] = []
    thread = threading.Thread(
        target=lambda: results.append(runner.run(job, job_dir)), daemon=True
    )
    thread.start()
    return thread, results


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


# --- map ---


def test_map_job_starts_claude_for_map_json_and_places_md(tmp_path):
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.MAP)
    fake = FakeClaude(lines=[line(INIT), line(RESULT)])
    result = fake.runner().run(job, job_dir)
    assert result == DeepResult(ok=True, message="大方針を更新した")
    assert len(fake.processes) == 1
    process = fake.processes[0]
    run_abs = root.resolve()
    context = read_prompt_context(job_dir)
    assert context.notes == ("map/map.json", "map/places.md")
    assert process.args == build_claude_command(
        build_deep_prompt(context), run_abs, context.notes, ClaudeCodeOptions()
    )
    # 書いてよいのは、まだ無い 2 つの文書だけ (deep が Write で作る)
    allowed = process.args[process.args.index("--allowedTools") + 1].split(",")
    notes = (run_abs / "notes").as_posix()
    assert allowed == [
        f"Read(/{run_abs.as_posix()}/**)",
        f"Edit(/{notes}/map/map.json)",
        f"Edit(/{notes}/map/places.md)",
    ]
    assert process.kwargs["cwd"] == run_abs
    assert (job_dir / "stream.jsonl").exists()
    assert read_json(job_dir / "result.json")["returncode"] == 0


def test_map_job_end_is_recorded_from_the_claude_result(tmp_path):
    root = make_run(tmp_path)
    fake = FakeClaude(lines=[line(RESULT)])
    scheduler, bodies = make_scheduler_with_manual_threads(root, fake.runner())
    assert scheduler.request(DocumentKind.MAP, DeepTrigger.AUTO, None).started
    for body in bodies:
        body()
    ends = [row for row in read_jsonl(root / "run.jsonl") if row["event"] == "deep_end"]
    assert len(ends) == 1
    assert ends[0]["kind"] == "map"
    assert ends[0]["status"] == "ok"
    assert ends[0]["message"] == "大方針を更新した"
    assert len(fake.processes) == 1


def link_map_json(root: Path, tmp_path: Path) -> Path:
    """notes/map/map.json を、実行ディレクトリーの外のファイルへのシンボリックリンクにする。"""
    secret = write_outside_file(tmp_path)
    os.symlink(secret, root / "notes" / "map" / "map.json")
    return root / "notes" / "map" / "map.json"


def link_map_directory(root: Path, tmp_path: Path) -> Path:
    """notes/map/ を、実行ディレクトリーの外へのシンボリックリンクにする。"""
    write_outside_file(tmp_path)
    (root / "notes" / "map").rmdir()
    os.symlink(tmp_path / "outside", root / "notes" / "map")
    return root / "notes" / "map"


@pytest.mark.parametrize("make_link", [link_map_json, link_map_directory])
def test_map_job_with_a_symlink_on_its_documents_does_not_start_claude(
    tmp_path, make_link
):
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.MAP)
    link = make_link(root, tmp_path)
    fake = FakeClaude(start_error=AssertionError("claude must not be started"))
    result = fake.runner().run(job, job_dir)
    expected_link = root.resolve() / link.relative_to(root)
    assert result == DeepResult(
        ok=False, message=symlink_refusal_message(expected_link)
    )
    assert fake.processes == []
    assert read_json(job_dir / "result.json")["error"] == result.message


# --- 起動の引数・作業ディレクトリー・環境変数 ---


def test_claude_is_started_with_the_built_command(tmp_path):
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    fake = FakeClaude(lines=[line(RESULT)])
    environ = {
        "TYPESAFE_API_KEY": "typesafe",
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
    }
    fake.runner(environ).run(job, job_dir)
    assert len(fake.processes) == 1
    process = fake.processes[0]
    run_abs = root.resolve()
    context = read_prompt_context(job_dir)
    assert process.args == build_claude_command(
        build_deep_prompt(context), run_abs, context.notes, ClaudeCodeOptions()
    )
    assert context.notes == ("strategy.md",)
    assert process.args[process.args.index("--model") + 1] == "claude-opus-5-5"
    assert process.args[process.args.index("--effort") + 1] == "high"
    kwargs = process.kwargs
    assert kwargs["cwd"] == run_abs
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE
    assert kwargs["text"] is True
    assert kwargs["env"] == {
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
    }


def test_claude_does_not_receive_variables_of_the_parent_claude_code_session(tmp_path):
    # deep-once を Claude Code のセッションの中から動かしても、親のセッションの変数を claude に渡さない
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    fake = FakeClaude(lines=[line(RESULT)])
    environ = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_CODE_SESSION_ID": "parent-session",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/inbox.sock",
        "CLAUDE_CODE_MESSAGING_TOKEN": "token",
        "CLAUDE_EFFORT": "max",
        "CLAUDE_PID": "4242",
        "CLAUDE_CONFIG_DIR": "/home/user/.claude",
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
    }
    fake.runner(environ).run(job, job_dir)
    assert fake.processes[0].kwargs["env"] == {
        "CLAUDE_CONFIG_DIR": "/home/user/.claude",
        "ANTHROPIC_API_KEY": "anthropic",
        "PATH": "/usr/bin",
    }


def test_relative_job_directory_uses_absolute_paths(tmp_path, monkeypatch):
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    monkeypatch.chdir(tmp_path)
    relative = job_dir.relative_to(tmp_path)
    fake = FakeClaude(lines=[line(RESULT)])
    fake.runner().run(job, relative)
    process = fake.processes[0]
    assert process.kwargs["cwd"] == root.resolve()
    allowed = process.args[process.args.index("--allowedTools") + 1]
    assert allowed == (
        f"Read(/{root.resolve().as_posix()}/**),"
        f"Edit(/{root.resolve().as_posix()}/notes/strategy.md)"
    )


def test_edit_rules_name_the_documents_of_the_job(tmp_path):
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.TIPS, "scene-001")
    fake = FakeClaude(lines=[line(RESULT)])
    fake.runner().run(job, job_dir)
    args = fake.processes[0].args
    allowed = args[args.index("--allowedTools") + 1].split(",")
    notes = (root.resolve() / "notes").as_posix()
    assert allowed[1:] == [
        f"Edit(/{notes}/scenes/scene-001/tips.md)",
        f"Edit(/{notes}/scenes/scene-001/box_targets.md)",
        f"Edit(/{notes}/common/tips.md)",
    ]


def test_runner_writes_only_records_in_the_job_directory(tmp_path):
    # 文書は deep (Claude Code) が直接編集する。runner は文書を書かず、ジョブのディレクトリーに記録だけを足す
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    notes_before = {
        path.relative_to(root): path.read_bytes()
        for path in (root / "notes").rglob("*")
        if path.is_file()
    }
    fake = FakeClaude(lines=[line(RESULT)])
    assert fake.runner().run(job, job_dir).ok
    assert sorted(path.name for path in job_dir.iterdir()) == [
        "job.json",
        "result.json",
        "stream.jsonl",
    ]
    assert {
        path.relative_to(root): path.read_bytes()
        for path in (root / "notes").rglob("*")
        if path.is_file()
    } == notes_before


def test_default_start_process_is_subprocess_popen(tmp_path, monkeypatch):
    # 既定の起動は、作るときに subprocess.Popen を引く (テストで差し替えられる)
    fake = FakeClaude(lines=[line(RESULT)])
    monkeypatch.setattr(subprocess, "Popen", fake.start)
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    runner = ClaudeCodeDeepRunner(environ={"PATH": "/usr/bin"}, clock=fake.clock)
    assert runner.run(job, job_dir).ok
    assert len(fake.processes) == 1


# --- 起動前のシンボリックリンクの確認 ---


def write_outside_file(tmp_path: Path) -> Path:
    """実行ディレクトリーの外の、deep に読み書きさせたくないファイルを作って返す。"""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.md"
    secret.write_text("text outside of notes\n", encoding="utf-8")
    return secret


def link_notes_directory(root: Path, tmp_path: Path) -> Path:
    """notes/ を、中身を移した先へのシンボリックリンクにする。リンクのパスを返す。"""
    real = tmp_path / "notes-real"
    os.rename(root / "notes", real)
    os.symlink(real, root / "notes")
    return root / "notes"


def link_common_directory(root: Path, tmp_path: Path) -> Path:
    """notes/common/ を、実行ディレクトリーの外へのシンボリックリンクにする。リンクのパスを返す。"""
    write_outside_file(tmp_path)
    (root / "notes" / "common" / "tips.md").unlink()
    (root / "notes" / "common").rmdir()
    os.symlink(tmp_path / "outside", root / "notes" / "common")
    return root / "notes" / "common"


def link_scene_directory(root: Path, tmp_path: Path) -> Path:
    """notes/scenes/scene-001/ を、実行ディレクトリーの外へのシンボリックリンクにする。リンクのパスを返す。"""
    write_outside_file(tmp_path)
    (root / "notes" / "scenes").mkdir(exist_ok=True)
    os.symlink(tmp_path / "outside", root / "notes" / "scenes" / "scene-001")
    return root / "notes" / "scenes" / "scene-001"


def link_scene_tips(root: Path, tmp_path: Path) -> Path:
    """notes/scenes/scene-001/tips.md を、実行ディレクトリーの外のファイルへのシンボリックリンクにする。"""
    secret = write_outside_file(tmp_path)
    scene_dir = root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(secret, scene_dir / "tips.md")
    return scene_dir / "tips.md"


def link_box_targets_inside_notes(root: Path, tmp_path: Path) -> Path:
    """notes/scenes/scene-001/box_targets.md を、notes/ の中の別の文書へのシンボリックリンクにする。"""
    scene_dir = root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(root / "notes" / "strategy.md", scene_dir / "box_targets.md")
    return scene_dir / "box_targets.md"


@pytest.mark.parametrize(
    "make_link",
    [
        link_notes_directory,
        link_common_directory,
        link_scene_directory,
        link_scene_tips,
        link_box_targets_inside_notes,
    ],
)
def test_job_with_a_symlink_on_its_documents_does_not_start_claude(tmp_path, make_link):
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.TIPS, "scene-001")
    link = make_link(root, tmp_path)
    fake = FakeClaude(start_error=AssertionError("claude must not be started"))
    result = fake.runner().run(job, job_dir)
    # リンクのパスは、実行ディレクトリーのシンボリックリンクを解決した絶対パスの下の、解決しないパス
    expected_link = root.resolve() / link.relative_to(root)
    assert result == DeepResult(
        ok=False, message=symlink_refusal_message(expected_link)
    )
    assert result.stopped is False
    assert expected_link.as_posix() in result.message
    assert fake.processes == []
    assert not (job_dir / "stream.jsonl").exists()
    record = read_json(job_dir / "result.json")
    assert record["returncode"] is None
    assert record["error"] == result.message
    assert record["stopped"] is False


def test_symlink_refusal_message_names_the_link():
    link = Path("/home/user/runs/20261115-120000/notes/common")
    assert symlink_refusal_message(link) == (
        "Claude Code was not started: "
        "/home/user/runs/20261115-120000/notes/common is a symbolic link"
    )


def test_symlink_refusal_is_recorded_in_deep_end(tmp_path):
    root = make_run(tmp_path)
    secret = write_outside_file(tmp_path)
    (root / "notes" / "strategy.md").unlink()
    os.symlink(secret, root / "notes" / "strategy.md")
    fake = FakeClaude(start_error=AssertionError("claude must not be started"))
    scheduler, bodies = make_scheduler_with_manual_threads(root, fake.runner())
    assert scheduler.request(DocumentKind.STRATEGY, DeepTrigger.AUTO, None).started
    for body in bodies:
        body()
    ends = [row for row in read_jsonl(root / "run.jsonl") if row["event"] == "deep_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "failed"
    link = root.resolve() / "notes" / "strategy.md"
    assert ends[0]["message"] == symlink_refusal_message(link)
    assert link.as_posix() in ends[0]["message"]
    assert fake.processes == []
    # 外のファイルは変わらない
    assert secret.read_text(encoding="utf-8") == "text outside of notes\n"


def test_symlinks_off_the_documents_of_the_job_do_not_stop_the_start(tmp_path):
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.TIPS, "scene-001")
    secret = write_outside_file(tmp_path)
    # 対象の文書でも、その親でもないパスのリンク
    os.symlink(secret, root / "notes" / "advice.md")
    os.symlink(tmp_path / "outside", root / "notes" / "scenes-linked")
    fake = FakeClaude(lines=[line(RESULT)])
    assert fake.runner().run(job, job_dir).ok
    assert len(fake.processes) == 1


def test_missing_documents_and_scene_directory_do_not_stop_the_start(tmp_path):
    # まだ無い文書と、まだ無い場面のディレクトリーは、deep が Write で作る
    root = make_run(tmp_path)
    job, job_dir = prepare(root, DocumentKind.CONTROLS, "scene-009")
    assert not (root / "notes" / "scenes").exists()
    fake = FakeClaude(lines=[line(RESULT)])
    assert fake.runner().run(job, job_dir).ok
    assert len(fake.processes) == 1


def test_symlinked_run_directory_is_resolved_before_the_check(tmp_path):
    # notes/ より上 (実行ディレクトリー) のリンクは確かめず、解決した絶対パスで起動する
    root = make_run(tmp_path)
    linked_runs = tmp_path / "linked-runs"
    os.symlink(root.parent, linked_runs)
    linked_root = linked_runs / root.name
    job, job_dir = prepare(linked_root)
    fake = FakeClaude(lines=[line(RESULT)])
    assert fake.runner().run(job, job_dir).ok
    process = fake.processes[0]
    assert process.kwargs["cwd"] == root.resolve()
    allowed = process.args[process.args.index("--allowedTools") + 1]
    assert f"Edit(/{root.resolve().as_posix()}/notes/strategy.md)" in allowed


# --- stream.jsonl と result.json ---


def test_stream_json_and_result_json_are_recorded(tmp_path):
    root = make_run(tmp_path)
    job, job_dir = prepare(root)
    fake = FakeClaude(
        lines=[line(INIT), line(ASSISTANT), "not json\n", line(RESULT)],
        stderr_text="warning\n",
    )
    result = fake.runner().run(job, job_dir)
    assert result == DeepResult(ok=True, message="大方針を更新した")
    assert read_jsonl(job_dir / "stream.jsonl") == [
        {"elapsed_s": 1.0, "data": INIT},
        {"elapsed_s": 2.0, "data": ASSISTANT},
        {"elapsed_s": 3.0, "raw": "not json"},
        {"elapsed_s": 4.0, "data": RESULT},
    ]
    assert read_json(job_dir / "result.json") == {
        "returncode": 0,
        "duration_s": 10.0,
        "cost_usd": 0.42,
        "last_message": "大方針を更新した",
        "is_error": False,
        "permission_denials": [],
        "stopped": False,
        "killed": False,
        "error": None,
        "stderr": "warning\n",
    }


def test_long_last_message_is_cut_in_the_result(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line({**RESULT, "result": "あ" * 1000})])
    result = fake.runner().run(job, job_dir)
    assert result.ok
    assert result.message == "あ" * 300
    assert read_json(job_dir / "result.json")["last_message"] == "あ" * 1000


def test_error_result_is_a_failure(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line(ERROR_RESULT)], exit_code=1)
    result = fake.runner().run(job, job_dir)
    assert result.ok is False
    assert result.stopped is False
    assert result.message == "claude exited with code 1: 失敗した"
    assert read_json(job_dir / "result.json")["returncode"] == 1


def test_exit_without_result_line_is_a_failure(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line(INIT)], exit_code=0)
    result = fake.runner().run(job, job_dir)
    assert result.ok is False
    assert result.message.startswith("claude exited with code 0")


def test_result_without_is_error_is_a_failure(tmp_path):
    # 成功の判定は、result.json から読む判定 (deep_job_success.is_success_result_record) と同じにする
    job, job_dir = prepare(make_run(tmp_path))
    result_line = {key: value for key, value in RESULT.items() if key != "is_error"}
    fake = FakeClaude(lines=[line(result_line)], exit_code=0)
    result = fake.runner().run(job, job_dir)
    assert result.ok is False
    assert result.message.startswith("claude exited with code 0")


def test_claude_that_cannot_start_is_recorded_as_a_failure(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(start_error=FileNotFoundError(2, "No such file", "claude"))
    result = fake.runner().run(job, job_dir)
    assert result.ok is False
    assert result.stopped is False
    record = read_json(job_dir / "result.json")
    assert record["returncode"] is None
    assert "FileNotFoundError" in record["error"]
    assert result.message == record["error"]


def test_broken_job_file_is_recorded_as_a_failure(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    (job_dir / "job.json").write_text("{", encoding="utf-8")
    fake = FakeClaude(start_error=AssertionError("claude must not be started"))
    result = fake.runner().run(job, job_dir)
    assert result.ok is False
    assert fake.processes == []
    assert read_json(job_dir / "result.json")["error"]


# --- stop と kill ---


def wait_started(fake: FakeClaude) -> FakeClaudeProcess:
    assert fake.started.wait(WAIT_LIMIT_S)
    return fake.processes[0]


def test_stop_sends_sigterm_to_the_process_group(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line(INIT), line(ASSISTANT)], wait_for_signal=True)
    runner = fake.runner()
    thread, results = run_in_thread(runner, job, job_dir)
    process = wait_started(fake)
    runner.stop(job_dir)
    assert fake.signals == [(process.pid, signal.SIGTERM)]
    thread.join(WAIT_LIMIT_S * 2)
    assert not thread.is_alive()
    assert results == [DeepResult(ok=False, message=STOPPED_MESSAGE, stopped=True)]
    record = read_json(job_dir / "result.json")
    assert record["returncode"] == 143
    assert record["stopped"] is True
    assert record["killed"] is False
    # SIGTERM で止めると result の行は出ない。最後のメッセージは最後の assistant の文
    assert record["cost_usd"] is None
    assert record["last_message"] == "大方針を読む"


def test_stop_and_kill_return_without_waiting(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(wait_for_signal=True, ignore_sigterm=True)
    runner = fake.runner()
    thread, results = run_in_thread(runner, job, job_dir)
    process = wait_started(fake)
    began = time.monotonic()
    runner.stop(job_dir)
    assert time.monotonic() - began < 1.0
    # SIGTERM を無視する子プロセスは、まだ終わっていない
    thread.join(0.2)
    assert thread.is_alive()
    began = time.monotonic()
    runner.kill(job_dir)
    assert time.monotonic() - began < 1.0
    thread.join(WAIT_LIMIT_S * 2)
    assert not thread.is_alive()
    assert fake.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert results == [DeepResult(ok=False, message=KILLED_MESSAGE, stopped=True)]
    record = read_json(job_dir / "result.json")
    assert record["stopped"] is True
    assert record["killed"] is True


@pytest.mark.parametrize("request_stop", ["stop", "kill"])
def test_stop_or_kill_before_run_prevents_the_start(tmp_path, request_stop):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(start_error=AssertionError("claude must not be started"))
    runner = fake.runner()
    getattr(runner, request_stop)(job_dir)
    result = runner.run(job, job_dir)
    assert result.ok is False
    assert result.stopped is True
    assert fake.processes == []
    assert fake.signals == []


def test_stop_and_kill_after_run_returned_send_no_signal(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line(RESULT)])
    runner = fake.runner()
    assert runner.run(job, job_dir).ok
    runner.stop(job_dir)
    runner.kill(job_dir)
    assert fake.signals == []


def test_stop_ignores_errors_from_signals(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(wait_for_signal=True)

    def gone(pgid: int, sig: int) -> None:
        raise ProcessLookupError(pgid)

    runner = ClaudeCodeDeepRunner(
        start_process=fake.start,
        signal_process_group=gone,
        environ={"PATH": "/usr/bin"},
        clock=fake.clock,
    )
    thread, results = run_in_thread(runner, job, job_dir)
    process = wait_started(fake)
    runner.stop(job_dir)
    runner.kill(job_dir)
    process.finish(143)
    thread.join(WAIT_LIMIT_S * 2)
    assert results and results[0].stopped is True


def test_stop_of_unknown_job_does_not_raise(tmp_path):
    fake = FakeClaude()
    runner = fake.runner()
    runner.stop(tmp_path / "deep" / "advice-009")
    runner.kill(tmp_path / "deep" / "advice-009")
    assert fake.signals == []


def test_concurrent_jobs_are_stopped_separately(tmp_path):
    root = make_run(tmp_path)
    first_job, first_dir = prepare(root)
    second_job, second_dir = prepare(root, DocumentKind.ADVICE)
    fake = FakeClaude(wait_for_signal=True)
    runner = fake.runner()
    first_thread, first_results = run_in_thread(runner, first_job, first_dir)
    assert fake.started.wait(WAIT_LIMIT_S)
    fake.started.clear()
    second_thread, second_results = run_in_thread(runner, second_job, second_dir)
    assert fake.started.wait(WAIT_LIMIT_S)
    first_process, second_process = fake.processes
    assert first_process.kwargs["cwd"] == second_process.kwargs["cwd"]
    runner.stop(second_dir)
    second_thread.join(WAIT_LIMIT_S * 2)
    assert fake.signals == [(second_process.pid, signal.SIGTERM)]
    assert second_results[0].stopped is True
    assert first_thread.is_alive()
    runner.stop(first_dir)
    first_thread.join(WAIT_LIMIT_S * 2)
    assert first_results[0].stopped is True


# --- run が例外で抜けるときの後片付け ---


class InterruptingStdout:
    """lines を返した後、次の読み取りで KeyboardInterrupt を出す標準出力の偽物 (deep-once の最中の Ctrl+C の代わり)。"""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        raise KeyboardInterrupt


def interrupting_runner(
    fake: FakeClaude, wait: Callable[..., int] | None = None
) -> ClaudeCodeDeepRunner:
    """起動した子プロセスの標準出力を、1 行の後に KeyboardInterrupt を出すものに差し替える runner を返す。

    wait を渡すと、子プロセスの wait もそれに差し替える。
    """

    def start(args: list[str], **kwargs) -> FakeClaudeProcess:
        process = fake.start(args, **kwargs)
        process.stdout = InterruptingStdout([line(INIT)])
        if wait is not None:
            process.wait = wait
        return process

    return ClaudeCodeDeepRunner(
        start_process=start,
        signal_process_group=fake.signal_group,
        environ={"PATH": "/usr/bin"},
        clock=fake.clock,
    )


def test_interrupted_run_stops_claude_before_raising(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(wait_for_signal=True)
    runner = interrupting_runner(fake)
    with pytest.raises(KeyboardInterrupt):
        runner.run(job, job_dir)
    [process] = fake.processes
    # プロセスグループに SIGTERM を送り、終わるのを待って回収してから例外を出す
    assert fake.signals == [(process.pid, signal.SIGTERM)]
    assert process.returncode == 143
    # 読んだ行は stream.jsonl に残る。result.json は書かない
    assert read_jsonl(job_dir / "stream.jsonl") == [{"elapsed_s": 0.0, "data": INIT}]
    assert not (job_dir / "result.json").exists()
    # 実行中の記録から外れているため、後から stop・kill を呼んでもシグナルを送らない
    runner.stop(job_dir)
    runner.kill(job_dir)
    assert fake.signals == [(process.pid, signal.SIGTERM)]


def test_interrupted_run_kills_claude_that_ignores_sigterm(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code_runner, "ERROR_STOP_GRACE_S", 0.1)
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(wait_for_signal=True, ignore_sigterm=True)
    runner = interrupting_runner(fake)
    with pytest.raises(KeyboardInterrupt):
        runner.run(job, job_dir)
    [process] = fake.processes
    assert fake.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.returncode == -signal.SIGKILL


def test_second_interrupt_while_waiting_sends_sigkill(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(wait_for_signal=True, ignore_sigterm=True)

    def interrupted_wait(timeout: float | None = None) -> int:
        # SIGTERM の後の待ちの最中に、もう一度 Ctrl+C を押した
        raise KeyboardInterrupt

    runner = interrupting_runner(fake, wait=interrupted_wait)
    with pytest.raises(KeyboardInterrupt):
        runner.run(job, job_dir)
    [process] = fake.processes
    assert fake.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


def test_failure_to_create_stream_log_stops_claude(tmp_path):
    job, job_dir = prepare(make_run(tmp_path))
    # stream.jsonl をディレクトリーにして、作れなくする
    (job_dir / "stream.jsonl").mkdir()
    fake = FakeClaude(lines=[line(INIT)], wait_for_signal=True)
    runner = fake.runner()
    with pytest.raises(OSError):
        runner.run(job, job_dir)
    [process] = fake.processes
    assert fake.signals == [(process.pid, signal.SIGTERM)]
    assert process.returncode == 143


def test_exception_after_claude_ended_sends_no_signal(tmp_path, monkeypatch):
    job, job_dir = prepare(make_run(tmp_path))
    fake = FakeClaude(lines=[line(RESULT)])
    runner = fake.runner()

    def failing_join(self, timeout: float) -> str:
        raise RuntimeError("stderr reader failed")

    # 子プロセスを回収した後 (wait の後) の例外では、シグナルを送らない
    monkeypatch.setattr(claude_code_runner._StderrReader, "join", failing_join)
    with pytest.raises(RuntimeError):
        runner.run(job, job_dir)
    assert fake.processes[0].returncode == 0
    assert fake.signals == []


# --- タイムアウト (DeepScheduler が stop・kill を呼ぶ) ---


def wait_for_deep_end(run_log_path: Path) -> dict:
    """run.jsonl に deep_end が書かれるまで待ち、その行を返す (上限 WAIT_LIMIT_S * 2 秒)。"""
    deadline = time.monotonic() + WAIT_LIMIT_S * 2
    while time.monotonic() < deadline:
        if run_log_path.exists():
            ends = [
                row for row in read_jsonl(run_log_path) if row["event"] == "deep_end"
            ]
            if ends:
                return ends[0]
        time.sleep(0.01)
    raise AssertionError("deep_end was not written")


def make_scheduler(
    root: Path, runner: ClaudeCodeDeepRunner, monotonic: FakeClock
) -> DeepScheduler:
    clock = RunClock(monotonic, origin=START_T)
    return DeepScheduler(
        DeepSchedulerParts(
            paths=RunPaths(root),
            runner=runner,
            clock=clock,
            run_log=RunLog(root / "run.jsonl", clock),
            current_scene_id=lambda: None,
            latest_frame_t=lambda: None,
            start_job_thread=start_daemon_thread,
        ),
        DeepSchedulerSettings(
            min_start_interval_s=15, auto_seconds=60, timeout_s=10, stop_grace_s=5
        ),
    )


def test_timeout_stops_claude_with_sigterm(tmp_path):
    root = make_run(tmp_path)
    scheduler_clock = FakeClock()
    fake = FakeClaude(wait_for_signal=True)
    scheduler = make_scheduler(root, fake.runner(), scheduler_clock)
    assert scheduler.request(DocumentKind.STRATEGY, DeepTrigger.AUTO, None).started
    process = wait_started(fake)
    scheduler_clock.now = START_T + 10
    scheduler.check_timeouts()
    end = wait_for_deep_end(root / "run.jsonl")
    assert end["status"] == "stopped"
    assert end["stop_reason"] == "timeout"
    assert fake.signals == [(process.pid, signal.SIGTERM)]


def test_timeout_kills_claude_that_ignores_sigterm(tmp_path):
    root = make_run(tmp_path)
    scheduler_clock = FakeClock()
    fake = FakeClaude(wait_for_signal=True, ignore_sigterm=True)
    scheduler = make_scheduler(root, fake.runner(), scheduler_clock)
    assert scheduler.request(DocumentKind.STRATEGY, DeepTrigger.AUTO, None).started
    process = wait_started(fake)
    scheduler_clock.now = START_T + 10
    scheduler.check_timeouts()
    scheduler_clock.now = START_T + 15
    scheduler.check_timeouts()
    end = wait_for_deep_end(root / "run.jsonl")
    assert end["status"] == "killed"
    assert fake.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
