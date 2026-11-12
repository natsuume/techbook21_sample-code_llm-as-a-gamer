import json

import cv2
import numpy as np
import pytest
from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.controller_bridge import BridgeStartError

from switch2_agent import run
from switch2_agent.decision_request import JpegCache
from switch2_agent.frame_history import TimedFrame
from switch2_agent.game_notes import NOTE_KINDS

REQUIRED = ["--game", "zelda", "--model", "jev"]
TEST_API_KEY = "test-key-not-a-secret"


class Recorder:
    """偽の部品の生成と close の順を記録する。"""

    def __init__(self):
        self.events: list[str] = []
        self.bridge_error: Exception | None = None
        self.pipeline_error: BaseException | None = None


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """run.main が使う部品を偽物に差し替え、tmp_path を ch04_techniques に見立てた作業ディレクトリーにする。

    偽物は本物と同じ引数だけを受け取り、ほかの引数を渡すと TypeError になる。
    偽の Pipeline の run は、rec.pipeline_error があればそれを送出し、無ければすぐ戻る。
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
        def __init__(self, sender):
            self.sender = sender
            rec.output = self
            rec.events.append("output created")

        def neutral(self):
            rec.events.append("output neutral")

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

    class FakeAdvisor:
        def __init__(
            self, workdir, notes_dir, runs_dir, game, timeout_s, hidden_env_names, log
        ):
            self.args = (workdir, notes_dir, runs_dir, game, timeout_s)
            self.hidden_env_names = hidden_env_names
            self.log = log
            rec.advisor = self
            rec.events.append("advisor created")

        def close(self):
            rec.events.append("advisor closed")

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
        def __init__(self, parts, max_consecutive_errors):
            self.parts = parts
            self.max_consecutive_errors = max_consecutive_errors
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
        "advisor created",
        "FakeJevModel created",
        "pipeline created",
    ]
    assert "pipeline run" in recorder.events


def test_main_closes_parts_in_order(recorder):
    run.main(REQUIRED)
    assert closed(recorder) == [
        "output neutral",
        "output closed",
        "advisor closed",
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


def test_main_passes_run_dir_and_notes_dir_to_advisor(recorder):
    run.main([*REQUIRED, "--advisor-timeout", "120"])
    workdir, notes_dir, runs_dir, game, timeout_s = recorder.advisor.args
    assert workdir == recorder.workdir
    assert notes_dir == recorder.workdir / "game_notes" / "zelda"
    assert runs_dir.parent == recorder.workdir / "runs"
    assert (game, timeout_s) == ("zelda", 120.0)


def test_main_hides_api_keys_from_advisor(recorder):
    run.main(REQUIRED)
    assert set(recorder.advisor.hidden_env_names) == {
        "TYPESAFE_API_KEY",
        "OPENAI_API_KEY",
    }


def test_main_wires_pipeline_parts(recorder):
    run.main(
        [
            *REQUIRED,
            "--threshold-press",
            "0.6",
            "--threshold-advisor",
            "0.7",
            "--max-consecutive-errors",
            "3",
        ]
    )
    parts = recorder.pipeline.parts
    assert recorder.pipeline.max_consecutive_errors == 3
    assert parts.output is recorder.output
    assert parts.advisor is recorder.advisor
    assert parts.model is recorder.model
    assert recorder.model.api_key == TEST_API_KEY
    assert parts.history is recorder.capture.history
    assert isinstance(parts.encode, JpegCache)
    assert (parts.thresholds.press, parts.thresholds.advisor) == (0.6, 0.7)
    assert parts.game == "zelda"
    assert len(parts.questions) == 43
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


def test_main_uses_decisions_model_with_openai_key(recorder, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", TEST_API_KEY)
    run.main(["--game", "zelda", "--model", "decisions"])
    assert "FakeDecisionsApiModel created" in recorder.events
    assert recorder.model.api_key == TEST_API_KEY


def test_main_stops_on_keyboard_interrupt_and_closes_parts(recorder):
    recorder.pipeline_error = KeyboardInterrupt()
    run.main(REQUIRED)
    assert recorder.stop.is_set()
    assert closed(recorder)[-1] == "supervisor closed"


def test_main_closes_parts_and_raises_pipeline_error(recorder):
    recorder.pipeline_error = RuntimeError("controller bridge could not be reconnected")
    with pytest.raises(RuntimeError, match="could not be reconnected"):
        run.main(REQUIRED)
    assert closed(recorder) == [
        "output neutral",
        "output closed",
        "advisor closed",
        "capture closed",
        "supervisor closed",
    ]


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
        "threshold_press": 0.5,
        "threshold_tap": 0.5,
        "threshold_stick": 0.5,
        "threshold_advisor": 0.5,
        "valley_ratio": 0.5,
        "similarity_threshold": 0.15,
        "advisor_timeout": 300.0,
        "max_consecutive_errors": 5,
    }


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
        (["--threshold-press", "0"], "threshold_press", 0.0),
        (["--threshold-tap", "1"], "threshold_tap", 1.0),
        (["--threshold-stick", "0.75"], "threshold_stick", 0.75),
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
        ["--threshold-press", "-0.1"],
        ["--threshold-tap", "1.1"],
        ["--threshold-stick", "nan"],
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
