import dataclasses
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from switch2_layers.deep.deep_types import DeepRequestResult, DeepTrigger, DocumentKind
from switch2_layers.middle.middle_loop import (
    MiddleLoop,
    MiddleLoopParts,
    MiddleLoopSettings,
    middle_failure_reason,
    run_middle_once,
    run_middle_step,
)
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

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


def output(scene_name: str | None = "町", wait: float = 0.4) -> MiddleOutput:
    return MiddleOutput(
        scene_name=scene_name,
        is_stuck=False,
        stuck_description="",
        surface_wait_s=wait,
        timeline="## 直近",
        tasks="- 宿屋に入る\n",
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


@dataclass
class LoopHarness:
    root: Path
    monotonic: FakeMonotonic
    loop: MiddleLoop
    deep_sink: FakeDeepSink
    stops: list[str] = field(default_factory=list)


def make_loop(
    tmp_path: Path, *results: MiddleOutput | Exception, max_errors: int = 5
) -> LoopHarness:
    """MiddleLoop と偽の部品を組み立てる (テストの本体から呼ぶ)。時計は t = 10.0。"""
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    deep_sink = FakeDeepSink()
    stops: list[str] = []
    parts = MiddleLoopParts(
        paths=paths,
        analyzer=ScriptedAnalyzer(monotonic, *results),
        clock=clock,
        run_log=RunLog(paths.run_log, clock),
        deep_requests=deep_sink,
        on_stop=stops.append,
    )
    loop = MiddleLoop(
        parts, MiddleLoopSettings(interval_s=5.0, window_s=10.0, max_errors=max_errors)
    )
    return LoopHarness(root, monotonic, loop, deep_sink, stops)


# --- 1 回分の処理 ---


def test_step_writes_outputs_and_calls_row(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    analyzer = ScriptedAnalyzer(monotonic, output(), elapsed=0.5)
    sink = FakeDeepSink()
    result = run_middle_step(
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
    assert (root / "notes" / "scenes" / "scene-001" / "tasks.md").exists()
    rows = read_rows(root / "middle" / "calls.jsonl")
    assert len(rows) == 1
    assert rows[0]["t_sent"] == 10.0
    assert rows[0]["t_received"] == 10.5
    assert rows[0]["scene_id"] == "scene-001"
    assert rows[0]["error"] is None


def test_new_scene_requests_controls_and_tips_deep(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    result = run_middle_step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        sink,
    )
    assert result.new_scene is not None
    assert result.deep_requests == (DocumentKind.CONTROLS, DocumentKind.TIPS)
    assert sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
    ]
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["new_scene"] is True
    assert row["deep_requests"] == ["controls", "tips"]
    assert row["deep_requests_sent"] is True


def test_new_scene_is_requested_again_after_failed_write(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(monotonic, output("町"))
    # tasks.md の書き込みを失敗させる (notes/scenes をファイルにする)
    (root / "notes" / "scenes").write_text("not a directory", encoding="utf-8")
    result = run_middle_step(paths, analyzer, clock, 10.0, sink)
    assert result.ok is False
    assert result.new_scene is None
    assert sink.requests == []
    assert json.loads(
        (root / "notes" / "scene_labels.json").read_text(encoding="utf-8")
    ) == {"scenes": []}
    # 次の回では、同じ名前が初めての場面として扱われ、deep の要求が出る
    (root / "notes" / "scenes").unlink()
    result = run_middle_step(paths, analyzer, clock, 10.0, sink)
    assert result.ok is True
    assert result.new_scene is not None
    assert result.new_scene.id == "scene-001"
    assert sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
    ]


def test_known_scene_requests_no_deep(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(monotonic, output("町"))
    run_middle_step(RunPaths(root), analyzer, clock, 10.0, sink)
    sink.requests.clear()
    result = run_middle_step(RunPaths(root), analyzer, clock, 10.0, sink)
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
    result = run_middle_step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
        RunClock(monotonic, origin=ORIGIN),
        10.0,
        None,
    )
    assert result.deep_requests == (DocumentKind.CONTROLS, DocumentKind.TIPS)
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["deep_requests"] == ["controls", "tips"]
    assert row["deep_requests_sent"] is False
    assert list((root / "deep").iterdir()) == []


def test_failed_analyze_keeps_previous_output(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    sink = FakeDeepSink()
    analyzer = ScriptedAnalyzer(monotonic, output("町"), RuntimeError("luna failed"))
    run_middle_step(RunPaths(root), analyzer, clock, 10.0, sink)
    state_before = (root / "middle" / "state.json").read_bytes()
    tasks_before = (root / "notes" / "scenes" / "scene-001" / "tasks.md").read_bytes()
    sink.requests.clear()
    result = run_middle_step(RunPaths(root), analyzer, clock, 10.0, sink)
    assert result.ok is False
    assert result.state is None
    assert "luna failed" in (result.error or "")
    assert (root / "middle" / "state.json").read_bytes() == state_before
    assert (
        root / "notes" / "scenes" / "scene-001" / "tasks.md"
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
    run_middle_step(paths, analyzer, clock, 10.0, None)
    assert [row["source"] for row in analyzer.inputs[0].operation_rows] == ["apply"]
    assert read_state(root)["operations_rows_offset"] == file_size(
        root, "operations.jsonl"
    )
    # 同じ t (ミリ秒) の行が、読んだ後に追記される
    append_rows(root, "operations.jsonl", [{"t": 13.0, "source": "neutral"}])
    monotonic.now = ORIGIN + 15.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
    assert [row["source"] for row in analyzer.inputs[1].operation_rows] == ["neutral"]


def test_row_with_earlier_time_appended_after_input_is_in_next_input(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"), elapsed=0.0)
    append_rows(root, "surface.jsonl", [{"t_received": 8.0}])
    # 1 回目は t=10.0 に入力を作る。このとき 9.5 の行はまだ書かれていない (surface が送信の ack を待っている)
    run_middle_step(paths, analyzer, clock, 10.0, None)
    assert [row["t_received"] for row in analyzer.inputs[0].surface_rows] == [8.0]
    assert read_state(root)["based_on_t"] == 10.0
    # 入力を作った時刻 (10.0) より前の t_received を持つ行が、入力を作った後に追記される
    append_rows(
        root,
        "surface.jsonl",
        [{"t_received": 9.5, "scene_changed": True}, {"t_received": 12.0}],
    )
    monotonic.now = ORIGIN + 15.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
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
    run_middle_step(paths, analyzer, clock, 10.0, None)
    # 書きかけの行は読まず、位置にも含めない
    assert [row["t_received"] for row in analyzer.inputs[0].surface_rows] == [8.0]
    assert read_state(root)["surface_rows_offset"] == len(first_line.encode())
    with (root / "surface.jsonl").open("a", encoding="utf-8") as file:
        file.write("}\n")
    monotonic.now = ORIGIN + 15.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
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
    run_middle_step(paths, analyzer, clock, 10.0, None)
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
    run_middle_step(paths, analyzer, clock, 10.0, None)
    offset = read_state(root)["surface_rows_offset"]
    monotonic.now = ORIGIN + 15.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
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
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is True
    surface_offset = file_size(root, "surface.jsonl")
    operations_offset = file_size(root, "operations.jsonl")
    assert read_state(root)["surface_rows_offset"] == surface_offset
    append_rows(root, "surface.jsonl", [{"t_received": 12.0}])
    append_rows(root, "operations.jsonl", [{"t": 13.0}])
    # 2 回目 (t=15.0) は失敗し、middle/state.json (と位置) は 1 回目のまま
    monotonic.now = ORIGIN + 15.0
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is False
    assert [row["t_received"] for row in analyzer.inputs[1].surface_rows] == [12.0]
    assert read_state(root)["surface_rows_offset"] == surface_offset
    assert read_state(root)["operations_rows_offset"] == operations_offset
    append_rows(root, "surface.jsonl", [{"t_received": 17.0}])
    append_rows(root, "operations.jsonl", [{"t": 18.0}])
    # 3 回目 (t=20.0) の入力には、失敗した 2 回目に渡した行 (12.0・13.0) も含める
    monotonic.now = ORIGIN + 20.0
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is True
    third = analyzer.inputs[2]
    assert third.previous_state is not None
    assert third.previous_state.surface_rows_offset == surface_offset
    assert [row["t_received"] for row in third.surface_rows] == [12.0, 17.0]
    assert [row["t"] for row in third.operation_rows] == [13.0, 18.0]
    # 成功した 3 回目の後は、その後に書かれた行だけになる
    monotonic.now = ORIGIN + 25.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[3].surface_rows == ()
    assert analyzer.inputs[3].operation_rows == ()


# --- deep の要求の例外 ---


def test_deep_request_error_is_collected_and_other_requests_continue(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    sink.fail_for[DocumentKind.CONTROLS] = OSError("disk full")
    result = run_middle_step(
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
    ]
    row = read_rows(root / "middle" / "calls.jsonl")[0]
    assert row["error"] is None
    assert row["deep_requests_sent"] is True


def test_loop_records_deep_request_error_without_counting_failure(tmp_path):
    harness = make_loop(tmp_path, output("町"), max_errors=1)
    harness.deep_sink.fail_for[DocumentKind.TIPS] = OSError("disk full")
    result = harness.loop.run_once()
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
        assert harness.loop.run_once().ok is False
    assert harness.stops == []
    assert harness.loop.consecutive_errors == 4
    harness.loop.run_once()
    assert harness.stops == [middle_failure_reason(5)]
    assert middle_failure_reason(5) == "middle failed 5 times in a row"
    run_rows = read_rows(harness.root / "run.jsonl")
    assert [row["event"] for row in run_rows] == ["middle_error"] * 5
    assert [row["count"] for row in run_rows] == [1, 2, 3, 4, 5]
    assert all("luna failed" in row["error"] for row in run_rows)


def test_success_resets_failures(tmp_path):
    error = RuntimeError("luna failed")
    harness = make_loop(tmp_path, error, error, output(), error, max_errors=3)
    harness.loop.run_once()
    harness.loop.run_once()
    assert harness.loop.consecutive_errors == 2
    assert harness.loop.run_once().ok is True
    assert harness.loop.consecutive_errors == 0
    harness.loop.run_once()
    harness.loop.run_once()
    assert harness.stops == []
    harness.loop.run_once()
    assert harness.stops == [middle_failure_reason(3)]


def test_loop_passes_new_scene_to_deep_scheduler(tmp_path):
    harness = make_loop(tmp_path, output("町"))
    harness.loop.run_once()
    assert harness.deep_sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"),
        (DocumentKind.TIPS, DeepTrigger.NEW_SCENE, "scene-001"),
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
    assert row["deep_requests"] == ["controls", "tips"]
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
    result = run_middle_step(
        RunPaths(root), analyzer, RunClock(monotonic, origin=ORIGIN), 10.0, sink, stop
    )
    assert result.stopped is True
    assert result.ok is False
    assert result.error is None
    assert len(analyzer.inputs) == 1
    # tasks.md・state.json・scene_labels.json・calls.jsonl に書かず、deep も呼ばない
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
    result = harness.loop.run_once(stop)
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
        ("tasks.md", False, False),
        ("state.json", True, False),
        ("scene_labels.json", True, True),
    ],
)
def test_stop_while_writing_outputs_leaves_the_rest_unwritten(
    tmp_path, monkeypatch, written_file, state_written, label_added
):
    root = make_run(tmp_path)
    stop = threading.Event()
    # 書く順は tasks.md → middle/state.json → notes/scene_labels.json → deep の要求 → calls.jsonl
    monkeypatch.setattr(os, "replace", StopAfterReplace(os.replace, written_file, stop))
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    sink = FakeDeepSink()
    result = run_middle_step(
        RunPaths(root),
        ScriptedAnalyzer(monotonic, output("町")),
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
    result = harness.loop.run_once(stop)
    assert result.stopped is True
    # 残りの種類 (tips) は要求せず、calls.jsonl の行も run.jsonl の deep_request_error も書かない
    assert harness.deep_sink.requests == [
        (DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001")
    ]
    assert read_rows(harness.root / "middle" / "calls.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
