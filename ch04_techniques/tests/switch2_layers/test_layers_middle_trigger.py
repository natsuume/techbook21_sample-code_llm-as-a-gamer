import threading
from concurrent.futures import ThreadPoolExecutor
from typing import get_args

import pytest

from switch2_layers.middle.middle_trigger import (
    MIDDLE_CALL_REASON_ORDER,
    MIN_MIDDLE_GAP_S,
    TRIGGER_POLL_S,
    MiddleCallReason,
    MiddleReasonTracker,
    MiddleTrigger,
)
from switch2_layers.run_clock import RunClock

INTERVAL_S = 5.0
STUCK_SECONDS = 30.0


def tracker() -> MiddleReasonTracker:
    return MiddleReasonTracker(interval_s=INTERVAL_S, stuck_seconds=STUCK_SECONDS)


def test_reasons_and_constants():
    assert get_args(MiddleCallReason) == (
        "queue_empty",
        "cancel_requested",
        "stuck",
        "scene_change",
        "interval",
    )
    assert MIDDLE_CALL_REASON_ORDER == get_args(MiddleCallReason)
    assert MIN_MIDDLE_GAP_S == 1.0
    assert TRIGGER_POLL_S == 0.1


# --- 一定の間隔 (interval) ---


def test_interval_after_the_interval_without_pending_reasons():
    reasons = tracker()
    assert reasons.ready(14.999, 10.0) is False
    assert reasons.ready(15.0, 10.0) is True
    assert reasons.take(15.0) == ("interval",)


def test_interval_is_not_added_to_pending_reasons():
    reasons = tracker()
    reasons.notify("queue_empty")
    assert reasons.ready(100.0, 10.0) is True
    assert reasons.take(100.0) == ("queue_empty",)


# --- 知らされた理由 (queue_empty・cancel_requested・scene_change) と 1 秒の下限 ---


@pytest.mark.parametrize("reason", ["queue_empty", "cancel_requested", "scene_change"])
def test_notified_reason_starts_the_next_call_after_the_gap(reason):
    reasons = tracker()
    reasons.notify(reason)
    # 前回の応答から 1 秒たつまでは始めない
    assert reasons.ready(10.999, 10.0) is False
    assert reasons.ready(11.0, 10.0) is True
    assert reasons.take(11.0) == (reason,)
    # take で保留は空になる
    assert reasons.ready(12.0, 10.0) is False


def test_gap_applies_even_with_a_shorter_interval():
    reasons = MiddleReasonTracker(interval_s=0.5, stuck_seconds=STUCK_SECONDS)
    assert reasons.ready(10.5, 10.0) is False
    assert reasons.ready(11.0, 10.0) is True


def test_ready_does_not_take_the_pending_reasons():
    reasons = tracker()
    reasons.notify("cancel_requested")
    assert reasons.ready(11.0, 10.0) is True
    assert reasons.ready(11.0, 10.0) is True
    assert reasons.take(11.0) == ("cancel_requested",)


def test_same_reason_notified_twice_is_taken_once():
    reasons = tracker()
    reasons.notify("queue_empty")
    reasons.notify("queue_empty")
    assert reasons.take(11.0) == ("queue_empty",)


def test_reasons_are_taken_in_the_order():
    reasons = tracker()
    reasons.notify("scene_change")
    reasons.notify("cancel_requested")
    reasons.observe_stuck(31.0, 20.0)
    reasons.notify("queue_empty")
    assert reasons.take(20.0) == (
        "queue_empty",
        "cancel_requested",
        "stuck",
        "scene_change",
    )


# --- 詰まり (stuck) ---


def test_stuck_when_the_oldest_task_exceeds_the_seconds():
    reasons = tracker()
    reasons.observe_stuck(30.001, 20.0)
    assert reasons.ready(11.0, 10.0) is True
    assert reasons.take(20.0) == ("stuck",)


@pytest.mark.parametrize("oldest_active_s", [None, 0.0, 29.999, 30.0])
def test_no_stuck_at_or_below_the_seconds_or_without_tasks(oldest_active_s):
    # ちょうど同じ値は超えない。現在の場面が無いかキューが空 (None) なら起きない
    reasons = tracker()
    reasons.observe_stuck(oldest_active_s, 20.0)
    assert reasons.ready(12.0, 10.0) is False
    assert reasons.take(12.0) == ("interval",)


def test_stuck_repeats_every_stuck_seconds_while_it_continues():
    reasons = tracker()
    reasons.observe_stuck(31.0, 100.0)
    assert reasons.take(100.0) == ("stuck",)
    # 前回 stuck で呼んでから 30 秒たつまでは、超えたままでも足さない
    reasons.observe_stuck(60.999, 129.999)
    assert reasons.take(129.999) == ("interval",)
    reasons.observe_stuck(61.0, 130.0)
    assert reasons.take(130.0) == ("stuck",)
    reasons.observe_stuck(90.0, 159.0)
    assert reasons.take(159.0) == ("interval",)
    reasons.observe_stuck(91.0, 160.0)
    assert reasons.take(160.0) == ("stuck",)


def test_stuck_time_is_the_time_of_take():
    reasons = tracker()
    reasons.observe_stuck(31.0, 100.0)
    # 保留の間に何度確かめても、stuck は 1 つ
    reasons.observe_stuck(40.0, 110.0)
    assert reasons.take(115.0) == ("stuck",)
    reasons.observe_stuck(70.0, 144.999)
    assert reasons.take(144.999) == ("interval",)
    reasons.observe_stuck(70.0, 145.0)
    assert reasons.take(145.0) == ("stuck",)


@pytest.mark.parametrize("below", [None, 5.0, 30.0])
def test_stuck_is_forgotten_at_or_below_the_seconds(below):
    reasons = tracker()
    reasons.observe_stuck(31.0, 100.0)
    assert reasons.take(100.0) == ("stuck",)
    # 最も古いタスクが完了したなどで、しきい値以下 (またはキューが空) に戻った
    reasons.observe_stuck(below, 105.0)
    # 次に超えたら、前回 stuck で呼んでからの秒数に依らず、すぐに足す
    reasons.observe_stuck(30.5, 110.0)
    assert reasons.take(110.0) == ("stuck",)


# --- 失敗した回の理由の持ち越し ---


def test_carry_over_keeps_the_reasons_except_interval():
    reasons = tracker()
    reasons.carry_over(("queue_empty", "stuck", "interval"))
    assert reasons.ready(11.0, 10.0) is True
    assert reasons.take(11.0) == ("queue_empty", "stuck")


def test_carry_over_of_interval_only_waits_for_the_interval():
    reasons = tracker()
    reasons.carry_over(("interval",))
    assert reasons.ready(14.0, 10.0) is False
    assert reasons.ready(15.0, 10.0) is True
    assert reasons.take(15.0) == ("interval",)


def test_carry_over_merges_with_reasons_notified_during_the_call():
    reasons = tracker()
    # 実行中に届いた理由と、失敗した回の理由を、次の回にまとめて渡す
    reasons.notify("scene_change")
    reasons.carry_over(("cancel_requested", "scene_change"))
    assert reasons.take(12.0) == ("cancel_requested", "scene_change")


def test_carried_stuck_is_taken_with_the_next_call():
    reasons = tracker()
    reasons.observe_stuck(31.0, 100.0)
    assert reasons.take(100.0) == ("stuck",)
    reasons.carry_over(("stuck",))
    # 前回 stuck で呼んでから 30 秒たっていなくても、持ち越した stuck は次の回に渡す
    reasons.observe_stuck(40.0, 105.0)
    assert reasons.take(105.0) == ("stuck",)
    # 次の stuck は、持ち越した stuck を渡した時刻から数える
    reasons.observe_stuck(70.0, 134.999)
    assert reasons.take(134.999) == ("interval",)
    reasons.observe_stuck(70.0, 135.0)
    assert reasons.take(135.0) == ("stuck",)


# --- スレッドから使う待ち (MiddleTrigger) ---


class ManualClock:
    """時刻を手で決める時計 (RunClock の monotonic に渡す)。"""

    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def run_clock(t: float) -> RunClock:
    return RunClock(ManualClock(t), origin=0.0)


def test_notify_ends_the_wait():
    # 間隔は長くして、間隔では明けないようにする
    trigger = MiddleTrigger(1000.0, STUCK_SECONDS, run_clock(20.0), lambda t: None)
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(trigger.wait_next, stop, 10.0)
        try:
            trigger.notify("scene_change")
            # スレッドで出た例外は、result() がテストのスレッドで出し直す
            assert future.result(timeout=5.0) == ("scene_change",)
        finally:
            stop.set()


def test_stop_ends_the_wait_with_none():
    trigger = MiddleTrigger(1000.0, STUCK_SECONDS, run_clock(20.0), lambda t: None)
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    try:
        assert trigger.wait_next(stop, 10.0) is None
    finally:
        timer.cancel()


def test_first_reasons_discard_reasons_notified_before_the_first_call():
    trigger = MiddleTrigger(1000.0, STUCK_SECONDS, run_clock(20.0), lambda t: None)
    trigger.notify("queue_empty")
    trigger.notify("scene_change")
    assert trigger.first_reasons() == ("interval",)
    # 捨てたので、次の待ちは知らせが無い限り明けない
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    try:
        assert trigger.wait_next(stop, 10.0) is None
    finally:
        timer.cancel()


def test_wait_next_ends_after_the_interval():
    trigger = MiddleTrigger(INTERVAL_S, STUCK_SECONDS, run_clock(15.0), lambda t: None)
    assert trigger.wait_next(threading.Event(), 10.0) == ("interval",)


def test_wait_next_checks_the_oldest_task_at_the_clock_time():
    seen: list[float] = []

    def oldest_active_s(t: float) -> float | None:
        seen.append(t)
        return 31.0

    trigger = MiddleTrigger(1000.0, STUCK_SECONDS, run_clock(20.0), oldest_active_s)
    assert trigger.wait_next(threading.Event(), 10.0) == ("stuck",)
    assert seen
    assert all(t == 20.0 for t in seen)


def test_carry_over_through_the_trigger():
    trigger = MiddleTrigger(1000.0, STUCK_SECONDS, run_clock(11.0), lambda t: None)
    trigger.carry_over(("cancel_requested", "interval"))
    assert trigger.wait_next(threading.Event(), 10.0) == ("cancel_requested",)
