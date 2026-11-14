import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.controller_bridge import BridgeStartError
from switch2_remote.controller_state import PadState

from switch2_agent import run
from switch2_agent.action_decision import Thresholds
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.dashboard_records import PadReport
from switch2_agent.decision_models import jev
from switch2_agent.decision_request import (
    DecisionRequest,
    JpegCache,
    LabeledImage,
    build_instructions,
)
from switch2_agent.frame_history import TimedFrame
from switch2_agent.game_notes import NOTE_KINDS
from switch2_agent.operation_log import DEFAULT_OPERATION_LOG_CAPACITY
from switch2_agent.questions import build_questions
from switch2_agent.request_log import RequestLog, RequestLogEntry
from switch2_agent.timeline.builder import (
    SECTION_NAMES,
    TimelineBuilder,
    TimelineSettings,
)

REQUIRED = ["--game", "zelda", "--model", "jev"]
TEST_API_KEY = "test-key-not-a-secret"
# 偽の Pipeline の started_at (経過秒の起点の time.monotonic の値)
PIPELINE_STARTED_AT = 12.5


class Recorder:
    """偽の部品の生成と close の順を記録する。"""

    def __init__(self):
        self.events: list[str] = []
        self.bridge_error: Exception | None = None
        self.pipeline_error: BaseException | None = None
        # 役割の name → 作った偽の advisor (作った順)
        self.advisors: dict = {}
        # この役割の advisor を作るときに送出する例外 (役割の name → 例外)
        self.advisor_errors: dict[str, Exception] = {}


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """run.main が使う部品を偽物に差し替え、tmp_path を ch04_techniques に見立てた作業ディレクトリーにする。

    偽物は本物と同じ引数だけを受け取り、ほかの引数を渡すと TypeError になる。
    偽の Pipeline の run は、rec.pipeline_error があればそれを送出し、無ければすぐ戻る。
    偽の Pipeline の min_request_interval_s は、渡されなければ None のまま残す (run.main が明示的に渡したかを
    確かめるため。本物の既定値は 0.0)。偽の Pipeline の started_at は PIPELINE_STARTED_AT。
    """
    rec = Recorder()
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", TEST_API_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeSupervisor:
        def __init__(self, start_bridge, reset_radio):
            if rec.bridge_error is not None:
                raise rec.bridge_error
            self.start_bridge = start_bridge
            self.reset_radio = reset_radio
            self.state = "ok"
            rec.supervisor = self
            rec.events.append("supervisor created")

        def close(self):
            rec.events.append("supervisor closed")

    class FakePadOutput:
        def __init__(self, sender, on_report=None):
            self.sender = sender
            self.on_report = on_report
            rec.output = self
            rec.events.append("output created")

        def neutral(self):
            rec.events.append("output neutral")

        def stop_output(self):
            rec.events.append("output stopped")

        def close(self):
            rec.events.append("output closed")

    class FakeCapture:
        def __init__(self, ffmpeg_fps, process_fps, history, on_frame):
            self.rates = (ffmpeg_fps, process_fps)
            self.history = history
            self.on_frame = on_frame
            self.ended = False
            rec.capture = self
            rec.events.append("capture created")

        def close(self):
            rec.events.append("capture closed")

    class FakeCaptureWatcher:
        def __init__(self, capture_ended, latest_frame_time, on_lost, stale_timeout_s):
            self.capture_ended = capture_ended
            self.latest_frame_time = latest_frame_time
            self.on_lost = on_lost
            self.stale_timeout_s = stale_timeout_s
            self.lost_reason: str | None = None
            rec.watcher = self
            rec.events.append("watcher created")

        def lost(self):
            return self.lost_reason

        def close(self):
            rec.events.append("watcher closed")

    class FakeAdvisor:
        def __init__(
            self,
            workdir,
            notes_dir,
            runs_dir,
            game,
            role,
            timeout_s,
            hidden_env_names,
            log,
            record_steps=False,
        ):
            if role.name in rec.advisor_errors:
                raise rec.advisor_errors[role.name]
            self.args = (workdir, notes_dir, runs_dir, game, timeout_s)
            self.role = role
            self.hidden_env_names = hidden_env_names
            self.log = log
            self.record_steps = record_steps
            rec.advisors[role.name] = self
            rec.events.append(f"advisor {role.name} created")

        def close(self):
            rec.events.append(f"advisor {self.role.name} closed")

    class FakeModel:
        def __init__(self, api_key):
            self.api_key = api_key
            rec.model = self
            rec.events.append(f"{type(self).__name__} created")

    class FakeJevModel(FakeModel):
        pass

    class FakeDecisionsApiModel(FakeModel):
        pass

    class FakePipeline:
        def __init__(self, parts, max_consecutive_errors, min_request_interval_s=None):
            self.parts = parts
            self.max_consecutive_errors = max_consecutive_errors
            self.min_request_interval_s = min_request_interval_s
            self.started_at = PIPELINE_STARTED_AT
            rec.pipeline = self
            rec.events.append("pipeline created")

        def run(self, stop):
            rec.stop = stop
            rec.events.append("pipeline run")
            if rec.pipeline_error is not None:
                raise rec.pipeline_error

    monkeypatch.setattr(run, "BridgeSupervisor", FakeSupervisor)
    monkeypatch.setattr(run, "PadOutput", FakePadOutput)
    monkeypatch.setattr(run, "CaptureWithHistory", FakeCapture)
    monkeypatch.setattr(run, "CaptureWatcher", FakeCaptureWatcher)
    monkeypatch.setattr(run, "ClaudeCodeHeadlessAdvisor", FakeAdvisor)
    monkeypatch.setattr(run, "JevModel", FakeJevModel)
    monkeypatch.setattr(run, "DecisionsApiModel", FakeDecisionsApiModel)
    monkeypatch.setattr(run, "Pipeline", FakePipeline)
    rec.workdir = tmp_path
    return rec


def created(recorder: Recorder) -> list[str]:
    return [event for event in recorder.events if event.endswith("created")]


def closed(recorder: Recorder) -> list[str]:
    return [event for event in recorder.events if event.endswith(("closed", "neutral"))]


def closed_steps(recorder: Recorder) -> list[str | set[str]]:
    """closed(recorder) のうち、続けて並ぶ advisor の close を 1 つの集合にまとめた列を返す。

    advisor は close_advisors で同時に閉じるため、advisor どうしの順は問わない。
    """
    steps: list[str | set[str]] = []
    for event in closed(recorder):
        if event.startswith("advisor "):
            if steps and isinstance(steps[-1], set):
                steps[-1].add(event)
            else:
                steps.append({event})
        else:
            steps.append(event)
    return steps


ALL_ADVISORS_CLOSED = {f"advisor {role.name} closed" for role in ADVISOR_ROLES}


def frame(time: float, sharpness: float, gray: int) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((1080, 1280), np.uint8),
        gray_small=np.full((180, 320), gray, np.uint8),
        sharpness=sharpness,
    )


# --- main ---


def test_main_starts_parts_in_order(recorder):
    run.main(REQUIRED)
    assert created(recorder) == [
        "supervisor created",
        "output created",
        "capture created",
        "watcher created",
        "advisor controls created",
        "advisor strategy created",
        "advisor advice created",
        "advisor tips created",
        "FakeJevModel created",
        "pipeline created",
    ]
    assert "pipeline run" in recorder.events


def test_main_closes_parts_in_order(recorder):
    run.main(REQUIRED)
    # advisor は output の後、capture の前に、すべてを同時に閉じる
    assert closed_steps(recorder) == [
        "watcher closed",
        "output neutral",
        "output closed",
        ALL_ADVISORS_CLOSED,
        "capture closed",
        "supervisor closed",
    ]


def test_main_builds_supervisor_with_bridge_and_radio_reset(recorder):
    run.main(REQUIRED)
    assert recorder.supervisor.start_bridge is run.Bridge
    assert recorder.supervisor.reset_radio is reset_bluetooth_radio
    assert recorder.output.sender is recorder.supervisor


def test_main_creates_game_notes_and_run_log(recorder):
    run.main(REQUIRED)
    notes_dir = recorder.workdir / "game_notes" / "zelda"
    for kind in NOTE_KINDS:
        assert (notes_dir / f"{kind}.md").is_file()
    run_logs = list((recorder.workdir / "runs").glob("*/run.jsonl"))
    assert len(run_logs) == 1
    events = [
        json.loads(line)["event"]
        for line in run_logs[0].read_text(encoding="utf-8").splitlines()
    ]
    assert events[0] == "start"


def test_main_records_advisor_steps_of_every_role_only_with_record_dashboard(
    recorder,
):
    run.main(REQUIRED)
    assert [advisor.record_steps for advisor in recorder.advisors.values()] == [
        False
    ] * len(ADVISOR_ROLES)
    run.main([*REQUIRED, "--record-dashboard"])
    assert [advisor.record_steps for advisor in recorder.advisors.values()] == [
        True
    ] * len(ADVISOR_ROLES)


def test_main_creates_one_advisor_for_each_role(recorder):
    run.main(REQUIRED)
    assert list(recorder.advisors) == [role.name for role in ADVISOR_ROLES]
    assert [advisor.role for advisor in recorder.advisors.values()] == list(
        ADVISOR_ROLES
    )


def test_main_passes_run_dir_notes_dir_and_timeout_to_every_advisor(recorder):
    run.main([*REQUIRED, "--advisor-timeout", "120"])
    run_dirs = set()
    for advisor in recorder.advisors.values():
        workdir, notes_dir, runs_dir, game, timeout_s = advisor.args
        assert workdir == recorder.workdir
        assert notes_dir == recorder.workdir / "game_notes" / "zelda"
        assert runs_dir.parent == recorder.workdir / "runs"
        assert (game, timeout_s) == ("zelda", 120.0)
        run_dirs.add(runs_dir)
    # 役割どうしで同じ実行のディレクトリーを使う
    assert len(run_dirs) == 1


def test_main_hides_api_keys_from_every_advisor(recorder):
    run.main(REQUIRED)
    assert len(recorder.advisors) == 4
    for advisor in recorder.advisors.values():
        assert set(advisor.hidden_env_names) == {
            "TYPESAFE_API_KEY",
            "OPENAI_API_KEY",
        }


def test_main_closes_advisors_created_before_one_fails(recorder):
    # 3 つ目の役割 (advice) の advisor を作るのに失敗しても、作れた 2 つを閉じる
    recorder.advisor_errors["advice"] = RuntimeError("cannot create advisor")
    with pytest.raises(RuntimeError, match="cannot create advisor"):
        run.main(REQUIRED)
    assert closed_steps(recorder) == [
        "watcher closed",
        "output neutral",
        "output closed",
        {"advisor controls closed", "advisor strategy closed"},
        "capture closed",
        "supervisor closed",
    ]


def test_main_wires_pipeline_parts(recorder):
    run.main(
        [
            *REQUIRED,
            "--threshold-advisor",
            "0.7",
            "--max-consecutive-errors",
            "3",
        ]
    )
    parts = recorder.pipeline.parts
    assert recorder.pipeline.max_consecutive_errors == 3
    assert parts.output is recorder.output
    assert dict(parts.advisors) == recorder.advisors
    assert parts.model is recorder.model
    assert recorder.model.api_key == TEST_API_KEY
    assert parts.history is recorder.capture.history
    assert isinstance(parts.encode, JpegCache)
    assert parts.thresholds == Thresholds(advisor=0.7)
    assert parts.game == "zelda"
    assert parts.questions == build_questions()
    assert set(parts.read_notes()) == set(NOTE_KINDS)


def test_main_passes_bridge_failed_and_capture_ended(recorder):
    run.main(REQUIRED)
    parts = recorder.pipeline.parts
    assert parts.bridge_failed() is False
    recorder.supervisor.state = "recovering"
    assert parts.bridge_failed() is False
    recorder.supervisor.state = "failed"
    assert parts.bridge_failed() is True
    assert parts.capture_ended() is False
    recorder.capture.ended = True
    assert parts.capture_ended() is True


def test_main_watches_capture_and_neutralizes_on_lost(recorder):
    run.main(REQUIRED)
    watcher = recorder.watcher
    assert watcher.stale_timeout_s == 2.0
    assert watcher.capture_ended() is False
    recorder.capture.ended = True
    assert watcher.capture_ended() is True
    # 最新のフレームの time を返す (まだ無ければ None)
    assert watcher.latest_frame_time() is None
    recorder.capture.history.add(frame(3.5, 0.0, gray=0))
    assert watcher.latest_frame_time() == 3.5
    # 止まったと判定したら、以後の apply を止めて押下状態を中立に戻す
    events_before = len(recorder.events)
    watcher.on_lost()
    assert recorder.events[events_before:] == ["output stopped"]


def test_main_passes_capture_lost_from_watcher(recorder):
    run.main(REQUIRED)
    parts = recorder.pipeline.parts
    assert parts.capture_lost() is None
    recorder.watcher.lost_reason = "capture ended"
    assert parts.capture_lost() == "capture ended"


def test_main_passes_bridge_recovering(recorder):
    run.main(REQUIRED)
    parts = recorder.pipeline.parts
    assert parts.bridge_recovering() is False
    recorder.supervisor.state = "recovering"
    assert parts.bridge_recovering() is True
    recorder.supervisor.state = "failed"
    assert parts.bridge_recovering() is False


def test_main_keeps_meaningful_frames_from_capture(recorder):
    run.main(REQUIRED)
    on_frame = recorder.capture.on_frame
    # 鮮明度 200 の山の後に谷 (50) が来て回復 (150) すると、山のフレームが意味のあるフレームになる
    for t, sharpness in enumerate([100, 200, 50, 150]):
        on_frame(frame(float(t), sharpness, gray=10))
    kept = recorder.pipeline.parts.store.latest_before(10.0, 5)
    assert [f.time for f in kept] == [1.0]


def test_main_encodes_frames_with_given_width_and_quality(recorder):
    run.main([*REQUIRED, "--image-width", "640", "--jpeg-quality", "50"])
    jpeg = recorder.pipeline.parts.encode(frame(1.0, 0.0, gray=0))
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (360, 640, 3)


def test_main_without_save_requests_writes_no_request_log(recorder):
    run.main(REQUIRED)
    assert recorder.pipeline.parts.request_log is None
    run_dirs = list((recorder.workdir / "runs").iterdir())
    assert len(run_dirs) == 1
    assert not (run_dirs[0] / "requests.jsonl").exists()
    assert not (run_dirs[0] / "frames").exists()


def test_main_with_save_requests_passes_request_log_and_writes_header(recorder):
    run.main([*REQUIRED, "--save-requests"])
    assert isinstance(recorder.pipeline.parts.request_log, RequestLog)
    run_logs = list((recorder.workdir / "runs").glob("*/run.jsonl"))
    assert len(run_logs) == 1
    # 要求のログは、run.jsonl と同じ実行のディレクトリーに書く
    run_dir = run_logs[0].parent
    assert (run_dir / "frames").is_dir()
    lines = [
        json.loads(line)
        for line in (run_dir / "requests.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(lines) == 1
    header = lines[0]
    assert header["event"] == "header"
    assert header["model"] == "jev"
    assert header["instructions"] == build_instructions()
    assert header["questions"] == [
        {
            "name": q.name,
            "text": q.text,
            "kind": q.kind,
            "answer_type": q.answer_type,
            "choices": [
                {"name": c.name, "description": c.description} for c in q.choices
            ],
        }
        for q in build_questions()
    ]


def test_main_writes_request_log_header_after_creating_pipeline(recorder, monkeypatch):
    class FakeRequestLog:
        def __init__(self, run_dir):
            self.run_dir = run_dir
            recorder.request_log = self
            recorder.events.append("request log opened")

        def write_header(self, model, instructions, questions):
            self.header = (model, instructions, questions)
            recorder.events.append("request log header")

    monkeypatch.setattr(run, "RequestLog", FakeRequestLog)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_API_KEY)
    run.main(["--game", "zelda", "--model", "decisions", "--save-requests"])
    events = recorder.events
    assert (
        events.index("request log opened")
        < events.index("pipeline created")
        < events.index("request log header")
        < events.index("pipeline run")
    )
    parts = recorder.pipeline.parts
    assert parts.request_log is recorder.request_log
    model, instructions, questions = recorder.request_log.header
    assert (model, instructions) == ("decisions", build_instructions())
    # Pipeline に渡したものと同じ問いの一覧を書く
    assert list(questions) == parts.questions
    assert recorder.request_log.run_dir.parent == recorder.workdir / "runs"


def test_main_uses_decisions_model_with_openai_key(recorder, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_API_KEY)
    run.main(["--game", "zelda", "--model", "decisions"])
    assert "FakeDecisionsApiModel created" in recorder.events
    assert recorder.model.api_key == TEST_API_KEY


def test_jev_min_request_interval_is_0_2_seconds():
    assert jev.JEV_MIN_REQUEST_INTERVAL_S == 0.2


def test_main_spaces_jev_requests_by_min_request_interval(recorder):
    run.main(REQUIRED)
    assert recorder.pipeline.min_request_interval_s == jev.JEV_MIN_REQUEST_INTERVAL_S


def test_main_does_not_space_decisions_requests(recorder, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_API_KEY)
    run.main(["--game", "zelda", "--model", "decisions"])
    assert recorder.pipeline.min_request_interval_s == 0.0


def test_main_stops_on_keyboard_interrupt_and_closes_parts(recorder):
    recorder.pipeline_error = KeyboardInterrupt()
    run.main(REQUIRED)
    assert recorder.stop.is_set()
    assert closed(recorder)[-1] == "supervisor closed"


def test_main_closes_parts_and_raises_pipeline_error(recorder):
    recorder.pipeline_error = RuntimeError("controller bridge could not be reconnected")
    with pytest.raises(RuntimeError, match="could not be reconnected"):
        run.main(REQUIRED)
    assert closed_steps(recorder) == [
        "watcher closed",
        "output neutral",
        "output closed",
        ALL_ADVISORS_CLOSED,
        "capture closed",
        "supervisor closed",
    ]


# --- close_advisors ---


def test_close_advisors_closes_all_advisors_at_the_same_time():
    # どの close も、ほかのすべての close が始まるまで戻らない。1 つずつ順に閉じると、最初の close が待ちきれずに
    # 失敗する
    count = 4
    barrier = threading.Barrier(count, timeout=2.0)
    results: list[str] = []

    class BlockingAdvisor:
        def close(self):
            try:
                barrier.wait()
                results.append("closed")
            except threading.BrokenBarrierError:
                results.append("broken")

    run.close_advisors([BlockingAdvisor() for _ in range(count)])
    assert results == ["closed"] * count


def test_close_advisors_returns_after_every_close_returns():
    finished = threading.Event()

    class SlowAdvisor:
        def close(self):
            finished.wait(0.1)
            finished.set()

    run.close_advisors([SlowAdvisor()])
    assert finished.is_set()


def test_close_advisors_with_no_advisors_returns():
    run.close_advisors([])


def test_main_exits_without_skills_directory(recorder, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with pytest.raises(SystemExit) as excinfo:
        run.main(REQUIRED)
    assert "ch04_techniques" in str(excinfo.value.code)
    assert recorder.events == []


def test_main_exits_without_api_key(recorder, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(SystemExit) as excinfo:
        run.main(REQUIRED)
    assert "TYPESAFE_API_KEY" in str(excinfo.value.code)
    assert recorder.events == []


def test_main_exits_with_invalid_game_name(recorder):
    with pytest.raises(SystemExit):
        run.main(["--game", "../outside", "--model", "jev"])
    assert recorder.events == []
    assert not (recorder.workdir / "outside").exists()


def test_main_exits_with_message_when_first_bridge_fails(recorder):
    recorder.bridge_error = BridgeStartError("controller bridge failed: fatal port")
    with pytest.raises(SystemExit) as excinfo:
        run.main(REQUIRED)
    assert excinfo.value.code == "controller bridge failed: fatal port"
    assert recorder.events == []


def test_main_closes_only_created_parts_when_capture_fails(recorder, monkeypatch):
    def failing_capture(ffmpeg_fps, process_fps, history, on_frame):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(run, "CaptureWithHistory", failing_capture)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        run.main(REQUIRED)
    assert closed(recorder) == ["output neutral", "output closed", "supervisor closed"]


# --- --record-dashboard ---


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def only_run_dir(recorder: Recorder) -> Path:
    [run_log] = list((recorder.workdir / "runs").glob("*/run.jsonl"))
    return run_log.parent


def run_log_lines(recorder: Recorder) -> list[dict]:
    return read_jsonl(only_run_dir(recorder) / "run.jsonl")


def call_during_pipeline_run(monkeypatch, during_run) -> None:
    """recorder の fixture の偽の Pipeline の run の中で、during_run() を呼ぶようにする。

    run.main が部品を作り終えてから片付けを始めるまでの間に、フレームやレポートを渡すために使う。
    """
    base = run.Pipeline

    class PipelineCallingBack(base):
        def run(self, stop):
            during_run()
            super().run(stop)

    monkeypatch.setattr(run, "Pipeline", PipelineCallingBack)


def sent_report(mono: float) -> PadReport:
    return PadReport(
        mono=mono,
        source="apply",
        state=PadState(frozenset({"A"}), {}),
        ok=True,
        error=None,
    )


def test_main_with_record_dashboard_writes_args_and_origin_to_start(recorder):
    argv = [*REQUIRED, "--record-dashboard", "--valley-ratio", "0.4"]
    run.main(argv)
    start = run_log_lines(recorder)[0]
    assert start["event"] == "start"
    # run.jsonl は JSON なので、タプル (--timeline の節) はリストになる
    assert start["args"] == json.loads(json.dumps(vars(run.parse_args(argv))))
    assert start["args"]["timeline"] == list(SECTION_NAMES)
    assert start["origin_mono"] == PIPELINE_STARTED_AT


def test_main_without_record_dashboard_keeps_start_keys(recorder):
    run.main(REQUIRED)
    start = run_log_lines(recorder)[0]
    assert set(start) == {"time", "event", "game", "model", "run_dir"}


def test_main_without_record_dashboard_writes_no_dashboard_records(recorder):
    run.main(REQUIRED)
    assert recorder.output.on_report is None
    run_dir = only_run_dir(recorder)
    for name in ("frames.jsonl", "pad.jsonl", "thumbs"):
        assert not (run_dir / name).exists()
    events = [line["event"] for line in run_log_lines(recorder)]
    assert "record_end" not in events


def test_main_with_record_dashboard_records_sent_reports(recorder, monkeypatch):
    call_during_pipeline_run(
        monkeypatch, lambda: recorder.output.on_report(sent_report(1.5))
    )
    run.main([*REQUIRED, "--record-dashboard"])
    assert read_jsonl(only_run_dir(recorder) / "pad.jsonl") == [
        {"mono": 1.5, "source": "apply", "state": "A", "ok": True, "error": None}
    ]


def test_main_with_record_dashboard_records_frames_from_capture(recorder, monkeypatch):
    def feed_frames():
        # 鮮明度 200 の山の後に谷 (50) が来て回復 (150) すると、山のフレームが意味のあるフレームになる
        for t, sharpness in enumerate([100, 200, 50, 150]):
            recorder.capture.on_frame(frame(float(t), sharpness, gray=10))

    call_during_pipeline_run(monkeypatch, feed_frames)
    run.main([*REQUIRED, "--record-dashboard"])
    run_dir = only_run_dir(recorder)
    # close で書き切ってから戻る
    lines = read_jsonl(run_dir / "frames.jsonl")
    assert [line["mono"] for line in lines] == [0.0, 1.0, 2.0, 3.0]
    assert all((run_dir / line["thumb"]).is_file() for line in lines)
    assert lines[3]["event"]["kind"] == "close"
    # 記録するときも、残した意味のあるフレームは保管に入る
    kept = recorder.pipeline.parts.store.latest_before(10.0, 5)
    assert [f.time for f in kept] == [1.0]


def test_main_with_record_dashboard_writes_record_end(recorder, monkeypatch):
    def feed_records():
        recorder.capture.on_frame(frame(1.0, 100, gray=10))
        recorder.capture.on_frame(frame(1.1, 100, gray=10))
        recorder.output.on_report(sent_report(1.05))

    call_during_pipeline_run(monkeypatch, feed_records)
    run.main([*REQUIRED, "--record-dashboard"])
    record_ends = [
        line for line in run_log_lines(recorder) if line["event"] == "record_end"
    ]
    assert len(record_ends) == 1
    record_end = record_ends[0]
    assert record_end["written"] == {"frames": 2, "pad": 1}
    assert record_end["dropped"] == {"frames": 0, "pad": 0}


def test_main_closes_dashboard_recorder_after_capture_and_before_supervisor(
    recorder, monkeypatch
):
    record_end_written_at_close: dict[str, bool] = {}

    def record_end_written() -> bool:
        return any(line["event"] == "record_end" for line in run_log_lines(recorder))

    capture_base = run.CaptureWithHistory
    supervisor_base = run.BridgeSupervisor

    class Capture(capture_base):
        def close(self):
            record_end_written_at_close["capture"] = record_end_written()
            super().close()

    class Supervisor(supervisor_base):
        def close(self):
            record_end_written_at_close["supervisor"] = record_end_written()
            super().close()

    monkeypatch.setattr(run, "CaptureWithHistory", Capture)
    monkeypatch.setattr(run, "BridgeSupervisor", Supervisor)
    run.main([*REQUIRED, "--record-dashboard"])
    assert record_end_written_at_close == {"capture": False, "supervisor": True}


def test_main_closes_dashboard_recorder_when_capture_fails(recorder, monkeypatch):
    def failing_capture(ffmpeg_fps, process_fps, history, on_frame):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(run, "CaptureWithHistory", failing_capture)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        run.main([*REQUIRED, "--record-dashboard"])
    record_ends = [
        line for line in run_log_lines(recorder) if line["event"] == "record_end"
    ]
    assert len(record_ends) == 1
    assert record_ends[0]["written"] == {"frames": 0, "pad": 0}


def test_main_with_only_record_dashboard_writes_request_lines_without_frames(recorder):
    run.main([*REQUIRED, "--record-dashboard"])
    assert isinstance(recorder.pipeline.parts.request_log, RequestLog)
    run_dir = only_run_dir(recorder)
    assert [line["event"] for line in read_jsonl(run_dir / "requests.jsonl")] == [
        "header"
    ]
    assert not (run_dir / "frames").exists()


# --- build_request_log ---


def minimal_entry() -> RequestLogEntry:
    """画像 1 枚 (フレームの time 101.5、経過秒 1.5) の、応答が無かった要求の記録を返す。"""
    request = DecisionRequest(
        instructions="指示",
        state_text="",
        images=[LabeledImage("直近 1/1", b"jpeg", 101.5)],
        questions=[],
    )
    return RequestLogEntry(
        number=1,
        origin=100.0,
        sent_at=102.0,
        encoded_at=102.0,
        received_at=None,
        request=request,
        decision=None,
        action=None,
        advisor_accepted=frozenset(),
        result="error",
        error="HTTP 500",
    )


def test_build_request_log_without_options_is_none(tmp_path):
    assert run.build_request_log(tmp_path, run.parse_args(REQUIRED)) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "options, frames_saved, thumb",
    [
        # --save-requests だけなら、画像を書き、thumb は足さない
        (["--save-requests"], True, None),
        (["--record-dashboard"], False, "thumbs/101500000.jpg"),
        (["--save-requests", "--record-dashboard"], True, "thumbs/101500000.jpg"),
    ],
)
def test_build_request_log_follows_options(tmp_path, options, frames_saved, thumb):
    request_log = run.build_request_log(tmp_path, run.parse_args([*REQUIRED, *options]))
    assert isinstance(request_log, RequestLog)
    request_log.write(minimal_entry())
    [line] = read_jsonl(tmp_path / "requests.jsonl")
    [image] = line["images"]
    assert (tmp_path / "frames").is_dir() is frames_saved
    assert image["file"] == ("frames/000001500000.jpg" if frames_saved else None)
    assert ("thumb" in image) is (thumb is not None)
    assert image.get("thumb") == thumb


# --- parse_args ---


def test_parse_args_uses_defaults():
    assert vars(run.parse_args(REQUIRED)) == {
        "game": "zelda",
        "model": "jev",
        "ffmpeg_fps": 30,
        "process_fps": 10,
        "history_seconds": 5.0,
        "image_width": 1280,
        "jpeg_quality": 85,
        "threshold_advisor": 0.5,
        "valley_ratio": 0.5,
        "similarity_threshold": 0.15,
        "advisor_timeout": 300.0,
        "max_consecutive_errors": 5,
        "timeline": SECTION_NAMES,
        "timeline_window": 120.0,
        "screen_change_threshold": 2.0,
        "save_requests": False,
        "record_dashboard": False,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("all", SECTION_NAMES),
        ("none", ()),
        ("screen", ("screen",)),
        # SECTION_NAMES の順に並べ直す。前後の空白は除く
        ("subgoals, actions", ("actions", "subgoals")),
    ],
)
def test_parse_args_reads_timeline_sections(value, expected):
    assert run.parse_args([*REQUIRED, "--timeline", value]).timeline == expected


@pytest.mark.parametrize("value", ["unknown", "actions,actions", "actions,", ""])
def test_parse_args_rejects_bad_timeline_sections(value, capsys):
    with pytest.raises(SystemExit) as raised:
        run.parse_args([*REQUIRED, "--timeline", value])
    assert raised.value.code == 2
    assert "--timeline" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--timeline-window", "0"),
        ("--timeline-window", "inf"),
        ("--timeline-window", "nan"),
        ("--screen-change-threshold", "-0.1"),
        ("--screen-change-threshold", "255.5"),
    ],
)
def test_parse_args_rejects_out_of_range_timeline_values(option, value, capsys):
    with pytest.raises(SystemExit):
        run.parse_args([*REQUIRED, option, value])
    assert option in capsys.readouterr().err


def test_operation_log_capacity_covers_timeline_window():
    assert run.operation_log_capacity(10.0) == DEFAULT_OPERATION_LOG_CAPACITY
    assert run.operation_log_capacity(300.0) == 1500


def test_main_wires_timeline_with_all_sections_by_default(recorder):
    run.main(REQUIRED)
    parts = recorder.pipeline.parts
    timeline = parts.timeline
    assert isinstance(timeline, TimelineBuilder)
    assert timeline.settings == TimelineSettings(SECTION_NAMES, action_window_s=120.0)
    assert timeline.operation_log is parts.operation_log
    # 120 秒 × 1 秒あたり 5 回 = 600 件 (既定の 500 件より多い)
    assert parts.operation_log.capacity == 600
    # キャプチャーのフレームが、画面の変化の追跡に届く
    recorder.capture.on_frame(frame(1.0, 100, gray=10))
    assert timeline.screen.static_since() == 1.0


def test_main_wires_timeline_settings_from_options(recorder):
    run.main(
        [
            *REQUIRED,
            "--timeline",
            "actions,notes",
            "--timeline-window",
            "300",
            "--screen-change-threshold",
            "5",
        ]
    )
    parts = recorder.pipeline.parts
    assert parts.timeline.settings == TimelineSettings(
        ("actions", "notes"), action_window_s=300.0
    )
    # screen の節が無ければ、画面の変化の追跡を作らない
    assert parts.timeline.screen is None
    assert parts.operation_log.capacity == 1500


def test_main_with_timeline_screen_uses_given_threshold(recorder):
    run.main([*REQUIRED, "--timeline", "screen", "--screen-change-threshold", "5"])
    assert recorder.pipeline.parts.timeline.screen.threshold == 5.0


def test_main_without_timeline_passes_none(recorder):
    run.main([*REQUIRED, "--timeline", "none"])
    assert recorder.pipeline.parts.timeline is None


def test_parse_args_accepts_save_requests():
    assert run.parse_args([*REQUIRED, "--save-requests"]).save_requests is True


def test_parse_args_accepts_record_dashboard():
    assert run.parse_args([*REQUIRED, "--record-dashboard"]).record_dashboard is True


def test_parse_args_accepts_decisions_model():
    assert (
        run.parse_args(["--game", "zelda", "--model", "decisions"]).model == "decisions"
    )


def test_parse_args_reads_command_line_when_argv_is_none(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["switch2-agent", "--game", "mario", "--model", "decisions"]
    )
    args = run.parse_args(None)
    assert (args.game, args.model) == ("mario", "decisions")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--game", "zelda"],
        ["--model", "jev"],
        ["--game", "zelda", "--model", "gpt"],
    ],
)
def test_parse_args_requires_game_and_model(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args(argv)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, attribute, expected",
    [
        (["--ffmpeg-fps", "1", "--process-fps", "1"], "process_fps", 1),
        (["--ffmpeg-fps", "60", "--process-fps", "60"], "ffmpeg_fps", 60),
        (["--history-seconds", "0.1"], "history_seconds", 0.1),
        (["--image-width", "498"], "image_width", 498),
        (["--image-width", "1280"], "image_width", 1280),
        (["--jpeg-quality", "1"], "jpeg_quality", 1),
        (["--jpeg-quality", "100"], "jpeg_quality", 100),
        (["--threshold-advisor", "0"], "threshold_advisor", 0.0),
        (["--threshold-advisor", "0.75"], "threshold_advisor", 0.75),
        (["--threshold-advisor", "1.0"], "threshold_advisor", 1.0),
        (["--valley-ratio", "0.01"], "valley_ratio", 0.01),
        (["--valley-ratio", "0.99"], "valley_ratio", 0.99),
        (["--similarity-threshold", "0"], "similarity_threshold", 0.0),
        (["--similarity-threshold", "255"], "similarity_threshold", 255.0),
        (["--advisor-timeout", "0.5"], "advisor_timeout", 0.5),
        (["--max-consecutive-errors", "1"], "max_consecutive_errors", 1),
    ],
)
def test_parse_args_accepts_values_in_range(extra, attribute, expected):
    assert getattr(run.parse_args([*REQUIRED, *extra]), attribute) == expected


@pytest.mark.parametrize(
    "extra",
    [
        ["--ffmpeg-fps", "0"],
        ["--ffmpeg-fps", "61"],
        ["--ffmpeg-fps", "abc"],
        ["--process-fps", "0"],
        ["--process-fps", "1.5"],
        ["--history-seconds", "0"],
        ["--history-seconds", "-1"],
        ["--history-seconds", "inf"],
        ["--history-seconds", "nan"],
        ["--image-width", "497"],
        ["--image-width", "1281"],
        ["--image-width", "640.5"],
        ["--jpeg-quality", "0"],
        ["--jpeg-quality", "101"],
        ["--threshold-advisor", "-0.1"],
        ["--threshold-advisor", "1.1"],
        ["--threshold-advisor", "nan"],
        ["--threshold-advisor", "2"],
        ["--valley-ratio", "0"],
        ["--valley-ratio", "1"],
        ["--similarity-threshold", "-0.1"],
        ["--similarity-threshold", "255.1"],
        ["--advisor-timeout", "0"],
        ["--advisor-timeout", "-5"],
        ["--advisor-timeout", "inf"],
        ["--advisor-timeout", "nan"],
        ["--max-consecutive-errors", "0"],
        ["--max-consecutive-errors", "2.5"],
    ],
)
def test_parse_args_rejects_values_out_of_range(extra, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args([*REQUIRED, *extra])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option", ["--threshold-press", "--threshold-tap", "--threshold-stick"]
)
def test_parse_args_has_no_button_or_stick_thresholds(option, capsys):
    # ボタンとスティックの問いはしきい値を使わないため、その起動引数は無い
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args([*REQUIRED, option, "0.5"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--history-seconds", "--advisor-timeout"])
def test_parse_args_rejects_infinite_seconds_with_message(option, capsys):
    with pytest.raises(SystemExit):
        run.parse_args([*REQUIRED, option, "inf"])
    assert f"{option} must be a finite number more than 0: inf" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "extra",
    [
        ["--ffmpeg-fps", "30", "--process-fps", "31"],
        ["--process-fps", "40"],
        # --process-fps の既定値 10 も --ffmpeg-fps 以下でなければならない
        ["--ffmpeg-fps", "5"],
    ],
)
def test_parse_args_rejects_process_fps_above_ffmpeg_fps(extra, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run.parse_args([*REQUIRED, *extra])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err
