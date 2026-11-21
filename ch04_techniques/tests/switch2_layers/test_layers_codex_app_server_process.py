import io
import json
import queue
import signal
import subprocess
import threading
from pathlib import Path

import pytest

from switch2_layers.orchestrator.claude_stream_session import (
    SessionLaunch,
    StreamEnd,
    StreamLine,
)
from switch2_layers.orchestrator.codex_app_server_process import (
    CodexAppServerProcess,
    CodexStreamLog,
)

# 偽物の子プロセスが、テストを止めないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
EOF = object()
LAUNCH = SessionLaunch(
    command=["codex", "app-server"],
    cwd=Path("/home/user/runs/20261121-120000"),
    env={"PATH": "/usr/bin"},
)
NOTIFICATION = {"method": "turn/started", "params": {"threadId": "t"}}


def line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


class FakeClock:
    def __init__(self, now: float = 20.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeStdout:
    def __init__(self, items: queue.Queue):
        self._items = items

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            item = self._items.get(timeout=WAIT_LIMIT_S)
        except queue.Empty:
            raise StopIteration from None
        if item is EOF:
            raise StopIteration
        return item


class FakeStdin:
    """標準入力の偽物。書いた文字列を覚える。broken なら BrokenPipeError を出す。"""

    def __init__(self, process: "FakeProcess"):
        self._process = process
        self.written: list[str] = []
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

    def close(self) -> None:
        self.closed = True
        self._process.on_stdin_closed()


class BlockingStdin(FakeStdin):
    """書き込みが、子プロセスにシグナルが届くまで戻らない標準入力。entered は書き込みが止まったときに立つ。"""

    def __init__(self, process: "FakeProcess"):
        super().__init__(process)
        self.entered = threading.Event()
        self.released = threading.Event()

    def write(self, text: str) -> int:
        self.entered.set()
        self.released.wait(WAIT_LIMIT_S * 2)
        raise BrokenPipeError(32, "Broken pipe")


class FakeProcess:
    """codex app-server の偽物。exit_on_stdin_close なら標準入力を閉じると 0 で、SIGTERM で 143 で終わる
    (ignore_sigterm なら SIGKILL まで終わらない)。"""

    def __init__(self, args, kwargs, exit_on_stdin_close: bool, ignore_sigterm: bool):
        self.args = args
        self.kwargs = kwargs
        self.pid = 53000
        self.returncode: int | None = None
        self._exit_code = 0
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        self.exit_on_stdin_close = exit_on_stdin_close
        self.ignore_sigterm = ignore_sigterm
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._items)
        self.stderr = io.StringIO("codex log\n")

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
            self._finished.wait(WAIT_LIMIT_S)
        self.returncode = self._exit_code
        return self.returncode


class FakeLauncher:
    def __init__(
        self,
        exit_on_stdin_close: bool = True,
        ignore_sigterm: bool = False,
        start_error: Exception | None = None,
    ):
        self.exit_on_stdin_close = exit_on_stdin_close
        self.ignore_sigterm = ignore_sigterm
        self.start_error = start_error
        self.processes: list[FakeProcess] = []
        self.signals: list[tuple[int, int]] = []
        self.on_signal = None

    def start(self, args, **kwargs) -> FakeProcess:
        if self.start_error is not None:
            raise self.start_error
        process = FakeProcess(
            args, kwargs, self.exit_on_stdin_close, self.ignore_sigterm
        )
        self.processes.append(process)
        return process

    def signal_group(self, pgid: int, sig: int) -> None:
        self.signals.append((pgid, sig))
        if self.on_signal is not None:
            self.on_signal()
        for process in self.processes:
            if process.pid == pgid:
                process.receive_signal(sig)
                return
        raise ProcessLookupError(pgid)


def make_process(
    tmp_path: Path, launcher: FakeLauncher, clock: FakeClock | None = None
) -> CodexAppServerProcess:
    return CodexAppServerProcess(
        LAUNCH,
        tmp_path / "stream.jsonl",
        clock if clock is not None else FakeClock(),
        start_process=launcher.start,
        signal_process_group=launcher.signal_group,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


def read_until_end(process: CodexAppServerProcess) -> list[object]:
    items: list[object] = []
    for _ in range(100):
        item = process.read(WAIT_LIMIT_S)
        items.append(item)
        if isinstance(item, StreamEnd) or item is None:
            return items
    raise AssertionError("StreamEnd did not come")


# --- 起動 ---


def test_start_launches_with_pipes_in_a_new_session(tmp_path):
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher)
    process.start()
    [child] = launcher.processes
    assert child.args == LAUNCH.command
    assert child.kwargs == {
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
    assert process.pid == child.pid
    assert (tmp_path / "stream.jsonl").exists()
    process.close(0.01)


def test_start_failure_is_raised_and_nothing_is_running(tmp_path):
    launcher = FakeLauncher(start_error=FileNotFoundError(2, "No such file", "codex"))
    process = make_process(tmp_path, launcher)
    with pytest.raises(FileNotFoundError):
        process.start()
    assert process.pid is None
    assert process.close(0.01) is None


def test_second_start_is_an_error(tmp_path):
    process = make_process(tmp_path, FakeLauncher())
    process.start()
    with pytest.raises(RuntimeError):
        process.start()
    process.close(0.01)


# --- 標準出力と stream.jsonl ---


def test_lines_are_read_in_order_then_the_end_and_logged(tmp_path):
    clock = FakeClock(20.0)
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher, clock)
    process.start()
    [child] = launcher.processes
    clock.now = 21.5
    child.emit(line(NOTIFICATION))
    child.emit("not json\n")
    child.finish(0)
    assert read_until_end(process) == [
        StreamLine(data=NOTIFICATION, elapsed_s=1.5),
        StreamLine(data=None, elapsed_s=1.5),
        StreamEnd(),
    ]
    assert process.read(0.01) == StreamEnd()
    process.close(0.01)
    assert read_jsonl(tmp_path / "stream.jsonl") == [
        {"elapsed_s": 1.5, "data": NOTIFICATION},
        {"elapsed_s": 1.5, "raw": "not json"},
    ]


def test_read_returns_none_when_nothing_arrives(tmp_path):
    process = make_process(tmp_path, FakeLauncher())
    process.start()
    assert process.read(0.05) is None
    process.close(0.01)


def test_read_before_start_is_the_end(tmp_path):
    assert make_process(tmp_path, FakeLauncher()).read(0.01) == StreamEnd()


# --- 標準入力 ---


def test_send_writes_one_json_line_and_logs_it_as_sent(tmp_path):
    clock = FakeClock(20.0)
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher, clock)
    process.start()
    [child] = launcher.processes
    clock.now = 22.25
    message = {
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "名前"}},
    }
    assert process.send(message) is True
    assert child.stdin.written == [json.dumps(message, ensure_ascii=False) + "\n"]
    process.close(0.01)
    assert read_jsonl(tmp_path / "stream.jsonl") == [
        {"elapsed_s": 2.25, "sent": message}
    ]


def test_send_to_an_ended_process_returns_false_and_is_not_logged(tmp_path):
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher)
    process.start()
    launcher.processes[0].stdin.broken = True
    assert process.send({"method": "initialized"}) is False
    process.close(0.01)
    assert read_jsonl(tmp_path / "stream.jsonl") == []


def test_send_before_start_and_after_close_returns_false(tmp_path):
    process = make_process(tmp_path, FakeLauncher())
    assert process.send({"method": "initialized"}) is False
    process.start()
    process.close(0.01)
    assert process.send({"method": "initialized"}) is False


# --- 終了 ---


def test_close_ends_by_closing_stdin_without_signals(tmp_path):
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher)
    process.start()
    assert process.close(0.01) == 0
    assert launcher.processes[0].stdin.closed is True
    assert launcher.signals == []
    assert process.returncode == 0
    assert process.stderr_text == "codex log\n"
    # 2 回目も同じ値
    assert process.close(0.01) == 0


def test_close_escalates_to_sigterm_and_sigkill(tmp_path):
    launcher = FakeLauncher(exit_on_stdin_close=False, ignore_sigterm=True)
    process = make_process(tmp_path, launcher)
    process.start()
    pid = launcher.processes[0].pid
    assert process.close(0.01) == -signal.SIGKILL
    assert launcher.signals == [(pid, signal.SIGTERM), (pid, signal.SIGKILL)]


def test_close_before_start_does_nothing(tmp_path):
    launcher = FakeLauncher()
    assert make_process(tmp_path, launcher).close(0.01) is None
    assert launcher.processes == []


def test_close_while_another_thread_writes_sends_sigterm_without_closing_stdin(
    tmp_path,
):
    launcher = FakeLauncher()
    process = make_process(tmp_path, launcher)
    process.start()
    [child] = launcher.processes
    stdin = BlockingStdin(child)
    child.stdin = stdin
    launcher.on_signal = stdin.released.set
    results: list[bool] = []
    writer = threading.Thread(
        target=lambda: results.append(process.send({"method": "initialized"}))
    )
    writer.start()
    assert stdin.entered.wait(WAIT_LIMIT_S)
    assert process.close(0.01) == 143
    assert stdin.closed is False
    assert launcher.signals == [(child.pid, signal.SIGTERM)]
    writer.join(WAIT_LIMIT_S)
    assert results == [False]


# --- stream.jsonl ---


def test_stream_log_stops_writing_after_a_failure(tmp_path):
    log = CodexStreamLog(tmp_path / "stream.jsonl")
    log.add_received(line(NOTIFICATION), 1.0)
    log.add_sent({"bad": "\ud800"}, 2.0)
    log.add_received(line(NOTIFICATION), 3.0)
    log.close()
    assert log.write_error is not None
    assert read_jsonl(tmp_path / "stream.jsonl") == [
        {"elapsed_s": 1.0, "data": NOTIFICATION}
    ]
