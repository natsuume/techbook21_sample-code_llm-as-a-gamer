import dataclasses
import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from switch2_remote.controller_state import BUTTON_ORDER, NEUTRAL_REPORT

from switch2_layers.deep.deep_scheduler import (
    DeepDocumentStatus,
    DeepJobEnd,
    DeepJobStatus,
)
from switch2_layers.deep.deep_types import (
    DeepRequestResult,
    DeepTrigger,
    DocumentKind,
)
from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.frames.surface_frames import BoxDrawingPlan, build_surface_image
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.pad_action import NEUTRAL_ACTION, PadAction
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.fake_surface import FakeSurfaceDecider
from switch2_layers.surface.jev_client import JevAnswers
from switch2_layers.surface.surface_loop import (
    BRIDGE_WAIT_S,
    DECIDE_ERROR_WAIT_S,
    NO_FRAME_RETRY_S,
    SurfaceLoop,
    SurfaceLoopParts,
    SurfaceLoopSettings,
    SurfaceStepOutcome,
    surface_failure_reason,
)
from switch2_layers.surface.surface_questions import (
    ADVISOR_KINDS,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)
from switch2_layers.surface.surface_record import answers_to_json, question_to_json
from switch2_layers.surface.surface_types import (
    Box,
    BoxSource,
    SurfaceExchange,
    SurfaceInput,
    SurfaceOutput,
    SurfaceRequest,
)

ORIGIN = 1000.0  # 偽の time.monotonic の起点 (t = 0)
NOW = 10.0  # step を呼ぶときの t
ALLOWED_WITHOUT_HOME = frozenset(BUTTON_ORDER) - {"HOME"}
COMMON_CONTROLS = "# 操作説明 (共通)\n\n- A: unknown\n"
COMMON_TIPS = "# 攻略情報 (共通)\n"
ADVICE = "# 助言\n"
A_BIT = 0x04  # レポートの b1
HOME_BIT = 0x10  # レポートの b2


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeSender:
    def __init__(self):
        self.reports: list[bytes] = []
        self.fail_with: Exception | None = None
        # 送信のたびに最初に呼ぶ関数 (ブリッジの応答を待つ間に全体が止められた場合を作る)
        self.on_send: Callable[[], None] | None = None

    def set_report(self, report: bytes) -> str:
        if self.on_send is not None:
            self.on_send()
        if self.fail_with is not None:
            raise self.fail_with
        self.reports.append(bytes(report))
        return "ack"


# FakeDeepSink.document_status が、statuses に無い種類について返す状況
IDLE_STATUS = DeepDocumentStatus(
    running_count=0,
    latest_running_start_t=None,
    last_end=None,
    min_interval_remaining_s=0.0,
    last_surface_request=None,
)


class FakeDeepSink:
    """DeepRequestSink と DeepStatusReader の偽物。受け取った要求と、状況を読んだ種類と場面を記録する。"""

    def __init__(self):
        self.requests: list[tuple[DocumentKind, DeepTrigger, str | None]] = []
        # この種類の要求では、この例外を出す (deep のジョブのディレクトリーの用意の失敗など)
        self.fail_for: dict[DocumentKind, Exception] = {}
        # 要求を記録した後に呼ぶ関数 (要求を処理する間に全体が止められた場合を作る)
        self.on_request: Callable[[], None] | None = None
        # document_status が種類ごとに返す状況 (無い種類は IDLE_STATUS) と、例外を出す種類
        self.statuses: dict[DocumentKind, DeepDocumentStatus] = {}
        self.status_fail_for: dict[DocumentKind, Exception] = {}
        self.status_calls: list[tuple[DocumentKind, str | None]] = []

    def request(
        self, kind: DocumentKind, trigger: DeepTrigger, scene_id: str | None
    ) -> DeepRequestResult:
        self.requests.append((kind, trigger, scene_id))
        if self.on_request is not None:
            self.on_request()
        if kind in self.fail_for:
            raise self.fail_for[kind]
        return DeepRequestResult(started=True)

    def document_status(
        self, kind: DocumentKind, scene_id: str | None
    ) -> DeepDocumentStatus:
        self.status_calls.append((kind, scene_id))
        if kind in self.status_fail_for:
            raise self.status_fail_for[kind]
        return self.statuses.get(kind, IDLE_STATUS)


class ScriptedDecider:
    """SurfaceDecider の偽物。results を順に返す (例外なら出す)。最後の 1 つは繰り返す。受け取った入力を記録する。"""

    def __init__(self, *results: SurfaceOutput | Exception):
        self.results = list(results)
        self.inputs: list[SurfaceInput] = []

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        self.inputs.append(surface_input)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


@dataclass
class Harness:
    root: Path
    monotonic: FakeMonotonic
    loop: SurfaceLoop
    sender: FakeSender
    deep_sink: FakeDeepSink
    buffer: FrameBuffer
    store: FrameStore
    stops: list[str] = field(default_factory=list)
    bridge: dict[str, str] = field(default_factory=lambda: {"state": "ok"})


def make_run(tmp_path: Path) -> Path:
    """共通の文書と助言だけを持つ実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    (root / "middle").mkdir()
    (root / "notes" / "common").mkdir(parents=True)
    (root / "notes" / "common" / "controls.md").write_text(
        COMMON_CONTROLS, encoding="utf-8"
    )
    (root / "notes" / "common" / "tips.md").write_text(COMMON_TIPS, encoding="utf-8")
    (root / "notes" / "advice.md").write_text(ADVICE, encoding="utf-8")
    return root


def make_harness(
    tmp_path: Path,
    decider: ScriptedDecider,
    max_errors: int = 5,
    allowed: frozenset[str] = ALLOWED_WITHOUT_HOME,
    boxes_at: Callable[[float], Sequence[Box]] | None = None,
) -> Harness:
    """surface のループと偽の部品を組み立てる (テストの本体から呼ぶ)。時計は t = NOW にしておく。"""
    root = make_run(tmp_path)
    paths = RunPaths(root)
    monotonic = FakeMonotonic(ORIGIN + NOW)
    clock = RunClock(monotonic, origin=ORIGIN)
    buffer = FrameBuffer()
    store = FrameStore(paths, interval_fps=5.0, interval_width=64, start_thread=False)
    sender = FakeSender()
    output = PadOutput(sender, allowed, clock=monotonic, start_timer=False)
    deep_sink = FakeDeepSink()
    stops: list[str] = []
    bridge = {"state": "ok"}
    parts = SurfaceLoopParts(
        paths=paths,
        decider=decider,
        frame_buffer=buffer,
        frame_store=store,
        pad_output=output,
        deep_requests=deep_sink,
        deep_status=deep_sink,
        run_log=RunLog(paths.run_log, clock),
        clock=clock,
        bridge_state=lambda: bridge["state"],
        on_stop=stops.append,
        boxes_at=boxes_at,
    )
    loop = SurfaceLoop(
        parts, SurfaceLoopSettings(max_errors=max_errors, image_width=320)
    )
    return Harness(
        root, monotonic, loop, sender, deep_sink, buffer, store, stops, bridge
    )


def add_frames(harness: Harness, times: list[float]) -> None:
    for t in times:
        nv12 = np.full((54, 64), 128, np.uint8)
        harness.buffer.add(CapturedFrame(t=t, nv12=nv12))


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_state(root: Path, **values: object) -> None:
    state = {
        "generated_t": 8.5,
        "based_on_t": 8.0,
        "scene_id": None,
        "scene_name": None,
        "is_stuck": False,
        "stuck_description": "",
        "surface_wait_s": 0.2,
        "timeline": "",
        **values,
    }
    (root / "middle" / "state.json").write_text(json.dumps(state), encoding="utf-8")


def neutral_output() -> SurfaceOutput:
    return SurfaceOutput(action=NEUTRAL_ACTION)


def test_constants_follow_the_issue():
    assert NO_FRAME_RETRY_S == 0.2
    assert DECIDE_ERROR_WAIT_S == 1.0
    assert BRIDGE_WAIT_S == 0.2


# --- フレームが無いとき ---


def test_no_frames_sends_no_request(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.NO_FRAMES
    assert result.wait_s == NO_FRAME_RETRY_S
    assert result.row is None
    assert decider.inputs == []
    assert read_rows(harness.root / "surface.jsonl") == []


def test_only_stale_frames_send_no_request(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [7.0, 7.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.NO_FRAMES
    assert decider.inputs == []


# --- 1 回の要求の流れ ---


def test_successful_step_sends_filtered_action_and_writes_row(tmp_path):
    output = SurfaceOutput(
        action=PadAction(held=frozenset({"A"}), taps=frozenset({"HOME"}))
    )
    decider = ScriptedDecider(output)
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [round(8.0 + i * 0.1, 3) for i in range(21)])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    # middle/state.json が無いので 0.2 秒待つ
    assert result.wait_s == 0.2

    surface_input = decider.inputs[0]
    assert surface_input.t == NOW
    assert [frame.t for frame in surface_input.frames] == [8.0, 8.5, 9.0, 9.5, 10.0]
    assert surface_input.frames[-1].label == "t=10.000"
    assert all(frame.jpeg.startswith(b"\xff\xd8") for frame in surface_input.frames)
    assert surface_input.documents.controls == COMMON_CONTROLS
    assert surface_input.documents.uses_common_controls is True
    assert surface_input.timeline == ""
    assert surface_input.timeline_generated_t is None
    assert surface_input.recent_actions == ()

    # HOME は送る直前に外す
    report = harness.sender.reports[-1]
    assert report[0] & A_BIT
    assert report[1] & HOME_BIT == 0

    rows = read_rows(harness.root / "surface.jsonl")
    assert rows == [result.row]
    row = rows[0]
    assert row["t_sent"] == NOW
    assert row["t_received"] == NOW
    assert [frame["t"] for frame in row["frames"]] == [8.0, 8.5, 9.0, 9.5, 10.0]
    assert row["frames"][-1]["file"] == "frames/surface/000010000.jpg"
    assert row["scene_id"] is None
    assert row["uses_common"] == {"controls": True, "tips": True}
    assert row["middle_generated_t"] is None
    assert row["action"]["held"] == ["A"]
    assert row["action"]["taps"] == []
    assert row["blocked_buttons"] == ["HOME"]
    assert row["deep_requests"] == []
    assert row["scene_changed"] is False
    assert row["wait_s"] == 0.2
    assert row["error"] is None


def test_home_is_sent_with_allow_home(tmp_path):
    decider = ScriptedDecider(SurfaceOutput(action=PadAction(held=frozenset({"HOME"}))))
    harness = make_harness(tmp_path, decider, allowed=frozenset(BUTTON_ORDER))
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert harness.sender.reports[-1][1] & HOME_BIT
    assert result.row is not None
    assert result.row["blocked_buttons"] == []


def test_sent_images_are_saved_once_per_frame(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.0, 9.5])
    harness.loop.step()
    harness.monotonic.now = ORIGIN + NOW + 0.2
    add_frames(harness, [10.1])
    harness.loop.step()
    harness.store.write_queued()
    saved = sorted(p.name for p in (harness.root / "frames" / "surface").iterdir())
    assert saved == ["000009000.jpg", "000009500.jpg", "000010100.jpg"]
    frame_rows = read_rows(harness.root / "frames.jsonl")
    assert sorted(row["t"] for row in frame_rows if row["kind"] == "surface") == [
        9.0,
        9.5,
        10.1,
    ]


def test_scene_documents_timeline_and_wait_come_from_middle_state(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    write_state(
        harness.root,
        scene_id="scene-001",
        scene_name="町",
        surface_wait_s=1.5,
        timeline="## 直近\n町",
        generated_t=8.5,
    )
    scene_dir = harness.root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True)
    (scene_dir / "controls.md").write_text("# 町の操作\n", encoding="utf-8")
    (scene_dir / "tasks.md").write_text("- 宿屋に入る\n", encoding="utf-8")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.wait_s == 1.5
    surface_input = decider.inputs[0]
    assert surface_input.timeline == "## 直近\n町"
    assert surface_input.timeline_generated_t == 8.5
    assert surface_input.documents.scene_id == "scene-001"
    assert surface_input.documents.controls == "# 町の操作\n"
    assert surface_input.documents.tasks == "- 宿屋に入る\n"
    assert surface_input.documents.tips == COMMON_TIPS
    assert result.row is not None
    assert result.row["scene_id"] == "scene-001"
    assert result.row["uses_common"] == {"controls": False, "tips": True}
    assert result.row["middle_generated_t"] == 8.5
    assert result.row["wait_s"] == 1.5


def test_wait_out_of_range_is_clamped(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    write_state(harness.root, surface_wait_s=5.0)
    add_frames(harness, [9.9])
    assert harness.loop.step().wait_s == 2.0


def test_broken_middle_state_waits_default(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    (harness.root / "middle" / "state.json").write_text("{broken", encoding="utf-8")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert result.wait_s == 0.2


def test_deep_requests_are_passed_to_scheduler_in_kind_order(tmp_path):
    output = SurfaceOutput(
        action=NEUTRAL_ACTION,
        deep_kinds=frozenset({DocumentKind.STRATEGY, DocumentKind.TIPS}),
        scene_changed=True,
    )
    decider = ScriptedDecider(output)
    harness = make_harness(tmp_path, decider)
    write_state(harness.root, scene_id="scene-002")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert harness.deep_sink.requests == [
        (DocumentKind.TIPS, DeepTrigger.SURFACE, "scene-002"),
        (DocumentKind.STRATEGY, DeepTrigger.SURFACE, "scene-002"),
    ]
    assert result.row is not None
    assert result.row["deep_requests"] == ["tips", "strategy"]
    assert result.row["scene_changed"] is True


def test_game_deep_request_is_passed_last_with_current_scene(tmp_path):
    output = SurfaceOutput(
        action=NEUTRAL_ACTION,
        deep_kinds=frozenset({DocumentKind.GAME, DocumentKind.ADVICE}),
    )
    decider = ScriptedDecider(output)
    harness = make_harness(tmp_path, decider)
    write_state(harness.root, scene_id="scene-002")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    # DOCUMENT_KINDS の順 (game は最後)。場面は呼んだ側が知っている現在の場面を渡す
    assert harness.deep_sink.requests == [
        (DocumentKind.ADVICE, DeepTrigger.SURFACE, "scene-002"),
        (DocumentKind.GAME, DeepTrigger.SURFACE, "scene-002"),
    ]
    assert result.row is not None
    assert result.row["deep_requests"] == ["advice", "game"]


def test_game_document_is_passed_to_the_decider(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    write_state(harness.root, scene_id="scene-001", scene_name="町")
    (harness.root / "notes" / "game.md").write_text(
        "## ジャンル\nRPG (推測)\n", encoding="utf-8"
    )
    add_frames(harness, [9.9])
    harness.loop.step()
    assert decider.inputs[0].documents.game == "## ジャンル\nRPG (推測)\n"


def test_deep_request_error_is_recorded_without_stopping(tmp_path):
    output = SurfaceOutput(
        action=NEUTRAL_ACTION,
        deep_kinds=frozenset({DocumentKind.MAP, DocumentKind.TIPS}),
    )
    decider = ScriptedDecider(output)
    harness = make_harness(tmp_path, decider, max_errors=1)
    harness.deep_sink.fail_for[DocumentKind.TIPS] = FileNotFoundError(
        "notes/map/.places.md.tmp"
    )
    write_state(harness.root, scene_id="scene-001")
    add_frames(harness, [9.9])
    for _ in range(2):
        result = harness.loop.step()
        assert result.outcome is SurfaceStepOutcome.SENT
    # 例外を出した種類の後の種類 (map) も要求を続ける
    assert harness.deep_sink.requests[:2] == [
        (DocumentKind.TIPS, DeepTrigger.SURFACE, "scene-001"),
        (DocumentKind.MAP, DeepTrigger.SURFACE, "scene-001"),
    ]
    # decide の失敗には数えず、全体も止めない
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    errors = [
        row
        for row in read_rows(harness.root / "run.jsonl")
        if row["event"] == "deep_request_error"
    ]
    assert len(errors) == 2
    assert errors[0]["source"] == "surface"
    assert errors[0]["kind"] == "tips"
    assert errors[0]["trigger"] == "surface"
    assert errors[0]["scene_id"] == "scene-001"
    assert ".places.md.tmp" in errors[0]["error"]
    assert result.row is not None
    assert result.row["deep_requests"] == ["tips", "map"]


def test_recent_actions_are_passed_to_next_request(tmp_path):
    first = SurfaceOutput(action=PadAction(held=frozenset({"B", "HOME"})))
    decider = ScriptedDecider(first, neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    harness.loop.step()
    harness.monotonic.now = ORIGIN + NOW + 0.3
    add_frames(harness, [10.2])
    harness.loop.step()
    recent = decider.inputs[1].recent_actions
    assert len(recent) == 1
    assert recent[0].t == NOW
    # 記録するのは制限に通した後の操作
    assert recent[0].action == PadAction(held=frozenset({"B"}))


# --- advisor の状況 (deep の呼び出しの状況) ---

RUNNING_STATUS = dataclasses.replace(
    IDLE_STATUS, running_count=2, latest_running_start_t=7.5
)
ENDED_STATUS = dataclasses.replace(
    IDLE_STATUS,
    last_end=DeepJobEnd(6.0, DeepJobStatus.FAILED),
    min_interval_remaining_s=4.0,
)


def test_advisor_statuses_are_read_for_advisor_kinds_in_current_scene(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    harness.deep_sink.statuses = {
        DocumentKind.TIPS: RUNNING_STATUS,
        DocumentKind.ADVICE: ENDED_STATUS,
    }
    write_state(harness.root, scene_id="scene-003")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    # ADVISOR_KINDS の順に、middle/state.json の現在の場面で読む (map は問わない)
    assert harness.deep_sink.status_calls == [
        (kind, "scene-003") for kind in ADVISOR_KINDS
    ]
    statuses = decider.inputs[0].advisor_statuses
    assert list(statuses) == list(ADVISOR_KINDS)
    assert statuses == {
        DocumentKind.CONTROLS: IDLE_STATUS,
        DocumentKind.TIPS: RUNNING_STATUS,
        DocumentKind.STRATEGY: IDLE_STATUS,
        DocumentKind.ADVICE: ENDED_STATUS,
        DocumentKind.GAME: IDLE_STATUS,
    }
    # 読み取りは deep の要求ではない
    assert harness.deep_sink.requests == []


def test_advisor_statuses_without_scene_use_no_scene(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    harness.loop.step()
    assert harness.deep_sink.status_calls == [(kind, None) for kind in ADVISOR_KINDS]


def test_advisor_statuses_are_read_again_for_each_request(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    harness.loop.step()
    harness.deep_sink.statuses = {DocumentKind.STRATEGY: RUNNING_STATUS}
    harness.monotonic.now = ORIGIN + NOW + 0.3
    add_frames(harness, [10.2])
    harness.loop.step()
    assert len(harness.deep_sink.status_calls) == 2 * len(ADVISOR_KINDS)
    assert decider.inputs[0].advisor_statuses[DocumentKind.STRATEGY] == IDLE_STATUS
    assert decider.inputs[1].advisor_statuses[DocumentKind.STRATEGY] == (RUNNING_STATUS)


def test_advisor_status_error_is_unreadable_without_counting_as_failure(
    tmp_path, caplog
):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider, max_errors=1)
    harness.deep_sink.statuses = {DocumentKind.ADVICE: ENDED_STATUS}
    harness.deep_sink.status_fail_for[DocumentKind.TIPS] = RuntimeError(
        "status lock broken"
    )
    add_frames(harness, [9.9])
    with caplog.at_level(logging.WARNING, logger="switch2_layers.surface.surface_loop"):
        result = harness.loop.step()
    # 例外を出した種類は None にし、残りの種類も読んで要求を続ける
    assert result.outcome is SurfaceStepOutcome.SENT
    assert harness.deep_sink.status_calls == [(kind, None) for kind in ADVISOR_KINDS]
    assert decider.inputs[0].advisor_statuses == {
        DocumentKind.CONTROLS: IDLE_STATUS,
        DocumentKind.TIPS: None,
        DocumentKind.STRATEGY: IDLE_STATUS,
        DocumentKind.ADVICE: ENDED_STATUS,
        DocumentKind.GAME: IDLE_STATUS,
    }
    # surface の失敗には数えず、全体も止めず、run.jsonl にも書かない。logging で警告を書く
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert any(
        record.levelno == logging.WARNING
        and record.name == "switch2_layers.surface.surface_loop"
        for record in caplog.records
    )


def test_all_advisor_status_errors_still_send_the_request(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider, max_errors=1)
    for kind in ADVISOR_KINDS:
        harness.deep_sink.status_fail_for[kind] = OSError("broken")
    add_frames(harness, [9.9])
    for _ in range(2):
        assert harness.loop.step().outcome is SurfaceStepOutcome.SENT
    assert decider.inputs[-1].advisor_statuses == dict.fromkeys(ADVISOR_KINDS)
    assert harness.stops == []


def test_advisor_statuses_are_not_read_without_a_request(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    # フレームが無い
    assert harness.loop.step().outcome is SurfaceStepOutcome.NO_FRAMES
    # ブリッジの復旧中
    add_frames(harness, [9.9])
    harness.bridge["state"] = "recovering"
    assert harness.loop.step().outcome is SurfaceStepOutcome.BRIDGE_NOT_READY
    assert harness.deep_sink.status_calls == []


def test_send_failure_is_recorded_without_counting_as_decide_error(tmp_path):
    decider = ScriptedDecider(SurfaceOutput(action=PadAction(held=frozenset({"A"}))))
    harness = make_harness(tmp_path, decider)
    harness.sender.fail_with = RuntimeError("bridge is reconnecting")
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert harness.loop.consecutive_errors == 0
    assert result.row is not None
    assert "reconnecting" in result.row["error"]
    # 送れなかったときも、制限に通した後の操作を記録する
    assert result.row["action"]["held"] == ["A"]


def test_conflicting_directions_are_removed_without_counting_as_error(tmp_path):
    output = SurfaceOutput(
        action=PadAction(held=frozenset({"UP", "DOWN", "A", "HOME"}))
    )
    decider = ScriptedDecider(output)
    harness = make_harness(tmp_path, decider, max_errors=1)
    add_frames(harness, [9.9])
    for _ in range(3):
        result = harness.loop.step()
        assert result.outcome is SurfaceStepOutcome.SENT
    assert harness.loop.consecutive_errors == 0
    assert harness.stops == []
    assert result.row is not None
    assert result.row["error"] is None
    # 相反する方向は両方とも外して送り、blocked_buttons には制限で外したボタンだけを書く
    assert result.row["action"]["held"] == ["A"]
    assert result.row["blocked_buttons"] == ["HOME"]
    assert harness.sender.reports[-1][2] == 15  # 十字キーを押していない hat の値


# --- decide の失敗 ---


def test_decide_error_neutralizes_and_waits_one_second(tmp_path):
    decider = ScriptedDecider(RuntimeError("model failed"))
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.DECIDE_ERROR
    assert result.wait_s == DECIDE_ERROR_WAIT_S
    assert harness.sender.reports[-1] == NEUTRAL_REPORT
    assert harness.loop.consecutive_errors == 1
    rows = read_rows(harness.root / "surface.jsonl")
    assert len(rows) == 1
    assert rows[0]["action"] is None
    assert "model failed" in rows[0]["error"]
    assert rows[0]["wait_s"] == DECIDE_ERROR_WAIT_S
    run_rows = read_rows(harness.root / "run.jsonl")
    assert [row["event"] for row in run_rows] == ["surface_error"]
    assert run_rows[0]["count"] == 1
    assert "model failed" in run_rows[0]["error"]
    assert harness.stops == []


def test_consecutive_decide_errors_stop_everything(tmp_path):
    decider = ScriptedDecider(RuntimeError("model failed"))
    harness = make_harness(tmp_path, decider, max_errors=5)
    add_frames(harness, [9.9])
    for _ in range(4):
        harness.loop.step()
    assert harness.stops == []
    harness.loop.step()
    assert harness.stops == [surface_failure_reason(5)]
    assert surface_failure_reason(5) == "surface failed 5 times in a row"


def test_success_resets_consecutive_errors(tmp_path):
    error = RuntimeError("model failed")
    decider = ScriptedDecider(
        error, error, neutral_output(), error, error, error, error
    )
    harness = make_harness(tmp_path, decider, max_errors=5)
    add_frames(harness, [9.9])
    for _ in range(3):
        harness.loop.step()
    assert harness.loop.consecutive_errors == 0
    for _ in range(4):
        harness.loop.step()
    assert harness.loop.consecutive_errors == 4
    assert harness.stops == []


# --- ブリッジの復旧中 ---


def test_bridge_recovering_sends_no_request_and_neutralizes_once(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    harness.bridge["state"] = "recovering"
    first = harness.loop.step()
    second = harness.loop.step()
    assert first.outcome is SurfaceStepOutcome.BRIDGE_NOT_READY
    assert first.wait_s == BRIDGE_WAIT_S
    assert second.outcome is SurfaceStepOutcome.BRIDGE_NOT_READY
    assert decider.inputs == []
    assert harness.sender.reports == [NEUTRAL_REPORT]
    assert read_rows(harness.root / "surface.jsonl") == []
    # 復旧したら要求を再開する
    harness.bridge["state"] = "ok"
    assert harness.loop.step().outcome is SurfaceStepOutcome.SENT
    assert len(decider.inputs) == 1


# --- decide の間に止められたとき ---


class StoppingDecider(ScriptedDecider):
    """decide の中で止める合図を立てる SurfaceDecider の偽物 (decide の応答を待つ間に全体が止められた場合)。"""

    def __init__(self, stop: threading.Event, result: SurfaceOutput | Exception):
        super().__init__(result)
        self.stop = stop

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        self.stop.set()
        return super().decide(surface_input)


def test_stop_during_decide_sends_and_writes_nothing(tmp_path):
    stop = threading.Event()
    output = SurfaceOutput(
        action=PadAction(held=frozenset({"A"})),
        deep_kinds=frozenset({DocumentKind.TIPS}),
    )
    decider = StoppingDecider(stop, output)
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    assert result.wait_s == 0.0
    assert result.row is None
    assert len(decider.inputs) == 1
    # 操作を送らず、surface.jsonl・run.jsonl に書かず、deep も呼ばない
    assert harness.sender.reports == []
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.deep_sink.requests == []


def test_stop_during_failed_decide_writes_nothing(tmp_path):
    stop = threading.Event()
    decider = StoppingDecider(stop, RuntimeError("model failed"))
    harness = make_harness(tmp_path, decider, max_errors=1)
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    # 失敗の回数に数えず、中立の操作も送らず、surface_error も書かない
    assert harness.loop.consecutive_errors == 0
    assert harness.sender.reports == []
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.stops == []


class StopBeforeApplyPadOutput(PadOutput):
    """apply の直前に全体が止められた場合の PadOutput (止める合図を立ててから出力を止める。app.request_stop の順)。"""

    def __init__(self, stop: threading.Event, sender: FakeSender):
        super().__init__(sender, ALLOWED_WITHOUT_HOME, start_timer=False)
        self.stop = stop

    def apply(self, action: PadAction):
        self.stop.set()
        self.stop_output()
        return super().apply(action)


def test_stop_between_decide_and_apply_writes_nothing(tmp_path):
    stop = threading.Event()
    output = SurfaceOutput(
        action=PadAction(held=frozenset({"A"})),
        deep_kinds=frozenset({DocumentKind.TIPS}),
    )
    harness = make_harness(tmp_path, ScriptedDecider(output))
    harness.loop.parts = dataclasses.replace(
        harness.loop.parts, pad_output=StopBeforeApplyPadOutput(stop, harness.sender)
    )
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    # decide の後の確かめでは立っていなかったが、apply が OutputStoppedError を出したときには立っていた
    assert result.outcome is SurfaceStepOutcome.STOPPED
    assert result.row is None
    assert harness.sender.reports == [NEUTRAL_REPORT]
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.deep_sink.requests == []


def test_run_returns_when_stopped_during_decide(tmp_path):
    stop = threading.Event()
    decider = StoppingDecider(stop, neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.9])
    # 同じスレッドで呼ぶ。decide の中で stop が立つので、1 回で戻る
    harness.loop.run(stop)
    assert len(decider.inputs) == 1
    assert read_rows(harness.root / "surface.jsonl") == []
    assert harness.stops == []


# --- 操作の送信・記録・deep の要求の間に止められたとき ---


@pytest.mark.parametrize(
    "send_error", [None, RuntimeError("bridge closed")], ids=["sent", "send_failed"]
)
def test_stop_during_sending_action_writes_no_row_and_requests_no_deep(
    tmp_path, send_error
):
    stop = threading.Event()
    output = SurfaceOutput(
        action=PadAction(held=frozenset({"A"})),
        deep_kinds=frozenset({DocumentKind.TIPS}),
    )
    harness = make_harness(tmp_path, ScriptedDecider(output))
    # 操作の送信 (ブリッジの応答待ち) の間に全体が止められる。送信は成功する場合と失敗する場合
    harness.sender.on_send = stop.set
    harness.sender.fail_with = send_error
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    assert result.wait_s == 0.0
    assert result.row is None
    # surface.jsonl の行も、deep の要求も増えない
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.deep_sink.requests == []


def test_stop_during_first_deep_request_skips_remaining_requests(tmp_path):
    stop = threading.Event()
    output = SurfaceOutput(
        action=NEUTRAL_ACTION,
        deep_kinds=frozenset({DocumentKind.TIPS, DocumentKind.STRATEGY}),
    )
    harness = make_harness(tmp_path, ScriptedDecider(output))
    write_state(harness.root, scene_id="scene-001")
    # 最初の種類 (tips) の要求を処理する間に全体が止められる
    harness.deep_sink.on_request = stop.set
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    assert result.row is None
    # 残りの種類 (strategy) は要求しない。行は要求の前に書いてある
    assert harness.deep_sink.requests == [
        (DocumentKind.TIPS, DeepTrigger.SURFACE, "scene-001")
    ]
    assert len(read_rows(harness.root / "surface.jsonl")) == 1


def test_stop_during_neutral_after_failed_decide_writes_nothing(tmp_path):
    stop = threading.Event()
    harness = make_harness(
        tmp_path, ScriptedDecider(RuntimeError("model failed")), max_errors=1
    )
    # decide の失敗の後に中立のレポートを送る間に全体が止められる
    harness.sender.on_send = stop.set
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    assert result.row is None
    assert harness.sender.reports == [NEUTRAL_REPORT]
    # 失敗の回数を変えず、surface_error も surface.jsonl の行も書かず、on_stop も呼ばない
    assert harness.loop.consecutive_errors == 0
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.stops == []


class StoppingRunLog(RunLog):
    """1 行書いた後に止める合図を立てる RunLog (run.jsonl に書いた直後に全体が止められた場合)。"""

    def __init__(self, path: Path, clock: RunClock, stop: threading.Event):
        super().__init__(path, clock)
        self.stop = stop

    def write(self, event: str, /, **fields: object) -> None:
        super().write(event, **fields)
        self.stop.set()


def test_stop_just_before_error_row_writes_no_surface_row(tmp_path):
    stop = threading.Event()
    harness = make_harness(
        tmp_path, ScriptedDecider(RuntimeError("model failed")), max_errors=1
    )
    parts = harness.loop.parts
    harness.loop.parts = dataclasses.replace(
        parts, run_log=StoppingRunLog(parts.paths.run_log, parts.clock, stop)
    )
    add_frames(harness, [9.9])
    result = harness.loop.step(stop)
    assert result.outcome is SurfaceStepOutcome.STOPPED
    # surface_error を書いた後、surface.jsonl の行を書く直前に止める合図を見て抜ける
    assert [row["event"] for row in read_rows(harness.root / "run.jsonl")] == [
        "surface_error"
    ]
    assert read_rows(harness.root / "surface.jsonl") == []
    assert harness.stops == []


# --- 送る画像への box の書き込み ---

BOX = Box("自機", 0.25, 0.25, 0.75, 0.75, 0.9, BoxSource.TRACKER)


class RecordingBoxes:
    """boxes_at の偽物。呼ばれた t を記録し、t が boxed_t のフレームにだけ BOX を返す。"""

    def __init__(self, boxed_t: float, error: Exception | None = None):
        self.boxed_t = boxed_t
        self.error = error
        self.calls: list[float] = []

    def __call__(self, t: float) -> Sequence[Box]:
        self.calls.append(t)
        if self.error is not None:
            raise self.error
        return [BOX] if t == self.boxed_t else []


def test_boxes_are_drawn_on_sent_and_saved_images(tmp_path):
    boxes = RecordingBoxes(boxed_t=10.0)
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider, boxes_at=boxes)
    add_frames(harness, [9.0, 10.0])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert boxes.calls == [9.0, 10.0]
    frames = {frame.t: frame for frame in harness.buffer.frames()}
    sent = decider.inputs[0].frames
    assert (
        sent[0].jpeg == build_surface_image(frames[9.0], 320, DEFAULT_JPEG_QUALITY).jpeg
    )
    assert (
        sent[1].jpeg
        == build_surface_image(frames[10.0], 320, DEFAULT_JPEG_QUALITY, [BOX]).jpeg
    )
    # 保存する画像は、送った画像と同じ
    harness.store.write_queued()
    saved = (harness.root / "frames" / "surface" / "000010000.jpg").read_bytes()
    assert saved == sent[1].jpeg


def test_boxes_error_draws_no_boxes_without_counting_as_failure(tmp_path):
    boxes = RecordingBoxes(boxed_t=10.0, error=RuntimeError("tracking failed"))
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider, boxes_at=boxes)
    add_frames(harness, [10.0])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert boxes.calls == [10.0]
    frame = harness.buffer.frames()[0]
    assert (
        decider.inputs[0].frames[0].jpeg
        == build_surface_image(frame, 320, DEFAULT_JPEG_QUALITY).jpeg
    )
    assert harness.loop.consecutive_errors == 0


# --- 要求ごとの書き込み方 (plan_boxes) ---

PLAYER = Box("自機", 0.1, 0.1, 0.4, 0.5, 0.9, BoxSource.TRACKER)
ENEMY = Box("敵", 0.5, 0.4, 0.9, 0.9, 0.8, BoxSource.TRACKER)
NUMBERS = {"自機": 1, "敵": 2}


class RecordingPlan:
    """plan_boxes の偽物。呼ばれた場面 ID を記録し、plans を順に返す (最後の 1 つは繰り返す)。"""

    def __init__(self, *plans: BoxDrawingPlan | Exception):
        self.plans = list(plans)
        self.calls: list[str | None] = []

    def __call__(self, scene_id: str | None) -> BoxDrawingPlan:
        self.calls.append(scene_id)
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        if isinstance(plan, Exception):
            raise plan
        return plan


def with_plan(harness: Harness, plan_boxes: RecordingPlan) -> None:
    """harness のループを、boxes_at がいつも [PLAYER, ENEMY] を返し、plan_boxes を使うものに作り直す。"""
    parts = dataclasses.replace(
        harness.loop.parts, boxes_at=lambda t: [PLAYER, ENEMY], plan_boxes=plan_boxes
    )
    harness.loop = SurfaceLoop(parts, harness.loop.settings)


def expected_jpeg(frame: CapturedFrame, boxes: list[Box]) -> bytes:
    return build_surface_image(frame, 320, DEFAULT_JPEG_QUALITY, boxes, NUMBERS).jpeg


def test_requeried_target_is_not_drawn_on_latest_frame(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    write_state(harness.root, scene_id="scene-002")
    plan = RecordingPlan(BoxDrawingPlan(NUMBERS, hidden_on_latest=frozenset({"自機"})))
    with_plan(harness, plan)
    add_frames(harness, [9.0, 10.0])
    assert harness.loop.step().outcome is SurfaceStepOutcome.SENT
    # 要求ごとに 1 回、文書の場面 ID で求める
    assert plan.calls == ["scene-002"]
    frames = {frame.t: frame for frame in harness.buffer.frames()}
    sent = decider.inputs[0].frames
    # 古いフレームには両方、最新のフレームには問い直す対象 (自機) を除いて書き込む
    assert sent[0].jpeg == expected_jpeg(frames[9.0], [PLAYER, ENEMY])
    assert sent[1].jpeg == expected_jpeg(frames[10.0], [ENEMY])


def test_same_t_with_different_boxes_is_saved_to_another_file(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    plan = RecordingPlan(
        BoxDrawingPlan(NUMBERS, hidden_on_latest=frozenset({"自機"})),
        BoxDrawingPlan(NUMBERS),
    )
    with_plan(harness, plan)
    add_frames(harness, [9.0, 10.0])
    harness.loop.step()
    # 次の要求では t = 10.0 は最新でなくなり、自機も書き込むので、別のファイルに保存する
    harness.monotonic.now = ORIGIN + 10.5
    add_frames(harness, [10.5])
    harness.loop.step()
    rows = read_rows(harness.root / "surface.jsonl")
    assert [frame["file"] for frame in rows[0]["frames"]] == [
        "frames/surface/000009000.jpg",
        "frames/surface/000010000.jpg",
    ]
    assert [frame["file"] for frame in rows[1]["frames"]] == [
        "frames/surface/000009000.jpg",
        "frames/surface/000010000-2.jpg",
        "frames/surface/000010500.jpg",
    ]
    harness.store.write_queued()
    surface_dir = harness.root / "frames" / "surface"
    first_sent = decider.inputs[0].frames[1].jpeg
    second_sent = decider.inputs[1].frames[1].jpeg
    # 記録の file は、その要求で送った画像を指す
    assert (surface_dir / "000010000.jpg").read_bytes() == first_sent
    assert (surface_dir / "000010000-2.jpg").read_bytes() == second_sent
    assert first_sent != second_sent


def test_same_boxes_with_different_colors_are_saved_to_another_file(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    # 対象の一覧の並びが変わり、同じ名前と座標の box の色の番号だけが変わる
    plan = RecordingPlan(
        BoxDrawingPlan({"自機": 1, "敵": 2}), BoxDrawingPlan({"自機": 2, "敵": 1})
    )
    with_plan(harness, plan)
    add_frames(harness, [10.0])
    harness.loop.step()
    harness.loop.step()
    rows = read_rows(harness.root / "surface.jsonl")
    assert [row["frames"][0]["file"] for row in rows] == [
        "frames/surface/000010000.jpg",
        "frames/surface/000010000-2.jpg",
    ]
    harness.store.write_queued()
    surface_dir = harness.root / "frames" / "surface"
    first_sent = decider.inputs[0].frames[0].jpeg
    second_sent = decider.inputs[1].frames[0].jpeg
    assert first_sent != second_sent
    assert (surface_dir / "000010000.jpg").read_bytes() == first_sent
    assert (surface_dir / "000010000-2.jpg").read_bytes() == second_sent


def test_plan_error_draws_no_boxes_without_counting_as_failure(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    with_plan(harness, RecordingPlan(RuntimeError("no run directory")))
    add_frames(harness, [10.0])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    frame = harness.buffer.frames()[0]
    assert (
        decider.inputs[0].frames[0].jpeg
        == build_surface_image(frame, 320, DEFAULT_JPEG_QUALITY).jpeg
    )
    assert harness.loop.consecutive_errors == 0


# --- 送った要求の記録 (surface_requests.jsonl) と、surface.jsonl の要求の番号・答え・usage ---

REQUEST_QUESTION = SurfaceQuestion(
    name="scene_changed",
    instructions="画面の種類が変わったか。",
    kind=QuestionKind.SCENE_CHANGE,
    target="",
    question_type=QuestionType.NOUL,
)
ANSWERS = JevAnswers(
    choices={"button_A": {"none": 0.2, "hold": 0.7, "tap": 0.1}},
    nouls={"scene_changed": 0.25},
    boxes={"box_1": None},
    usage={"input_tokens": 9, "output_tokens": 1},
)
SEND_DELAY_S = (
    0.05  # decide を呼んでから Jev に送るまでの時間 (RequestingDecider が時計を進める)
)


class RequestingDecider:
    """Jev を呼ぶ SurfaceDecider の偽物。

    decide のたびに要求の番号を 1 増やし、時計を SEND_DELAY_S 進めてから、request_sink に要求を渡す
    (Jev に送る直前に書く)。その後、results を順に返す (例外なら出す。最後の 1 つは繰り返す)。返す出力には、
    渡した要求と ANSWERS の exchange を付ける。swallow_sink_error なら、request_sink の例外を外に出さない。
    """

    def __init__(
        self,
        monotonic: FakeMonotonic,
        *results: SurfaceOutput | Exception,
        swallow_sink_error: bool = False,
    ):
        self.monotonic = monotonic
        self.results = list(results)
        self.swallow_sink_error = swallow_sink_error
        self.inputs: list[SurfaceInput] = []
        self.count = 0

    def decide(self, surface_input: SurfaceInput) -> SurfaceOutput:
        self.inputs.append(surface_input)
        self.count += 1
        self.monotonic.now += SEND_DELAY_S
        request = SurfaceRequest(
            request_index=self.count,
            model="jev-test",
            state={
                "frames": [
                    {"label": frame.label, "image": frame.file}
                    for frame in surface_input.frames
                ]
            },
            questions=(REQUEST_QUESTION,),
        )
        if surface_input.request_sink is not None:
            try:
                surface_input.request_sink(request)
            except Exception:
                if not self.swallow_sink_error:
                    raise
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return dataclasses.replace(result, exchange=SurfaceExchange(request, ANSWERS))


def use_decider(harness: Harness, decider) -> None:
    harness.loop.parts = dataclasses.replace(harness.loop.parts, decider=decider)


def requesting_harness(
    tmp_path: Path, *results: SurfaceOutput | Exception, **options: bool
) -> tuple[Harness, RequestingDecider]:
    harness = make_harness(tmp_path, ScriptedDecider(neutral_output()))
    decider = RequestingDecider(harness.monotonic, *results, **options)
    use_decider(harness, decider)
    return harness, decider


def requests_log(harness: Harness) -> Path:
    return harness.root / "surface_requests.jsonl"


def test_sent_frames_carry_the_saved_files(tmp_path):
    decider = ScriptedDecider(neutral_output())
    harness = make_harness(tmp_path, decider)
    add_frames(harness, [9.0, 9.5, 10.0])
    result = harness.loop.step()
    assert result.row is not None
    files = [frame.file for frame in decider.inputs[0].frames]
    # decide に渡す画像は、surface.jsonl と同じ、保存を予約したファイルを持つ
    assert files == [frame["file"] for frame in result.row["frames"]]
    assert files == [
        "frames/surface/000009000.jpg",
        "frames/surface/000009500.jpg",
        "frames/surface/000010000.jpg",
    ]


def test_request_line_is_written_just_before_sending(tmp_path):
    harness, _ = requesting_harness(tmp_path, neutral_output())
    add_frames(harness, [9.5, 9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    rows = read_rows(requests_log(harness))
    assert len(rows) == 1
    row = rows[0]
    assert list(row) == ["request", "t_sent", "model", "state", "questions"]
    assert row["request"] == 1
    # 送った時刻は、decide を呼んだ時刻 (surface.jsonl の t_sent) ではなく、送る直前の時刻
    assert row["t_sent"] == pytest.approx(NOW + SEND_DELAY_S)
    assert result.row is not None
    assert result.row["t_sent"] == NOW
    assert row["model"] == "jev-test"
    assert row["questions"] == [question_to_json(REQUEST_QUESTION)]
    # 画像は、surface.jsonl の frames と同じファイル名
    assert [frame["image"] for frame in row["state"]["frames"]] == [
        frame["file"] for frame in result.row["frames"]
    ]


def test_surface_row_has_request_answers_and_usage_of_the_output(tmp_path):
    harness, _ = requesting_harness(tmp_path, neutral_output())
    add_frames(harness, [9.9])
    harness.loop.step()
    harness.monotonic.now = ORIGIN + NOW + 0.3
    add_frames(harness, [10.2])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert result.row is not None
    assert result.row["request"] == 2
    assert result.row["answers"] == answers_to_json(ANSWERS)
    assert result.row["usage"] == {"input_tokens": 9, "output_tokens": 1}
    assert [row["request"] for row in read_rows(requests_log(harness))] == [1, 2]
    surface_rows = read_rows(harness.root / "surface.jsonl")
    assert [row["request"] for row in surface_rows] == [1, 2]


def test_request_failing_after_sending_keeps_its_line_and_number(tmp_path):
    harness, _ = requesting_harness(tmp_path, RuntimeError("HTTP 500"))
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.DECIDE_ERROR
    assert result.row is not None
    assert "HTTP 500" in result.row["error"]
    # 送った後に失敗した要求: 番号を書き、答えと usage は null。送った要求の行は残る
    assert result.row["request"] == 1
    assert result.row["answers"] is None
    assert result.row["usage"] is None
    assert [row["request"] for row in read_rows(requests_log(harness))] == [1]


def test_request_failing_before_sending_has_no_request_number(tmp_path):
    harness = make_harness(tmp_path, ScriptedDecider(RuntimeError("no run directory")))
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.DECIDE_ERROR
    assert result.row is not None
    assert result.row["request"] is None
    assert result.row["answers"] is None
    assert result.row["usage"] is None
    assert not requests_log(harness).exists()


def test_fake_surface_writes_no_request_lines(tmp_path):
    harness = make_harness(tmp_path, ScriptedDecider(neutral_output()))
    use_decider(harness, FakeSurfaceDecider())
    add_frames(harness, [9.9])
    result = harness.loop.step()
    assert result.outcome is SurfaceStepOutcome.SENT
    assert result.row is not None
    assert result.row["request"] is None
    assert result.row["answers"] is None
    assert result.row["usage"] is None
    assert not requests_log(harness).exists()


@pytest.mark.parametrize("file_name", ["surface.jsonl", "surface_requests.jsonl"])
def test_log_write_failure_stops_the_loop(tmp_path, file_name):
    harness, _ = requesting_harness(tmp_path, neutral_output())
    # 追記できないように、同じ名前のディレクトリーを置く
    (harness.root / file_name).mkdir()
    add_frames(harness, [9.9])
    with pytest.raises(OSError):
        harness.loop.step()
    # どちらのファイルでも、run は全体を止める
    harness.loop.run(threading.Event())
    assert len(harness.stops) == 1
    assert harness.stops[0].startswith("surface loop failed")


@pytest.mark.parametrize("swallow", [False, True], ids=["raised", "swallowed"])
def test_request_log_write_failure_is_not_a_decide_failure(tmp_path, swallow):
    harness, _ = requesting_harness(
        tmp_path, neutral_output(), swallow_sink_error=swallow
    )
    (harness.root / "surface_requests.jsonl").mkdir()
    add_frames(harness, [9.9])
    # decide がその例外を出しても出さなくても、step の外に出す
    with pytest.raises(OSError):
        harness.loop.step()
    # decide の失敗には数えず、操作を送らず、surface.jsonl と run.jsonl にも書かない
    assert harness.loop.consecutive_errors == 0
    assert harness.sender.reports == []
    assert read_rows(harness.root / "surface.jsonl") == []
    assert read_rows(harness.root / "run.jsonl") == []
    assert harness.stops == []
