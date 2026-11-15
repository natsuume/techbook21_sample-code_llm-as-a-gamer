import json

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_call_record import MiddleCallRecord, middle_call_row
from switch2_layers.middle.middle_output_writer import MiddleWriteResult
from switch2_layers.middle.middle_types import MiddleFrame, MiddleInput, MiddleState
from switch2_layers.notes.scene_labels import SceneLabel

CALL_KEYS = [
    "t_sent",
    "t_received",
    "based_on_t",
    "frames",
    "surface_rows",
    "operation_rows",
    "scene_change_reported",
    "scene_id",
    "scene_name",
    "new_scene",
    "surface_wait_s",
    "deep_requests",
    "deep_requests_sent",
    "error",
]
INPUT = MiddleInput(
    t=10.0,
    frames=(
        MiddleFrame(t=4.0, file="frames/interval/000004000.jpg", jpeg=b"a"),
        MiddleFrame(t=10.0, file="frames/interval/000010000.jpg", jpeg=b"b"),
    ),
    surface_rows=({"t_received": 6.0}, {"t_received": 7.0, "scene_changed": True}),
    operation_rows=({"t": 6.5},),
    scene_change_reported=True,
    scene_labels=(),
    previous_state=None,
    strategy="",
    current_tasks="",
)
STATE = MiddleState(
    generated_t=10.6,
    based_on_t=10.0,
    scene_id="scene-001",
    scene_name="町",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.2,
    timeline="",
)


def test_row_of_successful_call_with_new_scene():
    record = MiddleCallRecord(
        t_sent=10.1,
        t_received=10.6,
        middle_input=INPUT,
        write_result=MiddleWriteResult(STATE, SceneLabel("scene-001", "町", 10.0)),
        deep_requests=[DocumentKind.CONTROLS, DocumentKind.TIPS],
        deep_requests_sent=True,
        error=None,
    )
    row = middle_call_row(record)
    assert list(row) == CALL_KEYS
    assert row == {
        "t_sent": 10.1,
        "t_received": 10.6,
        "based_on_t": 10.0,
        "frames": [
            {"t": 4.0, "file": "frames/interval/000004000.jpg"},
            {"t": 10.0, "file": "frames/interval/000010000.jpg"},
        ],
        "surface_rows": 2,
        "operation_rows": 1,
        "scene_change_reported": True,
        "scene_id": "scene-001",
        "scene_name": "町",
        "new_scene": True,
        "surface_wait_s": 0.2,
        "deep_requests": ["controls", "tips"],
        "deep_requests_sent": True,
        "error": None,
    }
    assert json.loads(json.dumps(row)) == row


def test_row_of_failed_call():
    record = MiddleCallRecord(
        t_sent=10.1,
        t_received=10.3,
        middle_input=INPUT,
        write_result=None,
        deep_requests=[],
        deep_requests_sent=False,
        error="RuntimeError: luna failed",
    )
    row = middle_call_row(record)
    assert row["scene_id"] is None
    assert row["scene_name"] is None
    assert row["surface_wait_s"] is None
    assert row["new_scene"] is False
    assert row["deep_requests"] == []
    assert row["error"] == "RuntimeError: luna failed"
    assert row["based_on_t"] == 10.0


def test_row_when_input_could_not_be_built():
    record = MiddleCallRecord(
        t_sent=3.0,
        t_received=3.0,
        middle_input=None,
        write_result=None,
        deep_requests=[],
        deep_requests_sent=False,
        error="OSError: disk",
    )
    row = middle_call_row(record)
    assert row["based_on_t"] is None
    assert row["frames"] == []
    assert row["surface_rows"] == 0
    assert row["operation_rows"] == 0
