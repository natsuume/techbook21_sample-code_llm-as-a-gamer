import math

import pytest

from switch2_agent.advisor_triggers.base import AdvisorCallState, ForcedCall
from switch2_agent.advisor_triggers.idle_timer import IdleTimerTrigger

ANY_REASON = "どの役割の advisor も 60 秒呼ばれていなかったため、定期の呼び出しで呼んだ"
EACH_REASON = (
    "この役割の advisor が 60 秒呼ばれていなかったため、定期の呼び出しで呼んだ"
)


def state(started_at: float = 100.0, **last_accepted: float) -> AdvisorCallState:
    return AdvisorCallState(
        started_at=started_at,
        last_accepted=last_accepted,
        advisor_probabilities={},
    )


@pytest.mark.parametrize("interval", [0.0, -1.0, math.inf, math.nan])
def test_rejects_bad_interval(interval):
    with pytest.raises(ValueError):
        IdleTimerTrigger(interval)


@pytest.mark.parametrize("roles", [(), ("unknown",), ("strategy", "strategy")])
def test_rejects_bad_roles(roles):
    with pytest.raises(ValueError):
        IdleTimerTrigger(60.0, roles)


def test_rejects_unknown_scope():
    with pytest.raises(ValueError):
        IdleTimerTrigger(60.0, ("strategy",), "sometimes")  # type: ignore[arg-type]


def test_defaults_are_any_scope_with_strategy_and_advice():
    trigger = IdleTimerTrigger()
    assert (trigger.interval_s, trigger.roles, trigger.scope) == (
        60.0,
        ("strategy", "advice"),
        "any",
    )


def test_roles_are_reordered_as_advisor_roles():
    assert IdleTimerTrigger(60.0, ("tips", "controls")).roles == ("controls", "tips")


def test_any_scope_counts_from_start_before_any_call():
    trigger = IdleTimerTrigger(60.0, ("strategy", "advice"), "any")
    assert trigger.due(state(), now=159.9) == []
    assert trigger.due(state(), now=160.0) == [
        ForcedCall("strategy", ANY_REASON),
        ForcedCall("advice", ANY_REASON),
    ]


def test_any_scope_counts_from_latest_call_of_any_role():
    trigger = IdleTimerTrigger(60.0, ("strategy",), "any")
    # tips が 150 秒に受け付けたので、strategy が 110 秒からでも 210 秒までは呼ばない
    current = state(strategy=110.0, tips=150.0)
    assert trigger.due(current, now=209.0) == []
    assert trigger.due(current, now=210.0) == [ForcedCall("strategy", ANY_REASON)]


def test_each_scope_counts_per_role():
    trigger = IdleTimerTrigger(60.0, ("strategy", "advice"), "each")
    # advice はまだ受け付けていないので開始 (100 秒) から、strategy は 150 秒から数える
    current = state(strategy=150.0)
    assert trigger.due(current, now=170.0) == [ForcedCall("advice", EACH_REASON)]
    assert trigger.due(current, now=210.0) == [
        ForcedCall("strategy", EACH_REASON),
        ForcedCall("advice", EACH_REASON),
    ]


def test_reason_writes_interval_without_trailing_zeros():
    trigger = IdleTimerTrigger(90.5, ("strategy",))
    [call] = trigger.due(state(), now=200.0)
    assert call.reason.startswith("どの役割の advisor も 90.5 秒")
