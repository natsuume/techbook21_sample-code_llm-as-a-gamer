import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from switch2_layers.deep.deep_job_directory import create_job_directory
from switch2_layers.deep.deep_scheduler import (
    SKIP_CLOSED,
    SKIP_MIN_START_INTERVAL,
    STOP_GRACE_S,
    DeepJobOutcome,
    DeepJobStatus,
    DeepScheduler,
    DeepSchedulerParts,
    DeepSchedulerSettings,
    execute_deep_job,
    run_deep_once,
)
from switch2_layers.deep.deep_types import (
    DOCUMENT_KINDS,
    DeepJob,
    DeepResult,
    DeepTrigger,
    DocumentKey,
    DocumentKind,
)
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

ORIGIN = 1000.0  # 偽の time.monotonic の起点 (t = 0)


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class DeferredThreads:
    """start_job_thread の偽物。ジョブの本体を溜めておき、run_all で呼ぶ (呼ぶまでジョブは実行中のまま)。"""

    def __init__(self):
        self.bodies: list[Callable[[], None]] = []

    def __call__(self, body: Callable[[], None]) -> None:
        self.bodies.append(body)

    def run_all(self) -> None:
        bodies, self.bodies = self.bodies, []
        for body in bodies:
            body()


SUCCESS = DeepResult(ok=True)


STOPPED = DeepResult(ok=False, message="stopped by request", stopped=True)


class FakeRunner:
    """DeepRunner の偽物。受け取ったジョブと stop・kill を記録する。

    run は result を返す (例外なら出す)。ただし、run を呼ぶより前に同じ job_dir の stop が呼ばれていれば、
    STOPPED を返す (止められて戻る runner)。
    """

    def __init__(self, result: DeepResult | Exception = SUCCESS):
        self.result = result
        self.calls: list[tuple[DeepJob, Path]] = []
        self.stopped: list[Path] = []
        self.killed: list[Path] = []

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        self.calls.append((job, job_dir))
        if job_dir in self.stopped:
            return STOPPED
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def stop(self, job_dir: Path) -> None:
        self.stopped.append(job_dir)

    def kill(self, job_dir: Path) -> None:
        self.killed.append(job_dir)


@dataclass
class Harness:
    root: Path
    monotonic: FakeMonotonic
    scheduler: DeepScheduler
    threads: DeferredThreads
    runner: FakeRunner
    scene: dict[str, str | None] = field(default_factory=lambda: {"id": None})
    latest: dict[str, float | None] = field(default_factory=lambda: {"t": None})
    # close の待ち (sleep) に渡された秒数
    sleeps: list[float] = field(default_factory=list)
    # sleep のたびに、偽の時計を進めた後で呼ぶ関数 (止めたジョブの run を戻らせるのに使う)
    on_sleep: list[Callable[[], None]] = field(default_factory=list)
    # progress_state が返す画面の停滞の計測の値
    progress: dict[str, ProgressState | None] = field(
        default_factory=lambda: {"state": None}
    )

    def stuck_for(self, no_novel_s: float | None) -> None:
        """progress_state が、新しい画面が出ていない秒数 no_novel_s の値を返すようにする (None なら値が無い)。"""
        self.progress["state"] = (
            None
            if no_novel_s is None
            else ProgressState(
                t=self.monotonic.now - ORIGIN,
                static_s=0.0,
                no_novel_s=no_novel_s,
                window_s=300.0,
            )
        )

    def at(self, t: float) -> None:
        self.monotonic.now = ORIGIN + t

    def sleep(self, seconds: float) -> None:
        """close の待ちの偽物。実時間では待たず、偽の時計を seconds 進める。"""
        self.sleeps.append(seconds)
        self.monotonic.now += seconds
        for hook in self.on_sleep:
            hook()


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "notes" / "map").mkdir()
    (root / "deep").mkdir()
    (root / "middle").mkdir()
    (root / "notes" / "common" / "controls.md").write_text(
        "# 操作説明\n", encoding="utf-8"
    )
    (root / "notes" / "strategy.md").write_text("# 大方針\n", encoding="utf-8")
    return root


class FailingPreparation:
    """prepare_job_directory の偽物。fail_kinds の種類では例外を出し、それ以外は create_job_directory を呼ぶ。

    呼ばれた種類を calls に記録する。
    """

    def __init__(self, *fail_kinds: DocumentKind):
        self.fail_kinds = set(fail_kinds)
        self.calls: list[DocumentKind] = []

    def __call__(self, paths: RunPaths, job: DeepJob) -> Path:
        self.calls.append(job.kind)
        if job.kind in self.fail_kinds:
            raise OSError(f"cannot prepare {job.kind}")
        return create_job_directory(paths, job)


def make_scheduler(
    tmp_path: Path,
    runner: FakeRunner | None = None,
    min_start_interval_s: float = 15.0,
    auto_seconds: float = 60.0,
    timeout_s: float = 300.0,
    prepare_job_directory: Callable[[RunPaths, DeepJob], Path] = create_job_directory,
    start_job_thread: Callable[[Callable[[], None]], None] | None = None,
    stuck_advice_seconds: float = 60.0,
) -> Harness:
    """DeepScheduler と偽の部品を組み立てる (テストの本体から呼ぶ)。スケジューラーは t = 0 に作る。

    ジョブの本体は DeferredThreads に溜め (start_job_thread を渡せば、それを使う)、close の待ちは
    Harness.sleep (偽の時計を進める) にする。stop_grace_s は既定の 5 秒のまま。
    """
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN)
    clock = RunClock(monotonic, origin=ORIGIN)
    threads = DeferredThreads()
    runner = runner or FakeRunner()
    scene: dict[str, str | None] = {"id": None}
    latest: dict[str, float | None] = {"t": None}
    sleeps: list[float] = []
    on_sleep: list[Callable[[], None]] = []
    progress: dict[str, ProgressState | None] = {"state": None}
    holder: list[Harness] = []
    parts = DeepSchedulerParts(
        paths=paths,
        runner=runner,
        clock=clock,
        run_log=RunLog(paths.run_log, clock),
        current_scene_id=lambda: scene["id"],
        latest_frame_t=lambda: latest["t"],
        start_job_thread=start_job_thread or threads,
        sleep=lambda seconds: holder[0].sleep(seconds),
        prepare_job_directory=prepare_job_directory,
        progress_state=lambda: progress["state"],
    )
    settings = DeepSchedulerSettings(
        min_start_interval_s=min_start_interval_s,
        auto_seconds=auto_seconds,
        timeout_s=timeout_s,
        stuck_advice_seconds=stuck_advice_seconds,
    )
    harness = Harness(
        root,
        monotonic,
        DeepScheduler(parts, settings),
        threads,
        runner,
        scene,
        latest,
        sleeps,
        on_sleep,
        progress,
    )
    holder.append(harness)
    return harness


def run_rows(root: Path, event: str | None = None) -> list[dict]:
    path = root / "run.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if event is None or row["event"] == event]


def job_json(job_dir: Path) -> dict:
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


# --- ジョブの実行の結果 ---


def test_execute_deep_job_statuses(tmp_path):
    job = DeepJob(DocumentKind.ADVICE, None, DeepTrigger.MANUAL, 1.0, None, None)
    assert execute_deep_job(
        FakeRunner(DeepResult(ok=True, message="done")), job, tmp_path
    ) == (DeepJobOutcome(DeepJobStatus.OK, "done"))
    assert execute_deep_job(
        FakeRunner(DeepResult(ok=False, message="no")), job, tmp_path
    ) == (DeepJobOutcome(DeepJobStatus.FAILED, "no"))
    outcome = execute_deep_job(
        FakeRunner(RuntimeError("claude crashed")), job, tmp_path
    )
    assert outcome.status is DeepJobStatus.ERROR
    assert "claude crashed" in outcome.message
    # 止められて戻った結果は、ok が False でも FAILED ではなく STOPPED
    assert execute_deep_job(FakeRunner(STOPPED), job, tmp_path) == (
        DeepJobOutcome(DeepJobStatus.STOPPED, "stopped by request")
    )


def test_deep_job_statuses_distinguish_stop_and_kill():
    assert {status.value for status in DeepJobStatus} == {
        "ok",
        "failed",
        "error",
        "stopped",
        "killed",
    }
    assert STOP_GRACE_S == 5.0
    assert DeepSchedulerSettings(1.0, 1.0, 1.0).stop_grace_s == STOP_GRACE_S


# --- 起動と記録 ---


def test_request_starts_job_in_another_thread_and_records_start_and_end(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(3.0)
    harness.latest["t"] = 2.5
    result = harness.scheduler.request(
        DocumentKind.CONTROLS, DeepTrigger.SURFACE, "scene-001"
    )
    assert result.started is True
    assert result.job_dir == harness.root / "deep" / "controls-001"
    value = job_json(result.job_dir)
    assert value["kind"] == "controls"
    assert value["scene_id"] == "scene-001"
    assert value["trigger"] == "surface"
    assert value["requested_t"] == 3.0
    assert value["latest_frame_t"] == 2.5
    assert value["previous_latest_frame_t"] is None
    # 本体は別スレッドで動くため、まだ DeepRunner.run は呼ばれていない
    assert harness.runner.calls == []
    assert harness.scheduler.is_running(DocumentKey(DocumentKind.CONTROLS, "scene-001"))
    assert run_rows(harness.root, "deep_start") == [
        {
            "t": 3.0,
            "event": "deep_start",
            "kind": "controls",
            "scene_id": "scene-001",
            "trigger": "surface",
            "job": "deep/controls-001",
        }
    ]
    harness.at(4.0)
    harness.threads.run_all()
    assert [job_dir for _, job_dir in harness.runner.calls] == [result.job_dir]
    assert not harness.scheduler.is_running(
        DocumentKey(DocumentKind.CONTROLS, "scene-001")
    )
    end = run_rows(harness.root, "deep_end")
    assert len(end) == 1
    assert end[0]["t"] == 4.0
    assert end[0]["job"] == "deep/controls-001"
    assert end[0]["status"] == "ok"
    assert end[0]["stop_reason"] is None
    assert harness.runner.stopped == []
    assert harness.runner.killed == []


@pytest.mark.parametrize(
    "result, status",
    [
        (DeepResult(ok=False, message="could not update"), "failed"),
        (RuntimeError("claude crashed"), "error"),
    ],
)
def test_job_failures_are_recorded_in_deep_end(tmp_path, result, status):
    harness = make_scheduler(tmp_path, FakeRunner(result))
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    end = run_rows(harness.root, "deep_end")
    assert end[0]["status"] == status
    assert end[0]["message"]


def test_scene_is_ignored_for_scene_independent_documents(tmp_path):
    harness = make_scheduler(tmp_path)
    result = harness.scheduler.request(
        DocumentKind.STRATEGY, DeepTrigger.SURFACE, "scene-001"
    )
    assert result.job_dir is not None
    assert job_json(result.job_dir)["scene_id"] is None


def test_previous_latest_frame_time_comes_from_previous_job_of_same_document(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0)
    harness.latest["t"] = 3.0
    harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    # 1 つ目のジョブが正常に終わる (run.jsonl に deep_end の status ok を書く)
    harness.threads.run_all()
    harness.latest["t"] = 8.0
    second = harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    other = harness.scheduler.request(
        DocumentKind.TIPS, DeepTrigger.SURFACE, "scene-001"
    )
    assert second.job_dir is not None and other.job_dir is not None
    assert job_json(second.job_dir)["previous_latest_frame_t"] == 3.0
    assert job_json(second.job_dir)["latest_frame_t"] == 8.0
    # 別の文書 (場面の文書) の前回は無い
    assert job_json(other.job_dir)["previous_latest_frame_t"] is None


def test_previous_latest_frame_time_skips_running_jobs(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0)
    harness.latest["t"] = 3.0
    harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    harness.latest["t"] = 8.0
    harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    # 2 つ目はまだ実行中なので、3 つ目の前回は、正常に終わった 1 つ目の時刻
    harness.latest["t"] = 12.0
    third = harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    assert third.job_dir is not None
    assert job_json(third.job_dir)["previous_latest_frame_t"] == 3.0


@pytest.mark.parametrize(
    "result",
    [
        DeepResult(ok=False, message="could not update"),
        RuntimeError("claude crashed"),
        STOPPED,
    ],
    ids=["failed", "error", "stopped"],
)
def test_previous_latest_frame_time_skips_jobs_that_did_not_end_normally(
    tmp_path, result
):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0)
    harness.latest["t"] = 3.0
    harness.scheduler.request(DocumentKind.STRATEGY, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    harness.runner.result = result
    harness.latest["t"] = 8.0
    harness.scheduler.request(DocumentKind.STRATEGY, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    harness.latest["t"] = 12.0
    third = harness.scheduler.request(DocumentKind.STRATEGY, DeepTrigger.SURFACE, None)
    assert third.job_dir is not None
    # 正常に終わらなかった 2 つ目に渡した期間 (3.0 から 8.0) は、3 つ目が読み直す
    assert job_json(third.job_dir)["previous_latest_frame_t"] == 3.0


def test_previous_latest_frame_time_skips_killed_jobs(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0, timeout_s=10.0)
    harness.latest["t"] = 3.0
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    harness.latest["t"] = 8.0
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    # 2 つ目はタイムアウトで止め、戻らないまま kill する
    harness.at(10.0)
    harness.scheduler.check_timeouts()
    harness.at(15.0)
    harness.scheduler.check_timeouts()
    assert [row["status"] for row in run_rows(harness.root, "deep_end")] == [
        "ok",
        "killed",
    ]
    harness.latest["t"] = 12.0
    third = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    assert third.job_dir is not None
    assert job_json(third.job_dir)["previous_latest_frame_t"] == 3.0


# --- 並行の起動と最小間隔 ---


def test_same_document_runs_in_parallel_after_min_interval(tmp_path):
    harness = make_scheduler(tmp_path)
    first = harness.scheduler.request(
        DocumentKind.CONTROLS, DeepTrigger.SURFACE, "scene-001"
    )
    harness.at(15.0)
    # 1 つ目がまだ実行中でも、最小間隔がたてば起動する
    second = harness.scheduler.request(
        DocumentKind.CONTROLS, DeepTrigger.SURFACE, "scene-001"
    )
    assert first.started and second.started
    assert second.job_dir == harness.root / "deep" / "controls-002"
    assert len(harness.threads.bodies) == 2
    harness.threads.run_all()
    assert len(run_rows(harness.root, "deep_end")) == 2


def test_request_within_min_interval_is_skipped_and_recorded(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.scheduler.request(DocumentKind.CONTROLS, DeepTrigger.SURFACE, "scene-001")
    harness.at(10.0)
    result = harness.scheduler.request(
        DocumentKind.CONTROLS, DeepTrigger.NEW_SCENE, "scene-001"
    )
    assert result.started is False
    assert result.job_dir is None
    assert result.reason == SKIP_MIN_START_INTERVAL
    assert not (harness.root / "deep" / "controls-002").exists()
    skipped = run_rows(harness.root, "deep_skipped")
    assert skipped == [
        {
            "t": 10.0,
            "event": "deep_skipped",
            "kind": "controls",
            "scene_id": "scene-001",
            "trigger": "new_scene",
            "reason": "min_start_interval",
            "since_last_start_s": 10.0,
        }
    ]


def test_min_interval_counts_from_start_not_end(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(1.0)
    harness.threads.run_all()
    harness.at(14.9)
    assert (
        harness.scheduler.request(
            DocumentKind.ADVICE, DeepTrigger.SURFACE, None
        ).started
        is False
    )
    harness.at(15.0)
    assert (
        harness.scheduler.request(
            DocumentKind.ADVICE, DeepTrigger.SURFACE, None
        ).started
        is True
    )


def test_different_documents_are_independent(tmp_path):
    harness = make_scheduler(tmp_path)
    scene = harness.scheduler.request(
        DocumentKind.CONTROLS, DeepTrigger.SURFACE, "scene-001"
    )
    common = harness.scheduler.request(DocumentKind.CONTROLS, DeepTrigger.SURFACE, None)
    tips = harness.scheduler.request(
        DocumentKind.TIPS, DeepTrigger.SURFACE, "scene-001"
    )
    assert scene.started and common.started and tips.started
    assert common.job_dir == harness.root / "deep" / "controls-002"
    assert tips.job_dir == harness.root / "deep" / "tips-001"


# --- 自動の呼び出し ---


def started_kinds(results) -> list[str]:
    return [job_json(result.job_dir)["kind"] for result in results if result.started]


def test_auto_starts_all_documents_after_auto_seconds_from_start(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(59.9)
    assert harness.scheduler.check_auto() == []
    harness.at(60.0)
    results = harness.scheduler.check_auto()
    assert started_kinds(results) == [kind.value for kind in DOCUMENT_KINDS]
    for result in results:
        value = job_json(result.job_dir)
        assert value["trigger"] == "auto"
        assert value["scene_id"] is None


def test_auto_does_not_start_while_job_is_running(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(60.0)
    harness.scheduler.check_auto()
    harness.at(500.0)
    # どの文書のジョブもまだ実行中
    assert harness.scheduler.check_auto() == []


def test_auto_waits_auto_seconds_after_previous_end(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(5.0)
    harness.scheduler.request(DocumentKind.STRATEGY, DeepTrigger.SURFACE, None)
    harness.at(70.0)
    harness.threads.run_all()
    harness.scheduler.check_auto()
    harness.threads.run_all()
    # strategy は 70.0 に終わったので、130.0 まで自動では呼ばない
    harness.at(129.9)
    assert "strategy" not in started_kinds(harness.scheduler.check_auto())
    harness.at(130.0)
    assert "strategy" in started_kinds(harness.scheduler.check_auto())


def test_auto_counts_from_when_scene_document_became_target(tmp_path):
    harness = make_scheduler(tmp_path, auto_seconds=60.0)
    harness.at(30.0)
    harness.scheduler.check_auto()
    harness.scene["id"] = "scene-001"
    harness.at(40.0)
    harness.scheduler.check_auto()
    harness.at(99.9)
    results = harness.scheduler.check_auto()
    started = [
        (job_json(r.job_dir)["kind"], job_json(r.job_dir)["scene_id"]) for r in results
    ]
    # 場面の文書は 40.0 に対象になったので、まだ呼ばない
    assert ("controls", "scene-001") not in started
    harness.at(100.0)
    results = harness.scheduler.check_auto()
    started = [
        (job_json(r.job_dir)["kind"], job_json(r.job_dir)["scene_id"]) for r in results
    ]
    assert ("controls", "scene-001") in started
    assert ("tips", "scene-001") in started


def test_auto_respects_min_interval(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=15.0, auto_seconds=5.0)
    harness.at(1.0)
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(2.0)
    harness.threads.run_all()
    harness.at(7.0)
    results = harness.scheduler.check_auto()
    advice = [r for r in results if r.reason == SKIP_MIN_START_INTERVAL]
    assert len(advice) == 1
    assert not (harness.root / "deep" / "advice-002").exists()


# --- 起動の失敗 ---


def test_request_failure_is_raised_without_starting(tmp_path):
    harness = make_scheduler(
        tmp_path, prepare_job_directory=FailingPreparation(DocumentKind.TIPS)
    )
    with pytest.raises(OSError, match="cannot prepare tips"):
        harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    assert not harness.scheduler.is_running(DocumentKey(DocumentKind.TIPS, None))
    assert run_rows(harness.root, "deep_start") == []
    assert harness.threads.bodies == []


def test_failed_preparation_does_not_count_for_min_interval(tmp_path):
    preparation = FailingPreparation(DocumentKind.TIPS)
    harness = make_scheduler(tmp_path, prepare_job_directory=preparation)
    harness.at(1.0)
    with pytest.raises(OSError):
        harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    preparation.fail_kinds.clear()
    harness.at(2.0)
    # 起動できなかった要求は、最小間隔の起点にしない
    result = harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    assert result.started is True


def test_auto_continues_with_later_kinds_after_a_kind_fails(tmp_path):
    harness = make_scheduler(
        tmp_path, prepare_job_directory=FailingPreparation(DocumentKind.TIPS)
    )
    harness.at(60.0)
    results = harness.scheduler.check_auto()
    # tips の後ろの種類 (strategy・advice・map) も判定して起動する
    assert started_kinds(results) == ["controls", "strategy", "advice", "map"]
    errors = run_rows(harness.root, "deep_request_error")
    assert len(errors) == 1
    assert errors[0]["t"] == 60.0
    assert errors[0]["source"] == "auto"
    assert errors[0]["kind"] == "tips"
    assert errors[0]["trigger"] == "auto"
    assert errors[0]["scene_id"] is None
    assert "cannot prepare tips" in errors[0]["error"]
    assert not (harness.root / "deep" / "tips-001").exists()


def test_failed_document_is_not_retried_until_auto_seconds_after_failure(tmp_path):
    preparation = FailingPreparation(DocumentKind.TIPS)
    harness = make_scheduler(tmp_path, prepare_job_directory=preparation)
    harness.at(60.0)
    harness.scheduler.check_auto()
    assert preparation.calls.count(DocumentKind.TIPS) == 1
    preparation.fail_kinds.clear()
    # 失敗した時刻 (60.0) から auto_seconds の間は、判定の周期ごとに同じ失敗を繰り返さない
    for t in [60.5, 90.0, 119.9]:
        harness.at(t)
        assert harness.scheduler.check_auto() == []
    assert preparation.calls.count(DocumentKind.TIPS) == 1
    assert len(run_rows(harness.root, "deep_request_error")) == 1
    harness.at(120.0)
    assert started_kinds(harness.scheduler.check_auto()) == ["tips"]
    assert (harness.root / "deep" / "tips-001").is_dir()


def test_thread_start_failure_is_recorded_and_not_retried_too_soon(tmp_path):
    started: list[Callable[[], None]] = []

    def failing_start(body: Callable[[], None]) -> None:
        started.append(body)
        raise RuntimeError("cannot start thread")

    harness = make_scheduler(tmp_path, start_job_thread=failing_start)
    harness.at(60.0)
    assert harness.scheduler.check_auto() == []
    assert len(started) == len(DOCUMENT_KINDS)
    # deep_start と対応する deep_end を status error で書き、実行中に数えない
    ends = run_rows(harness.root, "deep_end")
    assert [row["status"] for row in ends] == ["error"] * len(DOCUMENT_KINDS)
    assert all("cannot start thread" in row["message"] for row in ends)
    assert len(run_rows(harness.root, "deep_start")) == len(DOCUMENT_KINDS)
    assert len(run_rows(harness.root, "deep_request_error")) == len(DOCUMENT_KINDS)
    for kind in DOCUMENT_KINDS:
        assert not harness.scheduler.is_running(DocumentKey(kind, None))
    harness.at(119.9)
    harness.scheduler.check_auto()
    assert len(started) == len(DOCUMENT_KINDS)
    harness.at(120.0)
    harness.scheduler.check_auto()
    assert len(started) == 2 * len(DOCUMENT_KINDS)


# --- 終了 ---


def test_requests_after_close_are_skipped(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.scheduler.close()
    result = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    assert result.started is False
    assert result.reason == SKIP_CLOSED
    assert run_rows(harness.root, "deep_skipped")[0]["reason"] == "closed"
    harness.at(1000.0)
    assert harness.scheduler.check_auto() == []
    assert list((harness.root / "deep").iterdir()) == []
    harness.scheduler.close()
    # 実行中のジョブが無ければ待たない
    assert harness.sleeps == []


def test_close_stops_running_job_and_does_not_kill_when_run_returns(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(3.0)
    result = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    # close の待ちの間に、止められたジョブの run が戻る
    harness.on_sleep.append(harness.threads.run_all)
    harness.scheduler.close()
    assert harness.runner.stopped == [result.job_dir]
    assert harness.runner.killed == []
    assert 0 < sum(harness.sleeps) < STOP_GRACE_S
    stop = run_rows(harness.root, "deep_stop")
    assert stop == [
        {
            "t": 3.0,
            "event": "deep_stop",
            "kind": "advice",
            "scene_id": None,
            "trigger": "surface",
            "job": "deep/advice-001",
            "reason": "close",
        }
    ]
    end = run_rows(harness.root, "deep_end")
    assert len(end) == 1
    assert end[0]["status"] == "stopped"
    assert end[0]["stop_reason"] == "close"
    assert not harness.scheduler.is_running(DocumentKey(DocumentKind.ADVICE, None))


def test_close_kills_jobs_that_do_not_return_within_grace(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0)
    first = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    second = harness.scheduler.request(DocumentKind.MAP, DeepTrigger.SURFACE, None)
    # ジョブの本体を呼ばない (run が戻らない) まま close する
    harness.scheduler.close()
    assert harness.runner.stopped == [first.job_dir, second.job_dir]
    assert harness.runner.killed == [first.job_dir, second.job_dir]
    # 偽の時計で 5 秒待ってから kill した
    assert round(sum(harness.sleeps), 3) >= STOP_GRACE_S
    end = run_rows(harness.root, "deep_end")
    assert [row["status"] for row in end] == ["killed", "killed"]
    assert [row["stop_reason"] for row in end] == ["close", "close"]
    assert end[0]["t"] >= STOP_GRACE_S
    # kill の後に run が戻っても、deep_end を書き直さない
    harness.threads.run_all()
    assert len(run_rows(harness.root, "deep_end")) == 2


# --- タイムアウト ---


def test_timeout_stops_job_then_kills_after_grace(tmp_path):
    harness = make_scheduler(tmp_path, timeout_s=300.0)
    harness.at(10.0)
    result = harness.scheduler.request(DocumentKind.STRATEGY, DeepTrigger.AUTO, None)
    key = DocumentKey(DocumentKind.STRATEGY, None)
    harness.at(309.9)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == []
    harness.at(310.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == [result.job_dir]
    assert harness.runner.killed == []
    assert run_rows(harness.root, "deep_stop")[0]["reason"] == "timeout"
    assert harness.scheduler.is_running(key)
    # stop を呼び直さない
    harness.at(312.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == [result.job_dir]
    harness.at(314.9)
    harness.scheduler.check_timeouts()
    assert harness.runner.killed == []
    harness.at(315.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.killed == [result.job_dir]
    assert not harness.scheduler.is_running(key)
    end = run_rows(harness.root, "deep_end")
    assert len(end) == 1
    assert end[0]["t"] == 315.0
    assert end[0]["status"] == "killed"
    assert end[0]["stop_reason"] == "timeout"
    # kill の後に run が戻っても、deep_end を書き直さない
    harness.threads.run_all()
    assert len(run_rows(harness.root, "deep_end")) == 1


def test_timeout_does_not_kill_when_run_returns_after_stop(tmp_path):
    harness = make_scheduler(tmp_path, timeout_s=300.0)
    result = harness.scheduler.request(DocumentKind.TIPS, DeepTrigger.SURFACE, None)
    harness.at(300.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == [result.job_dir]
    harness.at(301.0)
    harness.threads.run_all()
    harness.at(400.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.killed == []
    end = run_rows(harness.root, "deep_end")
    assert len(end) == 1
    assert end[0]["t"] == 301.0
    assert end[0]["status"] == "stopped"
    assert end[0]["stop_reason"] == "timeout"


def test_timeout_is_measured_per_job_from_its_start(tmp_path):
    harness = make_scheduler(tmp_path, min_start_interval_s=0.0, timeout_s=300.0)
    first = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(100.0)
    second = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(300.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == [first.job_dir]
    harness.at(400.0)
    harness.scheduler.check_timeouts()
    assert harness.runner.stopped == [first.job_dir, second.job_dir]


def test_finished_jobs_are_not_stopped(tmp_path):
    harness = make_scheduler(tmp_path, timeout_s=300.0)
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(10.0)
    harness.threads.run_all()
    harness.at(1000.0)
    harness.scheduler.check_timeouts()
    harness.scheduler.close()
    assert harness.runner.stopped == []
    assert harness.runner.killed == []
    assert harness.sleeps == []


def test_close_waits_for_job_already_stopped_by_timeout(tmp_path):
    harness = make_scheduler(tmp_path, timeout_s=300.0)
    result = harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(300.0)
    harness.scheduler.check_timeouts()
    harness.scheduler.close()
    # タイムアウトで止めたジョブに stop を呼び直さず、kill する
    assert harness.runner.stopped == [result.job_dir]
    assert harness.runner.killed == [result.job_dir]
    end = run_rows(harness.root, "deep_end")
    assert [row["stop_reason"] for row in end] == ["timeout"]
    assert [row["status"] for row in end] == ["killed"]


def test_job_that_reports_stopped_by_itself_is_recorded_as_stopped(tmp_path):
    harness = make_scheduler(tmp_path, FakeRunner(STOPPED))
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.threads.run_all()
    end = run_rows(harness.root, "deep_end")
    assert end[0]["status"] == "stopped"
    assert end[0]["stop_reason"] is None


# --- deep-once ---


def write_frames(root: Path, times: list[float]) -> None:
    text = "".join(
        json.dumps(
            {
                "t": t,
                "kind": "interval",
                "file": f"frames/interval/{round(t * 1000):09d}.jpg",
                "width": 64,
                "height": 36,
            }
        )
        + "\n"
        for t in times
    )
    (root / "frames.jsonl").write_text(text, encoding="utf-8")


def test_run_deep_once_runs_manual_job_synchronously(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [1.0, 6.5])
    runner = FakeRunner(DeepResult(ok=True, message="ok"))
    result = run_deep_once(root, DocumentKind.STRATEGY, None, runner)
    assert result.job == "deep/strategy-001"
    assert result.job_dir == root / "deep" / "strategy-001"
    assert result.outcome == DeepJobOutcome(DeepJobStatus.OK, "ok")
    assert len(runner.calls) == 1
    value = job_json(result.job_dir)
    assert value["trigger"] == "manual"
    assert value["requested_t"] == 6.5
    assert value["latest_frame_t"] == 6.5
    assert not (root / "run.jsonl").exists()


def test_run_deep_once_uses_current_scene_for_controls(tmp_path):
    root = make_run(tmp_path)
    (root / "middle" / "state.json").write_text(
        json.dumps({"scene_id": "scene-003", "surface_wait_s": 0.2}), encoding="utf-8"
    )
    result = run_deep_once(root, DocumentKind.CONTROLS, None, FakeRunner())
    value = job_json(result.job_dir)
    assert value["scene_id"] == "scene-003"
    assert value["requested_t"] == 0.0
    assert value["latest_frame_t"] is None
    explicit = run_deep_once(root, DocumentKind.CONTROLS, "scene-001", FakeRunner())
    assert job_json(explicit.job_dir)["scene_id"] == "scene-001"
    assert explicit.job == "deep/controls-002"


def test_run_deep_once_without_scene_uses_common_document(tmp_path):
    root = make_run(tmp_path)
    result = run_deep_once(root, DocumentKind.TIPS, None, FakeRunner())
    assert job_json(result.job_dir)["scene_id"] is None
    assert job_json(result.job_dir)["documents"] == ["common/tips.md"]


def test_run_deep_once_errors(tmp_path):
    root = make_run(tmp_path)
    with pytest.raises(FileNotFoundError):
        run_deep_once(tmp_path / "missing", DocumentKind.ADVICE, None, FakeRunner())
    with pytest.raises(ValueError):
        run_deep_once(root, DocumentKind.CONTROLS, "../x", FakeRunner())


# --- 画面の停滞の計測による advice の要求 (きっかけ progress) ---

ADVICE_KEY = DocumentKey(DocumentKind.ADVICE, None)


def test_progress_settings_follow_the_issue():
    assert DeepTrigger.PROGRESS.value == "progress"
    assert DeepSchedulerSettings(1.0, 1.0, 1.0).stuck_advice_seconds == 60.0


def test_check_progress_without_value_requests_nothing(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(500.0)
    assert harness.scheduler.check_progress() is None
    assert run_rows(harness.root) == []
    assert list((harness.root / "deep").iterdir()) == []


def test_check_progress_requests_advice_when_no_novel_exceeds_the_threshold(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.scene["id"] = "scene-001"
    harness.latest["t"] = 99.8
    harness.at(100.0)
    # ちょうどしきい値では要求しない
    harness.stuck_for(60.0)
    assert harness.scheduler.check_progress() is None
    harness.stuck_for(60.5)
    result = harness.scheduler.check_progress()
    assert result is not None and result.started is True
    assert result.job_dir == harness.root / "deep" / "advice-001"
    value = job_json(result.job_dir)
    assert value["kind"] == "advice"
    assert value["trigger"] == "progress"
    # advice は場面に依らない
    assert value["scene_id"] is None
    assert value["latest_frame_t"] == 99.8
    assert run_rows(harness.root, "deep_start") == [
        {
            "t": 100.0,
            "event": "deep_start",
            "kind": "advice",
            "scene_id": None,
            "trigger": "progress",
            "job": "deep/advice-001",
        }
    ]
    assert harness.scheduler.is_running(ADVICE_KEY)


def test_check_progress_requests_again_every_threshold_seconds(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(100.0)
    harness.stuck_for(61.0)
    assert harness.scheduler.check_progress().started is True
    harness.at(101.0)
    harness.threads.run_all()
    for t in [101.5, 130.0, 159.9]:
        harness.at(t)
        harness.stuck_for(t - 39.0)
        assert harness.scheduler.check_progress() is None
    harness.at(160.0)
    harness.stuck_for(121.0)
    second = harness.scheduler.check_progress()
    assert second is not None and second.started is True
    assert second.job_dir == harness.root / "deep" / "advice-002"
    assert job_json(second.job_dir)["trigger"] == "progress"


def test_check_progress_waits_for_running_advice_job_of_any_trigger(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(90.0)
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.stuck_for(61.0)
    for t in [100.0, 130.0]:
        harness.at(t)
        assert harness.scheduler.check_progress() is None
    # 実行中のジョブが終わった後の判定で要求する
    harness.threads.run_all()
    harness.at(130.5)
    result = harness.scheduler.check_progress()
    assert result is not None and result.started is True
    assert job_json(result.job_dir)["trigger"] == "progress"
    assert [row["trigger"] for row in run_rows(harness.root, "deep_start")] == [
        "surface",
        "progress",
    ]


def test_check_progress_keeps_the_min_start_interval(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.at(95.0)
    harness.scheduler.request(DocumentKind.ADVICE, DeepTrigger.SURFACE, None)
    harness.at(96.0)
    harness.threads.run_all()
    harness.at(100.0)
    harness.stuck_for(61.0)
    result = harness.scheduler.check_progress()
    # 同じ文書の前回の起動から 15 秒たっていないため、要求を捨てる
    assert result is not None
    assert result.started is False
    assert result.reason == SKIP_MIN_START_INTERVAL
    assert run_rows(harness.root, "deep_skipped") == [
        {
            "t": 100.0,
            "event": "deep_skipped",
            "kind": "advice",
            "scene_id": None,
            "trigger": "progress",
            "reason": "min_start_interval",
            "since_last_start_s": 5.0,
        }
    ]
    # 捨てた要求も「要求した」に数え、判定のたびには出し直さない
    harness.at(110.0)
    assert harness.scheduler.check_progress() is None
    assert len(run_rows(harness.root, "deep_skipped")) == 1
    harness.at(160.0)
    assert harness.scheduler.check_progress().started is True


def test_check_progress_requests_at_once_after_returning_below_the_threshold(
    tmp_path,
):
    harness = make_scheduler(tmp_path)
    harness.at(100.0)
    harness.stuck_for(61.0)
    assert harness.scheduler.check_progress().started is True
    harness.threads.run_all()
    # 新しい画面が出た
    harness.at(110.0)
    harness.stuck_for(0.0)
    assert harness.scheduler.check_progress() is None
    # 前回の要求から 60 秒たっていなくても、次に超えたらすぐ要求する
    harness.at(120.0)
    harness.stuck_for(60.2)
    result = harness.scheduler.check_progress()
    assert result is not None and result.started is True


def test_check_progress_records_request_errors_and_does_not_retry_at_once(tmp_path):
    preparation = FailingPreparation(DocumentKind.ADVICE)
    harness = make_scheduler(tmp_path, prepare_job_directory=preparation)
    harness.at(100.0)
    harness.stuck_for(61.0)
    assert harness.scheduler.check_progress() is None
    errors = run_rows(harness.root, "deep_request_error")
    assert len(errors) == 1
    assert errors[0]["t"] == 100.0
    assert errors[0]["source"] == "progress"
    assert errors[0]["kind"] == "advice"
    assert errors[0]["trigger"] == "progress"
    assert errors[0]["scene_id"] is None
    assert "cannot prepare advice" in errors[0]["error"]
    harness.at(100.5)
    assert harness.scheduler.check_progress() is None
    assert preparation.calls == [DocumentKind.ADVICE]


def test_check_progress_does_nothing_after_close(tmp_path):
    harness = make_scheduler(tmp_path)
    harness.scheduler.close()
    harness.at(100.0)
    harness.stuck_for(61.0)
    assert harness.scheduler.check_progress() is None
    assert run_rows(harness.root, "deep_skipped") == []
    assert list((harness.root / "deep").iterdir()) == []


def test_check_progress_uses_the_configured_threshold(tmp_path):
    harness = make_scheduler(tmp_path, stuck_advice_seconds=5.0)
    harness.at(10.0)
    harness.stuck_for(5.5)
    result = harness.scheduler.check_progress()
    assert result is not None and result.started is True
