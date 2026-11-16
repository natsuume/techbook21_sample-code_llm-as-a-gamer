import json

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.surface.jev_client import JevAnswers, JevBox, build_question_json
from switch2_layers.surface.surface_questions import (
    Choice,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)
from switch2_layers.surface.surface_record import (
    SentFrame,
    SurfaceRecord,
    answers_to_json,
    box_to_json,
    info_label_to_json,
    question_to_json,
    surface_request_row,
    surface_row,
)
from switch2_layers.surface.surface_types import (
    Box,
    BoxSource,
    InfoLabel,
    SurfaceDocuments,
    SurfaceRequest,
)

DOCUMENTS = SurfaceDocuments(
    tasks="- 宿屋に入る",
    tips="",
    controls="",
    advice="",
    scene_id="scene-001",
    uses_common_controls=False,
    uses_common_tips=True,
)
SURFACE_KEYS = [
    "t_sent",
    "t_received",
    "frames",
    "scene_id",
    "uses_common",
    "middle_generated_t",
    "action",
    "blocked_buttons",
    "deep_requests",
    "info_labels",
    "boxes",
    "scene_changed",
    "wait_s",
    "error",
    "request",
    "answers",
    "usage",
]


def test_info_label_and_box_to_json():
    assert info_label_to_json(InfoLabel("menu_open", 0.75)) == {
        "name": "menu_open",
        "probability": 0.75,
    }
    assert box_to_json(Box("door", 0.1, 0.2, 0.3, 0.4, 0.9)) == {
        "label": "door",
        "x_min": 0.1,
        "y_min": 0.2,
        "x_max": 0.3,
        "y_max": 0.4,
        "confidence": 0.9,
        "source": None,
    }


def test_box_to_json_writes_source_and_null_confidence():
    jev_box = Box("自機", 0.1, 0.2, 0.3, 0.4, None, BoxSource.JEV)
    assert box_to_json(jev_box)["confidence"] is None
    assert box_to_json(jev_box)["source"] == "jev"
    tracker_box = Box("敵", 0.5, 0.5, 0.6, 0.7, 0.82, BoxSource.TRACKER)
    assert box_to_json(tracker_box)["source"] == "tracker"
    assert list(box_to_json(tracker_box)) == [
        "label",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "confidence",
        "source",
    ]


def test_surface_row_has_the_keys_of_the_issue():
    record = SurfaceRecord(
        t_sent=10.0,
        t_received=10.4,
        frames=[
            SentFrame(9.0, "frames/surface/000009000.jpg"),
            SentFrame(10.0, None),
        ],
        documents=DOCUMENTS,
        middle_generated_t=8.5,
        action=PadAction(held=frozenset({"A"})),
        blocked_buttons=["HOME"],
        deep_requests=[DocumentKind.TIPS, DocumentKind.ADVICE],
        info_labels=[InfoLabel("menu_open", 0.75)],
        boxes=[Box("door", 0.1, 0.2, 0.3, 0.4, 0.9)],
        scene_changed=True,
        wait_s=0.5,
        error=None,
    )
    row = surface_row(record)
    assert list(row) == SURFACE_KEYS
    assert row == {
        "t_sent": 10.0,
        "t_received": 10.4,
        "frames": [
            {"t": 9.0, "file": "frames/surface/000009000.jpg"},
            {"t": 10.0, "file": None},
        ],
        "scene_id": "scene-001",
        "uses_common": {"controls": False, "tips": True},
        "middle_generated_t": 8.5,
        "action": {
            "held": ["A"],
            "taps": [],
            "sticks": {"LS": [0.0, 0.0], "RS": [0.0, 0.0]},
        },
        "blocked_buttons": ["HOME"],
        "deep_requests": ["tips", "advice"],
        "info_labels": [{"name": "menu_open", "probability": 0.75}],
        "boxes": [
            {
                "label": "door",
                "x_min": 0.1,
                "y_min": 0.2,
                "x_max": 0.3,
                "y_max": 0.4,
                "confidence": 0.9,
                "source": None,
            }
        ],
        "scene_changed": True,
        "wait_s": 0.5,
        "error": None,
        "request": None,
        "answers": None,
        "usage": None,
    }
    # そのまま JSON にできる
    assert json.loads(json.dumps(row)) == row


def test_surface_row_of_failed_request():
    record = SurfaceRecord(
        t_sent=1.0,
        t_received=1.2,
        frames=[SentFrame(0.9, "frames/surface/000000900.jpg")],
        documents=DOCUMENTS,
        middle_generated_t=None,
        action=None,
        blocked_buttons=[],
        deep_requests=[],
        info_labels=[],
        boxes=[],
        scene_changed=False,
        wait_s=1.0,
        error="RuntimeError: model failed",
    )
    row = surface_row(record)
    assert row["action"] is None
    assert row["error"] == "RuntimeError: model failed"
    assert row["deep_requests"] == []


# --- 答えと usage・送った要求 ---

BUTTON = SurfaceQuestion(
    name="button_A",
    instructions="今、A ボタンをどうするか。",
    kind=QuestionKind.BUTTON,
    target="A",
    question_type=QuestionType.CHOICE,
    choices=(Choice("none", "押さない"), Choice("hold", "押す"), Choice("tap", "短く")),
)
NOUL = SurfaceQuestion(
    name="scene_changed",
    instructions="画面の種類が変わったか。",
    kind=QuestionKind.SCENE_CHANGE,
    target="",
    question_type=QuestionType.NOUL,
)
BOX_QUESTION = SurfaceQuestion(
    name="box_1",
    instructions="`frames[1].image` の中の「自機」(勇者) を囲む box。",
    kind=QuestionKind.BOX,
    target="自機",
    question_type=QuestionType.BOX,
)
USAGE = {"input_tokens": 1200, "output_tokens": 3, "details": {"images": 2}}
ANSWERS = JevAnswers(
    choices={"button_A": {"none": 0.2, "hold": 0.7, "tap": 0.1}},
    nouls={"scene_changed": 0.25, "advisor_tips": 0.6},
    boxes={"box_1": JevBox(0.2, 0.66, 0.19, 0.68, None), "box_2": None},
    usage=USAGE,
)


def record_with(
    request: int | None, answers: JevAnswers | None, error: str | None = None
) -> SurfaceRecord:
    return SurfaceRecord(
        t_sent=1.0,
        t_received=1.2,
        frames=[SentFrame(0.9, "frames/surface/000000900.jpg")],
        documents=DOCUMENTS,
        middle_generated_t=None,
        action=None if answers is None else PadAction(),
        blocked_buttons=[],
        deep_requests=[],
        info_labels=[],
        boxes=[],
        scene_changed=False,
        wait_s=1.0,
        error=error,
        request=request,
        answers=answers,
    )


def test_answers_to_json_has_all_choice_and_noul_answers_without_boxes():
    assert answers_to_json(ANSWERS) == {
        "choices": {"button_A": {"none": 0.2, "hold": 0.7, "tap": 0.1}},
        "nouls": {"scene_changed": 0.25, "advisor_tips": 0.6},
    }
    assert list(answers_to_json(ANSWERS)) == ["choices", "nouls"]


def test_surface_row_writes_request_answers_and_usage():
    row = surface_row(record_with(request=3, answers=ANSWERS))
    assert list(row) == SURFACE_KEYS
    assert row["request"] == 3
    assert row["answers"] == answers_to_json(ANSWERS)
    # usage は受け取った形のまま
    assert row["usage"] == USAGE
    assert json.loads(json.dumps(row)) == row


def test_surface_row_without_usage_in_answers():
    answers = JevAnswers(choices={}, nouls={"scene_changed": 0.5}, boxes={})
    row = surface_row(record_with(request=1, answers=answers))
    assert row["answers"] == {"choices": {}, "nouls": {"scene_changed": 0.5}}
    assert row["usage"] is None


def test_surface_row_of_request_failing_after_sending():
    row = surface_row(record_with(request=4, answers=None, error="JevError: HTTP 500"))
    # 送った後に失敗した要求は、番号だけを書き、答えと usage は null
    assert row["request"] == 4
    assert row["answers"] is None
    assert row["usage"] is None


def test_surface_row_of_fake_or_failing_before_sending():
    row = surface_row(record_with(request=None, answers=None))
    assert row["request"] is None
    assert row["answers"] is None
    assert row["usage"] is None


def test_question_to_json_puts_id_before_the_sent_question():
    for question in [BUTTON, NOUL, BOX_QUESTION]:
        value = question_to_json(question)
        # Jev に送った問いの値 (criteria を含む) に、問いの ID を足したもの
        assert value == {"id": question.name, **build_question_json(question)}
        assert next(iter(value)) == "id"
    assert question_to_json(BUTTON) == {
        "id": "button_A",
        "type": "choice",
        "instructions": "今、A ボタンをどうするか。",
        "criteria": {"none": "押さない", "hold": "押す", "tap": "短く"},
    }
    assert "criteria" not in question_to_json(NOUL)


def test_surface_request_row_has_the_keys_of_the_issue():
    state = {
        "instructions": "説明",
        "frames": [
            {"label": "t=0.900 秒 (0.1 秒前)", "image": "frames/surface/000000900.jpg"},
            {"label": "t=1.000 秒 (0.0 秒前)", "image": None},
        ],
    }
    request = SurfaceRequest(
        request_index=7,
        model="jev-1.14.0",
        state=state,
        questions=(BUTTON, NOUL, BOX_QUESTION),
    )
    row = surface_request_row(request, t_sent=12.345)
    assert list(row) == ["request", "t_sent", "model", "state", "questions"]
    assert row == {
        "request": 7,
        "t_sent": 12.345,
        "model": "jev-1.14.0",
        "state": state,
        "questions": [
            question_to_json(BUTTON),
            question_to_json(NOUL),
            question_to_json(BOX_QUESTION),
        ],
    }
    assert json.loads(json.dumps(row, ensure_ascii=False)) == row
