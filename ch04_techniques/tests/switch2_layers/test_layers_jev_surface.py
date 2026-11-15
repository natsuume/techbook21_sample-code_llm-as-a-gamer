"""Jev を呼ぶ SurfaceDecider。Jev は偽物 (FakeAsker) か、httpx の MockTransport で受け、外部には送らない。"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import numpy as np
import pytest

from switch2_layers.buttons import allowed_buttons
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.pad_action import PadAction
from switch2_layers.run_directory import RunPaths
from switch2_layers.surface.box_targets import DEFAULT_BOX_TARGETS
from switch2_layers.surface.box_tracker import NormalizedBox, TrackingFrame, TrackResult
from switch2_layers.surface.box_tracking_state import BoxTrackingState
from switch2_layers.surface.jev_client import (
    IMAGES_HEADER,
    JevAnswers,
    JevBox,
    JevClient,
    JevError,
    JevImage,
)
from switch2_layers.surface.jev_surface import (
    NO_TIMELINE_TEXT,
    JevSurfaceDecider,
    JevSurfaceSettings,
    build_surface_state,
    documents_to_state,
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
ALLOWED = allowed_buttons(allow_home=False)


class FakeAsker:
    """JevAsker の偽物。問いごとに中立の答え (none 1.0、noul 0.0、box は JEV_BOX) を返し、overrides で置き換える。"""

    def __init__(self, overrides: Mapping[str, object] | None = None):
        self.overrides = dict(overrides or {})
        self.error: Exception | None = None
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
        return JevAnswers(choices=choices, nouls=nouls, boxes=boxes)

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
) -> SurfaceInput:
    frames = tuple(
        SurfaceFrame(t=t, jpeg=f"jpeg-{t}".encode(), label=f"t={t:.3f}") for t in times
    )
    return SurfaceInput(
        t=request_t,
        frames=frames,
        documents=documents,
        timeline=timeline,
        timeline_generated_t=timeline_generated_t,
        recent_actions=recent_actions,
    )


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
    assert list(value) == ["tasks", "tips", "controls", "advice"]
    assert value == {
        "tasks": "- 扉を開ける",
        "tips": "# 攻略情報",
        "controls": "- A: 決定",
        "advice": "# 助言",
    }


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
        RecentAction(9.2, PadAction(held=frozenset({"A"}))),
        RecentAction(9.8, PadAction(taps=frozenset({"B"}), left_stick=(0.5, 0.0))),
    )
    assert recent_actions_to_state(actions, 10.0) == [
        {
            "time": "t=9.200 秒 (0.8 秒前)",
            "held": ["A"],
            "taps": [],
            "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
        },
        {
            "time": "t=9.800 秒 (0.2 秒前)",
            "held": [],
            "taps": ["B"],
            "sticks": {"LS": [0.5, 0.0], "RS": [0.0, 0.0]},
        },
    ]


def test_build_surface_state_has_keys_in_order():
    state = build_surface_state(
        surface_input(timeline="系列", timeline_generated_t=8.5), ["a", "b", "c"]
    )
    assert list(state) == [
        "instructions",
        "documents",
        "timeline",
        "recent_actions",
        "frames",
    ]
    assert state["instructions"] == SURFACE_INSTRUCTIONS
    assert state["documents"] == documents_to_state(DOCUMENTS)
    assert state["timeline"] == {"created": "t=8.500 秒 (1.5 秒前)", "text": "系列"}
    assert state["recent_actions"] == []
    assert state["frames"] == [
        {"label": "t=9.000 秒 (1.0 秒前)", "image": "a"},
        {"label": "t=9.500 秒 (0.5 秒前)", "image": "b"},
        {"label": "t=10.000 秒 (0.0 秒前)", "image": "c"},
    ]


def test_build_surface_state_needs_one_reference_per_frame():
    with pytest.raises(ValueError):
        build_surface_state(surface_input(), ["a", "b"])


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
    assert len(names_of(questions, QuestionKind.ADVISOR)) == 4
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
    decider, _ = make_decider(tmp_path, asker, allowed=allowed_buttons(allow_home=True))
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
            "button_A": {"none": 0.1, "hold": 0.8, "tap": 0.1},
            "stick_LS_x": {"none": 0.0, "left": 0.0, "right": 1.0},
            "advisor_tips": 0.6,
            "advisor_advice": 0.5,
            "info_stuck": 0.7,
            "scene_changed": 0.8,
        }
    )
    decider, _ = make_decider(tmp_path, asker)
    output = decider.decide(surface_input())
    assert output.action == PadAction(held=frozenset({"A"}), left_stick=(0.25, 0.0))
    assert output.deep_kinds == frozenset({DocumentKind.TIPS})
    assert output.info_labels == (
        InfoLabel("controls", 0.0),
        InfoLabel("tips", 0.0),
        InfoLabel("strategy", 0.0),
        InfoLabel("stuck", 0.7),
        InfoLabel("map", 0.0),
    )
    assert output.scene_changed is True


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
