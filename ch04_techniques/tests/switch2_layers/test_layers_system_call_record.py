import dataclasses
import json

import pytest

from switch2_layers.middle.system_call_record import (
    CALL_ROW_KIND,
    RUN_ROW_KIND,
    SystemCallRecord,
    system_call_row,
    system_run_row,
)
from switch2_layers.middle.system_screen_types import (
    SystemCutReason,
    SystemGoal,
    SystemOperation,
    SystemRunResult,
    SystemScreenInput,
    SystemScreenOutput,
)

SCREEN_INPUT = SystemScreenInput(
    t=40.0,
    goal=SystemGoal.RECOVER,
    software_name="テストのソフト名",
    user_name="テストのユーザー名",
    frame_jpeg=b"\xff\xd8jpeg",
    frame_t=39.9,
    judged_screen="home_menu",
    judged_frame_t=39.5,
    history=(),
)
OUTPUT = SystemScreenOutput(
    observation="HOME メニュー",
    operations=(
        SystemOperation(
            screen="home_menu", kind="press", buttons=("L", "R"), seconds=0.4
        ),
        SystemOperation(screen="home_menu", kind="wait", buttons=(), seconds=1.0),
    ),
    done=False,
)
OUTPUT_JSON = {
    "observation": "HOME メニュー",
    "operations": [
        {"screen": "home_menu", "type": "press", "buttons": ["L", "R"], "seconds": 0.4},
        {"screen": "home_menu", "type": "wait", "buttons": [], "seconds": 1.0},
    ],
    "done": False,
}
DETAILS = {
    "usage": {
        "input_tokens": 3000,
        "cached_tokens": 1024,
        "cache_write_tokens": 0,
        "output_tokens": 400,
        "reasoning_tokens": 0,
    },
    "cost_usd": 0.00040784,
    "response": {"id": "resp_test"},
    "error_body_head": None,
}
CALL_ROW_KEYS = [
    "kind",
    "t_sent",
    "t_received",
    "goal",
    "frame_t",
    "judged_screen",
    "judged_frame_t",
    "output",
    "error",
    "usage",
    "cost_usd",
    "response",
    "error_body_head",
]


def test_row_kinds():
    assert CALL_ROW_KIND == "call"
    assert RUN_ROW_KIND == "run"


def test_call_row_after_success():
    row = system_call_row(
        SystemCallRecord(
            t_sent=40.1,
            t_received=41.3,
            screen_input=SCREEN_INPUT,
            output=OUTPUT,
            error=None,
            details=DETAILS,
        )
    )
    assert list(row) == CALL_ROW_KEYS
    assert row == {
        "kind": "call",
        "t_sent": 40.1,
        "t_received": 41.3,
        "goal": "recover",
        "frame_t": 39.9,
        "judged_screen": "home_menu",
        "judged_frame_t": 39.5,
        "output": OUTPUT_JSON,
        "error": None,
        **DETAILS,
    }
    assert json.loads(json.dumps(row, ensure_ascii=False)) == row


def test_call_row_after_failure():
    details = {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": "Bad Request",
    }
    screen_input = dataclasses.replace(
        SCREEN_INPUT, judged_screen=None, judged_frame_t=None
    )
    row = system_call_row(
        SystemCallRecord(
            t_sent=40.1,
            t_received=40.2,
            screen_input=screen_input,
            output=None,
            error="SystemScreenMiddleError: gpt-6-luna returned HTTP 400",
            details=details,
        )
    )
    assert list(row) == CALL_ROW_KEYS
    assert row["output"] is None
    assert row["error"] == "SystemScreenMiddleError: gpt-6-luna returned HTTP 400"
    assert row["judged_screen"] is None
    assert row["judged_frame_t"] is None
    assert row["error_body_head"] == "Bad Request"
    assert row["usage"] is None


def test_call_row_without_details_has_null_usage_and_cost():
    # フェイクの middle は記録を持たない
    row = system_call_row(
        SystemCallRecord(
            t_sent=1.0,
            t_received=1.0,
            screen_input=SCREEN_INPUT,
            output=OUTPUT,
            error=None,
        )
    )
    assert list(row) == CALL_ROW_KEYS
    assert row["usage"] is None
    assert row["cost_usd"] is None
    assert row["response"] is None
    assert row["error_body_head"] is None


def test_call_row_ignores_unknown_detail_keys():
    row = system_call_row(
        SystemCallRecord(
            t_sent=1.0,
            t_received=1.0,
            screen_input=SCREEN_INPUT,
            output=OUTPUT,
            error=None,
            details={**DETAILS, "kind": "other", "extra": 1},
        )
    )
    assert list(row) == CALL_ROW_KEYS
    assert row["kind"] == "call"


def test_run_row_after_cut():
    row = system_run_row(
        45.5,
        SystemRunResult(
            sent_count=1,
            cut_index=1,
            cut_reason=SystemCutReason.SCREEN_MISMATCH,
            blocked_buttons=frozenset({"HOME", "A"}),
        ),
    )
    assert row == {
        "kind": "run",
        "t": 45.5,
        "sent_count": 1,
        "cut_index": 1,
        "cut_reason": "screen_mismatch",
        "blocked_buttons": ["A", "HOME"],
    }
    assert list(row) == [
        "kind",
        "t",
        "sent_count",
        "cut_index",
        "cut_reason",
        "blocked_buttons",
    ]
    assert json.loads(json.dumps(row)) == row


def test_run_row_after_sending_all():
    row = system_run_row(
        46.0,
        SystemRunResult(
            sent_count=2, cut_index=None, cut_reason=None, blocked_buttons=frozenset()
        ),
    )
    assert row["cut_index"] is None
    assert row["cut_reason"] is None
    assert row["blocked_buttons"] == []
    assert row["sent_count"] == 2


@pytest.mark.parametrize("reason", list(SystemCutReason))
def test_run_row_cut_reason_is_plain_string(reason):
    row = system_run_row(
        1.0,
        SystemRunResult(
            sent_count=0, cut_index=0, cut_reason=reason, blocked_buttons=frozenset()
        ),
    )
    assert type(row["cut_reason"]) is str
    assert row["cut_reason"] == reason.value
