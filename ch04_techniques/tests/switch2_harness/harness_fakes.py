"""switch2_harness のテストで共有する fake (本物の代わりに使うテスト用の部品)。

本物のデーモン・claude・時計に触れずに、コマンドの流れを確かめるために使う。
ファイル名が test_ で始まらないため、pytest はこのファイルをテストとして集めない。
"""

import signal
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from switch2_harness.features import (
    MULTI_FRAME_FEATURE,
    REFLECTION_FEATURE,
    STAGNATION_ALERT_FEATURE,
    Feature,
    FileAppend,
    WritableFile,
)
from switch2_harness.workspace import WorkspacePaths

# テスト用の要素。本物の FEATURES に左右されずに、複数の要素の順やサブコマンドの追加を確かめるため、
# 要素の検証にはこれを available に渡す (FAKE_HISTORY は本物の行動履歴と名前が同じだが、commands などはテスト用の値)
FAKE_HISTORY = Feature(
    name="action-history",
    title="行動履歴",
    instruction_file="action-history.md",
    commands=("history",),
)
FAKE_NOTES = Feature(
    name="memo-files",
    title="メモファイル",
    instruction_file="memo-files.md",
    commands=("note", "notes"),
    writable_files=(WritableFile(name="memo.md", template_file="memo-initial.md"),),
)
FAKE_FEATURES: tuple[Feature, ...] = (FAKE_HISTORY, FAKE_NOTES)

# テスト用の、CLAUDE.md に節を足さない要素 (instruction_file が None。本物の停滞の知らせは節を足す)。
# 名前は本物の停滞の知らせと同じで、run が画面を記録するかと look が知らせを出すかの判定 (どちらも要素の名前で決まる) にも使う。
# FAKE_FEATURES には含めず、使うテストで available に足す
FAKE_STAGNATION_ALERT = Feature(
    name=STAGNATION_ALERT_FEATURE,
    title="停滞の知らせ",
    instruction_file=None,
    commands=(),
)

# テスト用の、メモファイルの要素 (FAKE_NOTES) と併用する要素。CLAUDE.md に節を足さず、memo.md の最初の内容の末尾に
# memo-reflection.md を足す。名前は本物の振り返りと同じで、run が振り返りに入るかと look が知らせを出すかの判定にも使う。
# FAKE_FEATURES には含めず、使うテストで available に足す
FAKE_REFLECTION = Feature(
    name=REFLECTION_FEATURE,
    title="振り返り",
    instruction_file=None,
    commands=(),
    requires=(FAKE_NOTES.name,),
    file_appends=(FileAppend(name="memo.md", template_file="memo-reflection.md"),),
)

# テスト用の、CLAUDE.md に節を足さない複数フレームの要素 (本物の複数フレームは節を足す)。
# 名前は本物の複数フレームと同じで、look が続けて撮るかの判定 (要素の名前で決まる) に使う。
# FAKE_FEATURES には含めず、使うテストで available に足す
FAKE_MULTI_FRAME = Feature(
    name=MULTI_FRAME_FEATURE,
    title="複数フレーム",
    instruction_file=None,
    commands=(),
)

# テスト用のテンプレート (instructions/ の下のファイル名 → 本文)
# memo-initial.md と memo-reflection.md の $goal は、書き換えられるファイルの最初の内容と、その末尾に足す内容が
# 置換されないことを確かめるため
FAKE_TEMPLATES: dict[str, str] = {
    "base.md": "# 基本 $game\n\n目標: $goal\n",
    "action-history.md": "## 行動履歴 ($game)\n",
    "memo-files.md": "## メモ\n\n$goal を目指す\n\n",
    "memo-initial.md": "# メモ $goal\n",
    "memo-reflection.md": "## 振り返り $goal\n",
}


def read_fake_template(file_name: str) -> str:
    """FAKE_TEMPLATES から本文を返す read_template の fake。"""
    return FAKE_TEMPLATES[file_name]


def make_paths(root: Path) -> WorkspacePaths:
    """root を作業ディレクトリーとする WorkspacePaths を直接組み立て、records/ と frames/ を作る。"""
    paths = WorkspacePaths(
        root=root,
        harness_file=root / "harness.json",
        instructions_file=root / "CLAUDE.md",
        records_dir=root / "records",
        frames_dir=root / "frames",
        events_file=root / "records" / "events.jsonl",
        agent_log_file=root / "records" / "agent.jsonl",
        agent_stderr_file=root / "records" / "agent-stderr.log",
    )
    paths.records_dir.mkdir(parents=True, exist_ok=True)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    return paths


class FakeClock:
    """手で進める時計。呼んでも時刻は進まず、advance か fake の送信・待機で進む。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeDaemon:
    """send の fake。送った引数を記録し、デーモンに似た応答を返す。

    応答は overrides に command (引数の 1 つ目) があればその値、無ければ次の既定の値:
        shot PATH → "ok PATH"、up ... → "ok held=none"、それ以外 → "ok <引数を空白でつないだもの>"
    clock を渡すと、送るたびに step_s 秒進める (送信にかかった時間の代わり)。
    送るたびに、受け取った timeout_s を timeouts に記録する。
    """

    def __init__(
        self,
        clock: FakeClock | None = None,
        overrides: Mapping[str, str] | None = None,
        step_s: float = 0.5,
    ) -> None:
        self.clock = clock
        self.overrides = dict(overrides or {})
        self.step_s = step_s
        self.sent: list[list[str]] = []
        self.timeouts: list[float | None] = []

    def __call__(self, args: list[str], *, timeout_s: float | None = None) -> str:
        self.sent.append(list(args))
        self.timeouts.append(timeout_s)
        if self.clock is not None:
            self.clock.advance(self.step_s)
        command = args[0]
        if command in self.overrides:
            return self.overrides[command]
        if command == "shot":
            return f"ok {args[1]}"
        if command == "up":
            return "ok held=none"
        return "ok " + " ".join(args)


# ImageWritingDaemon が書く画面の大きさ (幅, 高さ)。デーモンが元の大きさで保存する画面の代わり
SHOT_IMAGE_SIZE: tuple[int, int] = (1280, 720)


def shot_color(n: int) -> tuple[int, int, int]:
    """ImageWritingDaemon が n 回目 (1 から数える) の shot で書く画像の色 (B, G, R) を返す。

    回ごとに青の値を 30 ずつ変える (256 以上にならないよう 256 で割った余りにする)。例: 1 回目は (30, 100, 200)。
    """
    return ((n * 30) % 256, 100, 200)


def solid_image(
    color: tuple[int, int, int], size: tuple[int, int] = SHOT_IMAGE_SIZE
) -> np.ndarray:
    """全画素が color (B, G, R) の、大きさ size (幅, 高さ) の uint8 の BGR 画像を返す。"""
    width, height = size
    return np.full((height, width, 3), color, dtype=np.uint8)


class ImageWritingDaemon:
    """send の fake。shot PATH を受けると、PATH に単色の画像を書いて "ok PATH" を返す。

    n 回目 (1 から数える。失敗した回も数える) の shot では、solid_image(shot_color(n)) (1280x720) を
    cv2.imwrite で PATH に書く。
    fail_on_shot を渡すと、その回の shot では画像を書かずに "error no frame yet" を返す。
    shot 以外の引数には "ok <引数を空白でつないだもの>" を返す。
    clock を渡すと、FakeDaemon と同じく、送るたびに step_s 秒進める (送信にかかった時間の代わり)。
    送った引数を sent に、受け取った timeout_s を timeouts に記録する。
    """

    def __init__(
        self,
        clock: FakeClock | None = None,
        *,
        step_s: float = 0.5,
        fail_on_shot: int | None = None,
    ) -> None:
        self.clock = clock
        self.step_s = step_s
        self.fail_on_shot = fail_on_shot
        self.shots = 0
        self.sent: list[list[str]] = []
        self.timeouts: list[float | None] = []

    def __call__(self, args: list[str], *, timeout_s: float | None = None) -> str:
        self.sent.append(list(args))
        self.timeouts.append(timeout_s)
        if self.clock is not None:
            self.clock.advance(self.step_s)
        if args[0] != "shot":
            return "ok " + " ".join(args)
        self.shots += 1
        if self.shots == self.fail_on_shot:
            return "error no frame yet"
        if not cv2.imwrite(args[1], solid_image(shot_color(self.shots))):
            raise AssertionError(f"cannot write the fake shot: {args[1]}")
        return f"ok {args[1]}"


class FakeSleep:
    """sleep の fake。時計を進める。

    呼ばれた回数が max_calls を超えたら AssertionError にし、ループの誤りでテストが止まらなくなるのを防ぐ。
    interrupt_at_call を渡すと、その回の呼び出しで KeyboardInterrupt を送出する (Ctrl-C の代わり)。
    """

    def __init__(
        self,
        clock: FakeClock,
        max_calls: int = 10_000,
        interrupt_at_call: int | None = None,
    ) -> None:
        self.clock = clock
        self.max_calls = max_calls
        self.interrupt_at_call = interrupt_at_call
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if len(self.calls) > self.max_calls:
            raise AssertionError("sleep was called too many times")
        if (
            self.interrupt_at_call is not None
            and len(self.calls) == self.interrupt_at_call
        ):
            raise KeyboardInterrupt
        self.clock.advance(seconds)


@dataclass(frozen=True)
class ProcessPlan:
    """fake のプロセスのふるまい。

    runs_for_s: 起動から自分で終わるまでの秒数。None なら自分では終わらない
    stops_on: 受け取ると終わるシグナル (SIGKILL では必ず終わる)
    output_lines: 起動したときに agent.jsonl (stdout_path) に追記する行
    """

    runs_for_s: float | None
    stops_on: frozenset[int] = frozenset({signal.SIGINT})
    output_lines: tuple[str, ...] = ()


# fake のプロセスの番号の始まり。Linux の pid の上限 (PID_MAX_LIMIT = 2^22 = 4194304) より大きくし、
# stop_process が既定の関数で本物の /proc から子孫を探しても、何も見つからない (本物のプロセスを止めない) ようにする
FAKE_PID_BASE = 10_000_000


class FakeAgentProcess:
    """AgentProcess の fake。呼ばれた操作を calls に記録する。

    calls の要素: ("signal", シグナル) (プロセスグループに送ったもの)、("wait", timeout)
    動いている間の wait(timeout) は、時計を timeout 秒進めて subprocess.TimeoutExpired を送出する。
    signal_group は、プロセスが終わっていれば ProcessLookupError を送出する (本物のシグナルは送らない)。
    """

    def __init__(
        self, clock: FakeClock, plan: ProcessPlan, pid: int = FAKE_PID_BASE
    ) -> None:
        self.clock = clock
        self.plan = plan
        self.pid = pid
        self.exit_at = None if plan.runs_for_s is None else clock.now + plan.runs_for_s
        self.returncode: int | None = None
        self.calls: list[tuple[object, ...]] = []

    def poll(self) -> int | None:
        if (
            self.returncode is None
            and self.exit_at is not None
            and self.clock.now >= self.exit_at
        ):
            self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        returncode = self.poll()
        if returncode is not None:
            return returncode
        if timeout is None:
            raise AssertionError("wait() without timeout on a running process")
        self.clock.advance(timeout)
        raise subprocess.TimeoutExpired("claude", timeout)

    def signal_group(self, sig: int) -> None:
        if self.poll() is not None:
            raise ProcessLookupError(f"no process in group {self.pid}")
        self.calls.append(("signal", sig))
        if sig in self.plan.stops_on or sig == signal.SIGKILL:
            self.returncode = -sig

    def exit_now(self, returncode: int = 0) -> None:
        """テストから、プロセスが自分で終わったことにする (calls には記録しない)。"""
        if self.returncode is None:
            self.returncode = returncode


@dataclass(frozen=True)
class LaunchRecord:
    """FakeProcessStarter が受け取った起動の引数。"""

    args: list[str]
    cwd: Path
    env: dict[str, str]
    stdout_path: Path
    stderr_path: Path


@dataclass
class FakeProcessStarter:
    """start_process の fake。起動のたびに plans の次のふるまいのプロセスを作る (使い切ったら最後のものを繰り返す)。"""

    clock: FakeClock
    plans: list[ProcessPlan]
    launches: list[LaunchRecord] = field(default_factory=list)
    processes: list[FakeAgentProcess] = field(default_factory=list)

    def __call__(
        self,
        args: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> FakeAgentProcess:
        plan = self.plans[min(len(self.launches), len(self.plans) - 1)]
        self.launches.append(
            LaunchRecord(list(args), cwd, dict(env), stdout_path, stderr_path)
        )
        with open(stdout_path, "a", encoding="utf-8") as out:
            out.writelines(line + "\n" for line in plan.output_lines)
        process = FakeAgentProcess(
            self.clock, plan, pid=FAKE_PID_BASE + len(self.processes)
        )
        self.processes.append(process)
        return process


@dataclass(frozen=True)
class CriticLaunch:
    """FakeCriticStarter が受け取った、批評役の起動の引数。"""

    args: list[str]
    cwd: Path
    env: dict[str, str]
    input_text: str
    stdout_path: Path
    stderr_path: Path


@dataclass
class FakeCriticStarter:
    """start_critic (reflection_critic.StartCritic) の fake。

    起動のたびに、受け取った引数を launches に記録し、plans の次のふるまい (使い切ったら最後のものを繰り返す) の
    output_lines を stdout_path に書いて (本物と同じく、ファイルを作り直してから書く)、そのふるまいの FakeAgentProcess を返す。
    on_start を渡すと、stdout_path に書いた後、プロセスを作る前に、起動の回の番号 (1 から) を渡して呼ぶ
    (批評役が動いている間に、エージェントがファイルを書き足すことや、時計が進むことの代わり)。
    """

    clock: FakeClock
    plans: list[ProcessPlan]
    on_start: Callable[[int], None] | None = None
    launches: list[CriticLaunch] = field(default_factory=list)
    processes: list[FakeAgentProcess] = field(default_factory=list)

    def __call__(
        self,
        args: list[str],
        cwd: Path,
        env: dict[str, str],
        input_text: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> FakeAgentProcess:
        plan = self.plans[min(len(self.launches), len(self.plans) - 1)]
        self.launches.append(
            CriticLaunch(
                list(args), cwd, dict(env), input_text, stdout_path, stderr_path
            )
        )
        with open(stdout_path, "w", encoding="utf-8") as out:
            out.writelines(line + "\n" for line in plan.output_lines)
        if self.on_start is not None:
            self.on_start(len(self.launches))
        process = FakeAgentProcess(
            self.clock, plan, pid=FAKE_PID_BASE + len(self.processes)
        )
        self.processes.append(process)
        return process
