import io
import json
import queue
import signal
import subprocess
import threading
from pathlib import Path

import pytest

from switch2_layers.orchestrator.claude_stream_session import (
    ClaudeStreamSession,
    SessionLaunch,
    StreamEnd,
    StreamLine,
)
from switch2_layers.orchestrator.stream_messages import user_message_line

# 偽物の子プロセスが、テストを止めないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
EOF = object()

INIT = {"type": "system", "subtype": "init", "skills": ["switch-select-user"]}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}
CONTENT = [{"type": "text", "text": "## 時刻\nt=1.000"}]


def line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


class FakeClock:
    def __init__(self, now: float = 50.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStdout:
    """標準出力の偽物。キューの行を 1 つずつ返し、EOF で終わる。読むたびに on_read を呼ぶ。"""

    def __init__(self, items: queue.Queue, on_read):
        self._items = items
        self._on_read = on_read

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            item = self._items.get(timeout=WAIT_LIMIT_S)
        except queue.Empty:
            raise StopIteration from None
        if item is EOF:
            raise StopIteration
        self._on_read()
        return item


class FakeStdin:
    """標準入力の偽物。書いた文字列を覚える。broken なら BrokenPipeError を出す。"""

    def __init__(self, process: "FakeProcess"):
        self._process = process
        self.written: list[str] = []
        self.flushes = 0
        self.closed = False
        self.broken = False

    def write(self, text: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if self.broken:
            raise BrokenPipeError(32, "Broken pipe")
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        if self.broken:
            raise BrokenPipeError(32, "Broken pipe")
        self.flushes += 1

    def close(self) -> None:
        self.closed = True
        self._process.on_stdin_closed()


class FakeProcess:
    """claude の子プロセスの偽物。

    exit_on_stdin_close なら、標準入力を閉じると 0 で終わる。ignore_sigterm でなければ SIGTERM で 143、SIGKILL で -9 で
    終わる。wait(timeout) は、終わっていなければすぐ subprocess.TimeoutExpired を出す (時間がたったものとして扱う)。
    """

    def __init__(
        self,
        args,
        kwargs,
        clock: FakeClock,
        exit_on_stdin_close: bool,
        ignore_sigterm: bool,
        stderr_text: str,
    ):
        self.args = args
        self.kwargs = kwargs
        self.pid = 51000
        self.returncode: int | None = None
        self._exit_code = 0
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        self._clock = clock
        self.exit_on_stdin_close = exit_on_stdin_close
        self.ignore_sigterm = ignore_sigterm
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._items, self._advance_clock)
        self.stderr = io.StringIO(stderr_text)

    def _advance_clock(self) -> None:
        self._clock.now += 1.5

    def emit(self, text: str) -> None:
        self._items.put(text)

    def finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._exit_code = code
        self._items.put(EOF)
        self._finished.set()

    def on_stdin_closed(self) -> None:
        if self.exit_on_stdin_close:
            self.finish(0)

    def receive_signal(self, sig: int) -> None:
        if sig == signal.SIGKILL:
            self.finish(-signal.SIGKILL)
        elif sig == signal.SIGTERM and not self.ignore_sigterm:
            self.finish(143)

    def poll(self) -> int | None:
        if self._finished.is_set():
            self.returncode = self._exit_code
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._finished.is_set():
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if not self._finished.wait(WAIT_LIMIT_S):
                self.finish(-signal.SIGKILL)
        self.returncode = self._exit_code
        return self.returncode


class FakeLauncher:
    def __init__(
        self,
        clock: FakeClock,
        exit_on_stdin_close: bool = True,
        ignore_sigterm: bool = False,
        stderr_text: str = "",
        start_error: Exception | None = None,
    ):
        self.clock = clock
        self.exit_on_stdin_close = exit_on_stdin_close
        self.ignore_sigterm = ignore_sigterm
        self.stderr_text = stderr_text
        self.start_error = start_error
        self.processes: list[FakeProcess] = []
        self.signals: list[tuple[int, int]] = []

    def start(self, args, **kwargs) -> FakeProcess:
        if self.start_error is not None:
            raise self.start_error
        process = FakeProcess(
            args,
            kwargs,
            self.clock,
            self.exit_on_stdin_close,
            self.ignore_sigterm,
            self.stderr_text,
        )
        self.processes.append(process)
        return process

    def signal_group(self, pgid: int, sig: int) -> None:
        self.signals.append((pgid, sig))
        for process in self.processes:
            if process.pid == pgid:
                process.receive_signal(sig)
                return
        raise ProcessLookupError(pgid)


LAUNCH = SessionLaunch(
    command=["claude", "-p", "--input-format", "stream-json"],
    cwd=Path("/home/user/runs/20261116-120000"),
    env={"PATH": "/usr/bin"},
)


def make_session(tmp_path: Path, launcher: FakeLauncher) -> ClaudeStreamSession:
    return ClaudeStreamSession(
        LAUNCH,
        tmp_path / "stream.jsonl",
        launcher.clock,
        start_process=launcher.start,
        signal_process_group=launcher.signal_group,
    )


def read_until_end(session: ClaudeStreamSession) -> list[object]:
    items: list[object] = []
    for _ in range(100):
        item = session.read(WAIT_LIMIT_S)
        items.append(item)
        if isinstance(item, StreamEnd) or item is None:
            return items
    raise AssertionError("StreamEnd did not come")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


# --- 起動 ---


def test_start_launches_claude_with_pipes_in_a_new_session(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert process.args == LAUNCH.command
    assert process.kwargs == {
        "cwd": LAUNCH.cwd,
        "env": LAUNCH.env,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "start_new_session": True,
    }
    assert (tmp_path / "stream.jsonl").exists()
    session.close(0.01)


def test_start_failure_is_raised(tmp_path):
    launcher = FakeLauncher(
        FakeClock(), start_error=FileNotFoundError(2, "No such file", "claude")
    )
    session = make_session(tmp_path, launcher)
    with pytest.raises(FileNotFoundError):
        session.start()
    assert session.returncode is None
    assert session.close(0.01) is None


def test_second_start_is_an_error(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    with pytest.raises(RuntimeError):
        session.start()
    session.close(0.01)


def test_default_start_process_is_subprocess_popen(tmp_path, monkeypatch):
    launcher = FakeLauncher(FakeClock())
    monkeypatch.setattr(subprocess, "Popen", launcher.start)
    session = ClaudeStreamSession(LAUNCH, tmp_path / "stream.jsonl", launcher.clock)
    session.start()
    assert len(launcher.processes) == 1
    session.close(0.01)


# --- 標準出力 ---


def test_lines_are_read_in_order_then_the_end(tmp_path):
    launcher = FakeLauncher(FakeClock(50.0))
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(line(INIT))
    process.emit("not json\n")
    process.emit(line(RESULT))
    process.finish(0)
    items = read_until_end(session)
    assert items == [
        StreamLine(data=INIT, elapsed_s=1.5),
        StreamLine(data=None, elapsed_s=3.0),
        StreamLine(data=RESULT, elapsed_s=4.5),
        StreamEnd(),
    ]
    # 終わった後も StreamEnd を返す
    assert session.read(0.01) == StreamEnd()
    session.close(0.01)


def test_read_returns_none_when_nothing_arrives(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    assert session.read(0.05) is None
    session.close(0.01)


def test_stream_jsonl_has_the_same_form_as_deep(tmp_path):
    launcher = FakeLauncher(FakeClock(50.0))
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(line(INIT))
    process.emit("not json\n")
    process.emit(line(RESULT))
    process.finish(0)
    read_until_end(session)
    session.close(0.01)
    assert read_jsonl(tmp_path / "stream.jsonl") == [
        {"elapsed_s": 1.5, "data": INIT},
        {"elapsed_s": 3.0, "raw": "not json"},
        {"elapsed_s": 4.5, "data": RESULT},
    ]


def test_read_before_start_is_the_end(tmp_path):
    session = make_session(tmp_path, FakeLauncher(FakeClock()))
    assert session.read(0.01) == StreamEnd()


# --- 標準入力 ---


def test_send_writes_one_user_message_line_and_flushes(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.send(CONTENT) is True
    assert process.stdin.written == [user_message_line(CONTENT)]
    assert process.stdin.flushes >= 1
    session.close(0.01)


def test_send_to_an_ended_process_returns_false(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.stdin.broken = True
    assert session.send(CONTENT) is False
    session.close(0.01)


def test_send_before_start_and_after_close_returns_false(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    assert session.send(CONTENT) is False
    session.start()
    session.close(0.01)
    assert session.send(CONTENT) is False


# --- 終了 ---


def test_close_ends_by_closing_stdin_without_signals(tmp_path):
    launcher = FakeLauncher(FakeClock(), stderr_text="warning\n")
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.close(0.01) == 0
    assert process.stdin.closed is True
    assert launcher.signals == []
    assert session.returncode == 0
    assert session.stderr_text == "warning\n"


def test_close_sends_sigterm_when_stdin_close_is_not_enough(tmp_path):
    launcher = FakeLauncher(FakeClock(), exit_on_stdin_close=False)
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.close(0.01) == 143
    assert launcher.signals == [(process.pid, signal.SIGTERM)]


def test_close_sends_sigkill_when_sigterm_is_ignored(tmp_path):
    launcher = FakeLauncher(FakeClock(), exit_on_stdin_close=False, ignore_sigterm=True)
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.close(0.01) == -signal.SIGKILL
    assert launcher.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


def test_close_of_an_ended_process_sends_no_signal(tmp_path):
    launcher = FakeLauncher(FakeClock(), exit_on_stdin_close=False)
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.finish(1)
    read_until_end(session)
    assert session.close(0.01) == 1
    assert launcher.signals == []


def test_close_ignores_a_process_group_that_is_already_gone(tmp_path):
    launcher = FakeLauncher(FakeClock(), exit_on_stdin_close=False)

    def gone(pgid: int, sig: int) -> None:
        launcher.signals.append((pgid, sig))
        if sig == signal.SIGKILL:
            launcher.processes[0].finish(-signal.SIGKILL)
        raise ProcessLookupError(pgid)

    session = ClaudeStreamSession(
        LAUNCH,
        tmp_path / "stream.jsonl",
        launcher.clock,
        start_process=launcher.start,
        signal_process_group=gone,
    )
    session.start()
    assert session.close(0.01) == -signal.SIGKILL


def test_second_close_returns_the_same_code(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    session.start()
    assert session.close(0.01) == 0
    assert session.close(0.01) == 0
    assert launcher.signals == []


def test_close_before_start_does_nothing(tmp_path):
    launcher = FakeLauncher(FakeClock())
    session = make_session(tmp_path, launcher)
    assert session.close(0.01) is None
    assert launcher.processes == []
