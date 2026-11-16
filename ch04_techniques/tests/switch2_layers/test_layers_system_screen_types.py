import dataclasses

import pytest

from switch2_layers.middle.system_screen_types import (
    MAX_HISTORY_ENTRIES,
    NO_SCREEN_NAME,
    SystemCutReason,
    SystemGoal,
    SystemOperation,
    SystemRunResult,
)


def test_goal_values():
    assert [goal.value for goal in SystemGoal] == ["prepare", "recover", "clean_up"]


def test_cut_reason_values():
    assert [reason.value for reason in SystemCutReason] == [
        "screen_mismatch",
        "no_judgment",
        "decision_not_first",
        "stopped",
    ]


def test_constants():
    assert NO_SCREEN_NAME == "none"
    assert MAX_HISTORY_ENTRIES == 10


def test_types_are_frozen():
    operation = SystemOperation(
        screen="home_menu", kind="press", buttons=("DOWN",), seconds=None
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        operation.screen = "controllers"
    result = SystemRunResult(
        sent_count=0, cut_index=None, cut_reason=None, blocked_buttons=frozenset()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.sent_count = 1
