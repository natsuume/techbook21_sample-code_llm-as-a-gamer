"""LayersApp の Orchestrator 型を、本物の主ループ (OrchestratorMainLoop) と操作の実行と監視 (OrchestratorController)
をつないだまま数秒動かす統合テスト (実時間で待つ)。

偽物にするのは、キャプチャーの読み取り元・レポートの送信先・安全の確かめ・監視の要求の送り先・claude の子プロセス
だけにする。止めるときは、wait の見張りの待ち (LayersApp の sleep) から KeyboardInterrupt を出す (Ctrl+C と同じ)。
"""

import dataclasses
import io
import itertools
import json
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from switch2_remote.controller_state import BUTTON_BITS, NEUTRAL_REPORT

from switch2_layers.app import (
    REASON_INTERRUPTED,
    LayersApp,
    LayersParts,
    OrchestratorLayersParts,
    delegate_loop_failure_reason,
    monitor_loop_failure_reason,
)
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.hardware.capture_source import LatestCapture
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.system_screen_middle import (
    FAKE_DONE_OUTPUT,
    FakeSystemScreenMiddle,
)
from switch2_layers.orchestrator.live_trial import LiveTrial
from switch2_layers.orchestrator.main_loop import (
    decision_failure_reason,
    session_failure_reason,
)
from switch2_layers.orchestrator.monitor_questions import NONE_OPTION
from switch2_layers.orchestrator.orchestrator_instructions import (
    delegate_instructions,
)
from switch2_layers.orchestrator.turn_input import NONE_TEXT
from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.safety_judge import (
    unsafe_screen_at_start_reason,
    unsafe_screen_reason,
)
from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.settings import SAFETY_INTERVAL_MIN_S, RunMode, RunSettings
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_client import JevAnswers, JevError, JevImage
from switch2_layers.surface.surface_questions import SurfaceQuestion

# 止まらないときに、テストを終わらせるために Ctrl+C を出すまでの秒数。正しい実装ではここまで待たない
WAIT_LIMIT_S = 10.0
# 偽の claude の標準出力が、行の来ない間に待つ上限 (秒)。読むスレッドを残さないため
STDOUT_IDLE_LIMIT_S = 30.0
# 実行ディレクトリーの名前に使う時刻 (監視の偽物が orchestrator/monitor.jsonl の場所を知るため、固定にする)
RUN_STARTED_AT = datetime(2026, 11, 17, 12, 0, 0)
RUN_DIRECTORY_NAME = "20261117-120000"
INSTRUCTION = (
    "RPG のソフトをユーザー 1 で起動し、主人公の名前は自由に決めて冒険を始めてください"
)

# 主ループの Claude Code の応答の文 (最後の ```json のブロックを parse_decision が読む)
HOME_REPLY = (
    "HOME メニューを開く。\n\n"
    '```json\n{"actions": [{"type": "press", "buttons": ["HOME"]}]}\n```'
)
MACRO_SET_REPLY = (
    "メニューを開くマクロを登録する。\n\n"
    '```json\n{"macros": {"set": {"open_menu": [{"type": "press", "buttons": ["X"]}]}}}\n```'
)
MACRO_CALL_REPLY = (
    "マクロでメニューを開く。\n\n"
    '```json\n{"actions": [{"type": "macro", "name": "open_menu"}]}\n```'
)
MONITOR_REPLY = (
    "会話の枠を監視する。\n\n"
    '```json\n{"monitor": {"action": "create", "name": "dialog", "cases": '
    '[{"condition": "画面の下に会話の枠がある", "actions": [{"type": "press", "buttons": ["A"]}], '
    '"repeat": "every"}]}}\n```'
)
NO_JSON_REPLY = "JSON のブロックを書き忘れた。"


# --- キャプチャー・送信先・安全の確かめ・監視の偽物 ---


class FakeFrameReader:
    """キャプチャーの偽物。読むたびに、今の time.monotonic を時刻とする新しいフレームを返す。"""

    def __init__(self) -> None:
        self.nv12 = np.full((360 * 3 // 2, 640), 128, np.uint8)

    def read_latest(self) -> LatestCapture | None:
        return LatestCapture(monotonic_time=time.monotonic(), nv12=self.nv12)

    def is_ended(self) -> bool:
        return False


def has_button(report: bytes, button: str) -> bool:
    """8 バイトのレポートで button が押されていれば True。"""
    index, bit = BUTTON_BITS[button]
    return bool(report[index] & bit)


class FakeSender:
    """ブリッジの偽物。送られたレポートを記録する。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reports: list[bytes] = []

    def set_report(self, report: bytes) -> str:
        with self.lock:
            self.reports.append(bytes(report))
        return "ack"

    def pressed(self, button: str) -> bool:
        """button を押したレポートを 1 つでも受け取っていれば True。"""
        with self.lock:
            return any(has_button(report, button) for report in self.reports)


class UnsafeAtStartSafety:
    """SafetyChecker の偽物。オンラインの画面の確率を 0.9 にした答えを返す (実行を始めるときの確かめで止まる)。"""

    def check(self, jpeg: bytes) -> SafetyAnswer:
        answer = FakeSafetyChecker().check(jpeg)
        return dataclasses.replace(
            answer, probabilities={**answer.probabilities, "online": 0.9}
        )


class AnsweringMonitor:
    """監視の要求の送り先 (orchestrator.monitor_loop.MonitorAsker) の偽物。

    どの条件にも当たらない答えを返し、受け取った要求を数える。before_answer があれば、答えを返す前に呼ぶ。
    """

    def __init__(self, before_answer: Callable[[], None] | None = None) -> None:
        self.before_answer = before_answer
        self.lock = threading.Lock()
        self.count = 0

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        with self.lock:
            self.count += 1
        if self.before_answer is not None:
            self.before_answer()
        [question] = questions
        probabilities = {
            choice.name: 1.0 if choice.name == NONE_OPTION else 0.0
            for choice in question.choices
        }
        return JevAnswers(choices={question.name: probabilities}, nouls={}, boxes={})


# --- claude の子プロセスの偽物 ---


def reply_lines(text: str) -> list[str]:
    """1 つの入力への応答の stream-json の行 (init・最後の assistant の文・result)。

    使用量は小さくし、セッションを入れ替えない。
    """
    rows = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 1000},
            },
        },
        {"type": "result", "subtype": "success", "is_error": False, "result": text},
    ]
    return [json.dumps(row, ensure_ascii=False) + "\n" for row in rows]


class FakeStdout:
    def __init__(self, lines: queue.Queue) -> None:
        self._lines = lines

    def __iter__(self):
        return self

    def __next__(self) -> str:
        try:
            line = self._lines.get(timeout=STDOUT_IDLE_LIMIT_S)
        except queue.Empty:
            raise StopIteration from None
        if line is None:
            raise StopIteration
        return line


class FakeStdin:
    """標準入力の偽物。1 行届くたびに、子プロセスの on_input を呼ぶ。閉じると子プロセスが終わる。"""

    def __init__(self, process: "FakeClaudeProcess") -> None:
        self._process = process
        self._buffer = ""
        self.closed = False

    def write(self, text: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._process.on_input(json.loads(line))
        return len(text)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._process.finish()


class FakeClaudeProcess:
    """claude の子プロセスの偽物。

    入力 1 つごとに、台本 replies の応答を前から 1 つずつ返す。台本が尽きたら、標準入力が閉じられるまで何も返さない。
    標準入力を閉じるか、シグナルを受けると、終了コード 0 で終わる (標準出力も終わる)。
    """

    def __init__(self, args: list[str], pid: int, replies: list[str]) -> None:
        self.args = args
        self.pid = pid
        self.replies = list(replies)
        self.inputs: list[dict] = []
        self.returncode: int | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._finished = threading.Event()
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self._lines)
        self.stderr = io.StringIO("")

    def on_input(self, message: dict) -> None:
        self.inputs.append(message)
        if self.replies:
            for line in reply_lines(self.replies.pop(0)):
                self._lines.put(line)

    def finish(self) -> None:
        if not self._finished.is_set():
            self._finished.set()
            self._lines.put(None)

    def poll(self) -> int | None:
        if self._finished.is_set():
            self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._finished.is_set():
            if timeout is not None:
                # 終わらないまま時間がたったものとして扱う (実時間では待たない)
                raise subprocess.TimeoutExpired("claude", timeout)
            self._finished.wait(WAIT_LIMIT_S)
            self.finish()
        self.returncode = 0
        return 0


class FakeClaude:
    """start_process と signal_process_group の偽物。

    replies       最初の子プロセスが入力ごとに返す応答の文 (2 つ目以降の子プロセスは何も返さない)
    start_error   起動のたびに出す例外 (None なら起動する)
    """

    def __init__(
        self, replies: Sequence[str] = (), start_error: Exception | None = None
    ) -> None:
        self.replies = list(replies)
        self.start_error = start_error
        self.lock = threading.Lock()
        self.processes: list[FakeClaudeProcess] = []
        self.start_attempts = 0

    def start(self, args: list[str], **kwargs: object) -> FakeClaudeProcess:
        with self.lock:
            self.start_attempts += 1
            if self.start_error is not None:
                raise self.start_error
            replies = [] if self.processes else self.replies
            process = FakeClaudeProcess(
                list(args), 61000 + len(self.processes), replies
            )
            self.processes.append(process)
            return process

    def signal_group(self, pgid: int, sig: int) -> None:
        with self.lock:
            processes = list(self.processes)
        for process in processes:
            if process.pid == pgid:
                process.finish()
                return
        raise ProcessLookupError(pgid)

    def first_inputs(self) -> list[dict]:
        """最初の子プロセスが受け取った入力 (まだ起動していなければ空)。"""
        with self.lock:
            return list(self.processes[0].inputs) if self.processes else []


def input_text(message: dict) -> str:
    """claude に送った入力のテキストのブロック。"""
    return message["message"]["content"][0]["text"]


# 画像の前の時刻のラベルの、元の frames/interval/ のファイル
LABEL_FILE_PATTERN = re.compile(r"\((frames/interval/\d{9}\.jpg)\)$")


def labeled_image_files(message: dict) -> list[str]:
    """claude に送った入力の、画像の前の時刻のラベルに書いた frames/interval/ のファイル (古い順)。"""
    content = message["message"]["content"]
    files: list[str] = []
    for block, following in itertools.pairwise(content):
        if block["type"] == "text" and following["type"] == "image":
            match = LABEL_FILE_PATTERN.search(block["text"])
            files.append(match.group(1) if match else block["text"])
    return files


# --- 組み立てと実行 ---


def orchestrator_settings(tmp_path: Path, **changes: object) -> RunSettings:
    """Orchestrator 型の起動引数。安全の監視の間隔を範囲の下限にして、実行を始めるときの確かめを早く通す。

    Orchestrator 型の 6 つの値は、既定と違う値にする (run.jsonl と claude の起動の引数に届くことを確かめるため)。
    """
    base = RunSettings(
        mode=RunMode.ORCHESTRATOR,
        runs_dir=tmp_path / "runs",
        interval_width=160,
        safety_interval_s=SAFETY_INTERVAL_MIN_S,
        orchestrator_effort="high",
        rotate_tokens=90_000,
        turn_timeout_s=30.0,
        max_decision_errors=2,
        monitor_threshold=0.6,
        max_monitor_errors=4,
    )
    return dataclasses.replace(base, **changes)


def orchestrator_parts(
    tmp_path: Path,
    sender: FakeSender,
    claude: FakeClaude,
    *,
    monitor: AnsweringMonitor | None = None,
    safety: object | None = None,
    initial_instruction: str | None = None,
) -> OrchestratorLayersParts:
    """Orchestrator 型の部品。本体の操作のスキルの写す元は、tmp_path の空のディレクトリーにする。"""
    switch_skills_dir = tmp_path / "switch-skills"
    switch_skills_dir.mkdir(exist_ok=True)
    return OrchestratorLayersParts(
        frame_reader=FakeFrameReader(),
        report_sender=sender,
        bridge_state=lambda: "ok",
        safety=safety if safety is not None else FakeSafetyChecker(),
        monitor=monitor if monitor is not None else AnsweringMonitor(),
        initial_instruction=initial_instruction,
        local_now=lambda: RUN_STARTED_AT,
        start_process=claude.start,
        signal_process_group=claude.signal_group,
        environ={"PATH": "/usr/bin", "HOME": str(tmp_path)},
        switch_skills_dir=switch_skills_dir,
    )


class InterruptWhen:
    """LayersApp の sleep (wait の見張りの待ち) の代わり。

    condition() が真になるか、作ってから WAIT_LIMIT_S 秒たったら、KeyboardInterrupt を 1 回だけ出す (wait の待ちの
    途中で Ctrl+C が押されたときと同じ)。それ以外は seconds 秒待つ。
    """

    def __init__(self, condition: Callable[[], bool]) -> None:
        self.condition = condition
        self.deadline = time.monotonic() + WAIT_LIMIT_S
        self.interrupted = False

    def __call__(self, seconds: float) -> None:
        if not self.interrupted and (
            self.condition() or time.monotonic() >= self.deadline
        ):
            self.interrupted = True
            raise KeyboardInterrupt
        time.sleep(seconds)


def run_orchestrator(
    settings: RunSettings,
    parts: OrchestratorLayersParts,
    stop_when: Callable[[], bool] = lambda: False,
) -> tuple[str, Path]:
    """LayersApp を run し、stop_when() が真になったら Ctrl+C 相当で止め、(止まった理由, 実行ディレクトリー) を返す。

    stop_when を渡さなければ、止まる条件で止まるまで (WAIT_LIMIT_S 秒まで) 待つ。
    """
    app = LayersApp(settings, parts, sleep=InterruptWhen(stop_when))
    reason = app.run()
    assert app.paths is not None
    return reason, app.paths.root


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_events(root: Path) -> list[str]:
    return [row["event"] for row in read_rows(root / "run.jsonl")]


def assert_stopped_without_phases(root: Path, reason: str) -> None:
    """run.jsonl の最後が stop {reason} で、段階 (phase・clean_up_end) の行が無いことを確かめる。"""
    rows = read_rows(root / "run.jsonl")
    assert rows[-1]["event"] == "stop"
    assert rows[-1]["reason"] == reason
    events = [row["event"] for row in rows]
    assert "phase" not in events
    assert "clean_up_end" not in events


def orchestrator_operations(root: Path) -> list[dict]:
    """operations.jsonl の、主ループか監視の操作の行。"""
    return [
        row
        for row in read_rows(root / "operations.jsonl")
        if row["source"] in {"orchestrator", "monitor"}
    ]


# --- 動かす ---


def test_orchestrator_runs_the_main_loop_and_the_controller_without_the_three_layers(
    tmp_path,
):
    sender = FakeSender()
    claude = FakeClaude([HOME_REPLY])
    parts = orchestrator_parts(
        tmp_path, sender, claude, initial_instruction=INSTRUCTION
    )
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        parts,
        stop_when=lambda: sender.pressed("HOME"),
    )
    assert reason == REASON_INTERRUPTED
    assert root == tmp_path / "runs" / RUN_DIRECTORY_NAME

    # run.jsonl: start の args に mode と Orchestrator 型の値を書き、段階には入らない
    start = read_rows(root / "run.jsonl")[0]
    assert start["event"] == "start"
    assert start["args"]["mode"] == "orchestrator"
    assert start["args"]["orchestrator_effort"] == "high"
    assert start["args"]["rotate_tokens"] == 90_000
    assert start["args"]["turn_timeout_s"] == 30.0
    assert start["args"]["max_decision_errors"] == 2
    assert start["args"]["monitor_threshold"] == 0.6
    assert start["args"]["max_monitor_errors"] == 4
    assert_stopped_without_phases(root, REASON_INTERRUPTED)
    assert "orchestrator_session_start" in run_events(root)

    # notes/ は空のディレクトリーで始め (write_initial_notes の初期ファイルは無い)、surface・middle・deep は動かない
    assert (root / "notes").is_dir()
    assert list((root / "notes").iterdir()) == []
    assert not (root / "surface.jsonl").exists()
    assert not (root / "middle" / "state.json").exists()
    assert not (root / "middle" / "calls.jsonl").exists()
    assert not (root / "middle" / "system_calls.jsonl").exists()
    assert list((root / "deep").iterdir()) == []

    # 初期指示は orchestrator/instruction.md に写し、claude への最初の入力に入れる
    assert (root / "orchestrator" / "instruction.md").read_text(
        encoding="utf-8"
    ) == INSTRUCTION
    first = input_text(claude.first_inputs()[0])
    assert f"## 人の初期指示\n{INSTRUCTION}" in first
    # --orchestrator-effort は claude の起動の引数に届く
    claude_args = claude.processes[0].args
    assert claude_args[claude_args.index("--effort") + 1] == "high"

    # 1 ターン目の HOME は、17 個のボタンで作る PadOutput から送る (surface の関門で外さない)
    assert sender.pressed("HOME")
    assert any(
        row["source"] == "orchestrator" and row["held"] == ["HOME"]
        for row in read_rows(root / "operations.jsonl")
    )
    # 止めるときは、片付けをせずにすぐ操作を中立にする
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_macro_registered_in_one_turn_is_called_in_the_next_turn(tmp_path):
    sender = FakeSender()
    claude = FakeClaude([MACRO_SET_REPLY, MACRO_CALL_REPLY])
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude),
        stop_when=lambda: sender.pressed("X"),
    )
    assert reason == REASON_INTERRUPTED
    # 2 ターン目の JSON は、1 ターン目に登録したマクロを呼べる (JSON の誤りにならない)
    assert "orchestrator_decision_error" not in run_events(root)
    assert len(claude.first_inputs()) >= 2
    # マクロの操作は、呼んだ 2 ターン目に 1 回だけ送る (登録した 1 ターン目には送らない)
    assert sender.pressed("X")
    presses = [row for row in orchestrator_operations(root) if row["held"] == ["X"]]
    assert len(presses) == 1
    assert presses[0]["source"] == "orchestrator"


def test_latest_frames_are_replaced_and_sent_with_the_input(tmp_path):
    sender = FakeSender()
    claude = FakeClaude()
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude),
        stop_when=lambda: len(claude.first_inputs()) >= 1,
    )
    assert reason == REASON_INTERRUPTED
    # 一定間隔のフレームを保存するたびに、frames/latest/1.jpg〜 を置き換える (5 枚に満たなければ、ある分だけ)
    latest_dir = root / "frames" / "latest"
    names = (
        sorted(path.name for path in latest_dir.iterdir())
        if latest_dir.is_dir()
        else []
    )
    interval_rows = [
        row for row in read_rows(root / "frames.jsonl") if row["kind"] == "interval"
    ]
    assert interval_rows
    assert names == [f"{n}.jpg" for n in range(1, min(len(interval_rows), 5) + 1)]
    # 幅は frames/interval/ と同じ (--interval-width)
    for name in names:
        image = cv2.imread(str(latest_dir / name))
        assert image is not None
        assert image.shape[1] == 160
    # 入力には、その時点の最新のフレーム (最大 5 枚) を、元の frames/interval/ のファイルのラベル付きで添える
    files = labeled_image_files(claude.first_inputs()[0])
    assert 1 <= len(files) <= 5
    assert all((root / file).is_file() for file in files)
    # turns.jsonl の images は、添えた画像の元の frames/interval/ のファイル
    turn = read_rows(root / "orchestrator" / "turns.jsonl")[0]
    assert turn["images"] == files


def test_without_initial_instruction_the_main_loop_is_told_there_is_none(tmp_path):
    sender = FakeSender()
    claude = FakeClaude()
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude),
        stop_when=lambda: len(claude.first_inputs()) >= 1,
    )
    assert reason == REASON_INTERRUPTED
    assert not (root / "orchestrator" / "instruction.md").exists()
    first = input_text(claude.first_inputs()[0])
    assert f"## 人の初期指示\n{NONE_TEXT}\n## notes/ のファイル" in first


# --- 止まる理由 ---


def test_unsafe_screen_at_start_stops_before_the_main_loop_and_the_controller(
    tmp_path,
):
    sender = FakeSender()
    claude = FakeClaude([HOME_REPLY])
    parts = orchestrator_parts(
        tmp_path,
        sender,
        claude,
        safety=UnsafeAtStartSafety(),
        initial_instruction=INSTRUCTION,
    )
    reason, root = run_orchestrator(orchestrator_settings(tmp_path), parts)
    assert reason == unsafe_screen_at_start_reason("online")
    assert_stopped_without_phases(root, reason)
    # claude は起動せず、操作も送らない (送ったのは、止めるときの中立のレポートだけ)
    assert claude.start_attempts == 0
    assert not (root / "orchestrator" / "sessions.jsonl").exists()
    assert orchestrator_operations(root) == []
    assert sender.reports
    assert all(report == NEUTRAL_REPORT for report in sender.reports)


def test_invalid_json_up_to_the_limit_stops_the_run(tmp_path):
    sender = FakeSender()
    claude = FakeClaude([NO_JSON_REPLY])
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path, max_decision_errors=1),
        orchestrator_parts(tmp_path, sender, claude),
    )
    assert reason == decision_failure_reason(1)
    assert reason == "orchestrator decision invalid 1 times in a row"
    assert_stopped_without_phases(root, reason)
    assert "orchestrator_decision_error" in run_events(root)
    assert orchestrator_operations(root) == []


def test_claude_that_cannot_start_stops_the_run_as_failed_sessions(tmp_path):
    sender = FakeSender()
    claude = FakeClaude(start_error=FileNotFoundError(2, "No such file", "claude"))
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude),
    )
    assert reason == session_failure_reason(3)
    assert reason == "orchestrator session failed 3 times in a row"
    assert_stopped_without_phases(root, reason)
    assert claude.start_attempts == 3


def test_exception_in_the_monitor_loop_stops_the_run(tmp_path):
    # 監視の要求の答えを返す前に、monitor.jsonl の場所にディレクトリーを作り、monitor.jsonl への追記を失敗させる
    monitor_log = (
        tmp_path / "runs" / RUN_DIRECTORY_NAME / "orchestrator" / "monitor.jsonl"
    )
    monitor = AnsweringMonitor(
        before_answer=lambda: monitor_log.mkdir(parents=True, exist_ok=True)
    )
    sender = FakeSender()
    claude = FakeClaude([MONITOR_REPLY])
    reason, root = run_orchestrator(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=monitor),
    )
    assert reason.startswith("monitor loop failed: ")
    assert "IsADirectoryError" in reason
    assert_stopped_without_phases(root, reason)
    assert monitor.count >= 1
    # 止めるときは、すぐ操作を中立にする
    assert sender.reports[-1] == NEUTRAL_REPORT


class InterruptTwice:
    """LayersApp の sleep の代わり。uv run の下の Ctrl+C のように、1 回の割り込みで SIGINT が 2 回届くときを真似る。

    condition() が真になるか、作ってから WAIT_LIMIT_S 秒たった後の最初の待ちと、その次の待ちで、KeyboardInterrupt を
    1 回ずつ出す。それ以外は seconds 秒待つ。
    """

    def __init__(self, condition: Callable[[], bool]) -> None:
        self.condition = condition
        self.deadline = time.monotonic() + WAIT_LIMIT_S
        self.raised = 0

    def __call__(self, seconds: float) -> None:
        if self.raised == 1 or (
            self.raised == 0 and (self.condition() or time.monotonic() >= self.deadline)
        ):
            self.raised += 1
            raise KeyboardInterrupt
        time.sleep(seconds)


def test_interrupt_delivered_twice_is_absorbed_before_closing(tmp_path):
    # 片付けをせずに止まった後も、wait が 2 回目の SIGINT を捨ててから戻り、close を最後まで行う
    sender = FakeSender()
    claude = FakeClaude([HOME_REPLY])
    sleep = InterruptTwice(lambda: sender.pressed("HOME"))
    app = LayersApp(
        orchestrator_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude),
        sleep=sleep,
    )
    reason = app.run()
    assert reason == REASON_INTERRUPTED
    assert sleep.raised == 2
    assert app.paths is not None
    assert_stopped_without_phases(app.paths.root, REASON_INTERRUPTED)
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_monitor_loop_failure_reason():
    assert (
        monitor_loop_failure_reason("RuntimeError: broken")
        == "monitor loop failed: RuntimeError: broken"
    )


# --- run の型と部品の型 ---


def advisor_parts() -> LayersParts:
    return LayersParts(
        frame_reader=FakeFrameReader(),
        report_sender=FakeSender(),
        bridge_state=lambda: "ok",
        surface=FakeSurfaceDecider(),
        middle=FakeMiddleAnalyzer(),
        deep=FakeDeepRunner(),
        safety=FakeSafetyChecker(),
        system_middle=FakeSystemScreenMiddle(exhausted_output=FAKE_DONE_OUTPUT),
    )


def test_orchestrator_mode_with_layers_parts_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        LayersApp(orchestrator_settings(tmp_path), advisor_parts())
    assert not (tmp_path / "runs").exists()


def test_advisor_mode_with_orchestrator_parts_is_an_error(tmp_path):
    parts = orchestrator_parts(tmp_path, FakeSender(), FakeClaude())
    with pytest.raises(ValueError):
        LayersApp(RunSettings(runs_dir=tmp_path / "runs"), parts)
    assert not (tmp_path / "runs").exists()


# --- 委任型 (--mode delegate) ---

# 委任の行動のマクロを登録し、委任を始める応答 (actions が無いので、すぐに委任を始める)
DELEGATE_START_REPLY = (
    "X を押す行動を登録し、リアルタイムの場面の操作を委任する。\n\n"
    '```json\n{"macros": {"set": {"press_x": [{"type": "press", "buttons": ["X"]}]}}, '
    '"delegate": {"action": "start", "tasks": ["最重要: HP を 0 にせずに敵をすべて倒す"], '
    '"cautions": [], "choices": [{"name": "press_x", "description": "X を押して攻撃する"}]}}\n```'
)
# 委任中に、主ループが操作を書く応答 (委任中なので捨てられる)
DELEGATE_ACTIONS_REPLY = (
    "Y を押したいが、委任中。\n\n"
    '```json\n{"actions": [{"type": "press", "buttons": ["Y"]}]}\n```'
)
# 何も変えない応答
WAIT_REPLY = "委任の様子を見る。\n\n```json\n{}\n```"
# 台本が尽きた後に、最後の応答を返すまでの秒数 (委任中は、主ループのターンが続けて来るため)
REPEAT_DELAY_S = 0.05


class RepeatingClaudeProcess(FakeClaudeProcess):
    """台本が尽きたら、最後の応答を REPEAT_DELAY_S 秒待ってから返し続ける claude の子プロセスの偽物。"""

    def __init__(self, args: list[str], pid: int, replies: list[str]) -> None:
        super().__init__(args, pid, replies)
        self.last_reply = replies[-1] if replies else None

    def on_input(self, message: dict) -> None:
        if not self.replies and self.last_reply is not None:
            time.sleep(REPEAT_DELAY_S)
            self.replies.append(self.last_reply)
        super().on_input(message)


class RepeatingClaude(FakeClaude):
    """FakeClaude と同じく台本を返し、台本が尽きた後も最後の応答を返し続ける start_process の偽物。"""

    def start(self, args: list[str], **kwargs: object) -> FakeClaudeProcess:
        with self.lock:
            self.start_attempts += 1
            replies = [] if self.processes else self.replies
            process = RepeatingClaudeProcess(
                list(args), 61000 + len(self.processes), replies
            )
            self.processes.append(process)
            return process


class ChoosingJev:
    """委任の要求の送り先 (orchestrator.monitor_loop.MonitorAsker の形) の偽物。

    要求ごとに、picks の名前の選択肢を前から 1 つずつ選んだ答えを返す (尽きたら最後の名前を選び続ける)。受け取った
    要求を数え、受け取った画像を残す。before_answer があれば、答えを返す前に呼ぶ。
    """

    def __init__(
        self, picks: Sequence[str], before_answer: Callable[[], None] | None = None
    ) -> None:
        self.picks = list(picks)
        self.before_answer = before_answer
        self.lock = threading.Lock()
        self.count = 0
        self.images: list[JevImage] = []

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        with self.lock:
            self.count += 1
            self.images.extend(images)
            name = self.picks[min(self.count, len(self.picks)) - 1]
        if self.before_answer is not None:
            self.before_answer()
        [question] = questions
        probabilities = {
            choice.name: 1.0 if choice.name == name else 0.0
            for choice in question.choices
        }
        return JevAnswers(choices={question.name: probabilities}, nouls={}, boxes={})


class FailingJev:
    """委任の要求の送り先の偽物。要求のたびに JevError を出し、受け取った要求を数える。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.count = 0

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        with self.lock:
            self.count += 1
        raise JevError("HTTP 500 from the fake")


class UnsafeWhen:
    """SafetyChecker の偽物。condition() が真になったら、オンラインの画面の確率を 0.9 にした答えを返す。"""

    def __init__(self, condition: Callable[[], bool]) -> None:
        self.condition = condition

    def check(self, jpeg: bytes) -> SafetyAnswer:
        answer = FakeSafetyChecker().check(jpeg)
        if not self.condition():
            return answer
        return dataclasses.replace(
            answer, probabilities={**answer.probabilities, "online": 0.9}
        )


def delegate_settings(tmp_path: Path, **changes: object) -> RunSettings:
    """委任型の起動引数。委任型の 2 つの値は、既定と違う値にする (run.jsonl に届くことを確かめるため)。

    changes の値は、委任型の 2 つの値を含めて、ほかの値より優先する。
    """
    values: dict[str, object] = {
        "mode": RunMode.DELEGATE,
        "delegate_image_width": 640,
        "max_delegate_errors": 4,
    }
    values.update(changes)
    return orchestrator_settings(tmp_path, **values)


def any_input_contains(claude: FakeClaude, text: str) -> bool:
    return any(text in input_text(message) for message in claude.first_inputs())


def test_delegate_runs_the_main_loop_and_the_delegate_loop_without_the_monitor(
    tmp_path,
):
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    jev = ChoosingJev(["press_x"])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=jev),
        stop_when=lambda: sender.pressed("X"),
    )
    assert reason == REASON_INTERRUPTED
    # run.jsonl: start の args に mode と委任型の 2 つの値を書き、委任の始まりを書く
    start = read_rows(root / "run.jsonl")[0]
    assert start["args"]["mode"] == "delegate"
    assert start["args"]["delegate_image_width"] == 640
    assert start["args"]["max_delegate_errors"] == 4
    assert "delegate_start" in run_events(root)
    assert_stopped_without_phases(root, REASON_INTERRUPTED)
    # Jev が選んだ行動のマクロの操作は、source が delegate
    assert any(
        row["source"] == "delegate" and row["held"] == ["X"]
        for row in read_rows(root / "operations.jsonl")
    )
    delegate_rows = read_rows(root / "orchestrator" / "delegate.jsonl")
    assert delegate_rows
    assert delegate_rows[0]["handling"] == "started"
    # 監視は使わない
    assert not (root / "orchestrator" / "monitor.jsonl").exists()


def test_delegate_claude_is_launched_with_the_delegate_instructions(tmp_path):
    sender = FakeSender()
    claude = FakeClaude()
    reason, _ = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=ChoosingJev(["none"])),
        stop_when=lambda: len(claude.first_inputs()) >= 1,
    )
    assert reason == REASON_INTERRUPTED
    claude_args = claude.processes[0].args
    assert (
        claude_args[claude_args.index("--append-system-prompt") + 1]
        == delegate_instructions()
    )


def test_delegate_image_width_of_the_settings_reaches_the_jev_images(tmp_path):
    # --delegate-image-width の値で、Jev に送る画像を作る (キャプチャーの偽物のフレームは幅 640)
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    jev = ChoosingJev(["none"])
    reason, _ = run_orchestrator(
        delegate_settings(tmp_path, delegate_image_width=498),
        orchestrator_parts(tmp_path, sender, claude, monitor=jev),
        stop_when=lambda: jev.count >= 1,
    )
    assert reason == REASON_INTERRUPTED
    assert jev.images
    widths = {
        cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR).shape[1]
        for image in jev.images
    }
    assert widths == {498}


def test_delegate_max_errors_of_the_settings_return_control_to_the_main_loop(
    tmp_path,
):
    # --max-delegate-errors 回続けて Jev が失敗したら、操作権を主ループに返す (実行は続ける)
    run_log = tmp_path / "runs" / RUN_DIRECTORY_NAME / "run.jsonl"

    def delegate_ended() -> bool:
        return any(row["event"] == "delegate_end" for row in read_rows(run_log))

    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path, max_delegate_errors=2),
        orchestrator_parts(tmp_path, sender, claude, monitor=FailingJev()),
        stop_when=delegate_ended,
    )
    assert reason == REASON_INTERRUPTED
    ends = [
        row for row in read_rows(root / "run.jsonl") if row["event"] == "delegate_end"
    ]
    assert [row["reason"] for row in ends] == ["errors"]
    assert len(read_rows(root / "orchestrator" / "delegate.jsonl")) == 2


def test_delegate_main_loop_presses_home_like_the_orchestrator(tmp_path):
    # 主ループが押せるボタンは、監視型と同じ 17 個 (HOME を含む)
    sender = FakeSender()
    claude = FakeClaude([HOME_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=ChoosingJev(["none"])),
        stop_when=lambda: sender.pressed("HOME"),
    )
    assert reason == REASON_INTERRUPTED
    assert any(
        row["source"] == "orchestrator" and row["held"] == ["HOME"]
        for row in read_rows(root / "operations.jsonl")
    )


def test_delegate_interrupt_while_delegating_records_the_end_as_stop(tmp_path):
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=ChoosingJev(["press_x"])),
        stop_when=lambda: sender.pressed("X"),
    )
    assert reason == REASON_INTERRUPTED
    ends = [
        row for row in read_rows(root / "run.jsonl") if row["event"] == "delegate_end"
    ]
    assert [row["reason"] for row in ends] == ["stop"]
    # 止めるときは、すぐ操作を中立にする
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_delegate_monitor_in_the_json_is_an_error(tmp_path):
    sender = FakeSender()
    claude = FakeClaude([MONITOR_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path, max_decision_errors=1),
        orchestrator_parts(tmp_path, sender, claude, monitor=ChoosingJev(["none"])),
    )
    assert reason == decision_failure_reason(1)
    [error_row] = [
        row
        for row in read_rows(root / "run.jsonl")
        if row["event"] == "orchestrator_decision_error"
    ]
    assert any(message.startswith("monitor: ") for message in error_row["errors"])


def test_delegate_return_by_jev_is_told_in_the_next_input(tmp_path):
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(
            tmp_path, sender, claude, monitor=ChoosingJev(["return_control"])
        ),
        stop_when=lambda: any_input_contains(claude, "(jev_return)"),
    )
    assert reason == REASON_INTERRUPTED
    assert any_input_contains(claude, "(jev_return)")
    ends = [
        row for row in read_rows(root / "run.jsonl") if row["event"] == "delegate_end"
    ]
    assert [row["reason"] for row in ends] == ["jev_return"]


def test_delegate_return_by_jev_tells_the_chosen_counts_in_the_next_input(tmp_path):
    # Jev は 2 回の要求で「操作を返す」を続けて選ぶ。次の入力の出来事と run.jsonl に、その委任で選んだ回数が載る
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(
            tmp_path, sender, claude, monitor=ChoosingJev(["return_control"])
        ),
        stop_when=lambda: any_input_contains(claude, "(jev_return)"),
    )
    assert reason == REASON_INTERRUPTED
    assert any_input_contains(
        claude,
        "(jev_return): jev chose return_control 2 times in a row\n"
        "この委任で Jev が選んだ回数 (要求 2 回、失敗 0 回): "
        "none 0、press_x 0、return_control 2\n",
    )
    [end] = [
        row for row in read_rows(root / "run.jsonl") if row["event"] == "delegate_end"
    ]
    assert end["chosen_counts"] == {"none": 0, "press_x": 0, "return_control": 2}
    assert end["failures"] == 0


def test_delegate_actions_while_delegating_are_discarded_and_told(tmp_path):
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, DELEGATE_ACTIONS_REPLY, WAIT_REPLY])
    reason, _ = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=ChoosingJev(["none"])),
        stop_when=lambda: any_input_contains(claude, "委任中のため、操作を捨てた"),
    )
    assert reason == REASON_INTERRUPTED
    assert any_input_contains(claude, "委任中のため、操作を捨てた: 1 個")
    assert not sender.pressed("Y")


def test_delegate_loop_failure_stops_the_run(tmp_path):
    # 委任の要求の答えを返す前に、delegate.jsonl の場所にディレクトリーを作り、delegate.jsonl への追記を失敗させる
    delegate_log = (
        tmp_path / "runs" / RUN_DIRECTORY_NAME / "orchestrator" / "delegate.jsonl"
    )
    jev = ChoosingJev(
        ["none"], before_answer=lambda: delegate_log.mkdir(parents=True, exist_ok=True)
    )
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(tmp_path, sender, claude, monitor=jev),
    )
    assert reason.startswith("delegate loop failed: ")
    assert "IsADirectoryError" in reason
    assert_stopped_without_phases(root, reason)
    assert jev.count >= 1
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_delegate_unsafe_screen_while_delegating_stops_the_run(tmp_path):
    # 委任中も安全の監視は動き、止める画面なら実行全体を止める
    sender = FakeSender()
    claude = RepeatingClaude([DELEGATE_START_REPLY, WAIT_REPLY])
    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        orchestrator_parts(
            tmp_path,
            sender,
            claude,
            monitor=ChoosingJev(["press_x", "none"]),
            safety=UnsafeWhen(lambda: sender.pressed("X")),
        ),
    )
    assert reason == unsafe_screen_reason("online")
    assert_stopped_without_phases(root, reason)
    ends = [
        row for row in read_rows(root / "run.jsonl") if row["event"] == "delegate_end"
    ]
    assert [row["reason"] for row in ends] == ["stop"]


def test_delegate_loop_failure_reason():
    assert (
        delegate_loop_failure_reason("RuntimeError: broken")
        == "delegate loop failed: RuntimeError: broken"
    )


def test_delegate_mode_with_layers_parts_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        LayersApp(delegate_settings(tmp_path), advisor_parts())
    assert not (tmp_path / "runs").exists()


def test_live_trial_observes_applied_updates_and_real_jev_input_and_stops(tmp_path):
    live = LiveTrial()
    sent = []
    update = 'タスクを変更する。\n```json\n{"delegate":{"action":"update","tasks":["前に進む"]}}\n```'
    claude = RepeatingClaude([DELEGATE_START_REPLY, update, WAIT_REPLY])
    jev = ChoosingJev(["press_x"], before_answer=lambda: sent.append(live.snapshot()))
    parts = orchestrator_parts(tmp_path, FakeSender(), claude, monitor=jev)

    def stop_after_decision():
        state = live.snapshot()
        if (
            state.decision is not None
            and state.decision.chosen == "press_x"
            and "+ タスク　前に進む" in state.changes
        ):
            live.stop_requested.set()
        # run_orchestrator のタイムアウトだけを保険に使う
        return False

    reason, root = run_orchestrator(
        delegate_settings(tmp_path),
        dataclasses.replace(parts, live=live),
        stop_after_decision,
    )
    assert live.stop_requested.is_set()
    assert reason == REASON_INTERRUPTED
    state = live.snapshot()
    assert not state.delegated
    assert not state.main_thinking
    assert any(line.startswith("- タスク　") for line in state.changes)
    assert state.message
    assert all(s.jev_thinking and s.delegated for s in sent)
    assert [jpeg for s in sent for _, jpeg in s.images] == [
        image.jpeg for image in jev.images
    ]
    assert_stopped_without_phases(root, REASON_INTERRUPTED)


def test_extract_live_explanation_splits_only_at_fenced_json_line():
    from switch2_layers.orchestrator.main_loop import _extract_live_explanation

    inline = '次の ```json ブロックで委任します。\n```json\n{"actions":[]}\n```'
    assert _extract_live_explanation(inline) == "次の ```json ブロックで委任します。"
    assert _extract_live_explanation("画面を確認します。") == "画面を確認します。"
    assert _extract_live_explanation('```json\n{"actions":[]}\n```') == ""


def test_live_window_request_close_and_refresh_survive_errors():
    import queue

    from switch2_layers.orchestrator.live_trial import LiveSnapshot
    from switch2_layers.orchestrator.live_window import LiveWindow

    class FakeWidget:
        def __init__(self, width=400, height=300):
            self._width = width
            self._height = height
            self.configured = []
            self.children = []
            self.coords_calls = []

        def configure(self, **kwargs):
            self.configured.append(kwargs)

        def winfo_width(self):
            return self._width

        def winfo_height(self):
            return self._height

        def winfo_children(self):
            return list(self.children)

        def coords(self, *args):
            self.coords_calls.append(args)

        def itemconfigure(self, *args, **kwargs):
            self.configured.append(kwargs)

    class FakeRoot:
        def __init__(self):
            self.quit_called = 0
            self.after_calls = []

        def quit(self):
            self.quit_called += 1

        def after(self, ms, callback):
            self.after_calls.append((ms, callback))

    window = object.__new__(LiveWindow)
    window.root = FakeRoot()
    window.count_game_overs = True
    window.trial = LiveTrial()
    window.worker = None
    window.finished = queue.SimpleQueue()
    window.ended = None
    window.closing = False
    window.close_requested = threading.Event()
    window.previous = None
    window.video_size = (0, 0)
    window.thumb_width = 0
    window.metrics = FakeWidget()
    window.stop_button = FakeWidget()
    window.start_button = FakeWidget()
    window.input = FakeWidget()
    window.status = FakeWidget()
    window.video = FakeWidget()
    window.video_item = 1
    window.video_hint = 2
    window.images = FakeWidget()
    window.image_heading = FakeWidget()

    # 壊れた JPEG でも draw_images が落ちないこと
    window.draw_images(LiveSnapshot(images=((1.0, b"invalid-jpeg"),), request_t=1.0))

    # 描画中に例外が起きても refresh のタイマーが止まらないこと
    window.draw_agents = lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))
    window.refresh()
    assert len(window.root.after_calls) == 1

    # request_close は直接 quit せず、次の refresh で安全に終了すること
    window.request_close()
    assert window.root.quit_called == 0
    window.refresh()
    assert window.closing
    assert window.root.quit_called == 1
    assert len(window.root.after_calls) == 1
