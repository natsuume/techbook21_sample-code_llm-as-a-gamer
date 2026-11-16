import base64
import io
import itertools
import json
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from switch2_layers.file_io import append_json_line, write_bytes_atomic
from switch2_layers.frames.frame_store import FrameRecord, frame_record_row
from switch2_layers.orchestrator.claude_session_command import (
    ClaudeSessionOptions,
    build_session_command,
)
from switch2_layers.orchestrator.decision_types import (
    Decision,
    DecisionError,
    MonitorCase,
    MonitorCreate,
    PressAction,
)
from switch2_layers.orchestrator.main_loop import (
    MAX_SESSION_FAILURES,
    OrchestratorMainLoop,
    OrchestratorMainLoopParts,
    OrchestratorMainLoopSettings,
    decision_failure_reason,
    main_loop_failure_reason,
    session_failure_reason,
)
from switch2_layers.orchestrator.orchestrator_events import (
    CaseHit,
    OrchestratorEvents,
)
from switch2_layers.orchestrator.orchestrator_instructions import (
    handoff_request_text,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.parent_skill_names import find_parent_skill_names
from switch2_layers.progress.progress_state import ProgressState, write_progress_state
from switch2_layers.progress.progress_text import NO_PROGRESS_TEXT
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

# 偽物とテストが、止まらないために待つ上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 5.0
# 偽物の標準出力が、行の来ない間に待つ上限 (秒)。読むスレッドを daemon のまま残さないため
STDOUT_IDLE_LIMIT_S = 30.0
EOF = object()
START_T = 10.0  # 偽の時計の最初の時刻 (frames.jsonl の最新のフレームの時刻と同じ)

VALID_TEXT = (
    "会話を進めるため A を押す。\n\n"
    '```json\n{"actions": [{"type": "press", "buttons": ["A"]}]}\n```'
)
VALID_JSON = '{"actions": [{"type": "press", "buttons": ["A"]}]}'
INVALID_TEXT = "INVALID: JSON のブロックを書き忘れた"
HANDOFF_REPLY_TEXT = '引き継ぎを書いた。\n\n```json\n{"actions": []}\n```'
DECISION = Decision(
    actions=(PressAction(buttons=("A",), sticks={}, seconds=None),),
    macros_set={},
    macros_delete=(),
    monitor=None,
)
INIT_ROW = {"type": "system", "subtype": "init", "skills": ["switch-select-user"]}
COMPACT_ROW = {
    "type": "system",
    "subtype": "compact_boundary",
    "compact_metadata": {"trigger": "auto", "pre_tokens": 967_123},
}


# --- 偽の stream-json の行 ---


def usage(tokens: int) -> dict[str, int]:
    """input + cache_read + cache_creation が tokens になる usage (tokens は 502 以上)。"""
    return {
        "input_tokens": 2,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": tokens - 502,
        "output_tokens": 7,
    }


def assistant_row(content: list[dict], tokens: int | None) -> dict:
    message: dict[str, object] = {
        "model": "claude-opus-5-5",
        "role": "assistant",
        "content": content,
    }
    if tokens is not None:
        message["usage"] = usage(tokens)
    return {"type": "assistant", "message": message, "parent_tool_use_id": None}


@dataclass
class Reply:
    """1 つの入力への応答。実際の stream-json (2026-11-16、Claude Code 2.1.325) の行の並びにならう。

    text         最後の assistant の文。None なら文の行を出さない
    calls        API の呼び出しごとの使用量 (トークン)。1 つの呼び出しは、内容のブロックごとの複数の行に分かれ、
                 どの行も同じ usage を持つ。空なら usage の無い文の行だけを出す
    extra        result の前に足す行
    before       行を出す前に呼ぶ関数 (Claude が notes/ に書く代わりなど)。主ループのスレッドから呼ばれる
    exit_after   result の後に、この終了コードで終わる
    """

    text: str | None = VALID_TEXT
    calls: tuple[int, ...] = (1000,)
    extra: tuple[dict, ...] = ()
    before: Callable[["FakeClaudeProcess"], None] | None = None
    exit_after: int | None = None

    def rows(self) -> list[dict]:
        rows: list[dict] = [
            INIT_ROW,
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
        ]
        if not self.calls and self.text is not None:
            rows.append(assistant_row([{"type": "text", "text": self.text}], None))
        for index, tokens in enumerate(self.calls):
            rows.append(assistant_row([{"type": "thinking", "thinking": ""}], tokens))
            if index < len(self.calls) - 1:
                tool_id = f"toolu_{index}"
                rows.append(
                    assistant_row(
                        [
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": "Read",
                                "input": {"file_path": "notes/strategy.md"},
                            }
                        ],
                        tokens,
                    )
                )
                rows.append(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": "1\t# 大方針",
                                }
                            ],
                        },
                    }
                )
                rows.append(
                    {
                        "type": "system",
                        "subtype": "permission_denied",
                        "tool_name": "Write",
                    }
                )
            elif self.text is not None:
                rows.append(
                    assistant_row([{"type": "text", "text": self.text}], tokens)
                )
        rows.append(
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
        )
        rows.append({"type": "system", "subtype": "commands_changed", "commands": []})
        rows.extend(self.extra)
        rows.append(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": max(len(self.calls), 1),
                "result": self.text or "",
                # result の usage は呼び出しの合計。主ループの使用量には使わない
                "usage": usage(sum(self.calls) * 3 + 600_000),
            }
        )
        return rows


@dataclass
class Hang:
    """入力に init の行だけを返し、result を返さない (考えている途中)。テストが respond で応答を出せる。"""

    before: Callable[["FakeClaudeProcess"], None] | None = None


@dataclass
class Exit:
    """入力に init の行を返した後、result を返さずに終わる (異常終了・利用枠の上限など)。"""

    code: int = 1
    before: Callable[["FakeClaudeProcess"], None] | None = None


Step = Reply | Hang | Exit


# --- 偽の子プロセス ---


class FakeStdout:
    def __init__(self, items: queue.Queue):
        self._items = items

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            item = self._items.get(timeout=STDOUT_IDLE_LIMIT_S)
        except queue.Empty:
            raise StopIteration from None
        if item is EOF:
            raise StopIteration
        return item


class FakeStdin:
    """標準入力の偽物。1 行届くたびに、子プロセスの on_input を呼ぶ。"""

    def __init__(self, process: "FakeClaudeProcess"):
        self._process = process
        self._buffer = ""
        self.closed = False

    def write(self, text: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if self._process.writes_fail():
            raise BrokenPipeError(32, "Broken pipe")
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._process.on_input(json.loads(line))
        return len(text)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._process.on_stdin_closed()


@dataclass
class ProcessOptions:
    """偽の子プロセスのふるまい。

    exit_on_stdin_close   標準入力を閉じると 0 で終わる
    ignore_sigterm        SIGTERM を無視する
    fail_writes_after     この数の入力を受け取った後の書き込みを BrokenPipeError にする (None なら失敗させない)
    """

    exit_on_stdin_close: bool = True
    ignore_sigterm: bool = False
    fail_writes_after: int | None = None


class FakeClaudeProcess:
    def __init__(
        self, args, kwargs, pid: int, steps: list[Step], options: ProcessOptions
    ):
        self.args = args
        self.kwargs = kwargs
        self.pid = pid
        self.steps = list(steps)
        self.options = options
        self.inputs: list[dict] = []
        self.returncode: int | None = None
        self._exit_code = 0
        self._finished = threading.Event()
        self._items: queue.Queue = queue.Queue()
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._items)
        self.stderr = io.StringIO("")

    def writes_fail(self) -> bool:
        limit = self.options.fail_writes_after
        return limit is not None and len(self.inputs) >= limit

    def emit(self, row: dict) -> None:
        self._items.put(json.dumps(row, ensure_ascii=False) + "\n")

    def respond(self, reply: Reply) -> None:
        """テストのスレッドから応答の行を出す (Hang の後に使う)。"""
        for row in reply.rows():
            self.emit(row)
        if reply.exit_after is not None:
            self.finish(reply.exit_after)

    def on_input(self, message: dict) -> None:
        self.inputs.append(message)
        step = self.steps.pop(0) if self.steps else Hang()
        if step.before is not None:
            step.before(self)
        if isinstance(step, Reply):
            self.respond(step)
        elif isinstance(step, Exit):
            self.emit(INIT_ROW)
            self.finish(step.code)
        else:
            self.emit(INIT_ROW)

    def finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._exit_code = code
        self._items.put(EOF)
        self._finished.set()

    def on_stdin_closed(self) -> None:
        if self.options.exit_on_stdin_close:
            self.finish(0)

    def receive_signal(self, sig: int) -> None:
        if sig == signal.SIGKILL:
            self.finish(-signal.SIGKILL)
        elif sig == signal.SIGTERM and not self.options.ignore_sigterm:
            self.finish(143)

    def poll(self) -> int | None:
        if self._finished.is_set():
            self.returncode = self._exit_code
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._finished.is_set():
            if timeout is not None:
                # 終わらないまま時間がたったものとして扱う (実時間では待たない)
                raise subprocess.TimeoutExpired(self.args, timeout)
            if not self._finished.wait(WAIT_LIMIT_S):
                self.finish(-signal.SIGKILL)
        self.returncode = self._exit_code
        return self.returncode


class FakeClaude:
    """start_process と signal_process_group の偽物。

    scripts      起動ごとの応答の並び (足りなければ、どの入力にも Hang を返す)
    start_error  起動のたびに出す例外 (None なら起動する)
    options      起動ごとの ProcessOptions (足りなければ既定値)
    起動の時点の .claude/skills/ の中身を launch_skills に記録する。
    """

    def __init__(
        self,
        scripts: list[list[Step]] = (),
        start_error: Exception | None = None,
        options: list[ProcessOptions] = (),
    ):
        self.scripts = [list(script) for script in scripts]
        self.start_error = start_error
        self.options = list(options)
        self.processes: list[FakeClaudeProcess] = []
        self.start_attempts = 0
        self.signals: list[tuple[int, int]] = []
        self.launch_skills: list[list[str] | None] = []

    def start(self, args, **kwargs) -> FakeClaudeProcess:
        self.start_attempts += 1
        skills = Path(kwargs["cwd"]) / ".claude" / "skills"
        self.launch_skills.append(
            sorted(path.relative_to(skills).as_posix() for path in skills.rglob("*"))
            if skills.is_dir()
            else None
        )
        if self.start_error is not None:
            raise self.start_error
        number = len(self.processes)
        steps = self.scripts[number] if number < len(self.scripts) else []
        options = (
            self.options[number] if number < len(self.options) else ProcessOptions()
        )
        process = FakeClaudeProcess(args, kwargs, 61000 + number, steps, options)
        self.processes.append(process)
        return process

    def signal_group(self, pgid: int, sig: int) -> None:
        self.signals.append((pgid, sig))
        for process in self.processes:
            if process.pid == pgid:
                process.receive_signal(sig)
                return
        raise ProcessLookupError(pgid)


# --- 操作の実行と監視の部品のフェイク ---


def no_events() -> OrchestratorEvents:
    return OrchestratorEvents(
        case_hits=(),
        monitor_ends=(),
        discarded_action_count=0,
        blocked_buttons=(),
        running=False,
        remaining_action_count=0,
        macros={},
        monitor=None,
    )


class FakeController:
    """apply・take_events・run・set_started を持つフェイク。"""

    def __init__(
        self, events: OrchestratorEvents | None = None, error: Exception | None = None
    ):
        self.events = events if events is not None else no_events()
        self.error = error
        self.applied: list[Decision] = []
        self.take_count = 0

    def apply(self, decision: Decision) -> None:
        self.applied.append(decision)

    def take_events(self) -> OrchestratorEvents:
        self.take_count += 1
        if self.error is not None:
            raise self.error
        return self.events

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def set_started(self) -> None:
        pass


class FakeParser:
    """parse_decision のフェイク。```json を含み INVALID を含まない文なら DECISION、そうでなければ誤りを返す。"""

    def __init__(self):
        self.calls: list[str] = []
        self.on_call: Callable[[], None] | None = None

    def __call__(self, text: str) -> Decision | DecisionError:
        self.calls.append(text)
        if self.on_call is not None:
            self.on_call()
        if "INVALID" in text or "```json" not in text:
            return DecisionError(messages=(f"invalid #{len(self.calls)}",))
        return DECISION


# --- 実行ディレクトリーと主ループ ---


class ManualClock:
    """テストが進める時計 (秒)。"""

    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def interval_record(t: float) -> FrameRecord:
    return FrameRecord(
        t=t,
        kind="interval",
        file=f"frames/interval/{round(t * 1000):09d}.jpg",
        width=640,
        height=360,
    )


def frame_bytes(t: float) -> bytes:
    return f"jpeg-{t:.3f}".encode()


def make_run(tmp_path: Path) -> Path:
    """t=1〜10 の一定間隔のフレームと notes/ の文書がある実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "runs" / "20261116-120000"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "progress").mkdir()
    (root / "notes" / "common").mkdir(parents=True)
    (root / "notes" / "common" / "controls.md").write_text("# 操作\n", encoding="utf-8")
    (root / "notes" / "strategy.md").write_text("# 大方針\n", encoding="utf-8")
    for t in range(1, 11):
        record = interval_record(float(t))
        write_bytes_atomic(root / record.file, frame_bytes(float(t)))
        append_json_line(root / "frames.jsonl", frame_record_row(record))
    return root


@dataclass
class Harness:
    loop: OrchestratorMainLoop
    stop: threading.Event
    reasons: list[str]
    clock: ManualClock
    root: Path
    parser: FakeParser
    controller: FakeController
    thread: threading.Thread | None = field(default=None)

    def start(self, started: bool = True) -> None:
        self.thread = threading.Thread(
            target=self.loop.run, args=(self.stop,), daemon=True
        )
        self.thread.start()
        if started:
            self.loop.set_started()

    def join(self) -> None:
        assert self.thread is not None
        self.thread.join(WAIT_LIMIT_S * 2)
        assert not self.thread.is_alive(), "the main loop did not return"

    def finish(self) -> None:
        self.stop.set()
        self.join()


def make_harness(
    tmp_path: Path,
    fake: FakeClaude,
    *,
    controller: FakeController | None = None,
    settings: OrchestratorMainLoopSettings | None = None,
    initial_instruction: str | None = None,
    environ: dict[str, str] | None = None,
    switch_skills_dir: Path | None = None,
    claude: ClaudeSessionOptions | None = None,
    now: float = START_T,
) -> Harness:
    root = make_run(tmp_path)
    clock = ManualClock(now)
    run_clock = RunClock(monotonic=clock, origin=0.0)
    stop = threading.Event()
    reasons: list[str] = []

    def on_stop(reason: str) -> None:
        reasons.append(reason)
        stop.set()

    parser = FakeParser()
    controller = controller if controller is not None else FakeController()
    parts = OrchestratorMainLoopParts(
        paths=RunPaths(root),
        clock=run_clock,
        claude=claude if claude is not None else ClaudeSessionOptions(),
        parse_decision=parser,
        controller=controller,
        initial_instruction=initial_instruction,
        run_log=RunLog(root / "run.jsonl", run_clock),
        on_stop=on_stop,
        start_process=fake.start,
        signal_process_group=fake.signal_group,
        environ=environ
        if environ is not None
        else {"PATH": "/usr/bin", "HOME": "/home/user"},
        switch_skills_dir=(
            switch_skills_dir
            if switch_skills_dir is not None
            else tmp_path / "no-skills"
        ),
    )
    loop = OrchestratorMainLoop(
        parts,
        settings
        if settings is not None
        else OrchestratorMainLoopSettings(close_grace_s=0.01),
    )
    return Harness(loop, stop, reasons, clock, root, parser, controller)


def wait_until(predicate: Callable[[], bool], limit: float = WAIT_LIMIT_S) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# 偽物が入力を受け取ってから、主ループが送った時刻を覚え終えるまで待つ秒数 (時計を進める前に待つ)
AFTER_SEND_S = 0.1


def advance_after_send(harness: Harness, seconds: float) -> None:
    """入力を送り終えた主ループの時計を seconds 秒進める。"""
    time.sleep(AFTER_SEND_S)
    harness.clock.advance(seconds)


def inputs_of(fake: FakeClaude, number: int) -> list[dict]:
    """number 番目 (0 始まり) の子プロセスが受け取った入力。まだ起動していなければ空。"""
    return fake.processes[number].inputs if len(fake.processes) > number else []


def content_of(message: dict) -> list[dict]:
    return message["message"]["content"]


def text_of(message: dict) -> str:
    return content_of(message)[0]["text"]


def labels_of(message: dict) -> list[str]:
    """画像の前の時刻のラベル。"""
    content = content_of(message)
    return [
        block["text"]
        for block, following in itertools.pairwise(content)
        if block["type"] == "text" and following["type"] == "image"
    ]


def images_of(message: dict) -> list[bytes]:
    return [
        base64.b64decode(block["source"]["data"])
        for block in content_of(message)
        if block["type"] == "image"
    ]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


def sessions_rows(root: Path) -> list[dict]:
    return read_jsonl(root / "orchestrator" / "sessions.jsonl")


def turns_rows(root: Path) -> list[dict]:
    return read_jsonl(root / "orchestrator" / "turns.jsonl")


def end_rows(root: Path) -> list[dict]:
    return [row for row in sessions_rows(root) if row["event"] == "end"]


def write_skill(directory: Path, name: str) -> None:
    skill = directory / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8"
    )


# --- 起動の前 ---


def test_claude_is_not_started_before_set_started(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start(started=False)
    time.sleep(0.2)
    assert fake.start_attempts == 0
    harness.loop.set_started()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()


def test_stop_before_set_started_returns_without_starting_or_recording(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start(started=False)
    time.sleep(0.1)
    harness.finish()
    assert fake.start_attempts == 0
    assert not (harness.root / "orchestrator").exists()
    assert not (harness.root / ".claude").exists()
    assert not (harness.root / "run.jsonl").exists()


# --- 起動の引数・作業ディレクトリー・環境変数 ---


def test_session_is_launched_with_the_session_command_in_the_run_directory(tmp_path):
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "ANTHROPIC_API_KEY": "api-key",
        "ANTHROPIC_AUTH_TOKEN": "auth-token",
        "OPENAI_API_KEY": "openai",
        "TYPESAFE_API_KEY": "typesafe",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/run/user/1000/cc-socks/1.sock",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
    }
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake, environ=environ)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    [process] = fake.processes
    run_dir = harness.root.resolve()
    assert process.args == build_session_command(
        RunPaths(run_dir),
        orchestrator_instructions(),
        find_parent_skill_names(run_dir),
        ClaudeSessionOptions(),
        120_000,
    )
    args = process.args
    assert args[:11] == [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5-5",
        "--effort",
        "medium",
    ]
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in args
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep,Write,Edit,Skill"
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings["crossSessionInbound"] == "refuse"
    assert settings["disableAllHooks"] is True
    assert args[args.index("--append-system-prompt") + 1] == orchestrator_instructions()
    assert process.kwargs["cwd"] == run_dir
    assert process.kwargs["start_new_session"] is True
    assert process.kwargs["stdin"] == subprocess.PIPE
    assert process.kwargs["stdout"] == subprocess.PIPE
    assert process.kwargs["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    }


def test_effort_and_rotation_tokens_reach_the_command(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(
        tmp_path,
        fake,
        claude=ClaudeSessionOptions(effort="high"),
        settings=OrchestratorMainLoopSettings(
            rotate_tokens=980_000, close_grace_s=0.01
        ),
    )
    harness.start()
    assert wait_until(lambda: len(fake.processes) == 1)
    harness.finish()
    args = fake.processes[0].args
    assert args[args.index("--effort") + 1] == "high"
    assert args[-2:] == ["--autocompact", "1000000"]


# --- スキル ---


def test_switch_and_game_skills_are_in_place_before_launch(tmp_path):
    switch_dir = tmp_path / "package-skills"
    write_skill(switch_dir, "switch-select-user")
    write_skill(switch_dir, "record-tips")
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake, switch_skills_dir=switch_dir)
    write_skill(harness.root / "notes" / "skills", "game-dialog")
    write_skill(harness.root / "notes" / "skills", "dialog")
    harness.start()
    assert wait_until(lambda: len(fake.processes) == 1)
    harness.finish()
    assert fake.launch_skills[0] == [
        "game-dialog",
        "game-dialog/SKILL.md",
        "switch-select-user",
        "switch-select-user/SKILL.md",
    ]


def test_claude_skills_directory_exists_before_launch_without_any_skill(tmp_path):
    # 本体の操作のスキルの写す元が無くても、.claude/skills/ を作ってから起動する (後から写すスキルを読ませるため)
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake, switch_skills_dir=tmp_path / "missing")
    harness.start()
    assert wait_until(lambda: len(fake.processes) == 1)
    harness.finish()
    assert fake.launch_skills == [[]]


def test_game_skills_are_copied_after_each_result(tmp_path):
    seen: list[bool] = []
    fake = FakeClaude()
    harness = make_harness(tmp_path, fake)
    copied = harness.root / ".claude" / "skills" / "game-new" / "SKILL.md"

    def write_game_skill(process: FakeClaudeProcess) -> None:
        write_skill(harness.root / "notes" / "skills", "game-new")

    def check_copied(process: FakeClaudeProcess) -> None:
        seen.append(copied.is_file())

    fake.scripts = [[Reply(before=write_game_skill), Hang(before=check_copied)]]
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert seen == [True]


# --- 入力 ---


def test_first_input_has_the_opening_the_turn_text_and_the_latest_frame(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(
        tmp_path, fake, initial_instruction="主人公の名前は自由に決めてよい"
    )
    (harness.root / "notes" / "handoff.md").write_text(
        "# 引き継ぎ\n今の目標: 町に入る\n", encoding="utf-8"
    )
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    message = inputs_of(fake, 0)[0]
    assert message["type"] == "user"
    assert message["parent_tool_use_id"] is None
    assert message["message"]["role"] == "user"
    text = text_of(message)
    assert "主人公の名前は自由に決めてよい" in text
    assert "- common/controls.md" in text
    assert "- handoff.md" in text
    assert "- strategy.md" in text
    assert "今の目標: 町に入る" in text
    assert "t=10.000" in text
    assert text.index("## 人の初期指示") < text.index("## 時刻")
    assert labels_of(message) == ["t=10.000 の画面 (frames/interval/000010000.jpg)"]
    assert images_of(message) == [frame_bytes(10.0)]


def test_first_input_without_initial_instruction_has_no_instruction_section(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    text = text_of(inputs_of(fake, 0)[0])
    assert "## 人の初期指示" not in text
    assert "## notes/ のファイル" in text


def test_next_input_has_up_to_three_frames_since_the_previous_input(tmp_path):
    fake = FakeClaude([[Hang(), Hang()]])
    harness = make_harness(tmp_path, fake, now=5.0)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    assert labels_of(inputs_of(fake, 0)[0]) == [
        "t=5.000 の画面 (frames/interval/000005000.jpg)"
    ]
    advance_after_send(harness, 5.0)
    fake.processes[0].respond(Reply())
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    second = inputs_of(fake, 0)[1]
    assert labels_of(second) == [
        "t=6.000 の画面 (frames/interval/000006000.jpg)",
        "t=8.000 の画面 (frames/interval/000008000.jpg)",
        "t=10.000 の画面 (frames/interval/000010000.jpg)",
    ]
    assert images_of(second) == [frame_bytes(6.0), frame_bytes(8.0), frame_bytes(10.0)]
    # 2 つ目の入力には、セッションの開始の節を付けない
    assert "## notes/ のファイル" not in text_of(second)
    assert "## 時刻" in text_of(second)


def test_without_new_frames_the_latest_frame_is_sent_again(tmp_path):
    fake = FakeClaude([[Reply(), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert labels_of(inputs_of(fake, 0)[1]) == [
        "t=10.000 の画面 (frames/interval/000010000.jpg)"
    ]


def test_events_and_progress_are_in_the_input(tmp_path):
    monitor = MonitorCreate(
        name="dialog",
        cases=(
            MonitorCase(
                condition="画面の下に会話の枠がある",
                actions=(PressAction(buttons=("A",), sticks={}, seconds=None),),
                repeat="every",
            ),
        ),
    )
    events = OrchestratorEvents(
        case_hits=(
            CaseHit(
                monitor="dialog",
                case_index=0,
                condition="画面の下に会話の枠がある",
                count=2,
                actions=(PressAction(buttons=("A",), sticks={}, seconds=None),),
            ),
        ),
        monitor_ends=(),
        discarded_action_count=1,
        blocked_buttons=("HOME",),
        running=True,
        remaining_action_count=2,
        macros={
            "next_message": (PressAction(buttons=("A",), sticks={}, seconds=None),)
        },
        monitor=monitor,
    )
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake, controller=FakeController(events=events))
    write_progress_state(
        harness.root / "progress" / "state.json",
        ProgressState(t=7.0, static_s=12.4, no_novel_s=85.0, window_s=300.0),
    )
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    text = text_of(inputs_of(fake, 0)[0])
    assert "画面の下に会話の枠がある" in text
    assert "2 回当たり" in text
    assert "押せずに外したボタン: HOME" in text
    assert "新しい actions で捨てた前の操作: 1 個" in text
    assert "実行中: はい" in text
    assert "残りの操作: 2 個" in text
    assert '"name": "dialog"' in text
    assert '"next_message"' in text
    assert "計測した時刻: t=7.000" in text
    assert "frames/interval/" in text
    assert harness.controller.take_count == 1


def test_old_progress_is_not_in_the_input(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake, now=20.0)
    write_progress_state(
        harness.root / "progress" / "state.json",
        ProgressState(t=9.0, static_s=12.4, no_novel_s=85.0, window_s=300.0),
    )
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    text = text_of(inputs_of(fake, 0)[0])
    assert NO_PROGRESS_TEXT in text
    assert "計測した時刻" not in text


# --- result を受けての parse_decision と apply ---


def test_result_text_is_parsed_and_applied_and_the_next_input_follows(tmp_path):
    fake = FakeClaude([[Reply(), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert harness.parser.calls == [VALID_TEXT]
    assert harness.controller.applied == [DECISION]
    assert harness.reasons == []


def test_turn_without_assistant_text_does_not_reuse_the_previous_text(tmp_path):
    fake = FakeClaude([[Reply(), Reply(text=None), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 3)
    harness.finish()
    assert harness.parser.calls == [VALID_TEXT, ""]
    assert harness.controller.applied == [DECISION]


def test_invalid_json_is_counted_and_described_in_the_next_input(tmp_path):
    fake = FakeClaude([[Reply(INVALID_TEXT), Reply(), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 3)
    harness.finish()
    assert harness.controller.applied == [DECISION]
    second = text_of(inputs_of(fake, 0)[1])
    assert "- invalid #1" in second
    assert second.index("## 前の JSON の誤り") < second.index("- invalid #1")
    # 誤りの無い JSON の後の入力には、前の誤りを書かない
    assert "invalid #1" not in text_of(inputs_of(fake, 0)[2])
    errors = [
        row
        for row in read_jsonl(harness.root / "run.jsonl")
        if row["event"] == "orchestrator_decision_error"
    ]
    assert len(errors) == 1
    assert errors[0]["session"] == 1
    assert errors[0]["count"] == 1
    assert errors[0]["errors"] == ["invalid #1"]


def test_three_invalid_json_in_a_row_stop_the_run(tmp_path):
    fake = FakeClaude([[Reply(INVALID_TEXT), Reply(INVALID_TEXT), Reply(INVALID_TEXT)]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    harness.join()
    assert harness.reasons == ["orchestrator decision invalid 3 times in a row"]
    assert harness.reasons == [decision_failure_reason(3)]
    assert len(inputs_of(fake, 0)) == 3
    assert harness.controller.applied == []
    assert fake.processes[0].stdin.closed is True
    assert [row["reason"] for row in end_rows(harness.root)] == ["close"]


def test_valid_json_resets_the_invalid_count(tmp_path):
    fake = FakeClaude(
        [
            [
                Reply(INVALID_TEXT),
                Reply(INVALID_TEXT),
                Reply(),
                Reply(INVALID_TEXT),
                Reply(INVALID_TEXT),
                Hang(),
            ]
        ]
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 6)
    harness.finish()
    assert harness.reasons == []


def test_max_decision_errors_comes_from_the_settings(tmp_path):
    fake = FakeClaude([[Reply(INVALID_TEXT), Hang()]])
    harness = make_harness(
        tmp_path,
        fake,
        settings=OrchestratorMainLoopSettings(
            max_decision_errors=1, close_grace_s=0.01
        ),
    )
    harness.start()
    harness.join()
    assert harness.reasons == [decision_failure_reason(1)]
    assert len(inputs_of(fake, 0)) == 1


def test_invalid_count_continues_across_sessions(tmp_path):
    fake = FakeClaude(
        [
            [Reply(INVALID_TEXT, calls=(130_000,)), Reply(HANDOFF_REPLY_TEXT)],
            [Reply(INVALID_TEXT), Reply(INVALID_TEXT), Hang()],
        ]
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    harness.join()
    assert harness.reasons == [decision_failure_reason(3)]
    assert len(fake.processes) == 2
    assert len(inputs_of(fake, 1)) == 2


# --- 使用量と入れ替え ---


def test_session_usage_is_the_latest_assistant_line_not_the_largest(tmp_path):
    fake = FakeClaude([[Reply(calls=(130_000, 110_000)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert "## 時刻" in text_of(inputs_of(fake, 0)[1])


def test_usage_over_the_threshold_sends_the_handoff_request(tmp_path):
    fake = FakeClaude([[Reply(calls=(110_000, 121_000)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert content_of(inputs_of(fake, 0)[1]) == [
        {"type": "text", "text": handoff_request_text()}
    ]


def test_usage_exactly_at_the_threshold_does_not_rotate(tmp_path):
    fake = FakeClaude([[Reply(calls=(120_000,)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert "## 時刻" in text_of(inputs_of(fake, 0)[1])


def test_result_usage_is_not_the_session_usage(tmp_path):
    # result の usage は呼び出しの合計 (Reply では 600,000 を超える) で、しきい値の判定に使わない
    fake = FakeClaude([[Reply(calls=(1000, 2000)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert "## 時刻" in text_of(inputs_of(fake, 0)[1])


def test_rotate_tokens_comes_from_the_settings(tmp_path):
    fake = FakeClaude([[Reply(calls=(5000,)), Hang()]])
    harness = make_harness(
        tmp_path,
        fake,
        settings=OrchestratorMainLoopSettings(rotate_tokens=4000, close_grace_s=0.01),
    )
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert text_of(inputs_of(fake, 0)[1]) == handoff_request_text()


def test_handoff_then_a_new_session_starts_with_the_handoff(tmp_path):
    fake = FakeClaude()
    harness = make_harness(tmp_path, fake, initial_instruction="冒険を始める")

    def write_handoff(process: FakeClaudeProcess) -> None:
        (harness.root / "notes" / "handoff.md").write_text(
            "# 引き継ぎ\n今の目標: 町に入る\n", encoding="utf-8"
        )

    fake.scripts = [
        [
            Reply(calls=(130_000,)),
            Reply(HANDOFF_REPLY_TEXT, calls=(131_000,), before=write_handoff),
        ],
        [Hang()],
    ]
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 1)
    harness.finish()
    first, second = fake.processes
    assert content_of(first.inputs[1]) == [
        {"type": "text", "text": handoff_request_text()}
    ]
    # 引き継ぎの依頼の応答は読まない
    assert harness.parser.calls == [VALID_TEXT]
    assert harness.controller.applied == [DECISION]
    # 引き継ぎの依頼では出来事を取り出さない (次のセッションの最初の入力で取り出す)
    assert harness.controller.take_count == 2
    assert first.stdin.closed is True
    assert fake.signals == []
    opening = text_of(second.inputs[0])
    assert "冒険を始める" in opening
    assert "今の目標: 町に入る" in opening
    assert "- handoff.md" in opening
    assert len(images_of(second.inputs[0])) == 1
    sessions = sessions_rows(harness.root)
    assert [(row["event"], row["session"]) for row in sessions] == [
        ("start", 1),
        ("end", 1),
        ("start", 2),
        ("end", 2),
    ]
    assert sessions[1]["reason"] == "rotate"
    # 終了の行の使用量は、最後の assistant の行 (引き継ぎの依頼の応答) の値
    assert sessions[1]["usage_tokens"] == 131_000
    assert sessions[1]["completed_turns"] == 2
    assert sessions[1]["returncode"] == 0
    assert sessions[3]["reason"] == "close"
    turns = turns_rows(harness.root)
    assert [
        (row["session"], row["turn"], row["kind"], row["outcome"]) for row in turns
    ] == [
        (1, 1, "turn", "applied"),
        (1, 2, "handoff", "handoff"),
        (2, 1, "turn", "stopped"),
    ]
    assert turns[1]["input_text"] == handoff_request_text()
    assert turns[1]["images"] == []
    assert turns[1]["errors"] is None


def test_usage_starts_from_zero_in_a_new_session(tmp_path):
    fake = FakeClaude(
        [
            [Reply(calls=(130_000,)), Reply(HANDOFF_REPLY_TEXT, calls=(131_000,))],
            # usage の無い応答。前のセッションの使用量が残っていれば、次は引き継ぎの依頼になる
            [Reply(calls=()), Hang()],
        ]
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 2)
    harness.finish()
    assert "## 時刻" in text_of(inputs_of(fake, 1)[1])


def test_handoff_that_times_out_still_rotates(tmp_path):
    fake = FakeClaude([[Reply(calls=(130_000,)), Hang()], [Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    advance_after_send(harness, 120.0)
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 1)
    harness.finish()
    assert fake.processes[0].stdin.closed is True
    assert end_rows(harness.root)[0]["reason"] == "rotate"
    handoff = turns_rows(harness.root)[1]
    assert handoff["kind"] == "handoff"
    assert handoff["outcome"] == "timeout"
    assert handoff["t_result"] is None


# --- --turn-timeout・異常終了・失敗の続いた回数 ---


def test_turn_over_the_timeout_ends_the_session_without_handoff(tmp_path):
    fake = FakeClaude([[Reply(), Hang()], [Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    advance_after_send(harness, 119.9)
    time.sleep(0.3)
    assert len(fake.processes) == 1
    advance_after_send(harness, 0.1)
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 1)
    harness.finish()
    first = fake.processes[0]
    assert len(first.inputs) == 2
    assert "## 時刻" in text_of(first.inputs[1])
    assert first.stdin.closed is True
    assert end_rows(harness.root)[0]["reason"] == "timeout"
    timed_out = turns_rows(harness.root)[1]
    assert timed_out["outcome"] == "timeout"
    assert timed_out["t_result"] is None
    assert timed_out["duration_s"] is None
    assert harness.reasons == []


def test_turn_timeout_comes_from_the_settings(tmp_path):
    fake = FakeClaude([[Hang()], [Hang()]])
    harness = make_harness(
        tmp_path,
        fake,
        settings=OrchestratorMainLoopSettings(turn_timeout_s=30.0, close_grace_s=0.01),
    )
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    advance_after_send(harness, 30.0)
    assert wait_until(lambda: len(fake.processes) == 2)
    harness.finish()
    assert end_rows(harness.root)[0]["reason"] == "timeout"


def test_exit_in_the_middle_of_a_turn_starts_a_new_session(tmp_path):
    fake = FakeClaude([[Reply(), Exit(code=1)], [Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 1)
    harness.finish()
    first_end = end_rows(harness.root)[0]
    assert first_end["reason"] == "exit"
    assert first_end["returncode"] == 1
    exited = turns_rows(harness.root)[1]
    assert exited["outcome"] == "exited"
    assert harness.reasons == []


def test_failed_write_to_stdin_ends_the_session_as_exit(tmp_path):
    fake = FakeClaude(
        [[Reply(), Hang()], [Hang()]],
        options=[ProcessOptions(fail_writes_after=1), ProcessOptions()],
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 1)) == 1)
    harness.finish()
    assert len(inputs_of(fake, 0)) == 1
    assert end_rows(harness.root)[0]["reason"] == "exit"
    # 送れなかった入力の行は書かない
    assert [row["turn"] for row in turns_rows(harness.root) if row["session"] == 1] == [
        1
    ]


def test_sessions_ending_before_the_first_turn_three_times_stop_the_run(tmp_path):
    fake = FakeClaude([[Exit(code=1)], [Exit(code=1)], [Exit(code=1)], [Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    harness.join()
    assert MAX_SESSION_FAILURES == 3
    assert harness.reasons == ["orchestrator session failed 3 times in a row"]
    assert harness.reasons == [session_failure_reason(3)]
    assert len(fake.processes) == 3
    assert [row["reason"] for row in end_rows(harness.root)] == ["exit", "exit", "exit"]


def test_failure_to_start_claude_counts_as_a_failed_session(tmp_path):
    fake = FakeClaude(start_error=FileNotFoundError(2, "No such file", "claude"))
    harness = make_harness(tmp_path, fake)
    harness.start()
    harness.join()
    assert harness.reasons == [session_failure_reason(3)]
    assert fake.start_attempts == 3
    sessions = sessions_rows(harness.root)
    assert [row["event"] for row in sessions] == ["end", "end", "end"]
    assert all(row["returncode"] is None for row in sessions)
    assert all("FileNotFoundError" in row["error"] for row in sessions)
    ends = [
        row
        for row in read_jsonl(harness.root / "run.jsonl")
        if row["event"] == "orchestrator_session_end"
    ]
    assert len(ends) == 3


def test_first_turn_timeouts_count_as_failed_sessions(tmp_path):
    fake = FakeClaude([[Hang()], [Hang()], [Hang()], [Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    for number in range(3):
        assert wait_until(lambda n=number: len(inputs_of(fake, n)) == 1)
        advance_after_send(harness, 120.0)
    harness.join()
    assert harness.reasons == [session_failure_reason(3)]
    assert len(fake.processes) == 3


def test_a_session_that_finished_its_first_turn_resets_the_failure_count(tmp_path):
    fake = FakeClaude(
        [
            [Exit(code=1)],
            [Exit(code=1)],
            [Reply(), Exit(code=1)],
            [Exit(code=1)],
            [Exit(code=1)],
            [Hang()],
        ]
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 5)) == 1)
    harness.finish()
    assert harness.reasons == []


# --- 自動の要約・停止 ---


def test_compact_boundary_is_recorded_and_the_session_continues(tmp_path):
    fake = FakeClaude([[Reply(extra=(COMPACT_ROW,)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    assert len(fake.processes) == 1
    compacts = [row for row in sessions_rows(harness.root) if row["event"] == "compact"]
    assert compacts == [
        {
            "t": START_T,
            "event": "compact",
            "session": 1,
            "metadata": {"trigger": "auto", "pre_tokens": 967_123},
        }
    ]


def test_stop_in_the_middle_of_a_turn_ends_the_session_without_waiting(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    [process] = fake.processes
    assert process.stdin.closed is True
    assert harness.controller.applied == []
    assert harness.reasons == []
    assert [row["reason"] for row in end_rows(harness.root)] == ["close"]
    [turn] = turns_rows(harness.root)
    assert turn["outcome"] == "stopped"
    assert turn["t_result"] is None


def test_stop_after_a_result_applies_it_and_sends_no_next_input(tmp_path):
    fake = FakeClaude([[Reply(), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.parser.on_call = harness.stop.set
    harness.start()
    harness.join()
    assert harness.controller.applied == [DECISION]
    assert len(inputs_of(fake, 0)) == 1
    assert [row["reason"] for row in end_rows(harness.root)] == ["close"]


def test_closing_escalates_to_sigterm_and_sigkill(tmp_path):
    fake = FakeClaude(
        [[Hang()]],
        options=[ProcessOptions(exit_on_stdin_close=False, ignore_sigterm=True)],
    )
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 1)
    harness.finish()
    [process] = fake.processes
    assert fake.signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert end_rows(harness.root)[0]["returncode"] == -signal.SIGKILL


def test_unexpected_exception_closes_the_session_and_stops_the_run(tmp_path):
    fake = FakeClaude([[Hang()]])
    harness = make_harness(
        tmp_path, fake, controller=FakeController(error=RuntimeError("broken"))
    )
    harness.start()
    harness.join()
    assert harness.reasons == [main_loop_failure_reason("RuntimeError: broken")]
    assert harness.reasons == ["orchestrator main loop failed: RuntimeError: broken"]
    [process] = fake.processes
    assert process.stdin.closed is True
    assert [row["reason"] for row in end_rows(harness.root)] == ["close"]


# --- 記録 ---


def test_records_of_sessions_turns_and_stream(tmp_path):
    fake = FakeClaude([[Reply(calls=(1000, 2000)), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    sessions = sessions_rows(harness.root)
    assert sessions[0] == {
        "t": START_T,
        "event": "start",
        "session": 1,
        "stream": "orchestrator/session-001/stream.jsonl",
    }
    assert sessions[1] == {
        "t": START_T,
        "event": "end",
        "session": 1,
        "reason": "close",
        "usage_tokens": 2000,
        "returncode": 0,
        "completed_turns": 1,
        "error": None,
    }
    first_input = inputs_of(fake, 0)[0]
    turns = turns_rows(harness.root)
    assert turns[0] == {
        "session": 1,
        "turn": 1,
        "kind": "turn",
        "t_sent": START_T,
        "t_result": START_T,
        "duration_s": 0.0,
        "input_text": text_of(first_input),
        "images": ["frames/interval/000010000.jpg"],
        "response_json": VALID_JSON,
        "errors": None,
        "outcome": "applied",
        "usage_tokens": 2000,
    }
    assert turns[1]["turn"] == 2
    assert turns[1]["outcome"] == "stopped"
    stream = read_jsonl(harness.root / "orchestrator" / "session-001" / "stream.jsonl")
    assert [row["data"] for row in stream] == [
        *Reply(calls=(1000, 2000)).rows(),
        INIT_ROW,
    ]
    assert all(isinstance(row["elapsed_s"], float | int) for row in stream)
    events = [
        (row["event"], row.get("session"))
        for row in read_jsonl(harness.root / "run.jsonl")
    ]
    assert events == [
        ("orchestrator_session_start", 1),
        ("orchestrator_session_end", 1),
    ]


def test_invalid_turn_records_the_errors(tmp_path):
    fake = FakeClaude([[Reply(INVALID_TEXT), Hang()]])
    harness = make_harness(tmp_path, fake)
    harness.start()
    assert wait_until(lambda: len(inputs_of(fake, 0)) == 2)
    harness.finish()
    turn = turns_rows(harness.root)[0]
    assert turn["outcome"] == "invalid"
    assert turn["errors"] == ["invalid #1"]
    assert turn["response_json"] is None


# --- 設定の確かめ ---


@pytest.mark.parametrize(
    "settings",
    [
        OrchestratorMainLoopSettings(rotate_tokens=0),
        OrchestratorMainLoopSettings(rotate_tokens=1_000_000),
        OrchestratorMainLoopSettings(turn_timeout_s=0.0),
        OrchestratorMainLoopSettings(max_decision_errors=0),
        OrchestratorMainLoopSettings(close_grace_s=-1.0),
    ],
)
def test_settings_that_cannot_be_used_are_rejected(tmp_path, settings):
    fake = FakeClaude()
    root = make_run(tmp_path)
    clock = RunClock(monotonic=ManualClock(START_T), origin=0.0)
    parts = OrchestratorMainLoopParts(
        paths=RunPaths(root),
        clock=clock,
        claude=ClaudeSessionOptions(),
        parse_decision=FakeParser(),
        controller=FakeController(),
        initial_instruction=None,
        run_log=RunLog(root / "run.jsonl", clock),
        on_stop=lambda reason: None,
        start_process=fake.start,
        signal_process_group=fake.signal_group,
        environ={"PATH": "/usr/bin"},
    )
    with pytest.raises(ValueError):
        OrchestratorMainLoop(parts, settings)


def test_default_settings():
    settings = OrchestratorMainLoopSettings()
    assert settings.rotate_tokens == 120_000
    assert settings.turn_timeout_s == 120.0
    assert settings.max_decision_errors == 3
    assert settings.close_grace_s == 5.0
