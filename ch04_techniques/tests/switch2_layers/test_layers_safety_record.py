import json

from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.safety.safety_record import SafetyRecord, safety_row
from switch2_layers.safety.safety_types import SafetyPhase
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
)

ROW_KEYS = [
    "t_sent",
    "t_received",
    "frame_t",
    "phase",
    "probabilities",
    "system_screen",
    "system_screen_result",
    "usage",
    "error",
]


def test_row_of_a_successful_check():
    probabilities = {name: 0.1 for name in reversed(SAFETY_QUESTION_NAMES)}
    system_screen = {name: 0.0 for name in reversed(SYSTEM_SCREEN_CHOICE_NAMES)}
    system_screen["home_menu"] = 0.97
    row = safety_row(
        SafetyRecord(
            t_sent=12.5,
            t_received=13.25,
            frame_t=12.4,
            phase=SafetyPhase.START,
            probabilities=probabilities,
            system_screen=system_screen,
            system_screen_result="home_menu",
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
    # 3 つの問いのキーだけを、問いの順に並べる
    assert list(row["probabilities"]) == list(SAFETY_QUESTION_NAMES)
    assert row["probabilities"] == {name: 0.1 for name in SAFETY_QUESTION_NAMES}
    # 照合の確率は、選択肢の順 (none が先頭) に並べる
    assert list(row["system_screen"]) == list(SYSTEM_SCREEN_CHOICE_NAMES)
    assert row["system_screen"]["home_menu"] == 0.97
    assert row["system_screen_result"] == "home_menu"
    assert row["usage"] == {"input_tokens": 1200}
    assert row["error"] is None
    assert json.loads(json.dumps(row)) == row


def test_row_of_a_successful_check_without_a_system_screen_has_null_result():
    system_screen = {name: 0.0 for name in SYSTEM_SCREEN_CHOICE_NAMES}
    system_screen["none"] = 1.0
    row = safety_row(
        SafetyRecord(
            t_sent=1.0,
            t_received=1.3,
            frame_t=0.9,
            phase=SafetyPhase.MONITOR,
            probabilities={name: 0.0 for name in SAFETY_QUESTION_NAMES},
            system_screen=system_screen,
            system_screen_result=None,
            usage=None,
            error=None,
        )
    )
    assert list(row) == ROW_KEYS
    assert row["system_screen"] == system_screen
    assert row["system_screen_result"] is None
    assert row["usage"] is None
    assert json.loads(json.dumps(row)) == row


def test_row_of_a_failed_check_has_null_answers():
    row = safety_row(
        SafetyRecord(
            t_sent=20.0,
            t_received=30.0,
            frame_t=19.9,
            phase=SafetyPhase.MONITOR,
            probabilities=None,
            system_screen=None,
            system_screen_result=None,
            usage=None,
            error="JevError: request failed",
        )
    )
    assert list(row) == ROW_KEYS
    assert row["phase"] == "monitor"
    assert row["probabilities"] is None
    assert row["system_screen"] is None
    assert row["system_screen_result"] is None
    assert row["usage"] is None
    assert row["error"] == "JevError: request failed"
