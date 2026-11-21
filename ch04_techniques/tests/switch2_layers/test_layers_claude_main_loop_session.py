import io
import json
import os
import queue
import signal
import subprocess
import threading
from pathlib import Path

import pytest

from switch2_layers.orchestrator.claude_main_loop_session import (
    ClaudeMainLoopSession,
    ClaudeMainLoopSessionFactory,
    stream_line_events,
)
from switch2_layers.orchestrator.claude_session_command import (
    ClaudeSessionOptions,
    build_session_command,
    build_session_env,
)
from switch2_layers.orchestrator.claude_stream_session import (
    ClaudeStreamSession,
    SessionLaunch,
)
from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    SessionEnded,
    TurnResult,
)
from switch2_layers.orchestrator.parent_skill_names import find_parent_skill_names
from switch2_layers.orchestrator.stream_messages import user_message_line
from switch2_layers.orchestrator.turn_input import TurnImage, build_turn_content
from switch2_layers.run_directory import RunPaths

# 偽物の子プロセスが、テストを止めないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
EOF = object()

INIT_ROW = {"type": "system", "subtype": "init", "skills": ["switch-select-user"]}
THINKING_TOKENS_ROW = {
    "type": "system",
    "subtype": "thinking_tokens",
    "estimated_tokens": 50,
}
RATE_LIMIT_ROW = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
TOOL_RESULT_ROW = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_0", "content": "# 大方針"}
        ],
    },
}
COMPACT_ROW = {
    "type": "system",
    "subtype": "compact_boundary",
    "compact_metadata": {"trigger": "auto", "pre_tokens": 967_123},
}
RESULT_ROW = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "OK",
    # result の usage は呼び出しの合計。使用量の出来事にはしない
    "usage": {"input_tokens": 2, "cache_read_input_tokens": 600_000},
}
ERROR_RESULT_ROW = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "result": "API Error: 500",
}
TEXT = '会話を進める。\n\n```json\n{"actions": []}\n```'


def usage(input_tokens: int, cache_read: int, cache_creation: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "output_tokens": 7,
    }


def assistant_row(content: list[dict], message_usage: dict | None) -> dict:
    message: dict[str, object] = {
        "model": "claude-opus-5-5",
        "role": "assistant",
        "content": content,
    }
    if message_usage is not None:
        message["usage"] = message_usage
    return {"type": "assistant", "message": message, "parent_tool_use_id": None}


# --- stream-json の行から出来事への対応 (stream_line_events) ---


def test_assistant_text_line_gives_the_text_then_the_usage():
    row = assistant_row([{"type": "text", "text": TEXT}], usage(2, 1000, 500))
    assert stream_line_events(row) == (
        AssistantText(text=TEXT),
        ContextUsage(tokens=1502),
    )


def test_assistant_texts_in_one_line_are_joined_with_newlines():
    row = assistant_row(
        [{"type": "text", "text": "前半"}, {"type": "text", "text": "後半"}], None
    )
    assert stream_line_events(row) == (AssistantText(text="前半\n後半"),)


def test_assistant_line_without_text_gives_only_the_usage():
    row = assistant_row([{"type": "thinking", "thinking": ""}], usage(2, 1000, 500))
    assert stream_line_events(row) == (ContextUsage(tokens=1502),)


def test_assistant_line_without_usage_gives_only_the_text():
    row = assistant_row([{"type": "text", "text": TEXT}], None)
    assert stream_line_events(row) == (AssistantText(text=TEXT),)


def test_assistant_line_with_empty_usage_gives_zero_tokens():
    # 0 も使用量として出す (主ループはセッションの使用量を 0 にする)
    row = assistant_row([{"type": "thinking", "thinking": ""}], {})
    assert stream_line_events(row) == (ContextUsage(tokens=0),)


def test_compact_boundary_gives_the_compaction_with_its_metadata():
    assert stream_line_events(COMPACT_ROW) == (
        AutoCompaction(metadata={"trigger": "auto", "pre_tokens": 967_123}),
    )


def test_compact_boundary_without_metadata_gives_none_as_the_metadata():
    row = {"type": "system", "subtype": "compact_boundary"}
    assert stream_line_events(row) == (AutoCompaction(metadata=None),)


def test_result_line_gives_the_turn_result():
    assert stream_line_events(RESULT_ROW) == (TurnResult(is_error=False),)


def test_error_result_line_gives_an_error_turn_result():
    assert stream_line_events(ERROR_RESULT_ROW) == (TurnResult(is_error=True),)


@pytest.mark.parametrize("is_error", ["true", 1, None])
def test_result_is_an_error_only_when_is_error_is_true(is_error):
    row = {"type": "result", "subtype": "success", "is_error": is_error}
    assert stream_line_events(row) == (TurnResult(is_error=False),)


def test_result_line_without_is_error_is_not_an_error():
    assert stream_line_events({"type": "result"}) == (TurnResult(is_error=False),)


@pytest.mark.parametrize(
    "data",
    [
        INIT_ROW,
        THINKING_TOKENS_ROW,
        RATE_LIMIT_ROW,
        TOOL_RESULT_ROW,
        {"type": "system", "subtype": "permission_denied", "tool_name": "Write"},
        None,  # JSON として読めない行
        ["not", "an", "object"],
        "text",
    ],
)
def test_other_lines_give_no_event(data):
    assert stream_line_events(data) == ()


# --- 偽の子プロセス ---


class FakeStdout:
    """標準出力の偽物。キューの行を 1 つずつ返し、EOF で終わる。"""

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
        self._process.finish(0)


class FakeProcess:
    """claude の子プロセスの偽物。標準入力を閉じると 0 で、SIGTERM で 143 で終わる。"""

    def __init__(self, args, kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 52000
        self.returncode: int | None = None
        self._exit_code = 0
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._items)
        self.stderr = io.StringIO("")

    def emit(self, row: object) -> None:
        self._items.put(json.dumps(row, ensure_ascii=False) + "\n")

    def emit_text(self, text: str) -> None:
        self._items.put(text)

    def finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._exit_code = code
        self._items.put(EOF)
        self._finished.set()

    def receive_signal(self, sig: int) -> None:
        if sig == signal.SIGKILL:
            self.finish(-signal.SIGKILL)
        elif sig == signal.SIGTERM:
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
    """start_process と signal_process_group の偽物。"""

    def __init__(self, start_error: Exception | None = None):
        self.start_error = start_error
        self.processes: list[FakeProcess] = []
        self.signals: list[tuple[int, int]] = []

    def start(self, args, **kwargs) -> FakeProcess:
        if self.start_error is not None:
            raise self.start_error
        process = FakeProcess(args, kwargs)
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


def make_session(tmp_path: Path, launcher: FakeLauncher) -> ClaudeMainLoopSession:
    stream = ClaudeStreamSession(
        LAUNCH,
        tmp_path / "stream.jsonl",
        lambda: 50.0,
        start_process=launcher.start,
        signal_process_group=launcher.signal_group,
    )
    return ClaudeMainLoopSession(stream)


def read_until_ended(session: ClaudeMainLoopSession) -> list[object]:
    """SessionEnded が来るまで read の結果を並べる (None を含む)。"""
    results: list[object] = []
    for _ in range(100):
        result = session.read(WAIT_LIMIT_S)
        results.append(result)
        if isinstance(result, SessionEnded):
            return results
    raise AssertionError("SessionEnded did not come")


def image(t: float) -> TurnImage:
    return TurnImage(
        t=t, file=f"frames/interval/{round(t * 1000):09d}.jpg", jpeg=b"jpeg-bytes"
    )


# --- 出来事の受け取り (read) ---


def test_read_gives_the_events_of_each_line_in_order_then_the_end(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(INIT_ROW)
    process.emit(
        assistant_row([{"type": "thinking", "thinking": ""}], usage(2, 0, 500))
    )
    process.emit(assistant_row([{"type": "text", "text": TEXT}], usage(2, 1000, 500)))
    process.emit(RATE_LIMIT_ROW)
    process.emit(COMPACT_ROW)
    process.emit_text("not json\n")
    process.emit(RESULT_ROW)
    process.finish(0)
    assert read_until_ended(session) == [
        None,  # system/init は出来事にしない
        ContextUsage(tokens=502),
        AssistantText(text=TEXT),
        ContextUsage(tokens=1502),
        None,  # rate_limit_event
        AutoCompaction(metadata={"trigger": "auto", "pre_tokens": 967_123}),
        None,  # JSON として読めない行
        TurnResult(is_error=False),
        SessionEnded(),
    ]
    # 終わった後も SessionEnded を返す
    assert session.read(0.01) == SessionEnded()
    session.close(0.01)


def test_second_event_of_a_line_is_returned_without_waiting(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(assistant_row([{"type": "text", "text": TEXT}], usage(2, 1000, 500)))
    assert session.read(WAIT_LIMIT_S) == AssistantText(text=TEXT)
    # ためた出来事は、標準出力を待たずに返す
    assert session.read(0) == ContextUsage(tokens=1502)
    assert session.read(0.05) is None
    session.close(0.01)


def test_events_of_the_last_line_come_before_the_end(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(assistant_row([{"type": "text", "text": TEXT}], usage(2, 1000, 500)))
    process.finish(1)
    assert read_until_ended(session) == [
        AssistantText(text=TEXT),
        ContextUsage(tokens=1502),
        SessionEnded(),
    ]
    assert session.close(0.01) == 1


def test_read_returns_none_when_nothing_arrives(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    assert session.read(0.05) is None
    session.close(0.01)


def test_read_before_start_is_the_end(tmp_path):
    session = make_session(tmp_path, FakeLauncher())
    assert session.read(0.01) == SessionEnded()


def test_lines_are_written_to_the_stream_jsonl(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.emit(INIT_ROW)
    process.emit(RESULT_ROW)
    process.finish(0)
    read_until_ended(session)
    session.close(0.01)
    rows = [
        json.loads(text)
        for text in (tmp_path / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["data"] for row in rows] == [INIT_ROW, RESULT_ROW]


# --- 入力 (send_turn_in_background) ---


def test_send_writes_the_turn_content_as_one_user_message_line(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    images = [image(9.0), image(10.0)]
    pending = session.send_turn_in_background("## 時刻\nt=10.000", images)
    assert pending.wait(WAIT_LIMIT_S) is True
    assert process.stdin.written == [
        user_message_line(build_turn_content("## 時刻\nt=10.000", images))
    ]
    content = json.loads(process.stdin.written[0])["message"]["content"]
    assert [block["type"] for block in content] == [
        "text",
        "text",
        "image",
        "text",
        "image",
    ]
    session.close(0.01)


def test_send_without_images_writes_only_the_text_block(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.send_turn_in_background("引き継ぎを書く", ()).wait(WAIT_LIMIT_S)
    [written] = process.stdin.written
    assert json.loads(written)["message"]["content"] == [
        {"type": "text", "text": "引き継ぎを書く"}
    ]
    session.close(0.01)


def test_send_to_an_ended_process_finishes_as_false(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    process.stdin.broken = True
    assert session.send_turn_in_background("text", ()).wait(WAIT_LIMIT_S) is False
    session.close(0.01)


def test_send_before_start_finishes_as_false(tmp_path):
    session = make_session(tmp_path, FakeLauncher())
    pending = session.send_turn_in_background("text", ())
    assert pending.finished is True
    assert pending.wait(0) is False


# --- 起動と終了 ---


def test_start_failure_is_raised_as_os_error(tmp_path):
    launcher = FakeLauncher(start_error=FileNotFoundError(2, "No such file", "claude"))
    session = make_session(tmp_path, launcher)
    with pytest.raises(OSError):
        session.start()
    assert session.close(0.01) is None


def test_close_returns_the_return_code_of_the_stream(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    session.start()
    [process] = launcher.processes
    assert session.close(0.01) == 0
    assert process.stdin.closed is True
    assert launcher.signals == []
    # 2 回目も同じ値
    assert session.close(0.01) == 0


def test_close_before_start_does_nothing(tmp_path):
    launcher = FakeLauncher()
    session = make_session(tmp_path, launcher)
    assert session.close(0.01) is None
    assert launcher.processes == []


# --- セッションを作るもの (ClaudeMainLoopSessionFactory) ---


INSTRUCTIONS = "変わらない指示 (テストの文)"


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "20261116-120000"
    root.mkdir(parents=True)
    return root


def make_factory(
    root: Path,
    launcher: FakeLauncher,
    *,
    environ: dict[str, str] | None,
    claude: ClaudeSessionOptions | None = None,
    rotate_tokens: int = 120_000,
    instructions: str = INSTRUCTIONS,
) -> ClaudeMainLoopSessionFactory:
    return ClaudeMainLoopSessionFactory(
        paths=RunPaths(root),
        instructions=instructions,
        claude=claude if claude is not None else ClaudeSessionOptions(),
        rotate_tokens=rotate_tokens,
        clock=lambda: 50.0,
        environ=environ,
        start_process=launcher.start,
        signal_process_group=launcher.signal_group,
    )


def test_factory_launches_the_session_command_in_the_resolved_run_directory(
    tmp_path,
):
    real = make_run(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "ANTHROPIC_API_KEY": "api-key",
    }
    launcher = FakeLauncher()
    factory = make_factory(link, launcher, environ=environ)
    stream_path = tmp_path / "session-001" / "stream.jsonl"
    stream_path.parent.mkdir()
    session = factory.create(1, stream_path)
    # create は起動しない
    assert launcher.processes == []
    session.start()
    [process] = launcher.processes
    run_dir = real.resolve()
    assert process.args == build_session_command(
        RunPaths(run_dir),
        INSTRUCTIONS,
        find_parent_skill_names(run_dir),
        ClaudeSessionOptions(),
        120_000,
    )
    assert process.kwargs["cwd"] == run_dir
    assert process.kwargs["start_new_session"] is True
    assert process.kwargs["env"] == build_session_env(
        environ, ClaudeSessionOptions().hidden_env_names
    )
    assert process.kwargs["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    }
    assert stream_path.exists()
    session.close(0.01)


def test_factory_passes_the_options_and_the_rotation_tokens(tmp_path):
    launcher = FakeLauncher()
    factory = make_factory(
        make_run(tmp_path),
        launcher,
        environ={"PATH": "/usr/bin"},
        claude=ClaudeSessionOptions(effort="high"),
        rotate_tokens=980_000,
    )
    session = factory.create(2, tmp_path / "stream.jsonl")
    session.start()
    args = launcher.processes[0].args
    assert args[args.index("--effort") + 1] == "high"
    assert args[args.index("--append-system-prompt") + 1] == INSTRUCTIONS
    assert args[-2:] == ["--autocompact", "1000000"]
    session.close(0.01)


def test_factory_reads_os_environ_when_create_is_called(tmp_path, monkeypatch):
    launcher = FakeLauncher()
    factory = make_factory(make_run(tmp_path), launcher, environ=None)
    monkeypatch.setenv("LAYERS_TEST_VALUE", "after the factory")
    session = factory.create(1, tmp_path / "stream.jsonl")
    session.start()
    env = launcher.processes[0].kwargs["env"]
    assert env["LAYERS_TEST_VALUE"] == "after the factory"
    assert env == build_session_env(os.environ, ClaudeSessionOptions().hidden_env_names)
    session.close(0.01)


def test_factory_rejects_unusable_instructions_when_creating(tmp_path):
    launcher = FakeLauncher()
    factory = make_factory(
        make_run(tmp_path), launcher, environ={}, instructions="--bare"
    )
    with pytest.raises(ValueError):
        factory.create(1, tmp_path / "stream.jsonl")
    assert launcher.processes == []
