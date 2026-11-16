import json
from pathlib import Path

from switch2_layers.pad_action import NEUTRAL_ACTION, PadAction
from switch2_layers.records.operation_log import (
    OperationEvent,
    OperationLog,
    operation_row,
)
from switch2_layers.run_clock import RunClock


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_operation_row():
    action = PadAction(
        held=frozenset({"A", "X"}),
        taps=frozenset({"B"}),
        left_stick=(0.5, 0.0),
        hold_seconds={"A": 0.3},
    )
    row = operation_row(2.5, OperationEvent(source="apply", action=action, ok=True))
    assert row == {
        "t": 2.5,
        "source": "apply",
        "held": ["A", "X"],
        "hold_seconds": {"A": 0.3},
        "taps": ["B"],
        "sticks": {"LS": [0.5, 0.0], "RS": [0.0, 0.0]},
        "ok": True,
        "error": None,
    }


def test_operation_log_appends_rows(tmp_path):
    monotonic = FakeMonotonic(10.0)
    path = tmp_path / "operations.jsonl"
    log = OperationLog(path, RunClock(monotonic))
    monotonic.now = 11.25
    log.write(
        OperationEvent(
            source="neutral", action=NEUTRAL_ACTION, ok=False, error="failed"
        )
    )
    log.write(OperationEvent(source="stop", action=NEUTRAL_ACTION, ok=True))
    rows = read_rows(path)
    assert [row["source"] for row in rows] == ["neutral", "stop"]
    assert rows[0]["t"] == 1.25
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "failed"
    assert rows[1]["held"] == []
    assert rows[1]["hold_seconds"] == {}
    assert list(rows[0]) == [
        "t",
        "source",
        "held",
        "hold_seconds",
        "taps",
        "sticks",
        "ok",
        "error",
    ]
