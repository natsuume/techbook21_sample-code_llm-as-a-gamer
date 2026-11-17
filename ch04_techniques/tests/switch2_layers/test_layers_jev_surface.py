"""Jev を呼ぶ SurfaceDecider。Jev は偽物 (FakeAsker) か、httpx の MockTransport で受け、外部には送らない。"""

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest

from switch2_layers.buttons import ALL_BUTTONS, allowed_buttons
from switch2_layers.deep.deep_scheduler import (
    REQUEST_PENDING,
    SKIP_CLOSED,
    SKIP_MIN_START_INTERVAL,
    DeepDocumentStatus,
    DeepJobEnd,
    DeepJobStatus,
    DeepRequestOutcome,
)
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.pad_action import PadAction
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import NO_PROGRESS_TEXT
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.box_targets import DEFAULT_BOX_TARGETS
from switch2_layers.surface.box_tracker import NormalizedBox, TrackingFrame, TrackResult
from switch2_layers.surface.box_tracking_state import BoxTrackingState
from switch2_layers.surface.jev_client import (
    DEFAULT_JEV_MODEL,
    IMAGES_HEADER,
    JevAnswers,
    JevBox,
    JevClient,
    JevError,
    JevImage,
)
from switch2_layers.surface.jev_surface import (
    ADVISOR_STATUS_UNAVAILABLE_TEXT,
    DEEP_JOB_STATUS_TEXTS,
    NO_TIMELINE_TEXT,
    NOT_MEASURED_TEXT,
    JevSurfaceDecider,
    JevSurfaceSettings,
    advisor_to_state,
    build_recorded_state,
    build_surface_state,
    deep_status_text,
    documents_to_state,
    progress_to_state,
    recent_actions_to_state,
    relative_time_label,
    timeline_to_state,
)
from switch2_layers.surface.surface_questions import (
    SURFACE_INSTRUCTIONS,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)
from switch2_layers.surface.surface_types import (
    Box,
    BoxSource,
    InfoLabel,
    RecentAction,
    SurfaceDocuments,
    SurfaceFrame,
    SurfaceInput,
    SurfaceRequest,
)

FRAME_TIMES = (9.0, 9.5, 10.0)
REQUEST_T = 10.0
DOCUMENTS = SurfaceDocuments(
    tasks="- 扉を開ける",
    tips="# 攻略情報",
    controls="- A: 決定",
    advice="# 助言",
    scene_id=None,
    uses_common_controls=True,
    uses_common_tips=True,
)
JEV_BOX = JevBox(top=0.2, bottom=0.4, left=0.1, right=0.3, confidence=None)
ALLOWED = allowed_buttons()
FAKE_MODEL = "jev-test"
# FRAME_TIMES の画像の保存を予約したファイル (2 枚目は保存を捨てた)
FILES = ("frames/surface/000009000.jpg", None, "frames/surface/000010000-2.jpg")


class FakeAsker:
    """JevAsker の偽物。問いごとに中立の答え (none 1.0、noul 0.0、box は JEV_BOX) を返し、overrides で置き換える。

    答えの usage は usage 属性の値。
    """

    model = FAKE_MODEL

    def __init__(self, overrides: Mapping[str, object] | None = None):
        self.overrides = dict(overrides or {})
        self.error: Exception | None = None
        self.usage: object | None = None
        self.calls: list[
            tuple[Mapping[str, object], list[SurfaceQuestion], list[JevImage]]
        ] = []

    def ask(
        self,
        state: Mapping[str, object],
        questions: Sequence[SurfaceQuestion],
        images: Sequence[JevImage],
    ) -> JevAnswers:
        self.calls.append((state, list(questions), list(images)))
        if self.error is not None:
            raise self.error
        choices: dict[str, dict[str, float]] = {}
        nouls: dict[str, float] = {}
        boxes: dict[str, JevBox | None] = {}
        for question in questions:
            value = self.overrides.get(question.name)
            if question.question_type is QuestionType.CHOICE:
                choices[question.name] = (
                    dict(value)
                    if value is not None
                    else {
                        choice.name: 1.0 if choice.name == "none" else 0.0
                        for choice in question.choices
                    }
                )
            elif question.question_type is QuestionType.NOUL:
                nouls[question.name] = float(value) if value is not None else 0.0
            elif question.name in self.overrides:
                # JevBox か、使える box が無いことを表す None
                boxes[question.name] = self.overrides[question.name]
            else:
                boxes[question.name] = JEV_BOX
        return JevAnswers(choices=choices, nouls=nouls, boxes=boxes, usage=self.usage)

    def questions_of(self, call: int) -> list[SurfaceQuestion]:
        return self.calls[call][1]


class StillTracker:
    """BoxTracker の偽物。start の box をいつも見えているものとして返す。"""

    def __init__(self):
        self.box: NormalizedBox | None = None

    def start(self, frame: TrackingFrame, box: NormalizedBox) -> None:
        self.box = box

    def update(self, frame: TrackingFrame) -> TrackResult:
        assert self.box is not None
        return TrackResult(self.box, 0.8, True)


def references():
    count = 0

    def make() -> str:
        nonlocal count
        count += 1
        return f"ref-{count}"

    return make


def captured(t: float) -> CapturedFrame:
    return CapturedFrame(t=t, nv12=np.full((54, 64), 128, np.uint8))


def surface_input(
    times: Sequence[float] = FRAME_TIMES,
    request_t: float = REQUEST_T,
    documents: SurfaceDocuments = DOCUMENTS,
    timeline: str = "",
    timeline_generated_t: float | None = None,
    recent_actions: tuple[RecentAction, ...] = (),
    files: Sequence[str | None] | None = None,
    request_sink: Callable[[SurfaceRequest], None] | None = None,
    advisor_statuses: Mapping[DocumentKind, DeepDocumentStatus | None] | None = None,
    progress: ProgressState | None = None,
) -> SurfaceInput:
    """files は times と同じ順の、画像の保存を予約したファイル (None なら、どの画像も file を持たない)。

    advisor_statuses が None なら、SurfaceInput の既定 (空) のままにする。
    """
    frame_files = files if files is not None else [None] * len(times)
    frames = tuple(
        SurfaceFrame(t=t, jpeg=f"jpeg-{t}".encode(), label=f"t={t:.3f}", file=file)
        for t, file in zip(times, frame_files, strict=True)
    )
    value = SurfaceInput(
        t=request_t,
        frames=frames,
        documents=documents,
        timeline=timeline,
        timeline_generated_t=timeline_generated_t,
        recent_actions=recent_actions,
        request_sink=request_sink,
        progress=progress,
    )
    if advisor_statuses is None:
        return value
    return dataclasses.replace(value, advisor_statuses=advisor_statuses)


def make_decider(
    tmp_path: Path,
    asker,
    allowed: frozenset[str] = ALLOWED,
    requery_every: int = 5,
    threshold: float = 0.5,
    with_notes: bool = True,
) -> tuple[JevSurfaceDecider, BoxTrackingState]:
    tracking = BoxTrackingState(tracker_factory=StillTracker)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)
    decider = JevSurfaceDecider(
        asker,
        tracking,
        JevSurfaceSettings(
            allowed_buttons=allowed,
            threshold_advisor=threshold,
            box_requery_every=requery_every,
        ),
        notes_dir=notes_dir if with_notes else None,
        make_reference=references(),
    )
    return decider, tracking


def feed(tracking: BoxTrackingState, times: Sequence[float]) -> None:
    for t in times:
        tracking.on_frame(captured(t))


def names_of(questions: Sequence[SurfaceQuestion], kind: QuestionKind) -> list[str]:
    return [question.name for question in questions if question.kind is kind]


def all_visible() -> dict[str, float]:
    return {f"box_exists_{i}": 0.9 for i in range(1, len(DEFAULT_BOX_TARGETS) + 1)}


# --- state の部品 ---


def test_relative_time_label():
    assert relative_time_label(12.345, 12.745) == "t=12.345 秒 (0.4 秒前)"
    assert relative_time_label(3.0, 3.0) == "t=3.000 秒 (0.0 秒前)"
    assert relative_time_label(3.5, 3.0) == "t=3.500 秒 (0.0 秒前)"


def test_documents_to_state():
    value = documents_to_state(DOCUMENTS)
    assert list(value) == ["tasks", "tips", "controls", "advice", "game"]
    # ゲームの性質が無い (読めない) 文書は、ほかの文書と同じく空文字列
    assert value == {
        "tasks": "- 扉を開ける",
        "tips": "# 攻略情報",
        "controls": "- A: 決定",
        "advice": "# 助言",
        "game": "",
    }


def test_documents_to_state_puts_the_game_document_last():
    documents = replace(DOCUMENTS, game="## ジャンル\nRPG\n")
    value = documents_to_state(documents)
    assert list(value) == ["tasks", "tips", "controls", "advice", "game"]
    assert value["game"] == "## ジャンル\nRPG\n"


def test_sent_and_recorded_states_include_the_game_document():
    surface = surface_input(documents=replace(DOCUMENTS, game="## ジャンル\nRPG\n"))
    sent = build_surface_state(surface, ["a", "b", "c"])
    recorded = build_recorded_state(surface)
    for state in [sent, recorded]:
        documents = state["documents"]
        assert isinstance(documents, dict)
        assert list(documents) == ["tasks", "tips", "controls", "advice", "game"]
        assert documents["game"] == "## ジャンル\nRPG\n"


def test_timeline_to_state():
    assert timeline_to_state("止まっている", 8.5, 10.0) == {
        "created": "t=8.500 秒 (1.5 秒前)",
        "text": "止まっている",
    }
    assert timeline_to_state("", None, 10.0) == {
        "created": NO_TIMELINE_TEXT,
        "text": "",
    }


def test_recent_actions_to_state():
    actions = (
        RecentAction(
            9.2, PadAction(held=frozenset({"A", "B"}), hold_seconds={"A": 0.3})
        ),
        RecentAction(9.8, PadAction(taps=frozenset({"B"}), left_stick=(0.5, 0.0))),
    )
    assert recent_actions_to_state(actions, 10.0) == [
        {
            "time": "t=9.200 秒 (0.8 秒前)",
            "held": ["A", "B"],
            "hold_seconds": {"A": 0.3},
            "taps": [],
            "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
        },
        {
            "time": "t=9.800 秒 (0.2 秒前)",
            "held": [],
            "hold_seconds": {},
            "taps": ["B"],
            "sticks": {"LS": [0.5, 0.0], "RS": [0.0, 0.0]},
        },
    ]


# --- state の advisor (deep の呼び出しの状況の文) ---

# 何も起きていない文書の状況と、その文
IDLE_STATUS = DeepDocumentStatus(
    running_count=0,
    latest_running_start_t=None,
    pending_request_t=None,
    last_end=None,
    min_interval_remaining_s=0.0,
    last_surface_request=None,
)
IDLE_TEXT = (
    "実行中のジョブ: なし。保留中の要求: なし。前回の終了: まだ無い。今求めれば起動する。"
    "surface はまだ求めていない"
)
# 「今求めたときの扱い」の 4 通りの文 (捨てられるときは、残りの秒数を後ろに足す)
REQUEST_NOW_DISCARDED = "今求めると捨てられる"
REQUEST_NOW_REPLACES = "今求めると保留中の要求を置き換える"
REQUEST_NOW_HELD = "今求めると保留され、実行中のジョブの終了後に起動する"
REQUEST_NOW_STARTS = "今求めれば起動する"


def sentences(*parts: str) -> str:
    """5 つの部分を「。」でつないだ文 (最後に「。」を付けない)。"""
    assert len(parts) == 5
    return "。".join(parts)


def test_deep_status_text_without_any_job():
    assert deep_status_text(IDLE_STATUS, 120.0) == IDLE_TEXT


def test_deep_status_text_of_unreadable_status():
    assert deep_status_text(None, 10.0) == "状況を取得できない"
    assert ADVISOR_STATUS_UNAVAILABLE_TEXT == "状況を取得できない"


def test_deep_status_text_with_every_part():
    status = DeepDocumentStatus(
        running_count=1,
        latest_running_start_t=120.3,
        pending_request_t=130.3,
        last_end=DeepJobEnd(100.0, DeepJobStatus.OK),
        min_interval_remaining_s=0.0,
        last_surface_request=DeepRequestOutcome(
            130.3, started=False, reason=REQUEST_PENDING
        ),
    )
    assert deep_status_text(status, 135.5) == sentences(
        "実行中のジョブ: 1 件 (最も新しい起動 t=120.300 秒 (15.2 秒前))",
        "保留中の要求: t=130.300 秒 (5.2 秒前)",
        "前回の終了: t=100.000 秒 (35.5 秒前)、正常に終了",
        REQUEST_NOW_REPLACES,
        "surface が最後に求めた: t=130.300 秒 (5.2 秒前)、保留した",
    )


def test_deep_status_text_with_one_running_job():
    status = dataclasses.replace(
        IDLE_STATUS, running_count=1, latest_running_start_t=9.0
    )
    assert deep_status_text(status, 10.0) == sentences(
        "実行中のジョブ: 1 件 (最も新しい起動 t=9.000 秒 (1.0 秒前))",
        "保留中の要求: なし",
        "前回の終了: まだ無い",
        REQUEST_NOW_HELD,
        "surface はまだ求めていない",
    )


def test_deep_job_status_texts_cover_every_status():
    assert set(DEEP_JOB_STATUS_TEXTS) == set(DeepJobStatus)


@pytest.mark.parametrize(
    "job_status, text",
    [
        (DeepJobStatus.OK, "正常に終了"),
        (DeepJobStatus.FAILED, "文書を更新できずに終了"),
        (DeepJobStatus.ERROR, "エラーで終了"),
        (DeepJobStatus.STOPPED, "止めた"),
        (DeepJobStatus.KILLED, "強制終了した"),
    ],
    ids=["ok", "failed", "error", "stopped", "killed"],
)
def test_deep_status_text_says_how_the_last_job_ended(job_status, text):
    status = dataclasses.replace(IDLE_STATUS, last_end=DeepJobEnd(8.0, job_status))
    assert deep_status_text(status, 10.0) == sentences(
        "実行中のジョブ: なし",
        "保留中の要求: なし",
        f"前回の終了: t=8.000 秒 (2.0 秒前)、{text}",
        REQUEST_NOW_STARTS,
        "surface はまだ求めていない",
    )


def test_deep_status_text_says_when_the_pending_request_was_made():
    status = dataclasses.replace(
        IDLE_STATUS,
        running_count=1,
        latest_running_start_t=2.0,
        pending_request_t=7.25,
    )
    assert deep_status_text(status, 10.0) == sentences(
        "実行中のジョブ: 1 件 (最も新しい起動 t=2.000 秒 (8.0 秒前))",
        "保留中の要求: t=7.250 秒 (2.8 秒前)",
        "前回の終了: まだ無い",
        REQUEST_NOW_REPLACES,
        "surface はまだ求めていない",
    )


@pytest.mark.parametrize(
    "remaining, running_count, pending_request_t, text",
    [
        (0.0, 0, None, REQUEST_NOW_STARTS),
        (0.04, 0, None, f"{REQUEST_NOW_DISCARDED} (あと 0.0 秒)"),
        (0.5, 0, None, f"{REQUEST_NOW_DISCARDED} (あと 0.5 秒)"),
        (0.0, 1, None, REQUEST_NOW_HELD),
        (0.0, 1, 8.0, REQUEST_NOW_REPLACES),
        # 最小の起動間隔の中なら、実行中のジョブや保留中の要求があっても捨てられる
        (11.5, 1, None, f"{REQUEST_NOW_DISCARDED} (あと 11.5 秒)"),
        (2.0, 1, 8.0, f"{REQUEST_NOW_DISCARDED} (あと 2.0 秒)"),
    ],
    ids=[
        "starts",
        "tiny",
        "half",
        "held",
        "replaces",
        "discarded_while_running",
        "discarded_before_replacing",
    ],
)
def test_deep_status_text_says_what_a_request_now_would_do(
    remaining, running_count, pending_request_t, text
):
    status = dataclasses.replace(
        IDLE_STATUS,
        running_count=running_count,
        latest_running_start_t=5.0 if running_count else None,
        pending_request_t=pending_request_t,
        min_interval_remaining_s=remaining,
    )
    running = (
        "実行中のジョブ: 1 件 (最も新しい起動 t=5.000 秒 (5.0 秒前))"
        if running_count
        else "実行中のジョブ: なし"
    )
    pending = (
        "保留中の要求: なし"
        if pending_request_t is None
        else "保留中の要求: t=8.000 秒 (2.0 秒前)"
    )
    assert deep_status_text(status, 10.0) == sentences(
        running,
        pending,
        "前回の終了: まだ無い",
        text,
        "surface はまだ求めていない",
    )


@pytest.mark.parametrize(
    "outcome, text",
    [
        (DeepRequestOutcome(7.5, started=True), "、起動した"),
        (
            DeepRequestOutcome(7.5, started=False, reason=SKIP_MIN_START_INTERVAL),
            "、前回の起動から間もないため捨てられた",
        ),
        (
            DeepRequestOutcome(7.5, started=False, reason=REQUEST_PENDING),
            "、保留した",
        ),
        (
            DeepRequestOutcome(7.5, started=False, reason=SKIP_CLOSED),
            "、捨てられた (closed)",
        ),
    ],
    ids=["started", "min_interval", "pending", "other_reason"],
)
def test_deep_status_text_says_the_result_of_last_surface_request(outcome, text):
    status = dataclasses.replace(IDLE_STATUS, last_surface_request=outcome)
    assert deep_status_text(status, 10.0) == sentences(
        "実行中のジョブ: なし",
        "保留中の要求: なし",
        "前回の終了: まだ無い",
        REQUEST_NOW_STARTS,
        f"surface が最後に求めた: t=7.500 秒 (2.5 秒前){text}",
    )


def test_deep_status_text_times_after_the_request_are_zero_seconds_ago():
    status = dataclasses.replace(
        IDLE_STATUS,
        running_count=1,
        latest_running_start_t=10.2,
        pending_request_t=10.4,
        last_surface_request=DeepRequestOutcome(
            10.4, started=False, reason=REQUEST_PENDING
        ),
    )
    assert deep_status_text(status, 10.0) == sentences(
        "実行中のジョブ: 1 件 (最も新しい起動 t=10.200 秒 (0.0 秒前))",
        "保留中の要求: t=10.400 秒 (0.0 秒前)",
        "前回の終了: まだ無い",
        REQUEST_NOW_REPLACES,
        "surface が最後に求めた: t=10.400 秒 (0.0 秒前)、保留した",
    )


# 種類ごとに違う状況 (advisor の文が種類ごとに入ることを確かめる)
CONTROLS_STATUS = dataclasses.replace(
    IDLE_STATUS, running_count=1, latest_running_start_t=6.0
)
CONTROLS_TEXT = sentences(
    "実行中のジョブ: 1 件 (最も新しい起動 t=6.000 秒 (4.0 秒前))",
    "保留中の要求: なし",
    "前回の終了: まだ無い",
    REQUEST_NOW_HELD,
    "surface はまだ求めていない",
)
STRATEGY_STATUS = dataclasses.replace(
    IDLE_STATUS,
    last_end=DeepJobEnd(9.5, DeepJobStatus.KILLED),
    min_interval_remaining_s=3.0,
)
STRATEGY_TEXT = sentences(
    "実行中のジョブ: なし",
    "保留中の要求: なし",
    "前回の終了: t=9.500 秒 (0.5 秒前)、強制終了した",
    f"{REQUEST_NOW_DISCARDED} (あと 3.0 秒)",
    "surface はまだ求めていない",
)
ADVISOR_STATUSES: dict[DocumentKind, DeepDocumentStatus | None] = {
    DocumentKind.ADVICE: IDLE_STATUS,
    DocumentKind.STRATEGY: STRATEGY_STATUS,
    DocumentKind.TIPS: None,
    DocumentKind.CONTROLS: CONTROLS_STATUS,
}
# REQUEST_T (10.0) の要求での、ADVISOR_STATUSES の advisor (渡していない game は状況を取得できない)
ADVISOR_STATE = {
    "controls": CONTROLS_TEXT,
    "tips": "状況を取得できない",
    "strategy": STRATEGY_TEXT,
    "advice": IDLE_TEXT,
    "game": "状況を取得できない",
}


def test_advisor_to_state_lists_advisor_kinds_in_order():
    value = advisor_to_state(ADVISOR_STATUSES, REQUEST_T)
    # 渡した順ではなく ADVISOR_KINDS の順
    assert list(value) == ["controls", "tips", "strategy", "advice", "game"]
    assert value == ADVISOR_STATE


def test_advisor_to_state_treats_missing_kinds_as_unreadable_and_ignores_others():
    value = advisor_to_state(
        {DocumentKind.ADVICE: IDLE_STATUS, DocumentKind.MAP: IDLE_STATUS}, 10.0
    )
    assert value == {
        "controls": "状況を取得できない",
        "tips": "状況を取得できない",
        "strategy": "状況を取得できない",
        "advice": IDLE_TEXT,
        "game": "状況を取得できない",
    }
    assert advisor_to_state({}, 10.0) == {
        "controls": "状況を取得できない",
        "tips": "状況を取得できない",
        "strategy": "状況を取得できない",
        "advice": "状況を取得できない",
        "game": "状況を取得できない",
    }


def test_progress_to_state():
    progress = ProgressState(t=9.8, static_s=12.4, no_novel_s=85.0, window_s=300.0)
    assert progress_to_state(progress, 10.0) == {
        "measured": "t=9.800 秒 (0.2 秒前)",
        "text": (
            "画面が変わっていない: 12.4 秒\n"
            "直近 300 秒に見た画面だけが出ている: 85.0 秒"
        ),
    }
    assert progress_to_state(None, 10.0) == {
        "measured": NOT_MEASURED_TEXT,
        "text": NO_PROGRESS_TEXT,
    }


def test_build_surface_state_has_keys_in_order():
    progress = ProgressState(t=9.5, static_s=1.0, no_novel_s=70.0, window_s=300.0)
    state = build_surface_state(
        surface_input(timeline="系列", timeline_generated_t=8.5, progress=progress),
        ["a", "b", "c"],
    )
    # advisor は recent_actions の直後、frames の前
    assert list(state) == [
        "instructions",
        "documents",
        "timeline",
        "progress",
        "recent_actions",
        "advisor",
        "frames",
    ]
    assert state["instructions"] == SURFACE_INSTRUCTIONS
    assert state["documents"] == documents_to_state(DOCUMENTS)
    assert state["timeline"] == {"created": "t=8.500 秒 (1.5 秒前)", "text": "系列"}
    assert state["progress"] == progress_to_state(progress, REQUEST_T)
    assert state["recent_actions"] == []
    # 状況を渡していない (既定の空) ので、どの種類も状況を取得できない
    assert state["advisor"] == {
        "controls": "状況を取得できない",
        "tips": "状況を取得できない",
        "strategy": "状況を取得できない",
        "advice": "状況を取得できない",
        "game": "状況を取得できない",
    }
    assert state["frames"] == [
        {"label": "t=9.000 秒 (1.0 秒前)", "image": "a"},
        {"label": "t=9.500 秒 (0.5 秒前)", "image": "b"},
        {"label": "t=10.000 秒 (0.0 秒前)", "image": "c"},
    ]


def test_build_surface_state_says_when_there_is_no_progress():
    state = build_surface_state(surface_input(), ["a", "b", "c"])
    assert state["progress"] == {
        "measured": NOT_MEASURED_TEXT,
        "text": NO_PROGRESS_TEXT,
    }


def test_build_surface_state_needs_one_reference_per_frame():
    with pytest.raises(ValueError):
        build_surface_state(surface_input(), ["a", "b"])


def test_build_surface_state_puts_advisor_statuses_counted_from_request_time():
    state = build_surface_state(
        surface_input(advisor_statuses=ADVISOR_STATUSES), ["a", "b", "c"]
    )
    assert state["advisor"] == ADVISOR_STATE
    # 「何秒前」は要求の時刻から数える
    later = build_surface_state(
        surface_input(request_t=12.0, advisor_statuses=ADVISOR_STATUSES),
        ["a", "b", "c"],
    )
    assert later["advisor"]["controls"].startswith(
        "実行中のジョブ: 1 件 (最も新しい起動 t=6.000 秒 (6.0 秒前))"
    )


def test_recorded_state_has_the_same_advisor_as_sent_state():
    surface = surface_input(advisor_statuses=ADVISOR_STATUSES, files=FILES)
    sent = build_surface_state(surface, ["a", "b", "c"])
    recorded = build_recorded_state(surface)
    assert "advisor" in recorded
    assert recorded["advisor"] == sent["advisor"] == ADVISOR_STATE
    assert list(recorded) == list(sent)


# --- 要求 ---


def test_decide_before_run_directory_is_an_error_without_calling_jev(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker, with_notes=False)
    with pytest.raises(RuntimeError):
        decider.decide(surface_input())
    assert asker.calls == []


def test_use_run_directory_sets_notes_dir(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker, with_notes=False)
    root = tmp_path / "run"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "notes" / "common" / "box_targets.md").write_text(
        "- 扉: 木の扉\n", encoding="utf-8"
    )
    decider.use_run_directory(RunPaths(root))
    decider.decide(surface_input())
    exists = [q for q in asker.questions_of(0) if q.kind is QuestionKind.BOX_EXISTS]
    assert [question.target for question in exists] == ["扉"]


def test_request_sends_state_images_and_all_question_kinds(tmp_path):
    asker = FakeAsker()
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    decider.decide(surface_input(recent_actions=(RecentAction(9.8, PadAction()),)))
    state, questions, images = asker.calls[0]
    assert [image.reference for image in images] == ["ref-1", "ref-2", "ref-3"]
    assert [image.jpeg for image in images] == [b"jpeg-9.0", b"jpeg-9.5", b"jpeg-10.0"]
    assert [frame["image"] for frame in state["frames"]] == ["ref-1", "ref-2", "ref-3"]
    assert state["recent_actions"][0]["time"] == "t=9.800 秒 (0.2 秒前)"
    assert len(names_of(questions, QuestionKind.BUTTON)) == 16
    assert "button_HOME" not in names_of(questions, QuestionKind.BUTTON)
    assert len(names_of(questions, QuestionKind.STICK_DIRECTION)) == 4
    assert len(names_of(questions, QuestionKind.STICK_STRENGTH)) == 12
    assert names_of(questions, QuestionKind.ADVISOR) == [
        "advisor_controls",
        "advisor_tips",
        "advisor_strategy",
        "advisor_advice",
        "advisor_game",
    ]
    assert len(names_of(questions, QuestionKind.INFO_LABEL)) == 5
    assert names_of(questions, QuestionKind.SCENE_CHANGE) == ["scene_changed"]
    # 対象の一覧のファイルが無いので固定の 4 つ。最初の要求では、どれもまだ box が無いので問い直す
    assert len(names_of(questions, QuestionKind.BOX_EXISTS)) == 4
    assert names_of(questions, QuestionKind.BOX) == ["box_1", "box_2", "box_3", "box_4"]
    # 最新のフレームは、参照文字列ではなく state の中のパスで指す
    for question in questions:
        assert "ref-" not in question.instructions
        if question.kind in (QuestionKind.BOX, QuestionKind.BOX_EXISTS):
            assert "`frames[2].image`" in question.instructions


def test_home_question_is_sent_only_when_allowed(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker, allowed=frozenset(ALL_BUTTONS))
    decider.decide(surface_input())
    assert "button_HOME" in names_of(asker.questions_of(0), QuestionKind.BUTTON)


def test_request_count_counts_decide_calls(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    assert decider.request_count == 0
    decider.decide(surface_input())
    decider.decide(surface_input())
    assert decider.request_count == 2


def test_jev_error_is_raised_as_surface_failure(tmp_path):
    asker = FakeAsker()
    asker.error = JevError("HTTP 500")
    decider, _ = make_decider(tmp_path, asker)
    with pytest.raises(JevError):
        decider.decide(surface_input())


def test_request_json_through_mock_transport(tmp_path):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
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
                answers[name] = {
                    "type": "noul",
                    "noul": 0.9 if name == "box_exists_1" else 0.0,
                }
            else:
                answers[name] = {
                    "type": "box",
                    "box": {"top": 0.2, "bottom": 0.4, "left": 0.1, "right": 0.3},
                    "confidence": 0.75,
                }
        return httpx.Response(
            200, json={"model": "jev-1.14.0", "answers": answers, "usage": {}}
        )

    client = JevClient(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    tracking = BoxTrackingState(tracker_factory=StillTracker)
    (tmp_path / "notes").mkdir()
    decider = JevSurfaceDecider(
        client,
        tracking,
        JevSurfaceSettings(allowed_buttons=ALLOWED),
        notes_dir=tmp_path / "notes",
        make_reference=references(),
    )
    feed(tracking, FRAME_TIMES)
    output = decider.decide(surface_input())
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers[IMAGES_HEADER] == "true"
    payload = json.loads(request.content)
    assert payload["model"] == "jev-1.14.0"
    assert set(payload["assets"]) == {"ref-1", "ref-2", "ref-3"}
    without_assets = json.dumps({k: v for k, v in payload.items() if k != "assets"})
    for reference in ["ref-1", "ref-2", "ref-3"]:
        assert without_assets.count(reference) == 1
    assert payload["questions"]["box_1"] == {
        "type": "box",
        "instructions": "`frames[2].image` の中の「自機」(プレイヤーが操作しているキャラクター、乗り物、またはカーソル) を囲む box。",
    }
    assert output.boxes == (Box("自機", 0.1, 0.2, 0.3, 0.4, 0.75, BoxSource.JEV),)


# --- 出力 ---


def test_output_follows_answers(tmp_path):
    asker = FakeAsker(
        {
            "button_A": {"none": 0.1, "tap": 0.1, "hold_short": 0.0, "hold_long": 0.8},
            "stick_LS_x": {"none": 0.0, "left": 0.0, "right": 1.0},
            "advisor_tips": 0.6,
            "advisor_advice": 0.5,
            "info_stuck": 0.7,
            "scene_changed": 0.8,
        }
    )
    decider, _ = make_decider(tmp_path, asker)
    output = decider.decide(surface_input())
    assert output.action == PadAction(
        held=frozenset({"A"}), hold_seconds={"A": 1.0}, left_stick=(0.25, 0.0)
    )
    assert output.deep_kinds == frozenset({DocumentKind.TIPS})
    assert output.info_labels == (
        InfoLabel("controls", 0.0),
        InfoLabel("tips", 0.0),
        InfoLabel("strategy", 0.0),
        InfoLabel("stuck", 0.7),
        InfoLabel("map", 0.0),
    )
    assert output.scene_changed is True


def test_advisor_game_above_threshold_asks_for_the_game_document(tmp_path):
    asker = FakeAsker({"advisor_game": 0.7, "advisor_tips": 0.5})
    decider, _ = make_decider(tmp_path, asker)
    assert decider.decide(surface_input()).deep_kinds == frozenset({DocumentKind.GAME})


def test_threshold_advisor_is_used(tmp_path):
    asker = FakeAsker({"advisor_tips": 0.6, "advisor_strategy": 0.9})
    decider, _ = make_decider(tmp_path, asker, threshold=0.8)
    assert decider.decide(surface_input()).deep_kinds == frozenset(
        {DocumentKind.STRATEGY}
    )


def test_jev_boxes_are_output_with_null_confidence_and_hidden_targets_are_dropped(
    tmp_path,
):
    asker = FakeAsker({"box_exists_1": 0.9, "box_exists_2": 0.49, "box_exists_3": 0.5})
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    output = decider.decide(surface_input())
    assert output.boxes == (
        Box("自機", 0.1, 0.2, 0.3, 0.4, None, BoxSource.JEV),
        Box("敵の攻撃", 0.1, 0.2, 0.3, 0.4, None, BoxSource.JEV),
    )
    # 見えている対象は、問い合わせた画像 (最新のフレーム) で追跡を始める
    assert set(tracking.statuses()) == {"自機", "敵の攻撃"}


def test_unusable_jev_box_is_not_output_and_is_requeried(tmp_path):
    asker = FakeAsker({**all_visible(), "box_1": None})
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    output = decider.decide(surface_input())
    assert [box.label for box in output.boxes] == ["敵", "敵の攻撃", "目標地点"]
    assert "自機" not in tracking.statuses()
    feed(tracking, [10.2])
    del asker.overrides["box_1"]
    decider.decide(surface_input(times=(9.5, 10.0, 10.2), request_t=10.2))
    assert names_of(asker.questions_of(1), QuestionKind.BOX) == ["box_1"]


def test_jev_box_is_output_even_when_tracking_cannot_start(tmp_path):
    asker = FakeAsker({"box_exists_1": 0.9})
    decider, tracking = make_decider(tmp_path, asker)
    # 追跡の側に、問い合わせたフレーム (t = 10.0) が無い
    feed(tracking, [9.0, 9.5])
    output = decider.decide(surface_input())
    assert [box.source for box in output.boxes] == [BoxSource.JEV]
    assert tracking.statuses() == {}


def test_tracked_targets_are_not_requeried_until_n_requests(tmp_path):
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker, requery_every=2)
    feed(tracking, FRAME_TIMES)
    first = decider.decide(surface_input())
    assert all(box.source is BoxSource.JEV for box in first.boxes)
    feed(tracking, [10.2, 10.4])
    second = decider.decide(surface_input(times=(10.0, 10.2, 10.4), request_t=10.4))
    # 2 回目: どの対象も追えているので box は問わず、追跡の位置を出す
    assert names_of(asker.questions_of(1), QuestionKind.BOX) == []
    assert len(names_of(asker.questions_of(1), QuestionKind.BOX_EXISTS)) == 4
    assert [box.source for box in second.boxes] == [BoxSource.TRACKER] * 4
    assert second.boxes[0] == Box("自機", 0.1, 0.2, 0.3, 0.4, 0.8, BoxSource.TRACKER)
    feed(tracking, [10.6])
    decider.decide(surface_input(times=(10.2, 10.4, 10.6), request_t=10.6))
    # 3 回目: 前回の問い直し (1 回目) から 2 回たったので問い直す
    assert names_of(asker.questions_of(2), QuestionKind.BOX) == [
        "box_1",
        "box_2",
        "box_3",
        "box_4",
    ]


def test_hidden_target_stops_tracking_and_is_requeried_next_time(tmp_path):
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    decider.decide(surface_input())
    asker.overrides["box_exists_1"] = 0.2
    feed(tracking, [10.2])
    second = decider.decide(surface_input(times=(9.5, 10.0, 10.2), request_t=10.2))
    assert "自機" not in [box.label for box in second.boxes]
    assert "自機" not in tracking.statuses()
    feed(tracking, [10.4])
    decider.decide(surface_input(times=(10.0, 10.2, 10.4), request_t=10.4))
    assert names_of(asker.questions_of(2), QuestionKind.BOX) == ["box_1"]


def test_target_whose_description_changed_is_requeried(tmp_path):
    # 名前が同じでも説明が変われば別の物を指しうるため、その対象の追跡をやめて次の要求で問い直す
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker)
    targets_file = tmp_path / "notes" / "common" / "box_targets.md"
    targets_file.parent.mkdir(parents=True)
    targets_file.write_text("- 敵: クリボー\n- 扉: 木の扉\n", encoding="utf-8")
    feed(tracking, FRAME_TIMES)
    decider.decide(surface_input())
    assert names_of(asker.questions_of(0), QuestionKind.BOX) == ["box_1", "box_2"]
    targets_file.write_text("- 敵: ノコノコ\n- 扉: 木の扉\n", encoding="utf-8")
    feed(tracking, [10.2])
    second = decider.decide(surface_input(times=(9.5, 10.0, 10.2), request_t=10.2))
    assert names_of(asker.questions_of(1), QuestionKind.BOX) == ["box_1"]
    assert [(box.label, box.source) for box in second.boxes] == [
        ("敵", BoxSource.JEV),
        ("扉", BoxSource.TRACKER),
    ]


def test_failed_request_does_not_count_as_requery(tmp_path):
    asker = FakeAsker(all_visible())
    asker.error = JevError("timeout")
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    with pytest.raises(JevError):
        decider.decide(surface_input())
    asker.error = None
    decider.decide(surface_input())
    assert len(names_of(asker.questions_of(1), QuestionKind.BOX)) == 4


def test_box_at_frame_where_tracking_restarted_follows_drawn_box(tmp_path):
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    decider.decide(surface_input())
    # 新しいフレームが届かず、次の要求の最新のフレームも t = 10.0 (前の要求で Jev の box から追跡を始め直したフレーム)
    second = decider.decide(surface_input(request_t=10.3))
    assert names_of(asker.questions_of(1), QuestionKind.BOX) == []
    # 出力の box は、送る画像に描く box (boxes_at) と同じ: そのフレームの Jev の box
    expected = tuple(
        Box(target.name, 0.1, 0.2, 0.3, 0.4, None, BoxSource.JEV)
        for target in DEFAULT_BOX_TARGETS
    )
    assert second.boxes == expected
    assert tracking.boxes_at(10.0) == expected


# --- 送る画像への書き込み方 (plan_boxes) ---


def test_plan_boxes_gives_target_numbers_and_requeried_targets(tmp_path):
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker, requery_every=2)
    feed(tracking, FRAME_TIMES)
    first_plan = decider.plan_boxes(None)
    assert first_plan.target_numbers == {
        "自機": 1,
        "敵": 2,
        "敵の攻撃": 3,
        "目標地点": 4,
    }
    # 最初の要求では、どの対象もまだ box が無いので問い直す (最新のフレームには書き込まない)
    assert first_plan.hidden_on_latest == frozenset(first_plan.target_numbers)
    decider.decide(surface_input())
    feed(tracking, [10.2, 10.4])
    # 2 回目: どの対象も追えていて、問い直さない
    assert decider.plan_boxes(None).hidden_on_latest == frozenset()
    decider.decide(surface_input(times=(10.0, 10.2, 10.4), request_t=10.4))
    feed(tracking, [10.6])
    # 3 回目: 前回の問い直しから 2 回たったので、すべて問い直す
    assert decider.plan_boxes(None).hidden_on_latest == frozenset(
        first_plan.target_numbers
    )


def test_plan_boxes_reads_scene_targets(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    scene_dir = tmp_path / "notes" / "scenes" / "scene-003"
    scene_dir.mkdir(parents=True)
    (scene_dir / "box_targets.md").write_text(
        "- 扉: 木の扉\n- 鍵: 黄色い鍵\n", encoding="utf-8"
    )
    assert decider.plan_boxes("scene-003").target_numbers == {"扉": 1, "鍵": 2}


def test_decide_uses_planned_requery_targets(tmp_path):
    asker = FakeAsker(all_visible())
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    decider.decide(surface_input())
    feed(tracking, [10.2])
    plan = decider.plan_boxes(None)
    assert plan.hidden_on_latest == frozenset()
    # 書き込み方を決めた後に自機を見失っても、この要求では問い直さない (画像には自機を書き込んだため)
    tracking.forget("自機")
    decider.decide(surface_input(times=(9.5, 10.0, 10.2), request_t=10.2))
    assert names_of(asker.questions_of(1), QuestionKind.BOX) == []
    # 次の要求で問い直す
    feed(tracking, [10.4])
    assert decider.plan_boxes(None).hidden_on_latest == frozenset({"自機"})
    decider.decide(surface_input(times=(10.0, 10.2, 10.4), request_t=10.4))
    assert names_of(asker.questions_of(2), QuestionKind.BOX) == ["box_1"]


def test_plan_for_another_scene_is_not_used(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    scene_dir = tmp_path / "notes" / "scenes" / "scene-003"
    scene_dir.mkdir(parents=True)
    (scene_dir / "box_targets.md").write_text("- 扉: 木の扉\n", encoding="utf-8")
    decider.plan_boxes("scene-003")
    # decide の文書の場面 (場面なし) と違うので、対象の一覧を読み直す
    decider.decide(surface_input())
    exists = names_of(asker.questions_of(0), QuestionKind.BOX_EXISTS)
    assert len(exists) == len(DEFAULT_BOX_TARGETS)


def test_plan_boxes_before_run_directory_is_an_error(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker, with_notes=False)
    with pytest.raises(RuntimeError):
        decider.plan_boxes(None)


# --- 送る要求の記録 (request_sink) と、記録のための情報 (exchange) ---


class RequestSink:
    """request_sink の偽物。受け取った要求と、受け取ったときまでに Jev に送った回数を記録する。"""

    def __init__(self, asker: FakeAsker, error: Exception | None = None):
        self.asker = asker
        self.error = error
        self.requests: list[SurfaceRequest] = []
        self.sent_before: list[int] = []

    def __call__(self, request: SurfaceRequest) -> None:
        self.requests.append(request)
        self.sent_before.append(len(self.asker.calls))
        if self.error is not None:
            raise self.error


def without_frames(state: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in state.items() if key != "frames"}


def test_recorded_state_replaces_image_references_with_files():
    surface = surface_input(
        timeline="系列",
        timeline_generated_t=8.5,
        recent_actions=(RecentAction(9.8, PadAction(held=frozenset({"A"}))),),
        files=FILES,
    )
    sent = build_surface_state(surface, ["a", "b", "c"])
    recorded = build_recorded_state(surface)
    # 送る state と同じキー・同じ内容で、画像だけを保存したファイル (保存を捨てた画像は None) にする
    assert list(recorded) == list(sent)
    assert without_frames(recorded) == without_frames(sent)
    assert recorded["frames"] == [
        {"label": "t=9.000 秒 (1.0 秒前)", "image": "frames/surface/000009000.jpg"},
        {"label": "t=9.500 秒 (0.5 秒前)", "image": None},
        {"label": "t=10.000 秒 (0.0 秒前)", "image": "frames/surface/000010000-2.jpg"},
    ]


def test_request_is_passed_to_sink_once_just_before_sending(tmp_path):
    asker = FakeAsker()
    decider, tracking = make_decider(tmp_path, asker)
    feed(tracking, FRAME_TIMES)
    sink = RequestSink(asker)
    surface = surface_input(files=FILES, request_sink=sink)
    decider.decide(surface)
    # 1 回だけ、Jev に送る前に渡す
    assert sink.sent_before == [0]
    assert len(asker.calls) == 1
    request = sink.requests[0]
    state, questions, _ = asker.calls[0]
    assert request.request_index == 1
    assert request.model == FAKE_MODEL
    assert request.questions == tuple(questions)
    # 記録の state は、送った state と同じ内容で、画像の参照文字列だけを保存したファイルに置き換えたもの
    assert request.state == build_recorded_state(surface)
    assert without_frames(request.state) == without_frames(state)
    assert [frame["image"] for frame in request.state["frames"]] == list(FILES)
    assert "ref-" not in json.dumps(request.state, ensure_ascii=False)


def test_sent_and_recorded_requests_carry_the_advisor_of_the_input(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    sink = RequestSink(asker)
    decider.decide(
        surface_input(advisor_statuses=ADVISOR_STATUSES, files=FILES, request_sink=sink)
    )
    state, _, _ = asker.calls[0]
    assert state["advisor"] == ADVISOR_STATE
    # surface_requests.jsonl に書く state にも、送った state と同じ advisor が入る
    assert sink.requests[0].state["advisor"] == ADVISOR_STATE


def test_output_exchange_has_the_sent_request_and_the_answers(tmp_path):
    button_a = {"none": 0.1, "tap": 0.05, "hold_short": 0.8, "hold_long": 0.05}
    asker = FakeAsker({"button_A": button_a, "scene_changed": 0.8})
    asker.usage = {"input_tokens": 100, "output_tokens": 2}
    decider, _ = make_decider(tmp_path, asker)
    sink = RequestSink(asker)
    output = decider.decide(surface_input(request_sink=sink))
    exchange = output.exchange
    assert exchange is not None
    # 書き込み先に渡したものと同じ要求
    assert exchange.request == sink.requests[0]
    assert exchange.answers.choices["button_A"] == button_a
    assert exchange.answers.nouls["scene_changed"] == 0.8
    assert exchange.answers.usage == {"input_tokens": 100, "output_tokens": 2}


def test_output_exchange_without_sink(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    output = decider.decide(surface_input())
    assert output.exchange is not None
    assert output.exchange.request.request_index == 1
    assert output.exchange.request.questions == tuple(asker.questions_of(0))
    assert output.exchange.answers.usage is None


def test_request_failing_after_sending_is_recorded_and_numbers_continue(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    sink = RequestSink(asker)
    asker.error = JevError("HTTP 500")
    with pytest.raises(JevError):
        decider.decide(surface_input(request_sink=sink))
    asker.error = None
    output = decider.decide(surface_input(request_sink=sink))
    # 送った後に失敗した要求も書き込み先に渡す。番号は decide の回数と同じに数える
    assert [request.request_index for request in sink.requests] == [1, 2]
    assert sink.sent_before == [0, 1]
    assert output.exchange is not None
    assert output.exchange.request.request_index == decider.request_count == 2


def test_request_failing_before_sending_is_not_passed_to_sink(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker, with_notes=False)
    sink = RequestSink(asker)
    with pytest.raises(RuntimeError):
        decider.decide(surface_input(request_sink=sink))
    assert sink.requests == []
    assert asker.calls == []


def test_sink_error_is_raised_without_sending(tmp_path):
    asker = FakeAsker()
    decider, _ = make_decider(tmp_path, asker)
    sink = RequestSink(asker, error=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        decider.decide(surface_input(request_sink=sink))
    assert len(sink.requests) == 1
    assert asker.calls == []


def test_recorded_request_through_mock_transport(tmp_path):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = json.loads(request.content)
        answers: dict[str, object] = {}
        for name, question in payload["questions"].items():
            if question["type"] == "choice":
                answers[name] = {
                    "type": "choice",
                    "probabilities": {
                        choice: 1.0 if choice == "none" else 0.0
                        for choice in question["criteria"]
                    },
                }
            elif question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.0}
            else:
                answers[name] = {
                    "type": "box",
                    "box": {"top": 0.2, "bottom": 0.4, "left": 0.1, "right": 0.3},
                    "confidence": None,
                }
        usage = {"input_tokens": 7, "images": [1, 2, 3]}
        return httpx.Response(
            200, json={"model": DEFAULT_JEV_MODEL, "answers": answers, "usage": usage}
        )

    client = JevClient(
        "secret-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    (tmp_path / "notes").mkdir()
    decider = JevSurfaceDecider(
        client,
        BoxTrackingState(tracker_factory=StillTracker),
        JevSurfaceSettings(allowed_buttons=ALLOWED),
        notes_dir=tmp_path / "notes",
        make_reference=references(),
    )
    received: list[SurfaceRequest] = []
    output = decider.decide(surface_input(files=FILES, request_sink=received.append))
    assert len(seen) == 1
    payload = json.loads(seen[0].content)
    request = received[0]
    assert request.model == DEFAULT_JEV_MODEL
    assert [question.name for question in request.questions] == list(
        payload["questions"]
    )
    assert without_frames(request.state) == without_frames(payload["state"])
    assert [frame["image"] for frame in request.state["frames"]] == list(FILES)
    # 応答の usage を受け取った形のまま持つ
    assert output.exchange is not None
    assert output.exchange.request == request
    assert output.exchange.answers.usage == {"input_tokens": 7, "images": [1, 2, 3]}
