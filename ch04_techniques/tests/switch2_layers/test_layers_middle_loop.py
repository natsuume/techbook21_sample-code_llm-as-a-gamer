import dataclasses
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from switch2_layers.deep.deep_types import DeepRequestResult, DeepTrigger, DocumentKind
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.luna_middle import CALL_DETAIL_KEYS, LunaMiddleAnalyzer
from switch2_layers.middle.luna_request import build_user_sections
from switch2_layers.middle.middle_loop import (
    MiddleLoop,
    MiddleLoopParts,
    MiddleLoopSettings,
    MiddleRequestLog,
    middle_failure_reason,
    run_middle_once,
    run_middle_step,
)
from switch2_layers.middle.middle_trigger import MiddleCallReason, MiddleTrigger
from switch2_layers.middle.middle_types import (
    MapObservation,
    MiddleInput,
    MiddleOutput,
    ObservationKind,
)
from switch2_layers.notes.map_document import MapConnection, TilePosition
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.tasks.task_board import TaskBoard
from switch2_layers.tasks.task_queue import NewTask, TaskDestination

ORIGIN = 1000.0


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeDeepSink:
    def __init__(self):
        self.requests: list[tuple[DocumentKind, DeepTrigger, str | None]] = []
        # この種類の要求では、この例外を出す (deep のジョブのディレクトリーの用意の失敗など)
        self.fail_for: dict[DocumentKind, Exception] = {}
        # 要求を記録した後に呼ぶ関数 (要求を処理する間に全体が止められた場合を作る)
        self.on_request: Callable[[], None] | None = None

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        self.requests.append((kind, trigger, scene_id))
        if self.on_request is not None:
            self.on_request()
        if kind in self.fail_for:
            raise self.fail_for[kind]
        return DeepRequestResult(started=True)


class ScriptedAnalyzer:
    """MiddleAnalyzer の偽物。results を順に返す (例外なら出す)。最後の 1 つは繰り返す。

    analyze の間に偽の時計を elapsed 秒進め、受け取った入力を記録する。
    """

    def __init__(
        self,
        monotonic: FakeMonotonic,
        *results: MiddleOutput | Exception,
        elapsed: float = 0.5,
    ):
        self.monotonic = monotonic
        self.results = list(results)
        self.elapsed = elapsed
        self.inputs: list[MiddleInput] = []

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        self.inputs.append(middle_input)
        self.monotonic.now += self.elapsed
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


# 最初の実行と、きっかけの無い回の理由
INTERVAL: tuple[MiddleCallReason, ...] = ("interval",)
# 足すタスク (行き先なし)
TASKS = (NewTask(text="宿屋に入っている"),)


def output(
    scene_name: str | None = "町",
    wait: float = 0.4,
    new_tasks: tuple[NewTask, ...] = (),
    cancel_task_ids: tuple[str, ...] = (),
) -> MiddleOutput:
    return MiddleOutput(
        scene_name=scene_name,
        is_stuck=False,
        stuck_description="",
        surface_wait_s=wait,
        timeline="## 直近",
        new_tasks=new_tasks,
        cancel_task_ids=cancel_task_ids,
    )


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    (root / "notes" / "common").mkdir(parents=True)
    (root / "middle").mkdir()
    (root / "deep").mkdir()
    (root / "notes" / "scene_labels.json").write_text(
        '{"scenes": []}', encoding="utf-8"
    )
    return root


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def step(
    paths: RunPaths,
    analyzer: object,
    clock: RunClock,
    window_s: float,
    deep_requests: FakeDeepSink | None,
    stop: threading.Event | None = None,
    board: TaskBoard | None = None,
    reasons: tuple[MiddleCallReason, ...] = INTERVAL,
    trigger_notify: Callable[[MiddleCallReason], None] | None = None,
    request_log: MiddleRequestLog | None = None,
):
    """run_middle_step を呼ぶ。board が None なら空のキューの部品を渡す。"""
    return run_middle_step(
        paths,
        analyzer,
        clock,
        window_s,
        deep_requests,
        TaskBoard(paths) if board is None else board,
        reasons,
        trigger_notify,
        stop,
        request_log=request_log,
    )


@dataclass
class LoopHarness:
    root: Path
    monotonic: FakeMonotonic
    loop: MiddleLoop
    deep_sink: FakeDeepSink
    board: TaskBoard
    trigger: MiddleTrigger
    stops: list[str] = field(default_factory=list)


def make_loop(
    tmp_path: Path,
    *results: MiddleOutput | Exception,
    max_errors: int = 5,
    on_press_home: Callable[[], None] | None = None,
) -> LoopHarness:
    """MiddleLoop と偽の部品を組み立てる (テストの本体から呼ぶ)。時計は t = 10.0。"""
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    deep_sink = FakeDeepSink()
    stops: list[str] = []
    board = TaskBoard(paths)
    trigger = MiddleTrigger(5.0, 30.0, clock, lambda t: None)
    parts = MiddleLoopParts(
        paths=paths,
        analyzer=ScriptedAnalyzer(monotonic, *results),
        clock=clock,
        run_log=RunLog(paths.run_log, clock),
        deep_requests=deep_sink,
        on_stop=stops.append,
        board=board,
        trigger=trigger,
        on_press_home=on_press_home,
    )
    loop = MiddleLoop(
        parts, MiddleLoopSettings(interval_s=5.0, window_s=10.0, max_errors=max_errors)
    )
    return LoopHarness(root, monotonic, loop, deep_sink, board, trigger, stops)


# --- 1 回分の処理 ---


def test_step_writes_outputs_and_calls_row(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = ScriptedAnalyzer(monotonic, output(), elapsed=0.5)
    sink = FakeDeepSink()
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, sink
    )
    assert result.ok is True
    assert result.error is None
    assert result.state is not None
    assert result.state.based_on_t == 10.0
    assert result.state.generated_t == 10.5
    assert analyzer.inputs[0].t == 10.0
    state = json.loads((root / "middle" / "state.json").read_text(encoding="utf-8"))
    assert state["scene_id"] == "scene-001"
    rows = read_rows(root / "middle" / "calls.jsonl")
    assert len(rows) == 1
    assert rows[0]["t_sent"] == 10.0
    assert rows[0]["t_received"] == 10.5
    assert rows[0]["scene_id"] == "scene-001"
    assert rows[0]["error"] is None


def test_new_scene_requests_controls_tips_and_game_deep(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        sink,
    )
    assert result.new_scene is not None
    assert result.deep_requests == (
        DocumentKind.CONTROLS,
        DocumentKind.TIPS,
        DocumentKind.GAME,
    )
    assert sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.GAME, DeepTrigger.NEW_SCENE, "scene-001"),
    ]
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["new_scene"] is True
    assert row["deep_requests"] == ["controls", "tips", "game"]
    assert row["deep_requests_sent"] is True


def test_new_scene_is_requested_again_after_failed_write(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(monotonic, output("町", new_tasks=TASKS))
    # キューの部品の場面のファイルの書き込みを失敗させる (notes/scenes をファイルにする)
    (root / "notes" / "scenes").write_text("not a directory", encoding="utf-8")
    result = step(paths, analyzer, clock, 10.0, sink)
    assert result.ok is False
    assert result.new_scene is None
    assert sink.requests == []
    assert json.loads(
        (root / "notes" / "scene_labels.json").read_text(encoding="utf-8")
    ) == {"scenes": []}
    # 次の回では、同じ名前が初めての場面として扱われ、deep の要求が出る
    (root / "notes" / "scenes").unlink()
    result = step(paths, analyzer, clock, 10.0, sink)
    assert result.ok is True
    assert result.new_scene is not None
    assert result.new_scene.id == "scene-001"
    assert sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.GAME, DeepTrigger.NEW_SCENE, "scene-001"),
    ]


def test_known_scene_requests_no_deep(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(monotonic, output("町"))
    step(RunPaths(root), analyzer, clock, 10.0, sink)
    sink.requests.clear()
    result = step(RunPaths(root), analyzer, clock, 10.0, sink)
    assert result.new_scene is None
    assert result.deep_requests == ()
    assert sink.requests == []
    row = read_rows(root / "middle" / "calls.jsonl")[-1]
    assert row["new_scene"] is False
    assert row["deep_requests"] == []
    assert row["deep_requests_sent"] is False


def test_without_deep_sink_requests_are_only_recorded(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
    )
    assert result.deep_requests == (
        DocumentKind.CONTROLS,
        DocumentKind.TIPS,
        DocumentKind.GAME,
    )
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["deep_requests"] == ["controls", "tips", "game"]
    assert row["deep_requests_sent"] is False
    assert list((root / "deep").iterdir()) == []


def test_failed_analyze_keeps_previous_output(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(
        monotonic, output("町", new_tasks=TASKS), RuntimeError("luna failed")
    )
    step(RunPaths(root), analyzer, clock, 10.0, sink)
    state_before = (root / "middle" / "state.json").read_bytes()
    tasks_before = (root / "notes" / "scenes" / "scene-001" / "tasks.json").read_bytes()
    sink.requests.clear()
    result = step(RunPaths(root), analyzer, clock, 10.0, sink)
    assert result.ok is False
    assert result.state is None
    assert "luna failed" in (result.error or "")
    assert (root / "middle" / "state.json").read_bytes() == state_before
    assert (
        root / "notes" / "scenes" / "scene-001" / "tasks.json"
    ).read_bytes() == tasks_before
    assert sink.requests == []
    row = read_rows(root / "middle" / "calls.jsonl")[-1]
    assert "luna failed" in row["error"]
    assert row["scene_id"] is None


def append_rows(root: Path, name: str, rows: list[dict]) -> None:
    with (root / name).open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")


def read_state(root: Path) -> dict:
    return json.loads((root / "middle" / "state.json").read_text(encoding="utf-8"))


def file_size(root: Path, name: str) -> int:
    path = root / name
    return path.stat().st_size if path.exists() else 0


def test_same_time_row_appended_after_read_is_in_next_input(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    append_rows(root, "operations.jsonl", [{"t": 13.0, "source": "apply"}])
    step(paths, analyzer, clock, 10.0, None)
    assert [row["source"] for row in analyzer.inputs[0].operation_rows] == ["apply"]
    assert read_state(root)["operations_rows_offset"] == file_size(
        root, "operations.jsonl"
    )
    # 同じ t (ミリ秒) の行が、読んだ後に追記される
    append_rows(root, "operations.jsonl", [{"t": 13.0, "source": "neutral"}])
    monotonic.now = ORIGIN + 15.0
    step(paths, analyzer, clock, 10.0, None)
    assert [row["source"] for row in analyzer.inputs[1].operation_rows] == ["neutral"]


def test_row_with_earlier_time_appended_after_input_is_in_next_input(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    append_rows(root, "surface.jsonl", [{"t_received": 8.0}])
    # 1 回目は t=10.0 に入力を作る。このとき 9.5 の行はまだ書かれていない (surface が送信の ack を待っている)
    step(paths, analyzer, clock, 10.0, None)
    assert [row["t_received"] for row in analyzer.inputs[0].surface_rows] == [8.0]
    assert read_state(root)["based_on_t"] == 10.0
    # 入力を作った時刻 (10.0) より前の t_received を持つ行が、入力を作った後に追記される
    append_rows(
        root,
        "surface.jsonl",
        [{"t_received": 9.5, "scene_changed": True}, {"t_received": 12.0}],
    )
    monotonic.now = ORIGIN + 15.0
    step(paths, analyzer, clock, 10.0, None)
    second = analyzer.inputs[1]
    assert [row["t_received"] for row in second.surface_rows] == [9.5, 12.0]
    assert second.scene_change_reported is True
    assert read_state(root)["surface_rows_offset"] == file_size(root, "surface.jsonl")


def test_partial_line_is_read_after_it_is_completed(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    first_line = json.dumps({"t_received": 8.0}) + "\n"
    (root / "surface.jsonl").write_text(
        first_line + '{"t_received": 9.0', encoding="utf-8"
    )
    step(paths, analyzer, clock, 10.0, None)
    # 書きかけの行は読まず、位置にも含めない
    assert [row["t_received"] for row in analyzer.inputs[0].surface_rows] == [8.0]
    assert read_state(root)["surface_rows_offset"] == len(first_line.encode())
    with (root / "surface.jsonl").open("a", encoding="utf-8") as file:
        file.write("}\n")
    monotonic.now = ORIGIN + 15.0
    step(paths, analyzer, clock, 10.0, None)
    assert [row["t_received"] for row in analyzer.inputs[1].surface_rows] == [9.0]


def test_offset_beyond_file_size_reads_from_start(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    state = {
        "generated_t": 5.5,
        "based_on_t": 5.0,
        "scene_id": None,
        "scene_name": None,
        "is_stuck": False,
        "stuck_description": "",
        "surface_wait_s": 0.2,
        "timeline": "",
        "surface_rows_offset": 100_000,
        "operations_rows_offset": 0,
    }
    (root / "middle" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    # 記録した位置より小さいファイル (作り直した等)
    append_rows(root, "surface.jsonl", [{"t_received": 1.0}, {"t_received": 2.0}])
    step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[0].previous_state is not None
    assert [row["t_received"] for row in analyzer.inputs[0].surface_rows] == [1.0, 2.0]
    assert read_state(root)["surface_rows_offset"] == file_size(root, "surface.jsonl")


def test_offsets_stay_after_call_without_new_rows(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    append_rows(root, "surface.jsonl", [{"t_received": 8.0}])
    step(paths, analyzer, clock, 10.0, None)
    offset = read_state(root)["surface_rows_offset"]
    monotonic.now = ORIGIN + 15.0
    step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[1].surface_rows == ()
    assert read_state(root)["surface_rows_offset"] == offset
    assert read_state(root)["operations_rows_offset"] == 0


def test_rows_of_failed_call_are_included_in_next_input(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    paths = RunPaths(root)
    analyzer = ScriptedAnalyzer(
        monotonic, output("町"), RuntimeError("luna failed"), output("町"), elapsed=0.0
    )
    append_rows(root, "surface.jsonl", [{"t_received": 8.0}])
    append_rows(root, "operations.jsonl", [{"t": 9.0}])
    # 1 回目 (t=10.0) は成功し、読んだ行の末尾の位置を middle/state.json に書く
    assert step(paths, analyzer, clock, 10.0, None).ok is True
    surface_offset = file_size(root, "surface.jsonl")
    operations_offset = file_size(root, "operations.jsonl")
    assert read_state(root)["surface_rows_offset"] == surface_offset
    append_rows(root, "surface.jsonl", [{"t_received": 12.0}])
    append_rows(root, "operations.jsonl", [{"t": 13.0}])
    # 2 回目 (t=15.0) は失敗し、middle/state.json (と位置) は 1 回目のまま
    monotonic.now = ORIGIN + 15.0
    assert step(paths, analyzer, clock, 10.0, None).ok is False
    assert [row["t_received"] for row in analyzer.inputs[1].surface_rows] == [12.0]
    assert read_state(root)["surface_rows_offset"] == surface_offset
    assert read_state(root)["operations_rows_offset"] == operations_offset
    append_rows(root, "surface.jsonl", [{"t_received": 17.0}])
    append_rows(root, "operations.jsonl", [{"t": 18.0}])
    # 3 回目 (t=20.0) の入力には、失敗した 2 回目に渡した行 (12.0・13.0) も含める
    monotonic.now = ORIGIN + 20.0
    assert step(paths, analyzer, clock, 10.0, None).ok is True
    third = analyzer.inputs[2]
    assert third.previous_state is not None
    assert third.previous_state.surface_rows_offset == surface_offset
    assert [row["t_received"] for row in third.surface_rows] == [12.0, 17.0]
    assert [row["t"] for row in third.operation_rows] == [13.0, 18.0]
    # 成功した 3 回目の後は、その後に書かれた行だけになる
    monotonic.now = ORIGIN + 25.0
    step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[3].surface_rows == ()
    assert analyzer.inputs[3].operation_rows == ()


# --- deep の要求の例外 ---


def test_deep_request_error_is_collected_and_other_requests_continue(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    sink.fail_for[DocumentKind.CONTROLS] = OSError("disk full")
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        sink,
    )
    # 要求の例外は middle の失敗にしない
    assert result.ok is True
    assert [kind for kind, _ in result.deep_request_errors] == [DocumentKind.CONTROLS]
    assert "disk full" in result.deep_request_errors[0][1]
    assert sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.GAME, DeepTrigger.NEW_SCENE, "scene-001"),
    ]
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["error"] is None
    assert row["deep_requests_sent"] is True


def test_loop_records_deep_request_error_without_counting_failure(tmp_path):
    harness = make_loop(tmp_path, output("町"), max_errors=1)
    harness.deep_sink.fail_for[DocumentKind.TIPS] = OSError("disk full")
    result = harness.loop.run_once(INTERVAL)
    assert result.ok is True
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    errors = [
        row
        for row in read_rows(harness.root / "run.jsonl")
        if row["event"] == "deep_request_error"
    ]
    assert len(errors) == 1
    assert errors[0]["source"] == "middle"
    assert errors[0]["kind"] == "tips"
    assert errors[0]["trigger"] == "new_scene"
    assert errors[0]["scene_id"] == "scene-001"
    assert "disk full" in errors[0]["error"]


# --- ループの失敗の数え方 ---


def test_consecutive_failures_stop_everything(tmp_path):
    harness = make_loop(tmp_path, RuntimeError("luna failed"), max_errors=5)
    for _ in range(4):
        assert harness.loop.run_once(INTERVAL).ok is False
    assert harness.stops == []
    assert harness.loop.consecutive_errors == 4
    harness.loop.run_once(INTERVAL)
    assert harness.stops == [middle_failure_reason(5)]
    assert middle_failure_reason(5) == "middle failed 5 times in a row"
    run_rows = read_rows(harness.root / "run.jsonl")
    assert [row["event"] for row in run_rows] == ["middle_error"] * 5
    assert [row["count"] for row in run_rows] == [1, 2, 3, 4, 5]
    assert all("luna failed" in row["error"] for row in run_rows)


def test_success_resets_failures(tmp_path):
    error = RuntimeError("luna failed")
    harness = make_loop(tmp_path, error, error, output(), error, max_errors=3)
    harness.loop.run_once(INTERVAL)
    harness.loop.run_once(INTERVAL)
    assert harness.loop.consecutive_errors == 2
    assert harness.loop.run_once(INTERVAL).ok is True
    assert harness.loop.consecutive_errors == 0
    harness.loop.run_once(INTERVAL)
    harness.loop.run_once(INTERVAL)
    assert harness.stops == []
    harness.loop.run_once(INTERVAL)
    assert harness.stops == [middle_failure_reason(3)]


def test_press_home_of_a_successful_output_is_reported(tmp_path):
    pressed: list[bool] = []
    harness = make_loop(
        tmp_path,
        dataclasses.replace(output(), press_home=True),
        on_press_home=lambda: pressed.append(True),
    )
    assert harness.loop.run_once(INTERVAL).ok is True
    assert pressed == [True]
    state = json.loads((harness.root / "middle" / "state.json").read_text("utf-8"))
    assert state["press_home"] is True


def test_press_home_is_not_reported_without_press_home_or_on_failure(tmp_path):
    pressed: list[bool] = []
    harness = make_loop(
        tmp_path,
        output(),
        RuntimeError("luna failed"),
        on_press_home=lambda: pressed.append(True),
    )
    harness.loop.run_once(INTERVAL)
    harness.loop.run_once(INTERVAL)
    assert pressed == []


def test_loop_passes_new_scene_to_deep_scheduler(tmp_path):
    harness = make_loop(tmp_path, output("町"))
    harness.loop.run_once(INTERVAL)
    assert harness.deep_sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.GAME, DeepTrigger.NEW_SCENE, "scene-001"),
    ]


# --- middle-once ---


def write_frames(root: Path, times: list[float]) -> None:
    rows = []
    for t in times:
        file = f"frames/interval/{round(t * 1000):09d}.jpg"
        (root / file).write_bytes(b"jpeg")
        rows.append(
            {"t": t, "kind": "interval", "file": file, "width": 64, "height": 36}
        )
    text = "".join(json.dumps(row) + "\n" for row in rows)
    (root / "frames.jsonl").write_text(text, encoding="utf-8")


def test_middle_once_uses_latest_saved_time_and_does_not_start_deep(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [4.0, 8.0, 12.5])
    monotonic = FakeMonotonic(0.0)
    result = run_middle_once(
        root, ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0), 10.0
    )
    assert result.ok is True
    assert result.state is not None
    assert result.state.based_on_t == 12.5
    assert result.state.generated_t == 12.5
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["deep_requests"] == ["controls", "tips", "game"]
    assert row["deep_requests_sent"] is False
    assert [frame["t"] for frame in row["frames"]] == [4.0, 8.0, 12.5]
    assert list((root / "deep").iterdir()) == []
    # run.jsonl には書かない
    assert not (root / "run.jsonl").exists()


def test_middle_once_without_frames_uses_time_zero(tmp_path):
    root = make_run(tmp_path)
    result = run_middle_once(
        root, ScriptedAnalyzer(FakeMonotonic(0.0), output(None)), 10.0
    )
    assert result.ok is True
    assert result.state is not None
    assert result.state.based_on_t == 0.0
    assert result.state.scene_id is None


def test_middle_once_requires_run_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_middle_once(
            tmp_path / "missing", ScriptedAnalyzer(FakeMonotonic(0.0), output()), 10.0
        )


# --- analyze の間に止められたとき ---


class StoppingAnalyzer(ScriptedAnalyzer):
    """analyze の中で止める合図を立てる MiddleAnalyzer の偽物 (analyze の応答を待つ間に全体が止められた場合)。"""

    def __init__(
        self,
        stop: threading.Event,
        monotonic: FakeMonotonic,
        *results: MiddleOutput | Exception,
    ):
        super().__init__(monotonic, *results)
        self.stop = stop

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        self.stop.set()
        return super().analyze(middle_input)


def test_stop_during_analyze_writes_nothing(tmp_path):
    root = make_run(tmp_path)
    stop = threading.Event()
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = StoppingAnalyzer(stop, monotonic, output("町"))
    sink = FakeDeepSink()
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, sink, stop
    )
    assert result.stopped is True
    assert result.ok is False
    assert result.error is None
    assert len(analyzer.inputs) == 1
    # キューの部品の操作・state.json・scene_labels.json・calls.jsonl を行わず、deep も呼ばない
    assert not (root / "middle" / "state.json").exists()
    assert not (root / "notes" / "scenes").exists()
    assert json.loads(
        (root / "notes" / "scene_labels.json").read_text(encoding="utf-8")
    ) == {"scenes": []}
    assert read_rows(root / "middle" / "calls.jsonl") == []
    assert sink.requests == []


def test_stop_during_failed_analyze_is_not_counted(tmp_path):
    harness = make_loop(tmp_path, output(), max_errors=1)
    stop = threading.Event()
    harness.loop.parts = dataclasses.replace(
        harness.loop.parts,
        analyzer=StoppingAnalyzer(stop, harness.monotonic, RuntimeError("down")),
    )
    result = harness.loop.run_once(INTERVAL, stop)
    assert result.stopped is True
    # 失敗に数えず、middle_error も calls.jsonl の行も書かない
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert read_rows(harness.root / "middle" / "calls.jsonl") == []


def test_loop_run_returns_when_stopped_during_analyze(tmp_path):
    harness = make_loop(tmp_path, output())
    write_frames(harness.root, [9.0])
    stop = threading.Event()
    analyzer = StoppingAnalyzer(stop, harness.monotonic, output())
    harness.loop.parts = dataclasses.replace(harness.loop.parts, analyzer=analyzer)
    # 同じスレッドで呼ぶ。analyze の中で stop が立つので、1 回で戻る
    harness.loop.run(stop)
    assert len(analyzer.inputs) == 1
    assert not (harness.root / "middle" / "state.json").exists()
    assert read_rows(harness.root / "middle" / "calls.jsonl") == []
    assert harness.stops == []


# --- 出力の書き込みと deep の要求の間に止められたとき ---


class StopAfterReplace:
    """os.replace を包み、名前が file_name のファイルを置き換えた直後に止める合図を立てる。

    出力のファイルを書いている途中で全体が止められた場合を作る (出力のファイルは置き換えの書き込みで書く)。
    """

    def __init__(
        self,
        original: Callable[..., None],
        file_name: str,
        stop: threading.Event,
    ):
        self.original = original
        self.file_name = file_name
        self.stop = stop

    def __call__(self, src: object, dst: object, *args: object, **kwargs: object):
        self.original(src, dst, *args, **kwargs)
        if Path(str(dst)).name == self.file_name:
            self.stop.set()


def scene_ids(root: Path) -> list[str]:
    labels = json.loads(
        (root / "notes" / "scene_labels.json").read_text(encoding="utf-8")
    )
    return [scene["id"] for scene in labels["scenes"]]


@pytest.mark.parametrize(
    ("written_file", "state_written", "label_added"),
    [
        ("tasks.json", False, False),
        ("state.json", True, False),
        ("scene_labels.json", True, True),
    ],
)
def test_stop_while_writing_outputs_leaves_the_rest_unwritten(
    tmp_path, monkeypatch, written_file, state_written, label_added
):
    root = make_run(tmp_path)
    stop = threading.Event()
    # 書く順はキューの部品 (場面の tasks.json → tasks.md → tasks.jsonl) → middle/state.json →
    # notes/scene_labels.json → deep の要求 → calls.jsonl
    monkeypatch.setattr(os, "replace", StopAfterReplace(os.replace, written_file, stop))
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町", new_tasks=TASKS)),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        sink,
        stop,
    )
    assert result.stopped is True
    assert result.ok is False
    assert result.error is None
    # 止める合図が立つ前に書いたファイルは残り、後のファイルは書かない
    assert (root / "notes" / "scenes" / "scene-001" / "tasks.md").exists()
    assert (root / "middle" / "state.json").exists() is state_written
    assert scene_ids(root) == (["scene-001"] if label_added else [])
    assert sink.requests == []
    assert read_rows(root / "middle" / "calls.jsonl") == []


def test_stop_during_new_scene_deep_request_skips_the_rest(tmp_path):
    harness = make_loop(tmp_path, output("町"), max_errors=1)
    stop = threading.Event()
    # 最初の種類 (controls) の要求を処理する間に全体が止められ、その要求は例外を出す
    harness.deep_sink.on_request = stop.set
    harness.deep_sink.fail_for[DocumentKind.CONTROLS] = OSError("disk full")
    result = harness.loop.run_once(INTERVAL, stop)
    assert result.stopped is True
    # 残りの種類 (tips と game) は要求せず、calls.jsonl の行も run.jsonl の deep_request_error も書かない
    assert harness.deep_sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001")
    ]
    assert read_rows(harness.root / "middle" / "calls.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []


# --- MiddleAnalyzer の記録 (gpt-6-luna の usage など) を calls.jsonl の行に足す ---

# 骨組みの行のキー (middle_call_record.middle_call_row)
SKELETON_CALL_KEYS = [
    "t_sent",
    "t_received",
    "request",
    "based_on_t",
    "frames",
    "surface_rows",
    "operation_rows",
    "scene_change_reported",
    "scene_id",
    "scene_name",
    "new_scene",
    "surface_wait_s",
    "press_home",
    "deep_requests",
    "deep_requests_sent",
    "error",
    "reasons",
    "new_tasks",
    "destination_fixes",
    "cancel_task_ids",
    "cancel_used",
    "new_tasks_used",
]


class DetailsScriptedAnalyzer(ScriptedAnalyzer):
    """ScriptedAnalyzer に、analyze の 1 回ごとの記録 (take_call_details) を足した偽物。

    analyze の n 回目の記録は {"usage": {"input_tokens": n}, "cost_usd": n / 1000}。
    """

    def __init__(self, monotonic: FakeMonotonic, *results, elapsed: float = 0.5):
        super().__init__(monotonic, *results, elapsed=elapsed)
        self.calls = 0
        self.taken = 0
        self.details: dict[str, object] = {}

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        self.calls += 1
        self.details = {
            "usage": {"input_tokens": self.calls},
            "cost_usd": self.calls / 1000,
        }
        return super().analyze(middle_input)

    def take_call_details(self) -> dict[str, object]:
        self.taken += 1
        details, self.details = self.details, {}
        return details


def test_successful_row_has_analyzer_details_after_skeleton_keys(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = DetailsScriptedAnalyzer(monotonic, output("町"))
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, None
    )
    assert result.ok is True
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert list(row) == SKELETON_CALL_KEYS + ["usage", "cost_usd"]
    assert row["usage"] == {"input_tokens": 1}
    assert row["cost_usd"] == 0.001
    assert row["error"] is None
    assert analyzer.taken == 1


def test_failed_analyze_row_has_analyzer_details(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = DetailsScriptedAnalyzer(monotonic, RuntimeError("luna failed"))
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, None
    )
    assert result.ok is False
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert "luna failed" in row["error"]
    assert row["usage"] == {"input_tokens": 1}
    assert row["cost_usd"] == 0.001
    assert list(row) == SKELETON_CALL_KEYS + ["usage", "cost_usd"]


def test_failed_write_row_has_analyzer_details(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = DetailsScriptedAnalyzer(monotonic, output("町", new_tasks=TASKS))
    # キューの部品の場面のファイルの書き込みを失敗させる (notes/scenes をファイルにする)
    (root / "notes" / "scenes").write_text("not a directory", encoding="utf-8")
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, None
    )
    assert result.ok is False
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["error"] is not None
    # analyze は成功して費用がかかったため、記録を残す
    assert row["usage"] == {"input_tokens": 1}


def test_failed_input_build_does_not_take_stale_details(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = DetailsScriptedAnalyzer(monotonic, output("町"))
    # 前の回の記録が残っている
    analyzer.details = {"usage": {"input_tokens": 999}}
    # surface.jsonl をディレクトリーにして、入力の作成を失敗させる
    (root / "surface.jsonl").mkdir()
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, None
    )
    assert result.ok is False
    assert analyzer.calls == 0
    assert analyzer.taken == 0
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert list(row) == SKELETON_CALL_KEYS


def test_each_row_has_the_details_of_its_own_call(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = DetailsScriptedAnalyzer(
        monotonic, output("町"), RuntimeError("down"), output("町")
    )
    for _ in range(3):
        step(paths, analyzer, clock, 10.0, None)
    rows = read_rows(root / "middle" / "calls.jsonl")
    assert [row["usage"] for row in rows] == [
        {"input_tokens": 1},
        {"input_tokens": 2},
        {"input_tokens": 3},
    ]


def test_fake_analyzer_rows_have_only_skeleton_keys(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
    )
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert list(row) == SKELETON_CALL_KEYS


def test_stop_during_analyze_discards_details(tmp_path):
    root = make_run(tmp_path)
    stop = threading.Event()
    monotonic = FakeMonotonic(ORIGIN + 10.0)

    class StoppingDetailsAnalyzer(DetailsScriptedAnalyzer):
        def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
            result = super().analyze(middle_input)
            stop.set()
            return result

    analyzer = StoppingDetailsAnalyzer(monotonic, output("町"))
    result = step(
        RunPaths(root),
        analyzer,
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        stop,
    )
    assert result.stopped is True
    assert read_rows(root / "middle" / "calls.jsonl") == []


# --- マップの記録 (middle/map_records.jsonl) ---

MAP_CONNECTIONS = (
    MapConnection(
        from_place="自分の部屋 (2 階)",
        to_place="家の 1 階",
        how="右上の階段を下に",
        from_tile=TilePosition(7, 0),
        to_tile=None,
    ),
)
MAP_OBSERVATIONS = (
    MapObservation(
        kind=ObservationKind.EXIT,
        place="家の 1 階",
        description="左下の赤いマット",
        tile=TilePosition(1, 2),
    ),
)
CURRENT_TILE = TilePosition(7, 1)
MAP_ROW_ITEMS = {
    "connections": [
        {
            "from": "自分の部屋 (2 階)",
            "to": "家の 1 階",
            "how": "右上の階段を下に",
            "from_tile": {"x": 7, "y": 0},
            "to_tile": None,
        }
    ],
    "observations": [
        {
            "kind": "exit",
            "place": "家の 1 階",
            "description": "左下の赤いマット",
            "tile": {"x": 1, "y": 2},
        }
    ],
}


def map_output(
    scene_name: str | None = "町",
    place_name: str | None = "家の 1 階",
    current_tile: TilePosition | None = CURRENT_TILE,
) -> MiddleOutput:
    return dataclasses.replace(
        output(scene_name),
        place_name=place_name,
        current_tile=current_tile,
        new_connections=MAP_CONNECTIONS,
        new_observations=MAP_OBSERVATIONS,
    )


def map_rows(root: Path) -> list[dict]:
    return read_rows(root / "middle" / "map_records.jsonl")


def test_step_appends_map_record_with_latest_sent_frame_time(tmp_path):
    root = make_run(tmp_path)
    # t=10.0 の入力で送るのは 9.5 まで (12.0 は範囲の外で、送らない)
    write_frames(root, [4.0, 8.0, 9.5, 12.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = ScriptedAnalyzer(monotonic, map_output())
    result = step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, None
    )
    assert result.ok is True
    assert [frame.t for frame in analyzer.inputs[0].frames] == [4.0, 8.0, 9.5]
    assert map_rows(root) == [
        {
            "t": 9.5,
            "scene_id": "scene-001",
            "place_name": "家の 1 階",
            "current_tile": {"x": 7, "y": 1},
            **MAP_ROW_ITEMS,
        }
    ]


def test_map_record_without_scene_has_null_scene_id(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, map_output(scene_name=None, place_name="")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
    )
    assert result.ok is True
    # place_name が null なら、current_tile も null
    assert map_rows(root) == [
        {
            "t": 9.0,
            "scene_id": None,
            "place_name": None,
            "current_tile": None,
            **MAP_ROW_ITEMS,
        }
    ]


def test_place_and_current_tile_are_given_to_the_next_input(tmp_path):
    # middle/state.json に書いた現在地の場所とマスを、次の入力の前回の出力として読む (経路の計算に使う)
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, map_output(), elapsed=0.0)
    step(paths, analyzer, clock, 10.0, None)
    state = read_state(root)
    assert state["place_name"] == "家の 1 階"
    assert state["current_tile"] == {"x": 7, "y": 1}
    step(paths, analyzer, clock, 10.0, None)
    previous = analyzer.inputs[1].previous_state
    assert previous.place_name == "家の 1 階"
    assert previous.current_tile == CURRENT_TILE


def test_recorded_place_name_is_offered_in_the_next_input(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, map_output(), elapsed=0.0)
    step(paths, analyzer, clock, 10.0, None)
    step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[0].place_names == ()
    assert analyzer.inputs[1].place_names == ("家の 1 階",)


def test_call_without_sent_frames_writes_no_map_record(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, map_output(), elapsed=0.0)
    step(paths, analyzer, clock, 10.0, None)
    assert len(map_rows(root)) == 1
    # t=100.0 の入力では、直近 10 秒にフレームが無く、画像を送らない
    monotonic.now = ORIGIN + 100.0
    result = step(paths, analyzer, clock, 10.0, None)
    assert result.ok is True
    assert analyzer.inputs[1].frames == ()
    assert len(map_rows(root)) == 1


def test_call_with_nothing_new_writes_no_map_record(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    # 2 回目は場所を決められず、新しいつながりも観察も無い。3 回目はマップの項目を持たない出力
    nothing_new = dataclasses.replace(output("町"), place_name="")
    analyzer = ScriptedAnalyzer(
        monotonic, map_output(), nothing_new, output("町"), elapsed=0.0
    )
    for _ in range(3):
        assert step(paths, analyzer, clock, 10.0, None).ok is True
    assert len(map_rows(root)) == 1


def test_failed_analyze_writes_no_map_record(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(
        monotonic, map_output(), RuntimeError("luna failed"), elapsed=0.0
    )
    step(paths, analyzer, clock, 10.0, None)
    assert step(paths, analyzer, clock, 10.0, None).ok is False
    assert len(map_rows(root)) == 1


def test_failed_map_record_append_is_not_a_middle_failure(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    harness = make_loop(tmp_path, map_output(), max_errors=1)
    write_frames(harness.root, [9.0])
    # middle/map_records.jsonl をディレクトリーにして、記録の追記を失敗させる
    (harness.root / "middle" / "map_records.jsonl").mkdir()
    result = harness.loop.run_once(INTERVAL)
    # 失敗に数えず、止めず、ほかの出力は書く
    assert result.ok is True
    assert result.error is None
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert read_state(harness.root)["scene_id"] == "scene-001"
    [row] = read_rows(harness.root / "middle" / "calls.jsonl")
    assert row["error"] is None
    # 警告を書く
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_stop_before_map_record_leaves_it_unwritten(tmp_path, monkeypatch):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, map_output(), elapsed=0.0)
    # 1 回目は止めずに動かし、場面「町」を一覧に足して、記録を 1 行書く
    assert step(paths, analyzer, clock, 10.0, None).ok is True
    assert len(map_rows(root)) == 1
    # 2 回目は既知の場面なので一覧を書かず、middle/state.json を置き換えた直後に止める合図が立つ
    stop = threading.Event()
    monkeypatch.setattr(os, "replace", StopAfterReplace(os.replace, "state.json", stop))
    result = step(paths, analyzer, clock, 10.0, None, stop)
    assert result.stopped is True
    # 止める合図の後は、マップの記録も calls.jsonl の行も書かない
    assert len(map_rows(root)) == 1
    assert len(read_rows(root / "middle" / "calls.jsonl")) == 1


def test_middle_once_appends_map_record(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [4.0, 8.0, 12.5])
    result = run_middle_once(
        root, ScriptedAnalyzer(FakeMonotonic(0.0), map_output(), elapsed=0.0), 10.0
    )
    assert result.ok is True
    assert map_rows(root) == [
        {
            "t": 12.5,
            "scene_id": "scene-001",
            "place_name": "家の 1 階",
            "current_tile": {"x": 7, "y": 1},
            **MAP_ROW_ITEMS,
        }
    ]


# --- タスクのキューと、呼ばれた理由 ---

SCENE = "scene-001"


def write_labels(root: Path, *names: str) -> None:
    """notes/scene_labels.json に、names の場面を scene-001 から順に書く。"""
    scenes = [
        {"id": f"scene-{number:03d}", "name": name, "first_seen_t": 1.0}
        for number, name in enumerate(names, start=1)
    ]
    (root / "notes" / "scene_labels.json").write_text(
        json.dumps({"scenes": scenes}, ensure_ascii=False), encoding="utf-8"
    )


class NotifyRecorder:
    """trigger_notify の偽物。知らされた理由を順に記録する。"""

    def __init__(self) -> None:
        self.reasons: list[MiddleCallReason] = []

    def __call__(self, reason: MiddleCallReason) -> None:
        self.reasons.append(reason)


def write_middle_state(root: Path, scene_id: str | None) -> None:
    """前回の middle/state.json (場面 scene_id、読んだ位置はどれも 0) を書く。"""
    (root / "middle" / "state.json").write_text(
        json.dumps(
            {
                "generated_t": 5.5,
                "based_on_t": 5.0,
                "scene_id": scene_id,
                "scene_name": None if scene_id is None else "町",
                "is_stuck": False,
                "stuck_description": "",
                "surface_wait_s": 0.4,
                "timeline": "",
                "surface_rows_offset": 0,
                "operations_rows_offset": 0,
                "tasks_rows_offset": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_step_gives_reasons_and_queue_to_the_input_and_the_row(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    write_middle_state(root, SCENE)
    board = TaskBoard(paths)
    board.add(SCENE, TASKS, 1.0)
    # 入力を作る時刻 (t = 10.0) のキューの状態 (出力を書くと現在の場面が変わるため、先に求める)
    expected_queue = board.tasks(SCENE, 10.0)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = ScriptedAnalyzer(monotonic, output("町"))
    reasons: tuple[MiddleCallReason, ...] = ("cancel_requested", "stuck")
    step(
        paths,
        analyzer,
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        board=board,
        reasons=reasons,
    )
    assert analyzer.inputs[0].reasons == reasons
    assert analyzer.inputs[0].task_queue == expected_queue
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["reasons"] == ["cancel_requested", "stuck"]


def test_step_row_has_the_task_fields(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    destination = TaskDestination(place="町", tile=None)
    new_tasks = (NewTask(text="宿屋に入っている", destination=destination),)
    board = TaskBoard(paths)
    result = step(
        paths,
        ScriptedAnalyzer(
            monotonic, output("町", new_tasks=new_tasks, cancel_task_ids=("task-9",))
        ),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        board=board,
    )
    assert result.ok is True
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert list(row) == SKELETON_CALL_KEYS
    assert row["reasons"] == ["interval"]
    # マップが無いため、行き先を null にして足す
    assert row["new_tasks"] == [{"text": "宿屋に入っている", "destination": None}]
    assert row["destination_fixes"] == [
        {
            "index": 0,
            "before": {"place": "町", "tile": None},
            "after": None,
            "reason": "no_map",
        }
    ]
    # 理由に取り消しの要求も詰まりも無く、is_stuck も偽なので、取り消しは使わない
    assert row["cancel_task_ids"] == ["task-9"]
    assert row["cancel_used"] is False
    assert row["new_tasks_used"] is True
    assert [task.text for task in board.tasks(SCENE, 11.0)] == ["宿屋に入っている"]


def test_failed_step_row_has_reasons_and_null_task_fields(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    result = step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, RuntimeError("luna failed")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        reasons=("queue_empty", "scene_change"),
    )
    assert result.ok is False
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert list(row) == SKELETON_CALL_KEYS
    assert row["reasons"] == ["queue_empty", "scene_change"]
    for key in [
        "new_tasks",
        "destination_fixes",
        "cancel_task_ids",
        "cancel_used",
        "new_tasks_used",
    ]:
        assert row[key] is None, key


def test_task_events_after_the_previous_call_are_in_the_next_input(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    board = TaskBoard(paths)
    analyzer = ScriptedAnalyzer(
        monotonic,
        output(
            "町",
            new_tasks=(
                NewTask(text="宿屋に入っている"),
                NewTask(text="会話の枠が閉じている"),
            ),
        ),
        output("町"),
        elapsed=0.0,
    )
    step(paths, analyzer, clock, 10.0, None, board=board)
    # 1 回目の後に、surface が先頭のタスクを完了にした
    assert board.complete(SCENE, "task-001", 10.5) is True
    step(paths, analyzer, clock, 10.0, None, board=board)
    # 2 回目の入力には、1 回目の後の出来事 (added の行を除く) だけが入る
    events = analyzer.inputs[1].task_events
    assert [(row["event"], row["id"]) for row in events] == [("done", "task-001")]
    assert read_state(root)["tasks_rows_offset"] == file_size(root, "tasks.jsonl")


def test_step_notifies_queue_empty_when_cancel_empties_the_queue(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = TaskBoard(paths)
    board.add(SCENE, TASKS, 1.0)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    notify = NotifyRecorder()
    result = step(
        paths,
        ScriptedAnalyzer(monotonic, output("町", cancel_task_ids=("task-001",))),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        board=board,
        reasons=("cancel_requested",),
        trigger_notify=notify,
    )
    assert result.ok is True
    assert board.tasks(SCENE, 11.0) == ()
    assert notify.reasons == ["queue_empty"]
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["cancel_used"] is True


def test_failed_run_once_carries_the_reasons_over(tmp_path):
    harness = make_loop(tmp_path, RuntimeError("luna failed"))
    write_frames(harness.root, [9.0])
    result = harness.loop.run_once(("cancel_requested", "interval"))
    assert result.ok is False
    last_response_t = harness.loop.parts.clock.now()
    # 前回の応答から interval_s (5 秒) たった後の待ちは、持ち越した理由で明ける
    harness.monotonic.now += 5.0
    stop = threading.Event()
    assert harness.trigger.wait_next(stop, last_response_t) == ("cancel_requested",)


class TickingMonotonic(FakeMonotonic):
    """読むたびに step 秒進む偽の時計 (待ちのループで時間を進めるため)。"""

    def __init__(self, now: float, step_s: float):
        super().__init__(now)
        self.step_s = step_s

    def __call__(self) -> float:
        self.now += self.step_s
        return self.now


class NotifyingAnalyzer(ScriptedAnalyzer):
    """1 回目の analyze の間に notify で理由を知らせ、2 回目の analyze で止める合図を立てる偽物。"""

    def __init__(self, monotonic: FakeMonotonic, stop: threading.Event):
        super().__init__(monotonic, output("町"), elapsed=0.0)
        self.stop = stop
        self.trigger: MiddleTrigger | None = None

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        if not self.inputs and self.trigger is not None:
            self.trigger.notify("queue_empty")
        else:
            self.stop.set()
        return super().analyze(middle_input)


def test_loop_run_starts_with_interval_and_waits_for_the_trigger(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [9.0])
    monotonic = TickingMonotonic(ORIGIN + 10.0, step_s=0.25)
    clock = RunClock(monotonic, origin=ORIGIN)
    stop = threading.Event()
    analyzer = NotifyingAnalyzer(monotonic, stop)
    # 間隔は長くして、間隔では明けないようにする
    trigger = MiddleTrigger(1000.0, 30.0, clock, lambda t: None)
    analyzer.trigger = trigger
    loop = MiddleLoop(
        MiddleLoopParts(
            paths=paths,
            analyzer=analyzer,
            clock=clock,
            run_log=RunLog(paths.run_log, clock),
            deep_requests=FakeDeepSink(),
            on_stop=lambda reason: None,
            board=TaskBoard(paths),
            trigger=trigger,
        ),
        MiddleLoopSettings(interval_s=1000.0, window_s=10.0, max_errors=5),
    )
    # 最初の実行の前に届いた理由は捨てる
    trigger.notify("scene_change")
    loop.run(stop)
    assert [middle_input.reasons for middle_input in analyzer.inputs] == [
        ("interval",),
        ("queue_empty",),
    ]


def write_saved_queue(root: Path) -> None:
    """保存済みの実行ディレクトリーに、場面 scene-001 (町) のキュー (task-001・task-002) と記録と、場面が scene-001 の
    middle/state.json を書く。"""
    write_labels(root, "町")
    write_middle_state(root, SCENE)
    scene_dir = root / "notes" / "scenes" / SCENE
    scene_dir.mkdir(parents=True)
    items = [
        {
            "id": "task-001",
            "text": "宿屋に入っている",
            "added_t": 1.0,
            "active_s": 5.0,
            "cancel_requested_t": None,
            "destination": None,
        },
        {
            "id": "task-002",
            "text": "会話の枠が閉じている",
            "added_t": 1.0,
            "active_s": 5.0,
            "cancel_requested_t": None,
            "destination": None,
        },
    ]
    (scene_dir / "tasks.json").write_text(
        json.dumps({"t": 6.0, "tasks": items}, ensure_ascii=False), encoding="utf-8"
    )
    (scene_dir / "tasks.md").write_text(
        "- 宿屋に入っている\n- 会話の枠が閉じている\n", encoding="utf-8"
    )
    append_rows(
        root,
        "tasks.jsonl",
        [
            {
                "t": 1.0,
                "event": "added",
                "scene_id": SCENE,
                "id": f"task-{number:03d}",
                "text": text,
                "reason": None,
                "active_s": None,
                "destination": None,
            }
            for number, text in [(1, "宿屋に入っている"), (2, "会話の枠が閉じている")]
        ],
    )


def test_middle_once_uses_the_saved_queue_and_writes_it_back(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [4.0, 8.0, 12.5])
    write_saved_queue(root)
    analyzer = ScriptedAnalyzer(
        FakeMonotonic(0.0),
        output(
            "町",
            new_tasks=(NewTask(text="店の前にいる"),),
            cancel_task_ids=("task-001",),
        ),
        elapsed=0.0,
    )
    result = run_middle_once(root, analyzer, 10.0)
    assert result.ok is True
    # 保存済みのキューを入力に渡す (理由は interval)
    assert [task.id for task in analyzer.inputs[0].task_queue] == [
        "task-001",
        "task-002",
    ]
    assert analyzer.inputs[0].reasons == ("interval",)
    # run と同じ規則: 理由に取り消しの要求も詰まりも無いので取り消さず、足すタスクは次の連番の ID で足す
    saved = json.loads(
        (root / "notes" / "scenes" / SCENE / "tasks.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in saved["tasks"]] == [
        "task-001",
        "task-002",
        "task-003",
    ]
    assert (root / "notes" / "scenes" / SCENE / "tasks.md").read_text(
        encoding="utf-8"
    ) == "- 宿屋に入っている\n- 会話の枠が閉じている\n- 店の前にいる\n"
    last = read_rows(root / "tasks.jsonl")[-1]
    assert (last["event"], last["id"], last["t"]) == ("added", "task-003", 12.5)
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["reasons"] == ["interval"]
    assert row["cancel_used"] is False
    assert row["new_tasks_used"] is True


# --- gpt-6-luna に送った要求の記録 (middle/requests.jsonl) と、calls.jsonl の request ---

# gpt-6-luna の出力 (スキーマのキーの順)
LUNA_OUTPUT = {
    "place_name": "",
    "current_tile": None,
    "new_connections": [],
    "new_observations": [],
    "is_stuck": False,
    "stuck_description": "",
    "press_home": False,
    "surface_wait_s": 1.5,
    "timeline": "## 直近",
    "scene_name": "町",
    "cancel_task_ids": [],
    "new_tasks": [],
}
REQUEST_ROW_KEYS = [
    "request",
    "t_sent",
    "model",
    "reasoning_effort",
    "reasons",
    "sections",
    "frames",
]


def luna_body(
    output: dict | None = None,
    status: str = "completed",
    content: list | None = None,
) -> dict:
    """Responses API の応答の本文。content を渡さなければ output の JSON の output_text 1 つ。"""
    text = json.dumps(LUNA_OUTPUT if output is None else output, ensure_ascii=False)
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "incomplete_details": None
        if status == "completed"
        else {"reason": "max_output_tokens"},
        "model": "gpt-6-luna",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}]
                if content is None
                else content,
            }
        ],
        "usage": None,
    }


def ok_response() -> httpx.Response:
    return httpx.Response(200, json=luna_body())


class LunaHandler:
    """MockTransport の handler。受け取った要求を記録し、response を返す。

    on_request は要求を受け取ったときに呼ぶ関数 (要求を送った後、応答を待つ間に全体が止められた場合を作る)。
    """

    def __init__(self, response: httpx.Response):
        self.response = response
        self.requests: list[httpx.Request] = []
        self.on_request: Callable[[], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request()
        return self.response

    def user_texts(self, index: int) -> list[str]:
        """index 番目の要求の user メッセージの input_text の文 (送った順)。"""
        payload = json.loads(self.requests[index].content)
        return [
            part["text"]
            for part in payload["input"][1]["content"]
            if part["type"] == "input_text"
        ]


class RecordingLunaAnalyzer(LunaMiddleAnalyzer):
    """フェイクの HTTP (MockTransport) で送る LunaMiddleAnalyzer。受け取った入力を記録する。"""

    def __init__(self, handler: LunaHandler, reasoning_effort: str = "none"):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        super().__init__("sk-test", client=client, reasoning_effort=reasoning_effort)
        self.inputs: list[MiddleInput] = []

    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        self.inputs.append(middle_input)
        return super().analyze(middle_input)


def request_rows(root: Path) -> list[dict]:
    return read_rows(root / "middle" / "requests.jsonl")


def test_request_row_has_the_sections_and_frames_that_were_sent(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_frames(root, [4.0, 8.0, 9.5])
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    handler = LunaHandler(ok_response())
    analyzer = RecordingLunaAnalyzer(handler, reasoning_effort="low")
    result = step(
        paths,
        analyzer,
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        reasons=("queue_empty", "scene_change"),
        request_log=MiddleRequestLog(paths.middle_requests),
    )
    assert result.ok is True
    [row] = request_rows(root)
    assert list(row) == REQUEST_ROW_KEYS
    assert row["request"] == 1
    assert row["model"] == "gpt-6-luna"
    assert row["reasoning_effort"] == "low"
    assert row["reasons"] == ["queue_empty", "scene_change"]
    # 節の文は build_user_sections の結果で、送った input_text の文と同じ (画像の前の時刻のラベルは含まない)
    sections = build_user_sections(analyzer.inputs[0])
    assert row["sections"] == sections
    assert row["sections"] == handler.user_texts(0)[: len(sections)]
    assert row["frames"] == [
        {"t": 4.0, "file": "frames/interval/000004000.jpg"},
        {"t": 8.0, "file": "frames/interval/000008000.jpg"},
        {"t": 9.5, "file": "frames/interval/000009500.jpg"},
    ]
    # 画像の data URL と API キーは書かない
    text = (root / "middle" / "requests.jsonl").read_text(encoding="utf-8")
    assert "base64," not in text
    assert "sk-test" not in text


def test_calls_row_has_the_request_and_t_sent_of_its_request_row(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # 読むたびに 0.25 秒進む時計 (入力を作った時刻と、analyze を呼ぶ直前の時刻が違う)
    monotonic = TickingMonotonic(ORIGIN + 10.0, step_s=0.25)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = RecordingLunaAnalyzer(LunaHandler(ok_response()))
    log = MiddleRequestLog(paths.middle_requests)
    for _ in range(2):
        assert step(paths, analyzer, clock, 10.0, None, request_log=log).ok is True
    requests = request_rows(root)
    calls = read_rows(root / "middle" / "calls.jsonl")
    # 要求を送るたびに番号を 1 増やす (1 から数える)
    assert [row["request"] for row in requests] == [1, 2]
    assert [row["request"] for row in calls] == [1, 2]
    # t_sent は、入力を作った時刻ではなく、analyze を呼ぶ直前に読んだ時刻 (calls.jsonl と同じ値)
    assert [row["t_sent"] for row in requests] == [row["t_sent"] for row in calls]
    assert all(row["t_sent"] != row["based_on_t"] for row in calls)
    # request は t_received の後ろに置き、gpt-6-luna の記録はほかのキーの後ろに足す
    assert list(calls[0]) == SKELETON_CALL_KEYS + list(CALL_DETAIL_KEYS)
    # 画像を送らなかった回の frames は空の配列
    assert requests[0]["frames"] == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="server error"),
        httpx.Response(200, json=luna_body(status="incomplete")),
        httpx.Response(
            200,
            json=luna_body(content=[{"type": "refusal", "refusal": "Sorry, I can't."}]),
        ),
        httpx.Response(200, json=luna_body({**LUNA_OUTPUT, "extra": 1})),
    ],
    ids=["http-error", "incomplete", "refusal", "schema-mismatch"],
)
def test_request_failing_after_sending_keeps_its_row_and_number(tmp_path, response):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    handler = LunaHandler(response)
    result = step(
        paths,
        RecordingLunaAnalyzer(handler),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        request_log=MiddleRequestLog(paths.middle_requests),
    )
    assert result.ok is False
    assert len(handler.requests) == 1
    [request_row] = request_rows(root)
    [call_row] = read_rows(root / "middle" / "calls.jsonl")
    assert call_row["error"] is not None
    # 送った後に失敗した回も、要求の行が残り、calls.jsonl の失敗の行に同じ番号を書く
    assert request_row["request"] == 1
    assert call_row["request"] == 1
    assert call_row["t_sent"] == request_row["t_sent"]


def test_fake_middle_writes_no_request_row_and_null_request(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    result = step(
        paths,
        FakeMiddleAnalyzer(),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        request_log=MiddleRequestLog(paths.middle_requests),
    )
    assert result.ok is True
    assert not (root / "middle" / "requests.jsonl").exists()
    [row] = read_rows(root / "middle" / "calls.jsonl")
    assert row["request"] is None


def test_failed_input_build_writes_no_request_row_and_null_request(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    # surface.jsonl をディレクトリーにして、入力の作成を失敗させる
    (root / "surface.jsonl").mkdir()
    handler = LunaHandler(ok_response())
    result = step(
        paths,
        RecordingLunaAnalyzer(handler),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        request_log=MiddleRequestLog(paths.middle_requests),
    )
    assert result.ok is False
    assert handler.requests == []
    assert not (root / "middle" / "requests.jsonl").exists()
    [row] = read_rows(root / "middle" / "calls.jsonl")
    assert row["error"] is not None
    assert row["request"] is None


def test_stop_while_waiting_for_the_response_leaves_only_the_request_row(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    stop = threading.Event()
    handler = LunaHandler(ok_response())
    # 要求を送った後、応答を待つ間に全体が止められる
    handler.on_request = stop.set
    result = step(
        paths,
        RecordingLunaAnalyzer(handler),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
        stop,
        request_log=MiddleRequestLog(paths.middle_requests),
    )
    assert result.stopped is True
    # 送る直前に書いた要求の行だけが残り、calls.jsonl の行は書かない
    assert [row["request"] for row in request_rows(root)] == [1]
    assert read_rows(root / "middle" / "calls.jsonl") == []


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        (None, 1),
        ("", 1),
        ('{"request": 6}\n{"request": 7}\n', 8),
    ],
    ids=["no-file", "no-rows", "after-saved-rows"],
)
def test_middle_once_numbers_its_request_after_the_saved_ones(
    tmp_path, saved, expected
):
    root = make_run(tmp_path)
    write_frames(root, [4.0, 8.0, 12.5])
    if saved is not None:
        (root / "middle" / "requests.jsonl").write_text(saved, encoding="utf-8")
    handler = LunaHandler(ok_response())
    result = run_middle_once(root, RecordingLunaAnalyzer(handler), 10.0)
    assert result.ok is True
    # 保存済みの実行ディレクトリーの requests.jsonl に、最後の番号の次の番号で追記する
    rows = request_rows(root)
    saved_count = 0 if saved is None else saved.count("\n")
    assert len(rows) == saved_count + 1
    assert rows[-1]["request"] == expected
    assert rows[-1]["t_sent"] == 12.5
    [call] = read_rows(root / "middle" / "calls.jsonl")
    assert call["request"] == expected
    assert call["t_sent"] == 12.5


def test_failed_request_append_still_sends_and_is_not_a_middle_failure(
    tmp_path, caplog
):
    caplog.set_level(logging.WARNING)
    harness = make_loop(tmp_path, output(), max_errors=1)
    handler = LunaHandler(ok_response())
    harness.loop.parts = dataclasses.replace(
        harness.loop.parts, analyzer=RecordingLunaAnalyzer(handler)
    )
    # middle/requests.jsonl をディレクトリーにして、要求の記録の追記を失敗させる
    requests_path = harness.root / "middle" / "requests.jsonl"
    requests_path.mkdir()
    result = harness.loop.run_once(INTERVAL)
    # 要求は送り、middle の失敗に数えず、止めず、run.jsonl にも書かない
    assert len(handler.requests) == 1
    assert result.ok is True
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    assert read_rows(harness.root / "run.jsonl") == []
    # 警告を書く
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    # calls.jsonl の行には送った要求の番号を書く (requests.jsonl にその番号の行は無い)
    [row] = read_rows(harness.root / "middle" / "calls.jsonl")
    assert row["request"] == 1
    # 次の回の番号は 1 つ進んでいる
    requests_path.rmdir()
    assert harness.loop.run_once(INTERVAL).ok is True
    assert [row["request"] for row in request_rows(harness.root)] == [2]
    calls = read_rows(harness.root / "middle" / "calls.jsonl")
    assert [row["request"] for row in calls] == [1, 2]
