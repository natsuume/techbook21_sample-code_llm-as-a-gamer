"""主ループの JSON を読んだ結果の型 (orchestrator.decision_types) の既定値と、変えられないこと。"""

import dataclasses

import pytest

from switch2_layers.orchestrator.decision_types import (
    Decision,
    DecisionError,
    DelegateChoice,
    DelegateContext,
    DelegateStart,
    DelegateStop,
    DelegateUpdate,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    MonitorStop,
    PressAction,
    ProcedureAction,
    WaitAction,
)


def test_decision_defaults_change_nothing():
    decision = Decision()
    assert decision.actions == ()
    assert decision.macros_set == {}
    assert decision.macros_delete == ()
    assert decision.monitor is None


def test_press_defaults_to_a_short_press_without_sticks():
    press = PressAction(buttons=("A",))
    assert press.sticks == {}
    assert press.seconds is None


def test_procedure_defaults_to_no_arguments():
    assert ProcedureAction(name="walk").args == {}


@pytest.mark.parametrize(
    "value, field_name, new_value",
    [
        (PressAction(buttons=("A",)), "seconds", 1.0),
        (WaitAction(seconds=0.5), "seconds", 1.0),
        (ProcedureAction(name="walk"), "name", "tap_repeat"),
        (MacroAction(name="next"), "name", "other"),
        (MonitorCase("c", (), "every"), "repeat", "once"),
        (MonitorCreate("dialog", ()), "name", "other"),
        (Decision(), "monitor", MonitorStop()),
        (DecisionError(("x: bad",)), "messages", ("y: bad",)),
    ],
)
def test_types_are_frozen(value, field_name, new_value):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field_name, new_value)


def test_monitor_stop_has_no_fields_and_compares_equal():
    assert dataclasses.fields(MonitorStop) == ()
    assert MonitorStop() == MonitorStop()


def test_decision_error_needs_at_least_one_message():
    with pytest.raises(ValueError):
        DecisionError(())
    assert DecisionError(("actions[0].type: bad",)).messages == (
        "actions[0].type: bad",
    )


def test_field_names_follow_the_io_contract():
    def names(cls: type) -> list[str]:
        return [field.name for field in dataclasses.fields(cls)]

    assert names(PressAction) == ["buttons", "sticks", "seconds"]
    assert names(WaitAction) == ["seconds"]
    assert names(ProcedureAction) == ["name", "args"]
    assert names(MacroAction) == ["name"]
    assert names(MonitorCase) == ["condition", "actions", "repeat"]
    assert names(MonitorCreate) == ["name", "cases"]
    assert names(Decision) == [
        "actions",
        "macros_set",
        "macros_delete",
        "monitor",
        "delegate",
    ]
    assert names(DecisionError) == ["messages"]


def test_delegate_field_names_and_defaults_follow_the_io_contract():
    def names(cls: type) -> list[str]:
        return [field.name for field in dataclasses.fields(cls)]

    assert names(DelegateChoice) == ["name", "description"]
    assert names(DelegateStart) == ["tasks", "cautions", "choices"]
    assert names(DelegateUpdate) == ["tasks", "cautions", "choices"]
    assert dataclasses.fields(DelegateStop) == ()
    assert names(DelegateContext) == ["active", "previous", "macros"]
    # 委任の指示が無い Decision は、今の委任を変えない
    assert Decision().delegate is None
    # update で省いたキーは None (変えない)
    assert DelegateUpdate() == DelegateUpdate(tasks=None, cautions=None, choices=None)
