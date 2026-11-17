"""主ループへの知らせの型 (orchestrator.orchestrator_events) の既定値と、変えられないこと。"""

import dataclasses

import pytest

from switch2_layers.orchestrator.decision_types import PressAction
from switch2_layers.orchestrator.orchestrator_events import (
    CaseHit,
    DelegateEnd,
    DelegatePick,
    DelegationStatus,
    MonitorEnd,
    OrchestratorEvents,
)


def test_events_default_to_nothing_happened():
    events = OrchestratorEvents()
    assert events.case_hits == ()
    assert events.monitor_ends == ()
    assert events.discarded_action_count == 0
    assert events.blocked_buttons == ()
    assert events.running is False
    assert events.remaining_action_count == 0
    assert events.macros == {}
    assert events.monitor is None


@pytest.mark.parametrize(
    "value, field_name, new_value",
    [
        (CaseHit("dialog", 0, "c", 1, (PressAction(buttons=("A",)),)), "count", 2),
        (MonitorEnd("dialog", "completed", "done"), "reason", "failed"),
        (OrchestratorEvents(), "running", True),
    ],
)
def test_types_are_frozen(value, field_name, new_value):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field_name, new_value)


def test_field_names_follow_the_io_contract():
    def names(cls: type) -> list[str]:
        return [field.name for field in dataclasses.fields(cls)]

    assert names(CaseHit) == ["monitor", "case_index", "condition", "count", "actions"]
    assert names(MonitorEnd) == ["name", "reason", "detail"]
    assert names(OrchestratorEvents) == [
        "case_hits",
        "monitor_ends",
        "discarded_action_count",
        "blocked_buttons",
        "running",
        "remaining_action_count",
        "macros",
        "monitor",
        "delegate_ends",
        "delegate_discarded_action_count",
        "delegation",
    ]


def test_delegate_event_field_names_and_defaults_follow_the_io_contract():
    def names(cls: type) -> list[str]:
        return [field.name for field in dataclasses.fields(cls)]

    assert names(DelegateEnd) == ["reason", "detail"]
    assert names(DelegatePick) == ["name", "t"]
    assert names(DelegationStatus) == [
        "active",
        "started_t",
        "values",
        "last_pick",
        "return_streak",
    ]
    # 監視型の出来事では、委任の出来事も委任の状況も無い
    events = OrchestratorEvents()
    assert events.delegate_ends == ()
    assert events.delegate_discarded_action_count == 0
    assert events.delegation is None
