"""LayersApp をフェイクの層とフェイクのキャプチャー・ブリッジで数秒動かす統合テスト (実時間で待つ)。"""

import dataclasses
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from switch2_remote.controller_state import NEUTRAL_REPORT

from switch2_layers.app import (
    REASON_BRIDGE_FAILED,
    REASON_INTERRUPTED,
    LayersApp,
    LayersParts,
)
from switch2_layers.deep.deep_types import DeepJob, DeepResult
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.hardware.capture_source import LatestCapture
from switch2_layers.hardware.capture_watcher import REASON_STALE_FRAME
from switch2_layers.middle.fake_middle import FAKE_SCENE_NAME, FakeMiddleAnalyzer
from switch2_layers.middle.middle_loop import middle_failure_reason
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput
from switch2_layers.pad_action import PadAction
from switch2_layers.settings import RunSettings
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.surface_loop import surface_failure_reason
from switch2_layers.surface.surface_types import SurfaceInput, SurfaceOutput

RUN_SECONDS = 3.0  # 止めるまで動かす秒数
SAFETY_TIMEOUT_S = 20.0  # 止まらないときに、テストを終わらせるために止める秒数
SAFETY_REASON = "test safety timeout"
HOME_BIT = 0x10  # レポートの b2


class FakeFrameReader:
    """キャプチャーの偽物。読むたびに、今の time.monotonic を時刻とする新しいフレームを返す。

    freeze_after 秒たったら、最後のフレームを返し続ける (新しいフレームが届かない状態)。
    """

    def __init__(self, freeze_after: float | None = None):
        self.started = time.monotonic()
        self.freeze_after = freeze_after
        self.nv12 = np.full((360 * 3 // 2, 640), 128, np.uint8)
        self.last: LatestCapture | None = None
        self.lock = threading.Lock()

    def read_latest(self) -> LatestCapture | None:
        with self.lock:
            now = time.monotonic()
            frozen = (
                self.freeze_after is not None
                and now - self.started >= self.freeze_after
                and self.last is not None
            )
            if not frozen:
                self.last = LatestCapture(monotonic_time=now, nv12=self.nv12)
            return self.last

    def is_ended(self) -> bool:
        return False


class FakeSender:
    """ブリッジの偽物。送られたレポートを記録する。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.reports: list[bytes] = []

    def set_report(self, report: bytes) -> str:
        with self.lock:
            self.reports.append(bytes(report))
        return "ack"


class HomePressingSurface:
    """HOME と A を押そうとする surface の偽物 (ボタンの制限を確かめる)。"""

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        return SurfaceOutput(action=PadAction(held=frozenset({"HOME", "A"})))


class FailingSurface:
    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        raise RuntimeError("decision model failed")


class FailingMiddle:
    def analyze(self, middle_input: MiddleInput) -> MiddleOutput:
        raise RuntimeError("luna failed")


class BlockingDeepRunner:
    """deep の偽物。run は stop か kill が呼ばれるまで戻らず、止められたら stopped=True の結果を返す。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.events: dict[Path, threading.Event] = {}
        self.stopped: list[Path] = []
        self.killed: list[Path] = []

    def _event(self, job_dir: Path) -> threading.Event:
        with self.lock:
            return self.events.setdefault(job_dir, threading.Event())

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        self._event(job_dir).wait(SAFETY_TIMEOUT_S)
        return DeepResult(ok=False, message="stopped", stopped=True)

    def stop(self, job_dir: Path) -> None:
        with self.lock:
            self.stopped.append(job_dir)
        self._event(job_dir).set()

    def kill(self, job_dir: Path) -> None:
        with self.lock:
            self.killed.append(job_dir)
        self._event(job_dir).set()


def settings(tmp_path: Path, **changes: int) -> RunSettings:
    base = RunSettings(
        runs_dir=tmp_path / "runs", interval_width=160, middle_interval_s=1.0
    )
    return dataclasses.replace(base, **changes)


def parts(
    sender: FakeSender,
    reader: FakeFrameReader | None = None,
    bridge_state: Callable[[], str] = lambda: "ok",
    surface=None,
    middle=None,
    deep=None,
) -> LayersParts:
    return LayersParts(
        frame_reader=reader or FakeFrameReader(),
        report_sender=sender,
        bridge_state=bridge_state,
        surface=surface or FakeSurfaceDecider(),
        middle=middle or FakeMiddleAnalyzer(),
        deep=deep or FakeDeepRunner(),
    )


def run_app(app: LayersApp, stop_after: float | None) -> tuple[str, Path]:
    """app を start して、stop_after 秒後 (None なら止まる条件まで) に Ctrl+C 相当で止め、(止まった理由, 実行ディレクトリー) を返す。"""
    paths = app.start()
    timers = [
        threading.Timer(SAFETY_TIMEOUT_S, app.request_stop, args=(SAFETY_REASON,))
    ]
    if stop_after is not None:
        timers.append(
            threading.Timer(stop_after, app.request_stop, args=(REASON_INTERRUPTED,))
        )
    for timer in timers:
        timer.start()
    try:
        reason = app.wait()
    finally:
        for timer in timers:
            timer.cancel()
        app.close()
    return reason, paths.root


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_with_fake_layers_for_a_few_seconds(tmp_path):
    sender = FakeSender()
    app = LayersApp(settings(tmp_path), parts(sender))
    reason, root = run_app(app, stop_after=RUN_SECONDS)
    assert reason == REASON_INTERRUPTED
    assert root.parent == tmp_path / "runs"

    # surface: 0.2 秒ごとに要求を送り、1 行ずつ書いた
    surface_rows = read_rows(root / "surface.jsonl")
    assert len(surface_rows) >= 3
    assert all(row["error"] is None for row in surface_rows)
    assert all(1 <= len(row["frames"]) <= 5 for row in surface_rows)

    # middle: 固定の場面名を書き、初めての場面として一覧に足した
    state = read_json(root / "middle" / "state.json")
    assert state["scene_name"] == FAKE_SCENE_NAME
    assert state["scene_id"] == "scene-001"
    assert state["surface_wait_s"] == 0.2
    assert len(read_rows(root / "middle" / "calls.jsonl")) >= 2
    assert (
        read_json(root / "notes" / "scene_labels.json")["scenes"][0]["name"]
        == FAKE_SCENE_NAME
    )

    # deep: 初めての場面の操作説明と攻略情報のジョブを作った
    for kind in ["controls", "tips"]:
        job = read_json(root / "deep" / f"{kind}-001" / "job.json")
        assert job["trigger"] == "new_scene"
        assert job["scene_id"] == "scene-001"
        assert (root / "deep" / f"{kind}-001" / "work").is_dir()

    # 記録: start (起動引数を含む) で始まり、frames_dropped の後に stop (理由を含む) で終わる
    run_rows = read_rows(root / "run.jsonl")
    assert run_rows[0]["event"] == "start"
    assert run_rows[0]["args"]["middle_interval_s"] == 1.0
    assert [row["event"] for row in run_rows[-2:]] == ["frames_dropped", "stop"]
    assert run_rows[-1]["reason"] == REASON_INTERRUPTED
    events = [row["event"] for row in run_rows]
    assert events.count("deep_start") >= 2
    assert events.count("deep_end") >= 2

    # フレーム: 一定間隔の画像と surface に送った画像を保存した
    frame_rows = read_rows(root / "frames.jsonl")
    assert sum(row["kind"] == "interval" for row in frame_rows) >= 5
    assert any(row["kind"] == "surface" for row in frame_rows)
    assert all((root / row["file"]).is_file() for row in frame_rows)

    # 操作: 最後は中立にして出力を止めた
    operation_rows = read_rows(root / "operations.jsonl")
    assert any(row["source"] == "apply" for row in operation_rows)
    assert operation_rows[-1]["source"] in {"stop", "neutral"}
    assert any(row["source"] == "stop" for row in operation_rows)
    assert sender.reports[-1] == NEUTRAL_REPORT

    # 起動時の notes/
    controls = (root / "notes" / "common" / "controls.md").read_text(encoding="utf-8")
    assert "- A: unknown" in controls
    assert "HOME" not in controls


def test_home_is_never_sent_to_the_controller(tmp_path):
    sender = FakeSender()
    app = LayersApp(settings(tmp_path), parts(sender, surface=HomePressingSurface()))
    reason, root = run_app(app, stop_after=1.5)
    assert reason == REASON_INTERRUPTED
    assert sender.reports
    assert all(report[1] & HOME_BIT == 0 for report in sender.reports)
    surface_rows = read_rows(root / "surface.jsonl")
    assert surface_rows
    assert all(row["blocked_buttons"] == ["HOME"] for row in surface_rows)
    assert all(row["action"]["held"] == ["A"] for row in surface_rows)


def test_keyboard_interrupt_stops_with_interrupted(tmp_path):
    deadline: list[float] = []

    def sleep_then_interrupt(seconds: float) -> None:
        # wait の見張りの待ちの途中で Ctrl+C が押されたときと同じく、KeyboardInterrupt を出す
        if not deadline:
            deadline.append(time.monotonic() + 1.5)
        if time.monotonic() >= deadline[0]:
            raise KeyboardInterrupt
        time.sleep(seconds)

    sender = FakeSender()
    app = LayersApp(settings(tmp_path), parts(sender), sleep=sleep_then_interrupt)
    safety = threading.Timer(SAFETY_TIMEOUT_S, app.request_stop, args=(SAFETY_REASON,))
    safety.start()
    try:
        reason = app.run()
    finally:
        safety.cancel()
    assert reason == REASON_INTERRUPTED
    assert app.paths is not None
    run_rows = read_rows(app.paths.root / "run.jsonl")
    assert run_rows[-1]["event"] == "stop"
    assert run_rows[-1]["reason"] == REASON_INTERRUPTED
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_bridge_failure_stops_everything(tmp_path):
    state = {"value": "ok"}
    sender = FakeSender()
    app = LayersApp(
        settings(tmp_path), parts(sender, bridge_state=lambda: state["value"])
    )
    failing = threading.Timer(1.0, state.update, kwargs={"value": "failed"})
    failing.start()
    try:
        reason, root = run_app(app, stop_after=None)
    finally:
        failing.cancel()
    assert reason == REASON_BRIDGE_FAILED
    assert read_rows(root / "run.jsonl")[-1]["reason"] == REASON_BRIDGE_FAILED
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_stale_capture_stops_everything(tmp_path):
    sender = FakeSender()
    reader = FakeFrameReader(freeze_after=0.5)
    app = LayersApp(settings(tmp_path), parts(sender, reader=reader))
    reason, root = run_app(app, stop_after=None)
    assert reason == REASON_STALE_FRAME
    assert read_rows(root / "run.jsonl")[-1]["reason"] == REASON_STALE_FRAME
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_consecutive_surface_failures_stop_everything(tmp_path):
    sender = FakeSender()
    app = LayersApp(
        settings(tmp_path, max_surface_errors=2),
        parts(sender, surface=FailingSurface()),
    )
    reason, root = run_app(app, stop_after=None)
    assert reason == surface_failure_reason(2)
    run_rows = read_rows(root / "run.jsonl")
    assert [row["event"] for row in run_rows].count("surface_error") == 2
    assert run_rows[-1]["reason"] == surface_failure_reason(2)
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_consecutive_middle_failures_stop_everything(tmp_path):
    sender = FakeSender()
    app = LayersApp(
        settings(tmp_path, max_middle_errors=1),
        parts(sender, middle=FailingMiddle()),
    )
    reason, root = run_app(app, stop_after=None)
    assert reason == middle_failure_reason(1)
    run_rows = read_rows(root / "run.jsonl")
    assert [row["event"] for row in run_rows].count("middle_error") == 1
    assert not (root / "middle" / "state.json").exists()


def test_close_stops_running_deep_jobs(tmp_path):
    sender = FakeSender()
    deep = BlockingDeepRunner()
    app = LayersApp(settings(tmp_path), parts(sender, deep=deep))
    reason, root = run_app(app, stop_after=RUN_SECONDS)
    assert reason == REASON_INTERRUPTED
    # 初めての場面の操作説明と攻略情報のジョブは、終了時まで実行中だった
    jobs = {root / "deep" / "controls-001", root / "deep" / "tips-001"}
    assert set(deep.stopped) == jobs
    # stop で run が戻ったので、kill は呼ばない
    assert deep.killed == []
    run_rows = read_rows(root / "run.jsonl")
    stops = [row for row in run_rows if row["event"] == "deep_stop"]
    assert {row["job"] for row in stops} == {"deep/controls-001", "deep/tips-001"}
    assert all(row["reason"] == "close" for row in stops)
    ends = [row for row in run_rows if row["event"] == "deep_end"]
    assert {row["job"] for row in ends} == {"deep/controls-001", "deep/tips-001"}
    assert all(row["status"] == "stopped" for row in ends)
    assert all(row["stop_reason"] == "close" for row in ends)
    # deep の終わりは、run.jsonl の stop より前に記録する
    events = [row["event"] for row in run_rows]
    assert events[-1] == "stop"
    assert max(i for i, e in enumerate(events) if e == "deep_end") < len(events) - 1
