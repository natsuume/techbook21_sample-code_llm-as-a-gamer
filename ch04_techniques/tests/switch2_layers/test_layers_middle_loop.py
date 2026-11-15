import json
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

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        self.requests.append((kind, trigger, scene_id))
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


def test_next_input_uses_rows_since_previous_successful_output(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    analyzer = ScriptedAnalyzer(monotonic, output("町"))
    run_middle_step(RunPaths(root), analyzer, clock, 10.0, None)
    (root / "surface.jsonl").write_text(
        json.dumps({"t_received": 9.0})
        + "\n"
        + json.dumps({"t_received": 12.0})
        + "\n",
        encoding="utf-8",
    )
    monotonic.now = ORIGIN + 15.0
    run_middle_step(RunPaths(root), analyzer, clock, 10.0, None)
    # 前回の入力の時刻 (10.0) より後の行だけ
    assert [row["t_received"] for row in analyzer.inputs[1].surface_rows] == [12.0]
    assert analyzer.inputs[1].previous_state is not None


def append_rows(root: Path, name: str, rows: list[dict]) -> None:
    with (root / name).open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")


def test_rows_of_failed_call_are_included_in_next_input(tmp_path):
    root = make_run(tmp_path)
    monotonic = FakeMonotonic(ORIGIN + 10.0)
    clock = RunClock(monotonic, origin=ORIGIN)
    paths = RunPaths(root)
    analyzer = ScriptedAnalyzer(
        monotonic, output("町"), RuntimeError("luna failed"), output("町"), elapsed=0.0
    )
    # 1 回目 (t=10.0) は成功し、based_on_t = 10.0 の middle/state.json を書く
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is True
    append_rows(root, "surface.jsonl", [{"t_received": 12.0}])
    append_rows(root, "operations.jsonl", [{"t": 13.0}])
    # 2 回目 (t=15.0) は失敗し、middle/state.json は 1 回目のまま
    monotonic.now = ORIGIN + 15.0
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is False
    assert [row["t_received"] for row in analyzer.inputs[1].surface_rows] == [12.0]
    append_rows(root, "surface.jsonl", [{"t_received": 17.0}])
    append_rows(root, "operations.jsonl", [{"t": 18.0}])
    # 3 回目 (t=20.0) の入力には、失敗した 2 回目に渡した行 (12.0・13.0) も含める
    monotonic.now = ORIGIN + 20.0
    assert run_middle_step(paths, analyzer, clock, 10.0, None).ok is True
    third = analyzer.inputs[2]
    assert third.previous_state is not None
    assert third.previous_state.based_on_t == 10.0
    assert [row["t_received"] for row in third.surface_rows] == [12.0, 17.0]
    assert [row["t"] for row in third.operation_rows] == [13.0, 18.0]
    # 成功した 3 回目の後は、その入力の時刻 (20.0) より後の行だけになる
    monotonic.now = ORIGIN + 25.0
    run_middle_step(paths, analyzer, clock, 10.0, None)
    assert analyzer.inputs[3].surface_rows == ()
    assert analyzer.inputs[3].operation_rows == ()


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
