import io
import json
import os
import queue
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from switch2_layers.orchestrator.codex_main_loop_session import (
    CodexMainLoopSession,
    CodexMainLoopSessionFactory,
    CodexStartError,
    find_native_codex_dir,
    turn_image_path,
)
from switch2_layers.orchestrator.codex_messages import (
    build_turn_input,
    build_turn_start_params,
)
from switch2_layers.orchestrator.codex_session_command import (
    build_codex_command,
    build_codex_env,
    build_initialize_params,
    build_thread_start_params,
)
from switch2_layers.orchestrator.codex_skill_paths import (
    find_codex_skill_files_to_disable,
)
from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    SessionEnded,
    TurnResult,
)
from switch2_layers.orchestrator.turn_input import TurnImage
from switch2_layers.run_directory import RunPaths

# 偽物が、テストを止めないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
EOF = object()
THREAD = "019a1fe0-main-thread"
CHILD_THREAD = "019a1fe0-child-thread"
NATIVE = Path("/opt/codex/vendor/x86_64-unknown-linux-musl/bin")
MODEL = "gpt-6.2-sol"
INSTRUCTIONS = "Codex 版の変わらない指示 (テストの文)"
EFFORT = "low"
VALID_TEXT = (
    '押す。\n\n```json\n{"actions": [{"type": "press", "buttons": ["A"]}]}\n```'
)


def usage_notification(
    tokens: int, thread_id: str = THREAD, turn_id: str = "turn-1"
) -> dict:
    breakdown = {
        "totalTokens": tokens + 40,
        "inputTokens": tokens,
        "cachedInputTokens": tokens - 300,
        "cacheWriteInputTokens": 0,
        "outputTokens": 40,
        "reasoningOutputTokens": 0,
    }
    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": thread_id,
            "turnId": turn_id,
            "tokenUsage": {
                "last": breakdown,
                "total": breakdown,
                "modelContextWindow": 475000,
            },
        },
    }


def agent_message(text: str, phase: str, thread_id: str = THREAD) -> dict:
    return {
        "method": "item/completed",
        "params": {
            "threadId": thread_id,
            "turnId": "turn-1",
            "completedAtMs": 1,
            "item": {"type": "agentMessage", "id": "msg", "text": text, "phase": phase},
        },
    }


def turn_completed(status: str = "completed", thread_id: str = THREAD) -> dict:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": thread_id,
            "turn": {"id": "turn-1", "items": [], "status": status, "error": None},
        },
    }


@dataclass
class Turn:
    """turn/start 1 つへの偽物の応答。

    error        turn/start の応答を誤りにする (誤りの説明)。None なら turn の結果を返す
    messages     turn/start の応答の後に出す行 (通知やサーバーからの要求)
    exit_after   messages の後に、この終了コードで終わる
    """

    error: str | None = None
    messages: list[dict] = field(default_factory=list)
    exit_after: int | None = None


def reply_turn() -> Turn:
    """実際の app-server (2026-11-21) の並びにならう応答: 途中の文・使用量・最後の文・使用量・ターンの終わり。"""
    return Turn(
        messages=[
            {
                "method": "turn/started",
                "params": {"threadId": THREAD, "turn": {"id": "turn-1"}},
            },
            agent_message("確かめる。", "commentary"),
            usage_notification(13523),
            agent_message(VALID_TEXT, "final_answer"),
            usage_notification(13840),
            {"method": "account/rateLimits/updated", "params": {}},
            turn_completed(),
        ]
    )


@dataclass
class Script:
    """偽物の app-server のふるまい。

    initialize_error / thread_error   その要求の応答を誤りにする
    answer_initialize                 False なら initialize に答えない
    thread_result                     thread/start の応答の result (既定は THREAD のスレッド)
    exit_on_initialize                initialize を受け取ったら、答えずにこの終了コードで終わる
    before_initialize_answer          initialize の応答の前に出す行 (サーバーからの要求など)
    turns                             turn/start ごとの応答 (足りなければ応答だけを返す)
    exit_on_stdin_close               標準入力を閉じると 0 で終わる
    """

    initialize_error: str | None = None
    answer_initialize: bool = True
    thread_error: str | None = None
    thread_result: dict | None = None
    exit_on_initialize: int | None = None
    before_initialize_answer: list[dict] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    exit_on_stdin_close: bool = True


class FakeStdout:
    def __init__(self, items: queue.Queue):
        self._items = items

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            item = self._items.get(timeout=WAIT_LIMIT_S * 6)
        except queue.Empty:
            raise StopIteration from None
        if item is EOF:
            raise StopIteration
        return item


class FakeStdin:
    def __init__(self, server: "FakeAppServer"):
        self._server = server
        self._buffer = ""
        self.closed = False

    def write(self, text: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._server.on_message(json.loads(line))
        return len(text)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self) -> None:
        self.closed = True
        if self._server.script.exit_on_stdin_close:
            self._server.finish(0)


class FakeAppServer:
    """codex app-server の偽物 (台本どおりに JSON-RPC を返す子プロセス)。received に受け取った行を覚える。"""

    def __init__(self, args, kwargs, script: Script):
        self.args = args
        self.kwargs = kwargs
        self.script = script
        self.turns = list(script.turns)
        self.pid = 54000
        self.returncode: int | None = None
        self.received: list[dict] = []
        self._exit_code = 0
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._items)
        self.stderr = io.StringIO("")

    def emit(self, message: dict) -> None:
        self._items.put(json.dumps(message, ensure_ascii=False) + "\n")

    def on_message(self, message: dict) -> None:
        self.received.append(message)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            if self.script.exit_on_initialize is not None:
                self.finish(self.script.exit_on_initialize)
                return
            for row in self.script.before_initialize_answer:
                self.emit(row)
            if not self.script.answer_initialize:
                return
            self.answer(request_id, self.script.initialize_error, {"userAgent": "fake"})
        elif method == "thread/start":
            # 応答の前に、別の通知を出す (起動の間は読み捨てる)
            self.emit(
                {"method": "thread/started", "params": {"thread": {"id": THREAD}}}
            )
            result = self.script.thread_result
            if result is None:
                result = {"thread": {"id": THREAD}}
            self.answer(request_id, self.script.thread_error, result)
        elif method == "turn/start":
            turn = self.turns.pop(0) if self.turns else Turn()
            self.answer(
                request_id,
                turn.error,
                {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}},
            )
            for row in turn.messages:
                self.emit(row)
            if turn.exit_after is not None:
                self.finish(turn.exit_after)

    def answer(self, request_id, error: str | None, result: dict) -> None:
        if error is not None:
            self.emit({"id": request_id, "error": {"code": -32600, "message": error}})
        else:
            self.emit({"id": request_id, "result": result})

    def requests(self, method: str) -> list[dict]:
        return [row for row in self.received if row.get("method") == method]

    def replies(self) -> list[dict]:
        return [row for row in self.received if "method" not in row]

    def finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._exit_code = code
        self._items.put(EOF)
        self._finished.set()

    def receive_signal(self, sig: int) -> None:
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self.finish(143 if sig == signal.SIGTERM else -signal.SIGKILL)

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

    def __init__(
        self, script: Script | None = None, start_error: Exception | None = None
    ):
        self.script = script if script is not None else Script()
        self.start_error = start_error
        self.servers: list[FakeAppServer] = []
        self.signals: list[tuple[int, int]] = []

    def start(self, args, **kwargs) -> FakeAppServer:
        if self.start_error is not None:
            raise self.start_error
        server = FakeAppServer(args, kwargs, self.script)
        self.servers.append(server)
        return server

    def signal_group(self, pgid: int, sig: int) -> None:
        self.signals.append((pgid, sig))
        for server in self.servers:
            if server.pid == pgid:
                server.receive_signal(sig)
                return
        raise ProcessLookupError(pgid)


class StepClock:
    """呼ばれるたびに step 秒進む時計 (起動の上限を、実時間で待たずに確かめるため)。"""

    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "20261121-120000"
    (root / "orchestrator" / "session-001").mkdir(parents=True)
    (root / "notes").mkdir()
    return root


def make_factory(
    root: Path,
    launcher: FakeLauncher,
    *,
    environ: dict[str, str] | None = None,
    find_native_dir=None,
    startup_timeout_s: float = WAIT_LIMIT_S,
    monotonic=None,
    instructions: str = INSTRUCTIONS,
) -> CodexMainLoopSessionFactory:
    extra = {} if monotonic is None else {"monotonic": monotonic}
    return CodexMainLoopSessionFactory(
        paths=RunPaths(root),
        instructions=instructions,
        model=MODEL,
        effort=EFFORT,
        clock=lambda: 30.0,
        environ=environ
        if environ is not None
        else {"PATH": "/usr/bin", "HOME": str(root / "no-home")},
        start_process=launcher.start,
        signal_process_group=launcher.signal_group,
        find_native_dir=(
            find_native_dir
            if find_native_dir is not None
            else (lambda pid: NATIVE if pid == 54000 else None)
        ),
        startup_timeout_s=startup_timeout_s,
        **extra,
    )


def started_session(
    tmp_path: Path, script: Script | None = None
) -> tuple[CodexMainLoopSession, FakeAppServer, Path]:
    root = make_run(tmp_path)
    launcher = FakeLauncher(script)
    session = make_factory(root, launcher).create(
        1, RunPaths(root).orchestrator_session_stream(1)
    )
    session.start()
    [server] = launcher.servers
    return session, server, root


def read_events_until_result(session: CodexMainLoopSession) -> list[object]:
    """TurnResult か SessionEnded が来るまで、None でない出来事を並べる。"""
    events: list[object] = []
    for _ in range(200):
        event = session.read(WAIT_LIMIT_S)
        if event is None:
            continue
        events.append(event)
        if isinstance(event, TurnResult | SessionEnded):
            return events
    raise AssertionError("the turn did not end")


def image(t: float) -> TurnImage:
    return TurnImage(
        t=t,
        file=f"frames/interval/{round(t * 1000):09d}.jpg",
        jpeg=f"jpeg-{t}".encode(),
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


# --- 起動 ---


def test_start_initializes_and_starts_an_ephemeral_thread_with_the_profile(tmp_path):
    session, server, root = started_session(tmp_path)
    run_dir = root.resolve()
    assert session.thread_id == THREAD
    assert server.received == [
        {"id": 1, "method": "initialize", "params": build_initialize_params()},
        {"method": "initialized"},
        {
            "id": 2,
            "method": "thread/start",
            "params": build_thread_start_params(run_dir, MODEL, INSTRUCTIONS, NATIVE),
        },
    ]
    assert server.kwargs["cwd"] == run_dir
    assert server.kwargs["start_new_session"] is True
    assert session.close(0.01) == 0


def test_requests_from_the_server_during_the_start_are_answered(tmp_path):
    request = {"id": 90, "method": "item/tool/requestUserInput", "params": {}}
    session, server, _ = started_session(
        tmp_path, Script(before_initialize_answer=[request])
    )
    [reply] = server.replies()
    assert reply["id"] == 90
    assert "error" in reply
    session.close(0.01)


@pytest.mark.parametrize(
    "script, text",
    [
        (Script(initialize_error="not ready"), "initialize of codex failed: not ready"),
        (
            Script(thread_error="permissions need experimentalApi"),
            "thread/start of codex failed",
        ),
        (Script(thread_result={"thread": {}}), "no thread id"),
        (Script(exit_on_initialize=1), "codex ended before answering initialize"),
    ],
)
def test_start_failures_end_the_process_and_raise_a_start_error(tmp_path, script, text):
    root = make_run(tmp_path)
    launcher = FakeLauncher(script)
    session = make_factory(root, launcher).create(
        1, RunPaths(root).orchestrator_session_stream(1)
    )
    with pytest.raises(CodexStartError, match=text):
        session.start()
    [server] = launcher.servers
    # 起動に失敗したセッションは、主ループが close を呼ばないため、自分で終わらせる
    assert server.poll() is not None
    # 起動に失敗したセッションには送れない
    assert session.send_turn_in_background("本文", []).wait(0) is False


def test_start_error_is_an_os_error():
    assert issubclass(CodexStartError, OSError)


def test_start_without_an_answer_times_out(tmp_path):
    root = make_run(tmp_path)
    launcher = FakeLauncher(Script(answer_initialize=False))
    session = make_factory(
        root, launcher, startup_timeout_s=3.0, monotonic=StepClock(1.0)
    ).create(1, RunPaths(root).orchestrator_session_stream(1))
    with pytest.raises(CodexStartError, match="did not answer in time"):
        session.start()
    assert launcher.servers[0].poll() is not None


def test_start_without_the_native_codex_directory_times_out(tmp_path):
    root = make_run(tmp_path)
    launcher = FakeLauncher()
    session = make_factory(
        root,
        launcher,
        find_native_dir=lambda pid: None,
        startup_timeout_s=3.0,
        monotonic=StepClock(1.0),
    ).create(1, RunPaths(root).orchestrator_session_stream(1))
    with pytest.raises(CodexStartError, match="native codex directory"):
        session.start()
    # thread/start は送らない
    assert launcher.servers[0].requests("thread/start") == []


def test_start_failure_of_the_process_is_raised_as_it_is(tmp_path):
    root = make_run(tmp_path)
    launcher = FakeLauncher(start_error=FileNotFoundError(2, "No such file", "codex"))
    session = make_factory(root, launcher).create(
        1, RunPaths(root).orchestrator_session_stream(1)
    )
    with pytest.raises(FileNotFoundError):
        session.start()


# --- 入力 ---


def test_turn_input_writes_the_images_and_sends_turn_start(tmp_path):
    session, server, root = started_session(tmp_path)
    images = [image(9.0), image(10.0)]
    pending = session.send_turn_in_background("## 時刻\nt=10.000", images)
    assert pending.wait(WAIT_LIMIT_S) is True
    session_dir = (root / "orchestrator" / "session-001").resolve()
    paths = [turn_image_path(session_dir, 1, 1), turn_image_path(session_dir, 1, 2)]
    assert paths[0] == session_dir / "images" / "turn-0001-1.jpg"
    assert [path.read_bytes() for path in paths] == [b"jpeg-9.0", b"jpeg-10.0"]
    [request] = server.requests("turn/start")
    assert request["id"] == 3
    assert request["params"] == build_turn_start_params(
        THREAD,
        build_turn_input("## 時刻\nt=10.000", list(zip(images, paths, strict=True))),
        EFFORT,
    )
    assert all(
        Path(item["path"]).is_absolute()
        for item in request["params"]["input"]
        if item["type"] == "localImage"
    )
    session.close(0.01)


def test_the_next_turn_has_new_image_files_and_a_text_only_turn_has_no_images(tmp_path):
    session, server, _ = started_session(
        tmp_path, Script(turns=[reply_turn(), reply_turn()])
    )
    assert session.send_turn_in_background("1", [image(1.0)]).wait(WAIT_LIMIT_S)
    read_events_until_result(session)
    assert session.send_turn_in_background("引き継ぎ", []).wait(WAIT_LIMIT_S)
    first, second = server.requests("turn/start")
    assert first["params"]["input"][-1]["path"].endswith("turn-0001-1.jpg")
    assert second["params"]["input"] == [{"type": "text", "text": "引き継ぎ"}]
    session.close(0.01)


def test_turn_input_that_cannot_write_the_images_is_not_sent(tmp_path):
    root = make_run(tmp_path)
    launcher = FakeLauncher()
    stream_path = root / "orchestrator" / "session-001" / "stream.jsonl"
    session = make_factory(root, launcher).create(1, stream_path)
    session.start()
    # images/ を作れないようにする
    (stream_path.parent / "images").write_text("not a directory", encoding="utf-8")
    assert (
        session.send_turn_in_background("本文", [image(1.0)]).wait(WAIT_LIMIT_S)
        is False
    )
    assert launcher.servers[0].requests("turn/start") == []
    session.close(0.01)


def test_turn_input_before_start_and_after_close_is_not_sent(tmp_path):
    root = make_run(tmp_path)
    session = make_factory(root, FakeLauncher()).create(
        1, RunPaths(root).orchestrator_session_stream(1)
    )
    assert session.send_turn_in_background("本文", []).wait(0) is False
    session.start()
    session.close(0.01)
    assert session.send_turn_in_background("本文", []).wait(0) is False


# --- 出来事 ---


def test_a_turn_gives_the_texts_the_usages_and_the_result_in_order(tmp_path):
    session, _, _ = started_session(tmp_path, Script(turns=[reply_turn()]))
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [
        AssistantText(text="確かめる。"),
        ContextUsage(tokens=13523),
        AssistantText(text=VALID_TEXT),
        ContextUsage(tokens=13840),
        TurnResult(is_error=False),
    ]
    session.close(0.01)


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_a_turn_that_does_not_complete_is_an_error_result(tmp_path, status):
    session, _, _ = started_session(
        tmp_path, Script(turns=[Turn(messages=[turn_completed(status)])])
    )
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [TurnResult(is_error=True)]
    session.close(0.01)


def test_an_error_answer_to_turn_start_is_an_error_result(tmp_path):
    session, _, _ = started_session(tmp_path, Script(turns=[Turn(error="bad input")]))
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [TurnResult(is_error=True)]
    session.close(0.01)


def test_requests_from_the_server_in_a_turn_are_answered_without_events(tmp_path):
    approval = {
        "id": 91,
        "method": "item/commandExecution/requestApproval",
        "params": {},
    }
    question = {"id": 92, "method": "item/tool/requestUserInput", "params": {}}
    session, server, _ = started_session(
        tmp_path, Script(turns=[Turn(messages=[approval, question, turn_completed()])])
    )
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [TurnResult(is_error=False)]
    replies = server.replies()
    assert replies[0] == {"id": 91, "result": {"decision": "decline"}}
    assert replies[1]["id"] == 92 and "error" in replies[1]
    session.close(0.01)


def test_notifications_of_another_thread_are_not_events(tmp_path):
    session, _, _ = started_session(
        tmp_path,
        Script(
            turns=[
                Turn(
                    messages=[
                        agent_message("子の文", "final_answer", CHILD_THREAD),
                        usage_notification(999, CHILD_THREAD),
                        turn_completed(thread_id=CHILD_THREAD),
                        agent_message(VALID_TEXT, "final_answer"),
                        turn_completed(),
                    ]
                )
            ]
        ),
    )
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [
        AssistantText(text=VALID_TEXT),
        TurnResult(is_error=False),
    ]
    session.close(0.01)


def test_compaction_reported_twice_for_a_turn_is_one_event(tmp_path):
    item = {
        "method": "item/completed",
        "params": {
            "threadId": THREAD,
            "turnId": "turn-1",
            "item": {"type": "contextCompaction", "id": "c"},
        },
    }
    deprecated = {
        "method": "thread/compacted",
        "params": {"threadId": THREAD, "turnId": "turn-1"},
    }
    later = {
        "method": "thread/compacted",
        "params": {"threadId": THREAD, "turnId": "turn-2"},
    }
    session, _, _ = started_session(
        tmp_path,
        Script(turns=[Turn(messages=[item, deprecated, later, turn_completed()])]),
    )
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [
        AutoCompaction(metadata=item["params"]),
        AutoCompaction(metadata=later["params"]),
        TurnResult(is_error=False),
    ]
    session.close(0.01)


def test_the_end_of_the_output_is_the_session_end(tmp_path):
    session, _, _ = started_session(
        tmp_path,
        Script(
            turns=[Turn(messages=[agent_message("途中", "commentary")], exit_after=1)]
        ),
    )
    session.send_turn_in_background("本文", [])
    assert read_events_until_result(session) == [
        AssistantText(text="途中"),
        SessionEnded(),
    ]
    assert session.read(0.01) == SessionEnded()
    assert session.close(0.01) == 1


def test_read_returns_none_when_nothing_arrives(tmp_path):
    session, _, _ = started_session(tmp_path)
    assert session.read(0.05) is None
    session.close(0.01)


def test_stream_jsonl_has_the_received_and_the_sent_lines(tmp_path):
    session, _, root = started_session(tmp_path, Script(turns=[reply_turn()]))
    session.send_turn_in_background("本文", [])
    read_events_until_result(session)
    session.close(0.01)
    rows = read_jsonl(root / "orchestrator" / "session-001" / "stream.jsonl")
    sent = [row["sent"]["method"] for row in rows if "sent" in row]
    assert sent == ["initialize", "initialized", "thread/start", "turn/start"]
    received = [row["data"] for row in rows if "data" in row]
    assert turn_completed() in received
    assert all(
        set(row) in ({"elapsed_s", "sent"}, {"elapsed_s", "data"}) for row in rows
    )


# --- 終了 ---


def test_close_closes_stdin_and_escalates_when_codex_does_not_end(tmp_path):
    session, server, _ = started_session(tmp_path, Script(exit_on_stdin_close=False))
    assert session.close(0.01) == 143
    assert server.stdin.closed is True


# --- セッションを作るもの ---


def test_factory_builds_the_command_env_and_directories_without_starting(tmp_path):
    root = make_run(tmp_path)
    home = tmp_path / "home"
    user_skill = home / ".agents" / "skills" / "user-skill" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text(
        "---\nname: user-skill\ndescription: x\n---\n", encoding="utf-8"
    )
    environ = {
        "PATH": "/usr/bin",
        "HOME": str(home),
        "OPENAI_API_KEY": "openai",
        "TYPESAFE_API_KEY": "ts",
    }
    launcher = FakeLauncher()
    stream_path = RunPaths(root).orchestrator_session_stream(1)
    session = make_factory(root, launcher, environ=environ).create(1, stream_path)
    assert launcher.servers == []
    run_dir = root.resolve()
    env = build_codex_env(environ, ("TYPESAFE_API_KEY", "OPENAI_API_KEY"))
    assert env == {"PATH": "/usr/bin", "HOME": str(home)}
    assert session.process.launch.command == build_codex_command(
        "codex", find_codex_skill_files_to_disable(run_dir, env)
    )
    assert user_skill in find_codex_skill_files_to_disable(run_dir, env)
    assert session.process.launch.env == env
    assert session.process.launch.cwd == run_dir
    assert session.run_dir == run_dir
    assert session.session_dir == stream_path.parent.resolve()


def test_factory_reads_os_environ_when_create_is_called(tmp_path, monkeypatch):
    root = make_run(tmp_path)
    factory = CodexMainLoopSessionFactory(
        paths=RunPaths(root),
        instructions=INSTRUCTIONS,
        model=MODEL,
        effort=EFFORT,
        clock=lambda: 0.0,
    )
    monkeypatch.setenv("LAYERS_CODEX_TEST", "after the factory")
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    session = factory.create(1, RunPaths(root).orchestrator_session_stream(1))
    assert session.process.launch.env["LAYERS_CODEX_TEST"] == "after the factory"
    assert "OPENAI_API_KEY" not in session.process.launch.env
    assert session.process.launch.command[0] == "codex"


def test_factory_rejects_empty_instructions_when_creating(tmp_path):
    root = make_run(tmp_path)
    launcher = FakeLauncher()
    with pytest.raises(ValueError):
        make_factory(root, launcher, instructions="").create(
            1, RunPaths(root).orchestrator_session_stream(1)
        )
    assert launcher.servers == []


# --- Codex 本体のディレクトリーを探す ---


def make_proc(proc: Path, pid: int, exe: Path | None, children: list[int]) -> None:
    task = proc / str(pid) / "task" / str(pid)
    task.mkdir(parents=True)
    (task / "children").write_text(" ".join(str(c) for c in children), encoding="ascii")
    if exe is not None:
        (proc / str(pid) / "exe").symlink_to(exe)


def test_native_codex_dir_is_the_directory_of_a_descendant_named_codex(tmp_path):
    proc = tmp_path / "proc"
    native = tmp_path / "vendor" / "bin" / "codex"
    make_proc(proc, 100, tmp_path / "node", [101])
    make_proc(proc, 101, tmp_path / "sh", [102])
    make_proc(proc, 102, native, [])
    assert find_native_codex_dir(100, proc) == native.parent


def test_native_codex_dir_is_none_without_such_a_descendant(tmp_path):
    proc = tmp_path / "proc"
    make_proc(proc, 100, tmp_path / "node", [101])
    make_proc(proc, 101, None, [])
    assert find_native_codex_dir(100, proc) is None
    assert find_native_codex_dir(999, proc) is None


def test_native_codex_dir_of_this_process_is_none():
    # このテストのプロセス (python) とその子孫には codex がいない
    assert find_native_codex_dir(os.getpid()) is None
