import json

from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_record import SafetyRecord, safety_row
from switch2_layers.safety.safety_types import SafetyPhase

ROW_KEYS = [
    "t_sent",
    "t_received",
    "frame_t",
    "phase",
    "probabilities",
    "usage",
    "error",
]


def test_row_of_a_successful_check():
    probabilities = {name: 0.1 for name in reversed(SAFETY_QUESTION_NAMES)}
    row = safety_row(
        SafetyRecord(
            t_sent=12.5,
            t_received=13.25,
            frame_t=12.4,
            phase=SafetyPhase.START,
            probabilities=probabilities,
            usage={"input_tokens": 1200},
            error=None,
        )
    )
    assert list(row) == ROW_KEYS
    assert row["t_sent"] == 12.5
    assert row["t_received"] == 13.25
    assert row["frame_t"] == 12.4
    assert row["phase"] == "start"
    assert type(row["phase"]) is str
    # 問いの順に並べる
    assert list(row["probabilities"]) == list(SAFETY_QUESTION_NAMES)
    assert row["usage"] == {"input_tokens": 1200}
    assert row["error"] is None
    assert json.loads(json.dumps(row)) == row


def test_row_of_a_failed_check_has_null_probabilities():
    row = safety_row(
        SafetyRecord(
            t_sent=20.0,
            t_received=30.0,
            frame_t=19.9,
            phase=SafetyPhase.MONITOR,
            probabilities=None,
            usage=None,
            error="JevError: request failed",
        )
    )
    assert list(row) == ROW_KEYS
    assert row["phase"] == "monitor"
    assert row["probabilities"] is None
    assert row["usage"] is None
    assert row["error"] == "JevError: request failed"
