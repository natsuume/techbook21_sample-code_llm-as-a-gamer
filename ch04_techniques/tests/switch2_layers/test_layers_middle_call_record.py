import dataclasses
import json

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.fake_middle import FakeMiddleAnalyzer
from switch2_layers.middle.middle_call_record import (
    MiddleCallDetailsSource,
    MiddleCallRecord,
    middle_call_row,
    middle_call_row_with_details,
    take_analyzer_call_details,
)
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
    "press_home",
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
    surface_rows_offset=120,
    operations_rows_offset=40,
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
    surface_rows_offset=120,
    operations_rows_offset=40,
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
        "press_home": False,
        "deep_requests": ["controls", "tips"],
        "deep_requests_sent": True,
        "error": None,
    }
    assert json.loads(json.dumps(row)) == row


def test_row_has_press_home_of_the_state():
    record = MiddleCallRecord(
        t_sent=10.1,
        t_received=10.6,
        middle_input=INPUT,
        write_result=MiddleWriteResult(
            dataclasses.replace(STATE, press_home=True), None
        ),
        deep_requests=[],
        deep_requests_sent=True,
        error=None,
    )
    assert middle_call_row(record)["press_home"] is True


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
    assert row["press_home"] is None
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


# --- MiddleAnalyzer の記録を足した行 ---


FAILED_RECORD = MiddleCallRecord(
    t_sent=10.1,
    t_received=10.3,
    middle_input=INPUT,
    write_result=None,
    deep_requests=[],
    deep_requests_sent=False,
    error="LunaCallError: HTTP 500",
)
DETAILS = {
    "usage": {
        "input_tokens": 3000,
        "cached_tokens": 1024,
        "cache_write_tokens": 0,
        "output_tokens": 400,
        "reasoning_tokens": 0,
    },
    "cost_usd": 0.00040784,
    "response": None,
    "error_body_head": '{"error": {"message": "server error"}}',
}


def test_row_with_details_appends_keys_after_the_skeleton_keys():
    row = middle_call_row_with_details(FAILED_RECORD, DETAILS)
    assert list(row) == CALL_KEYS + [
        "usage",
        "cost_usd",
        "response",
        "error_body_head",
    ]
    assert {key: row[key] for key in CALL_KEYS} == middle_call_row(FAILED_RECORD)
    assert {key: row[key] for key in DETAILS} == DETAILS
    assert json.loads(json.dumps(row)) == row


def test_row_with_empty_details_is_the_skeleton_row():
    assert middle_call_row_with_details(FAILED_RECORD, {}) == middle_call_row(
        FAILED_RECORD
    )
    assert list(middle_call_row_with_details(FAILED_RECORD, {})) == CALL_KEYS


def test_details_do_not_override_skeleton_keys():
    row = middle_call_row_with_details(
        FAILED_RECORD, {"error": "overridden", "t_sent": -1.0, "cost_usd": 0.1}
    )
    assert row["error"] == "LunaCallError: HTTP 500"
    assert row["t_sent"] == 10.1
    assert row["cost_usd"] == 0.1
    assert list(row) == CALL_KEYS + ["cost_usd"]


class DetailsAnalyzer:
    """take_call_details を持つ MiddleAnalyzer の偽物。"""

    def __init__(self, details: dict[str, object]):
        self.details = details
        self.taken = 0

    def analyze(self, middle_input):
        raise AssertionError("not used")

    def take_call_details(self) -> dict[str, object]:
        self.taken += 1
        details, self.details = self.details, {}
        return details


def test_take_analyzer_call_details_from_source():
    analyzer = DetailsAnalyzer(dict(DETAILS))
    assert isinstance(analyzer, MiddleCallDetailsSource)
    assert take_analyzer_call_details(analyzer) == DETAILS
    assert analyzer.taken == 1
    # 渡した後は空
    assert take_analyzer_call_details(analyzer) == {}


def test_take_analyzer_call_details_from_fake_is_empty():
    analyzer = FakeMiddleAnalyzer()
    assert not isinstance(analyzer, MiddleCallDetailsSource)
    assert take_analyzer_call_details(analyzer) == {}
