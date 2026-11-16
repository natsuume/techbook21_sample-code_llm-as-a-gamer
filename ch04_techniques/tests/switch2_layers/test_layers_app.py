"""LayersApp をフェイクの層とフェイクのキャプチャー・ブリッジで数秒動かす統合テスト (実時間で待つ)。"""

import dataclasses
import itertools
import json
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
from switch2_remote.controller_state import NEUTRAL_REPORT

import switch2_layers.app as app_module
import switch2_layers.deep.deep_scheduler as deep_scheduler_module
from switch2_layers.app import (
    REASON_BRIDGE_FAILED,
    REASON_INTERRUPTED,
    LayersApp,
    LayersParts,
)
from switch2_layers.deep.deep_types import DeepJob, DeepResult
from switch2_layers.deep.fake_deep import FakeDeepRunner
from switch2_layers.error_text import describe_exception
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.surface_frames import BoxDrawingPlan
from switch2_layers.hardware.capture_source import LatestCapture
from switch2_layers.hardware.capture_watcher import REASON_STALE_FRAME
from switch2_layers.middle.fake_middle import FAKE_SCENE_NAME, FakeMiddleAnalyzer
from switch2_layers.middle.middle_loop import middle_failure_reason
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput
from switch2_layers.pad_action import PadAction
from switch2_layers.run_directory import RunPaths
from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_types import SafetyAnswer
from switch2_layers.settings import DeepRunnerChoice, RunSettings
from switch2_layers.surface.box_targets import DEFAULT_BOX_TARGETS, parse_box_targets
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_client import DEFAULT_JEV_MODEL, IMAGES_HEADER
from switch2_layers.surface.jev_surface import ADVISOR_STATUS_UNAVAILABLE_TEXT
from switch2_layers.surface.surface_assembly import build_surface_assembly
from switch2_layers.surface.surface_loop import surface_failure_reason
from switch2_layers.surface.surface_questions import ADVISOR_KINDS
from switch2_layers.surface.surface_types import Box, SurfaceInput, SurfaceOutput

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
    safety=None,
) -> LayersParts:
    return LayersParts(
        frame_reader=reader or FakeFrameReader(),
        report_sender=sender,
        bridge_state=bridge_state,
        surface=surface or FakeSurfaceDecider(),
        middle=middle or FakeMiddleAnalyzer(),
        deep=deep or FakeDeepRunner(),
        safety=safety or FakeSafetyChecker(),
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


def test_start_record_has_the_deep_runner_choice(tmp_path):
    # Claude Code とフェイクのどちらで deep を動かしたかを、run.jsonl の start の args に値の文字列で残す
    app = LayersApp(settings(tmp_path, deep=DeepRunnerChoice.FAKE), parts(FakeSender()))
    reason, root = run_app(app, stop_after=0.5)
    assert reason == REASON_INTERRUPTED
    start = read_rows(root / "run.jsonl")[0]
    assert start["event"] == "start"
    assert start["args"]["deep"] == "fake"


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


class InterruptOnceSender(FakeSender):
    """armed の後、テストのスレッドからの最初の送信で KeyboardInterrupt を出す (停止の処理の途中の Ctrl+C)。"""

    def __init__(self):
        super().__init__()
        self.armed_thread: threading.Thread | None = None

    def set_report(self, report: bytes) -> str:
        if self.armed_thread is threading.current_thread():
            self.armed_thread = None
            raise KeyboardInterrupt
        return super().set_report(report)


def test_close_redoes_stop_output_interrupted_inside_request_stop(tmp_path):
    sender = InterruptOnceSender()
    app = LayersApp(settings(tmp_path), parts(sender))
    app.start()
    try:
        time.sleep(1.0)
        sender.armed_thread = threading.current_thread()
        # 中立のレポートを送る途中で割り込まれ、理由だけが決まった状態になる
        try:
            app.request_stop(REASON_INTERRUPTED)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("request_stop should have been interrupted")
        count_before_close = len(sender.reports)
    finally:
        app.close()
    # close が停止の処理をやり直し、中立のレポートを送った
    assert len(sender.reports) > count_before_close
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert app.stop_reason == REASON_INTERRUPTED


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
    # 初めての場面の操作説明・攻略情報・ゲームの性質のジョブは、終了時まで実行中だった
    jobs = {
        root / "deep" / "controls-001",
        root / "deep" / "tips-001",
        root / "deep" / "game-001",
    }
    assert set(deep.stopped) == jobs
    # stop で run が戻ったので、kill は呼ばない
    assert deep.killed == []
    run_rows = read_rows(root / "run.jsonl")
    stops = [row for row in run_rows if row["event"] == "deep_stop"]
    assert {row["job"] for row in stops} == {
        "deep/controls-001",
        "deep/tips-001",
        "deep/game-001",
    }
    assert all(row["reason"] == "close" for row in stops)
    ends = [row for row in run_rows if row["event"] == "deep_end"]
    assert {row["job"] for row in ends} == {
        "deep/controls-001",
        "deep/tips-001",
        "deep/game-001",
    }
    assert all(row["status"] == "stopped" for row in ends)
    assert all(row["stop_reason"] == "close" for row in ends)
    # deep の終わりは、run.jsonl の stop より前に記録する
    events = [row["event"] for row in run_rows]
    assert events[-1] == "stop"
    assert max(i for i, e in enumerate(events) if e == "deep_end") < len(events) - 1


# --- start の途中の例外 ---


class InjectedStartError(Exception):
    """start の途中で出す例外 (テストで差し込む)。"""


class NoFrameReader:
    """フレームを返さないキャプチャーの偽物。起動したスレッドに、画像の保存や surface の要求をさせない。"""

    def read_latest(self) -> LatestCapture | None:
        return None

    def is_ended(self) -> bool:
        return False


# start が作る部品のうち、スレッドを持つ部品とループの組み立ての、app モジュールでの名前 (作る順)。
# PadOutput は run.jsonl に start を書いた後に作る
START_PART_NAMES = (
    "PadOutput",
    "FrameStore",
    "CaptureSource",
    "DeepScheduler",
    "SurfaceLoop",
)


class CallCounter:
    """呼ばれた回数を数えてから、元の関数を呼ぶ。"""

    def __init__(self, original: Callable[..., object]):
        self.original = original
        self.count = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.count += 1
        return self.original(*args, **kwargs)


class StartPartsRecorder:
    """app モジュールの部品のクラスを包み、start が作った部品を覚える。fail_at の名前の部品は作らずに error を出す。

    覚えた部品はテストの最後に閉じる (start が後片付けをしなかったときに、スレッドを残さないため)。
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fail_at: str | None,
        error: BaseException,
        names: tuple[str, ...] = START_PART_NAMES,
    ):
        self.made: list[object] = []
        for name in names:
            original = getattr(app_module, name)
            monkeypatch.setattr(
                app_module, name, self._wrap(name, original, fail_at, error)
            )

    def _wrap(
        self,
        name: str,
        original: Callable[..., object],
        fail_at: str | None,
        error: BaseException,
    ) -> Callable[..., object]:
        def build(*args: object, **kwargs: object) -> object:
            if name == fail_at:
                raise error
            part = original(*args, **kwargs)
            self.made.append(part)
            return part

        return build

    def count_teardown_calls(self) -> list[CallCounter]:
        """作った部品の close と stop_output を、呼ばれた回数を数えてから元の関数を呼ぶものに差し替え、その一覧を返す。"""
        counters: list[CallCounter] = []
        for part in self.made:
            for method_name in ("close", "stop_output"):
                method = getattr(part, method_name, None)
                if method is not None:
                    counter = CallCounter(method)
                    setattr(part, method_name, counter)
                    counters.append(counter)
        return counters

    def close_all(self) -> None:
        for part in reversed(self.made):
            close = getattr(part, "close", None)
            if close is not None:
                close()


class FailingThreadStarter:
    """ループのスレッドを started_before_failure 本だけ起動し、次の起動で error を出す (LayersApp の start_thread)。"""

    def __init__(self, started_before_failure: int, error: BaseException):
        self.started_before_failure = started_before_failure
        self.error = error
        self.started: list[threading.Thread] = []

    def __call__(self, thread: threading.Thread) -> None:
        if len(self.started) == self.started_before_failure:
            raise self.error
        thread.start()
        self.started.append(thread)


def only_run_dir(tmp_path: Path) -> Path:
    (run_dir,) = (tmp_path / "runs").iterdir()
    return run_dir


def check_failed_start(
    tmp_path: Path,
    app: LayersApp,
    error: BaseException,
    recorder: StartPartsRecorder,
    loop_threads: list[threading.Thread],
) -> None:
    """app.start が error をそのまま外に出し、それまでに作った部品の後片付けを済ませていることを確かめる。

    最後に、残った部品とスレッドを止める (start が後片付けをしなかったときのため)。
    """
    before = set(threading.enumerate())
    try:
        with pytest.raises(type(error)) as raised:
            app.start()
        assert raised.value is error
        # start が起動したスレッドが残っていない
        assert [t for t in threading.enumerate() if t not in before] == []
        # run.jsonl は start で始まり、理由つきの stop で終わる
        run_log = only_run_dir(tmp_path) / "run.jsonl"
        rows = read_rows(run_log)
        assert rows[0]["event"] == "start"
        assert rows[-1]["event"] == "stop"
        assert describe_exception(error) in rows[-1]["reason"]
        assert app.stop_reason == rows[-1]["reason"]
        # 後から close を呼んでも、二重に閉じない (作った部品の close・stop_output を呼ばず、run.jsonl も変わらない)
        written = run_log.read_bytes()
        counters = recorder.count_teardown_calls()
        app.close()
        assert [counter.count for counter in counters] == [0] * len(counters)
        assert run_log.read_bytes() == written
    finally:
        app.request_stop("test cleanup")
        for thread in loop_threads:
            thread.join(SAFETY_TIMEOUT_S)
        recorder.close_all()


@pytest.mark.parametrize("error_type", [InjectedStartError, KeyboardInterrupt])
@pytest.mark.parametrize("fail_at", START_PART_NAMES)
def test_failure_while_building_parts_cleans_up_and_writes_stop(
    tmp_path, monkeypatch, fail_at, error_type
):
    error = error_type("injected")
    recorder = StartPartsRecorder(monkeypatch, fail_at, error)
    app = LayersApp(settings(tmp_path), parts(FakeSender(), reader=NoFrameReader()))
    check_failed_start(tmp_path, app, error, recorder, [])


@pytest.mark.parametrize("error_type", [InjectedStartError, KeyboardInterrupt])
@pytest.mark.parametrize(
    "started_before_failure",
    [0, 1],
    ids=["first_loop_thread", "after_surface_thread"],
)
def test_failure_while_starting_loop_threads_cleans_up_and_writes_stop(
    tmp_path, monkeypatch, started_before_failure, error_type
):
    error = error_type("injected")
    recorder = StartPartsRecorder(monkeypatch, None, error)
    starter = FailingThreadStarter(started_before_failure, error)
    app = LayersApp(
        settings(tmp_path),
        parts(FakeSender(), reader=NoFrameReader()),
        start_thread=starter,
    )
    check_failed_start(tmp_path, app, error, recorder, starter.started)


def threading_with_unstartable_thread(error: BaseException) -> types.SimpleNamespace:
    """threading と同じ名前を持ち、Thread だけを start で error を出すスレッドに替えたものを返す。

    スレッドを作れないとき (資源が足りないときなど) の再現に使う。
    """

    class UnstartableThread(threading.Thread):
        def start(self) -> None:
            raise error

    names = {name: getattr(threading, name) for name in dir(threading)}
    names["Thread"] = UnstartableThread
    return types.SimpleNamespace(**names)


def test_failure_while_starting_deep_scheduler_thread_cleans_up_and_writes_stop(
    tmp_path, monkeypatch
):
    # DeepScheduler.start がスレッドを起動できないと、後片付けの DeepScheduler.close も例外を出す。
    # その後の段階 (キャプチャー・画像の書き出し・PadOutput を閉じる、stop を書く) も行われることを確かめる
    error = RuntimeError("can't start new thread")
    monkeypatch.setattr(
        deep_scheduler_module, "threading", threading_with_unstartable_thread(error)
    )
    recorder = StartPartsRecorder(monkeypatch, None, error)
    app = LayersApp(settings(tmp_path), parts(FakeSender(), reader=NoFrameReader()))
    check_failed_start(tmp_path, app, error, recorder, [])


@pytest.mark.parametrize("error_type", [InjectedStartError, KeyboardInterrupt])
def test_failure_while_building_safety_monitor_cleans_up_and_writes_stop(
    tmp_path, monkeypatch, error_type
):
    error = error_type("injected")
    recorder = StartPartsRecorder(
        monkeypatch, "SafetyMonitor", error, (*START_PART_NAMES, "SafetyMonitor")
    )
    app = LayersApp(settings(tmp_path), parts(FakeSender(), reader=NoFrameReader()))
    check_failed_start(tmp_path, app, error, recorder, [])


@pytest.mark.parametrize("error_type", [InjectedStartError, KeyboardInterrupt])
def test_failure_while_starting_safety_thread_cleans_up_and_writes_stop(
    tmp_path, monkeypatch, error_type
):
    # 安全の監視のスレッドは、surface と middle のループのスレッドの後に起動する
    error = error_type("injected")
    recorder = StartPartsRecorder(monkeypatch, None, error)
    starter = FailingThreadStarter(2, error)
    app = LayersApp(
        settings(tmp_path),
        parts(FakeSender(), reader=NoFrameReader()),
        start_thread=starter,
    )
    check_failed_start(tmp_path, app, error, recorder, starter.started)


class SurfaceHooks:
    """LayersParts の surface の接続点 (on_run_directory・on_frame・surface_boxes_at) の偽物。呼ばれ方を記録する。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.run_directories: list[Path] = []
        # on_run_directory が呼ばれたときに、box の対象の一覧の初期ファイルがあったか
        self.box_targets_existed: list[bool] = []
        self.frames_before_run_directory = 0
        self.frame_times: list[float] = []
        self.boxes_at_times: list[float] = []

    def on_run_directory(self, paths: RunPaths) -> None:
        with self.lock:
            self.run_directories.append(paths.root)
            self.box_targets_existed.append(
                (paths.notes_dir / "common" / "box_targets.md").is_file()
            )
            self.frames_before_run_directory = len(self.frame_times)

    def on_frame(self, frame: CapturedFrame) -> None:
        with self.lock:
            self.frame_times.append(frame.t)

    def boxes_at(self, t: float) -> list[Box]:
        with self.lock:
            self.boxes_at_times.append(t)
        return []


def test_surface_hooks_are_connected(tmp_path):
    sender = FakeSender()
    hooks = SurfaceHooks()
    base = parts(sender)
    app = LayersApp(
        settings(tmp_path),
        dataclasses.replace(
            base,
            on_frame=hooks.on_frame,
            surface_boxes_at=hooks.boxes_at,
            on_run_directory=hooks.on_run_directory,
        ),
    )
    reason, root = run_app(app, stop_after=1.5)
    assert reason == REASON_INTERRUPTED
    # 実行ディレクトリーを作り、notes/ の初期ファイルを書いた後、フレームを渡す前に 1 回だけ呼んだ
    assert hooks.run_directories == [root]
    assert hooks.box_targets_existed == [True]
    assert hooks.frames_before_run_directory == 0
    # キャプチャーの新しいフレームを渡した (時刻は増える順)
    assert len(hooks.frame_times) >= 5
    assert hooks.frame_times == sorted(set(hooks.frame_times))
    # surface に送った画像ごとに、その時刻の box を求めた
    surface_rows = read_rows(root / "surface.jsonl")
    sent_times = {frame["t"] for row in surface_rows for frame in row["frames"]}
    assert sent_times
    assert sent_times <= set(hooks.boxes_at_times)
    assert set(hooks.boxes_at_times) <= set(hooks.frame_times)


def test_initial_notes_include_box_targets(tmp_path):
    sender = FakeSender()
    app = LayersApp(settings(tmp_path), parts(sender))
    reason, root = run_app(app, stop_after=0.5)
    assert reason == REASON_INTERRUPTED
    text = (root / "notes" / "common" / "box_targets.md").read_text(encoding="utf-8")
    assert parse_box_targets(text) == DEFAULT_BOX_TARGETS


class RecordingPlanBoxes:
    """LayersParts.surface_plan_boxes の偽物。呼ばれた場面 ID を記録し、どの box も書き込まない書き込み方を返す。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[str | None] = []

    def __call__(self, scene_id: str | None) -> BoxDrawingPlan:
        with self.lock:
            self.calls.append(scene_id)
        return BoxDrawingPlan(target_numbers={})


def test_surface_plan_boxes_is_asked_once_per_request(tmp_path):
    sender = FakeSender()
    plan = RecordingPlanBoxes()
    app = LayersApp(
        settings(tmp_path), dataclasses.replace(parts(sender), surface_plan_boxes=plan)
    )
    reason, root = run_app(app, stop_after=1.5)
    assert reason == REASON_INTERRUPTED
    rows = read_rows(root / "surface.jsonl")
    assert rows
    # 要求ごとに 1 回 (止める合図で記録を書かずに抜けた最後の 1 回の分だけ多いことがある)
    assert len(rows) <= len(plan.calls) <= len(rows) + 1
    # 文書の場面 ID (middle が決める前は None)
    assert [row["scene_id"] for row in rows] == plan.calls[: len(rows)]


def mock_jev_answers(request: httpx.Request) -> httpx.Response:
    """Jev の偽物の応答。選択肢の問いは none、box の存在は 0.9、ほかの noul は 0.0、box は固定の位置。"""
    payload = json.loads(request.content)
    answers: dict[str, object] = {}
    for name, question in payload["questions"].items():
        if question["type"] == "choice":
            answers[name] = {
                "type": "choice",
                "choice": "none",
                "confidence": 1.0,
                "probabilities": {
                    choice: 1.0 if choice == "none" else 0.0
                    for choice in question["criteria"]
                },
            }
        elif question["type"] == "noul":
            value = 0.9 if name.startswith("box_exists_") else 0.0
            answers[name] = {"type": "noul", "noul": value}
        else:
            answers[name] = {
                "type": "box",
                "box": {"top": 0.2, "bottom": 0.4, "left": 0.1, "right": 0.3},
                "confidence": None,
            }
    return httpx.Response(
        200, json={"model": "jev-1.14.0", "answers": answers, "usage": {}}
    )


def test_jev_surface_parts_run_together_with_mock_jev(tmp_path):
    sender = FakeSender()
    http = httpx.Client(transport=httpx.MockTransport(mock_jev_answers))
    run_settings = settings(tmp_path)
    surface = build_surface_assembly(run_settings, "test-key", http_client=http)
    app = LayersApp(
        run_settings,
        dataclasses.replace(
            parts(sender, surface=surface.decider),
            on_frame=surface.on_frame,
            surface_boxes_at=surface.boxes_at,
            surface_plan_boxes=surface.plan_boxes,
            on_run_directory=surface.on_run_directory,
        ),
    )
    try:
        reason, root = run_app(app, stop_after=1.5)
    finally:
        surface.close()
    assert reason == REASON_INTERRUPTED
    rows = read_rows(root / "surface.jsonl")
    assert rows
    assert all(row["error"] is None for row in rows)
    # 最初の要求では、どの対象もまだ box が無いので Jev に問い、Jev の box を記録する
    first_boxes = rows[0]["boxes"]
    assert [box["label"] for box in first_boxes] == [
        target.name for target in DEFAULT_BOX_TARGETS
    ]
    assert {box["source"] for box in first_boxes} == {"jev"}
    # 記録した画像のファイルは、保存した画像の一覧にある
    saved = {row["file"] for row in read_rows(root / "frames.jsonl")}
    assert {frame["file"] for frame in rows[0]["frames"]} <= saved
    assert read_rows(root / "run.jsonl")[0]["args"]["surface"] == "jev"


def test_jev_surface_records_requests_and_answers_with_mock_jev(tmp_path):
    sender = FakeSender()
    http = httpx.Client(transport=httpx.MockTransport(mock_jev_answers))
    run_settings = settings(tmp_path)
    surface = build_surface_assembly(run_settings, "test-key", http_client=http)
    app = LayersApp(
        run_settings,
        dataclasses.replace(
            parts(sender, surface=surface.decider),
            on_frame=surface.on_frame,
            surface_boxes_at=surface.boxes_at,
            surface_plan_boxes=surface.plan_boxes,
            on_run_directory=surface.on_run_directory,
        ),
    )
    try:
        reason, root = run_app(app, stop_after=1.5)
    finally:
        surface.close()
    assert reason == REASON_INTERRUPTED
    request_text = (root / "surface_requests.jsonl").read_text(encoding="utf-8")
    request_rows = read_rows(root / "surface_requests.jsonl")
    surface_rows = read_rows(root / "surface.jsonl")
    assert request_rows
    assert surface_rows
    assert [row["request"] for row in request_rows] == list(
        range(1, len(request_rows) + 1)
    )
    assert all(row["model"] == DEFAULT_JEV_MODEL for row in request_rows)
    # API キーと HTTP のヘッダーは書かない
    assert "test-key" not in request_text
    assert "Authorization" not in request_text
    assert IMAGES_HEADER not in request_text
    sent = {row["request"]: row for row in request_rows}
    for row in surface_rows:
        request = sent[row["request"]]
        # 送った state の画像は、surface.jsonl と同じ、保存を予約したファイル名
        assert [frame["image"] for frame in request["state"]["frames"]] == [
            frame["file"] for frame in row["frames"]
        ]
        # 要求したすべての choice と noul の問いの答えを書き、box の問いの答えは書かない
        types = {question["id"]: question["type"] for question in request["questions"]}
        assert set(row["answers"]["choices"]) == {
            name for name, kind in types.items() if kind == "choice"
        }
        assert set(row["answers"]["nouls"]) == {
            name for name, kind in types.items() if kind == "noul"
        }
        # usage は受け取った形のまま (偽の Jev は空の object を返す)
        assert row["usage"] == {}


def test_jev_surface_sends_and_records_deep_status_with_mock_jev(tmp_path):
    sent_states: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent_states.append(json.loads(request.content)["state"])
        return mock_jev_answers(request)

    sender = FakeSender()
    http = httpx.Client(transport=httpx.MockTransport(respond))
    run_settings = settings(tmp_path)
    surface = build_surface_assembly(run_settings, "test-key", http_client=http)
    app = LayersApp(
        run_settings,
        dataclasses.replace(
            parts(sender, surface=surface.decider),
            on_frame=surface.on_frame,
            surface_boxes_at=surface.boxes_at,
            surface_plan_boxes=surface.plan_boxes,
            on_run_directory=surface.on_run_directory,
        ),
    )
    try:
        reason, root = run_app(app, stop_after=1.5)
    finally:
        surface.close()
    assert reason == REASON_INTERRUPTED
    request_rows = read_rows(root / "surface_requests.jsonl")
    assert sent_states
    # 要求の行は送る直前に書くので、送った要求より 1 行多いことがある
    assert len(sent_states) <= len(request_rows) <= len(sent_states) + 1
    for row, sent_state in zip(
        request_rows[: len(sent_states)], sent_states, strict=True
    ):
        recorded = row["state"]
        assert list(recorded) == [
            "instructions",
            "documents",
            "timeline",
            "recent_actions",
            "advisor",
            "frames",
        ]
        # 記録の state には、送った state と同じ advisor が入る
        assert recorded["advisor"] == sent_state["advisor"]
        assert list(recorded["advisor"]) == [kind.value for kind in ADVISOR_KINDS]
        for text in recorded["advisor"].values():
            # フェイクの deep でも、deep のスケジューラーから状況を読める
            assert text != ADVISOR_STATUS_UNAVAILABLE_TEXT
            assert text.startswith("実行中のジョブ: ")
            # 偽の Jev は advisor 判定の確率を 0.0 にするので、surface は deep を求めない
            assert text.endswith("surface はまだ求めていない")


# --- 安全の監視 ---
# 止める条件の境界 (しきい値ちょうど・続けて 2 回・失敗の回数・--allow-home) は、test_layers_safety_judge と
# test_layers_safety_monitor で 1 回ずつ確かめる。ここでは、3 層と組み合わせたときの止まり方を確かめる

SAFETY_INTERVAL_S = 0.2  # 監視の間隔 (テストを短くするため、範囲の下限にする)
ALL_SAFE = {name: 0.0 for name in SAFETY_QUESTION_NAMES}


def safety_answer(**values: float) -> SafetyAnswer:
    """すべての問いを 0 にし、values の問いだけをその確率にした答え。"""
    return SafetyAnswer(probabilities={**ALL_SAFE, **values})


class ScriptedSafety:
    """SafetyChecker の偽物。results を順に返す (例外なら出す。最後の結果は繰り返す)。

    first_check_gate があれば、1 回目の check はその合図が立つまで (最大 SAFETY_TIMEOUT_S 秒) 戻らない。
    """

    def __init__(
        self,
        results: list[SafetyAnswer | Exception],
        first_check_gate: threading.Event | None = None,
    ):
        self.lock = threading.Lock()
        self.results = list(results)
        self.first_check_gate = first_check_gate
        self.count = 0

    def check(self, jpeg: bytes) -> SafetyAnswer:
        with self.lock:
            self.count += 1
            first = self.count == 1
            result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if first and self.first_check_gate is not None:
            self.first_check_gate.wait(SAFETY_TIMEOUT_S)
        if isinstance(result, Exception):
            raise result
        return result


def safety_settings(tmp_path: Path, **changes: object) -> RunSettings:
    return dataclasses.replace(
        settings(tmp_path), safety_interval_s=SAFETY_INTERVAL_S, **changes
    )


def assert_no_operation_was_sent(root: Path, sender: FakeSender) -> None:
    """surface が操作を 1 つも送っていない (送ったのは、止めるときの中立のレポートだけ) ことを確かめる。"""
    assert read_rows(root / "surface.jsonl") == []
    sources = [row["source"] for row in read_rows(root / "operations.jsonl")]
    assert "apply" not in sources
    assert all(report == NEUTRAL_REPORT for report in sender.reports)


def test_unsafe_screen_at_start_stops_without_sending_operations(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([safety_answer(home_menu=0.9)])
    app = LayersApp(safety_settings(tmp_path), parts(sender, safety=safety))
    reason, root = run_app(app, stop_after=3.0)
    assert reason == "unsafe screen at start: home_menu"
    assert read_rows(root / "run.jsonl")[-1]["reason"] == reason
    assert_no_operation_was_sent(root, sender)
    # middle のループも始めない
    assert read_rows(root / "middle" / "calls.jsonl") == []
    # 確かめは 1 回だけで、safety.jsonl に記録した
    assert safety.count == 1
    [row] = read_rows(root / "safety.jsonl")
    assert row["phase"] == "start"
    assert row["probabilities"]["home_menu"] == 0.9
    assert row["frame_t"] >= 0


def test_safety_check_failure_at_start_stops_without_sending_operations(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([RuntimeError("jev is down")])
    app = LayersApp(safety_settings(tmp_path), parts(sender, safety=safety))
    reason, root = run_app(app, stop_after=3.0)
    assert reason == "safety check failed at start"
    assert read_rows(root / "run.jsonl")[-1]["reason"] == reason
    assert_no_operation_was_sent(root, sender)
    [row] = read_rows(root / "safety.jsonl")
    assert row["phase"] == "start"
    assert row["probabilities"] is None
    assert "jev is down" in row["error"]


def test_surface_and_middle_wait_for_the_start_check(tmp_path):
    sender = FakeSender()
    gate = threading.Event()
    safety = ScriptedSafety([safety_answer()], first_check_gate=gate)
    app = LayersApp(safety_settings(tmp_path), parts(sender, safety=safety))
    paths = app.start()
    try:
        time.sleep(1.0)
        # 実行を始めるときの確かめの応答を待つ間は、surface も middle も始めない
        assert safety.count == 1
        assert read_rows(paths.surface_log) == []
        assert read_rows(paths.middle_calls) == []
        assert "apply" not in [row["source"] for row in read_rows(paths.operations_log)]
        gate.set()
        time.sleep(1.5)
        assert read_rows(paths.surface_log)
        app.request_stop(REASON_INTERRUPTED)
        assert app.wait() == REASON_INTERRUPTED
    finally:
        gate.set()
        app.request_stop("test cleanup")
        app.close()


def test_unsafe_screen_twice_while_running_stops_everything(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([safety_answer(), safety_answer(online=0.9)])
    app = LayersApp(safety_settings(tmp_path), parts(sender, safety=safety))
    reason, root = run_app(app, stop_after=5.0)
    assert reason == "unsafe screen: online"
    assert read_rows(root / "run.jsonl")[-1]["reason"] == reason
    assert sender.reports[-1] == NEUTRAL_REPORT
    rows = read_rows(root / "safety.jsonl")
    assert [row["phase"] for row in rows] == ["start", "monitor", "monitor"]
    # 監視の確かめは、前の確かめを送ってから --safety-interval 秒ごと
    sent = [row["t_sent"] for row in rows]
    assert all(b - a >= SAFETY_INTERVAL_S - 0.01 for a, b in itertools.pairwise(sent))


def test_consecutive_safety_failures_stop_everything(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([safety_answer(), RuntimeError("timeout")])
    app = LayersApp(
        safety_settings(tmp_path, max_safety_errors=2), parts(sender, safety=safety)
    )
    reason, root = run_app(app, stop_after=5.0)
    assert reason == "safety check failed 2 times in a row"
    assert read_rows(root / "run.jsonl")[-1]["reason"] == reason
    assert sender.reports[-1] == NEUTRAL_REPORT
    rows = read_rows(root / "safety.jsonl")
    assert [row["phase"] for row in rows] == ["start", "monitor", "monitor"]
    assert [row["probabilities"] is None for row in rows] == [False, True, True]


def test_allow_home_keeps_running_on_the_home_menu(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([safety_answer(home_menu=0.99, user_select=0.99)])
    app = LayersApp(
        safety_settings(tmp_path, allow_home=True), parts(sender, safety=safety)
    )
    reason, root = run_app(app, stop_after=1.5)
    assert reason == REASON_INTERRUPTED
    assert read_rows(root / "surface.jsonl")
    rows = read_rows(root / "safety.jsonl")
    assert rows[0]["phase"] == "start"
    assert len(rows) >= 3
    assert all(row["phase"] == "monitor" for row in rows[1:])


def test_safety_monitor_keeps_checking_while_the_bridge_is_recovering(tmp_path):
    sender = FakeSender()
    safety = ScriptedSafety([safety_answer()])
    app = LayersApp(
        safety_settings(tmp_path),
        parts(sender, bridge_state=lambda: "recovering", safety=safety),
    )
    reason, root = run_app(app, stop_after=1.5)
    assert reason == REASON_INTERRUPTED
    # surface は要求を送らないが、安全の監視は続ける
    assert read_rows(root / "surface.jsonl") == []
    rows = read_rows(root / "safety.jsonl")
    assert len(rows) >= 3
    assert all(row["error"] is None for row in rows)
