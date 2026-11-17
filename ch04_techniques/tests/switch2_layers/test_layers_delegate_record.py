"""orchestrator/delegate.jsonl の 1 行の形と書き込み (orchestrator.delegate_record)。"""

import json

from switch2_layers.orchestrator.delegate_record import (
    DelegateLog,
    DelegateRecord,
    delegate_row,
)
from switch2_layers.run_directory import RunPaths

STARTED = DelegateRecord(
    t_sent=12.0,
    t_received=12.4,
    frame_ts=(11.2, 11.4, 11.6, 11.8, 12.0),
    probabilities={"none": 0.1, "attack": 0.7, "move_up": 0.1, "return_control": 0.1},
    chosen="attack",
    handling="started",
    usage={"input_tokens": 1234},
    error=None,
)
FAILED = DelegateRecord(
    t_sent=13.0,
    t_received=13.1,
    frame_ts=(12.2, 12.4),
    probabilities=None,
    chosen=None,
    handling="continued",
    usage=None,
    error="JevError: HTTP 500",
)


def test_delegate_row_has_the_keys_of_the_record_in_order():
    row = delegate_row(STARTED)
    assert list(row) == [
        "t_sent",
        "t_received",
        "frame_ts",
        "probabilities",
        "chosen",
        "handling",
        "usage",
        "error",
    ]
    assert row == {
        "t_sent": 12.0,
        "t_received": 12.4,
        "frame_ts": [11.2, 11.4, 11.6, 11.8, 12.0],
        "probabilities": {
            "none": 0.1,
            "attack": 0.7,
            "move_up": 0.1,
            "return_control": 0.1,
        },
        "chosen": "attack",
        "handling": "started",
        "usage": {"input_tokens": 1234},
        "error": None,
    }
    # 確率は選択肢の順のまま
    assert list(row["probabilities"]) == ["none", "attack", "move_up", "return_control"]


def test_delegate_row_of_a_failed_request_has_the_error_and_no_probabilities():
    row = delegate_row(FAILED)
    assert row["probabilities"] is None
    assert row["chosen"] is None
    assert row["usage"] is None
    assert row["error"] == "JevError: HTTP 500"
    assert row["handling"] == "continued"


def test_delegate_log_creates_the_orchestrator_directory_and_appends_rows(tmp_path):
    paths = RunPaths(tmp_path)
    log = DelegateLog(paths.orchestrator_delegate_log)
    log.write(STARTED)
    log.write(FAILED)
    lines = paths.orchestrator_delegate_log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        delegate_row(STARTED),
        delegate_row(FAILED),
    ]
