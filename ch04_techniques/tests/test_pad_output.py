import time

import pytest
from switch2_remote.controller_state import NEUTRAL_REPORT, PadState, build_report

from switch2_agent.action_decision import PadAction
from switch2_agent.pad_output import (
    TAP_PRESS_S,
    TAP_RELEASE_GAP_S,
    TIMER_INTERVAL_S,
    PadOutput,
    compose_report_state,
)

WAIT_TIMEOUT = 2.0  # タイマースレッドが短押しを離すのを待つ上限 (秒)
POLL_INTERVAL = 0.01


def pad(*buttons: str, **sticks: tuple[float, float]) -> PadState:
    return PadState(frozenset(buttons), dict(sticks))


def report(*buttons: str, **sticks: tuple[float, float]) -> bytes:
    return build_report(pad(*buttons, **sticks))


def action(held: PadState | None = None, taps: set[str] | None = None) -> PadAction:
    return PadAction(
        held=held if held is not None else PadState(),
        taps=frozenset(taps or ()),
        advisor_requested=False,
        advisor_probability=0.0,
    )


class FakeSender:
    """送られたレポートを記録する偽の ReportSender。fail_with を設定すると、その例外を送出する。"""

    def __init__(self):
        self.reports: list[bytes] = []
        self.fail_with: Exception | None = None

    def set_report(self, report: bytes) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.reports.append(bytes(report))
        return f"ack {bytes(report).hex()}"


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_output(now: float = 0.0):
    """タイマースレッドを起動しない PadOutput と、偽の sender・clock を返す。"""
    sender = FakeSender()
    clock = FakeClock(now)
    output = PadOutput(sender, clock=clock, start_timer=False)
    return output, sender, clock


def test_constants_match_chapter2_tap():
    assert TAP_PRESS_S == 0.12
    assert TAP_RELEASE_GAP_S == 0.1
    assert TIMER_INTERVAL_S == 0.01


# --- compose_report_state ---


def test_compose_adds_tapping_buttons_to_held():
    state = compose_report_state(pad("A", LS=(0.5, 0.0)), frozenset({"B"}))
    assert state.buttons == frozenset({"A", "B"})
    assert state.sticks == {"LS": (0.5, 0.0)}


def test_compose_removes_held_opposite_of_tapping_direction():
    state = compose_report_state(pad("UP", "A"), frozenset({"DOWN"}))
    assert state.buttons == frozenset({"DOWN", "A"})


# --- 短押し ---


def test_tap_is_released_after_120ms():
    output, sender, clock = make_output()
    output.apply(action(taps={"A"}))
    assert sender.reports == [report("A")]
    clock.now = 0.11
    output.release_due_taps()
    assert sender.reports == [report("A")]
    clock.now = 0.12
    output.release_due_taps()
    assert sender.reports == [report("A"), NEUTRAL_REPORT]


def test_release_without_due_taps_sends_nothing():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    clock.now = 1.0
    output.release_due_taps()
    assert sender.reports == [report("A")]


def test_tap_keeps_held_inputs():
    output, sender, clock = make_output()
    output.apply(action(held=pad("L", LS=(0.0, 1.0)), taps={"A"}))
    assert sender.reports == [report("L", "A", LS=(0.0, 1.0))]
    clock.now = 0.12
    output.release_due_taps()
    assert sender.reports[-1] == report("L", LS=(0.0, 1.0))


def test_multiple_buttons_can_be_tapped_together():
    output, sender, _ = make_output()
    output.apply(action(taps={"A", "B"}))
    assert sender.reports == [report("A", "B")]


def test_held_button_is_not_tapped():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A"), taps={"A"}))
    assert sender.reports == [report("A")]
    assert output.tapping == {}
    clock.now = 0.2
    output.release_due_taps()
    assert sender.reports == [report("A")]


def test_ongoing_tap_is_not_restarted():
    output, sender, clock = make_output()
    output.apply(action(taps={"A"}))
    clock.now = 0.05
    output.apply(action(taps={"A"}))
    assert sender.reports == [report("A"), report("A")]
    # 始め直していれば 0.17 まで押したままになる。最初の短押しの 0.12 で離す
    clock.now = 0.12
    output.release_due_taps()
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_tap_waits_release_gap_after_previous_tap():
    output, sender, clock = make_output()
    output.apply(action(taps={"A"}))
    clock.now = 0.12
    output.release_due_taps()
    # 離してから 0.08 秒 (TAP_RELEASE_GAP_S 未満) では、短押しを始めない
    clock.now = 0.2
    output.apply(action(taps={"A"}))
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert "A" not in output.tapping
    # 離してから 0.13 秒たてば始める
    clock.now = 0.25
    output.apply(action(taps={"A"}))
    assert sender.reports[-1] == report("A")
    assert output.tapping["A"] == pytest.approx(0.25 + TAP_PRESS_S)


def test_release_gap_is_per_button():
    output, sender, clock = make_output()
    output.apply(action(taps={"A"}))
    clock.now = 0.12
    output.release_due_taps()
    clock.now = 0.15
    output.apply(action(taps={"A", "B"}))
    assert sender.reports[-1] == report("B")


def test_apply_replaces_held():
    output, sender, _ = make_output()
    output.apply(action(held=pad("A", LS=(1.0, 0.0))))
    output.apply(action(held=pad("B")))
    assert sender.reports == [report("A", LS=(1.0, 0.0)), report("B")]
    assert output.held == pad("B")


# --- 押したままから外したボタンの短押し ---


def test_tap_of_button_released_from_held_starts_after_release_gap():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    clock.now = 0.5
    output.apply(action(taps={"A"}))
    # 押したままの A を離したレポートを先に送り、短押しはまだ始めない
    assert sender.reports == [report("A"), NEUTRAL_REPORT]
    assert output.tapping == {}
    assert output.pending_taps == pytest.approx({"A": 0.5 + TAP_RELEASE_GAP_S})
    clock.now = output.pending_taps["A"]
    output.start_due_taps()
    assert sender.reports == [report("A"), NEUTRAL_REPORT, report("A")]
    assert output.pending_taps == {}
    clock.now = output.tapping["A"]
    assert clock.now == pytest.approx(0.5 + TAP_RELEASE_GAP_S + TAP_PRESS_S)
    output.release_due_taps()
    assert sender.reports == [
        report("A"),
        NEUTRAL_REPORT,
        report("A"),
        NEUTRAL_REPORT,
    ]


def test_pending_tap_is_not_started_before_start_time():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    output.apply(action(taps={"A"}))
    clock.now = TAP_RELEASE_GAP_S / 2
    output.start_due_taps()
    assert sender.reports == [report("A"), NEUTRAL_REPORT]
    assert output.tapping == {}
    assert "A" in output.pending_taps


def test_pending_tap_is_dropped_when_button_is_held_again():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    output.apply(action(taps={"A"}))
    clock.now = 0.05
    output.apply(action(held=pad("A")))
    clock.now = output.pending_taps["A"]
    output.start_due_taps()
    # 始めずに捨てるので、何も送らない
    assert output.pending_taps == {}
    assert output.tapping == {}
    assert sender.reports == [report("A"), NEUTRAL_REPORT, report("A")]


def test_button_released_from_held_waits_release_gap_before_tap():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    clock.now = 0.5
    output.apply(action())
    assert sender.reports == [report("A"), NEUTRAL_REPORT]
    assert output.last_released == {"A": 0.5}
    # 押したままから外してから 0.05 秒 (TAP_RELEASE_GAP_S 未満) では、短押しを始めない
    clock.now = 0.55
    output.apply(action(taps={"A"}))
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert "A" not in output.tapping
    # 外してから 0.15 秒たてば始める
    clock.now = 0.65
    output.apply(action(taps={"A"}))
    assert sender.reports[-1] == report("A")


def test_tap_is_not_started_while_opposite_direction_is_pending():
    output, sender, clock = make_output()
    output.apply(action(held=pad("UP")))
    output.apply(action(taps={"UP"}))
    clock.now = 0.05
    output.apply(action(taps={"DOWN"}))
    assert "DOWN" not in output.tapping
    assert "DOWN" not in output.pending_taps
    assert sender.reports == [report("UP"), NEUTRAL_REPORT, NEUTRAL_REPORT]
    # 待っていた UP の短押しは、始める時刻に始める
    clock.now = output.pending_taps["UP"]
    output.start_due_taps()
    assert sender.reports[-1] == report("UP")


def test_released_direction_does_not_wait_while_opposite_direction_is_tapped():
    output, _, clock = make_output()
    output.apply(action(held=pad("UP"), taps={"DOWN"}))
    clock.now = 0.05
    output.apply(action(taps={"UP"}))
    assert output.pending_taps == {}
    assert "UP" not in output.tapping


# --- 十字キーの相反 ---


def test_tap_overrides_opposite_held_direction_only_in_report():
    output, sender, clock = make_output()
    output.apply(action(held=pad("UP"), taps={"DOWN"}))
    assert sender.reports == [report("DOWN")]
    # held 自体は書き換えない
    assert output.held == pad("UP")
    clock.now = 0.12
    output.release_due_taps()
    assert sender.reports[-1] == report("UP")


def test_tap_is_not_started_while_opposite_direction_is_tapped():
    output, sender, clock = make_output()
    output.apply(action(taps={"UP"}))
    clock.now = 0.05
    output.apply(action(taps={"DOWN"}))
    assert sender.reports == [report("UP"), report("UP")]
    assert "DOWN" not in output.tapping
    clock.now = 0.12
    output.release_due_taps()
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_opposite_direction_can_be_tapped_after_release():
    output, sender, clock = make_output()
    output.apply(action(taps={"LEFT"}))
    clock.now = 0.12
    output.release_due_taps()
    clock.now = 0.13
    output.apply(action(taps={"RIGHT"}))
    assert sender.reports[-1] == report("RIGHT")


# --- neutral ---


def test_neutral_clears_held_and_taps_and_sends_neutral_report():
    output, sender, clock = make_output()
    output.apply(action(held=pad("B"), taps={"A"}))
    output.neutral()
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert output.held == PadState()
    assert output.tapping == {}
    clock.now = 0.2
    output.release_due_taps()
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert len(sender.reports) == 2


def test_neutral_clears_pending_taps():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    output.apply(action(taps={"A"}))
    output.neutral()
    assert output.pending_taps == {}
    clock.now = 1.0
    output.start_due_taps()
    assert output.tapping == {}
    assert sender.reports == [report("A"), NEUTRAL_REPORT, NEUTRAL_REPORT]


# --- 送信の失敗 ---


def test_apply_raises_send_error():
    output, sender, _ = make_output()
    sender.fail_with = RuntimeError("bridge failed")
    with pytest.raises(RuntimeError, match="bridge failed"):
        output.apply(action(held=pad("A")))


def test_release_records_send_error_without_raising():
    output, sender, clock = make_output()
    output.apply(action(taps={"A"}))
    sender.fail_with = RuntimeError("bridge failed")
    clock.now = 0.12
    output.release_due_taps()
    assert output.last_error is not None
    assert "bridge failed" in output.last_error


def test_start_due_taps_records_send_error_without_raising():
    output, sender, clock = make_output()
    output.apply(action(held=pad("A")))
    output.apply(action(taps={"A"}))
    sender.fail_with = RuntimeError("bridge failed")
    clock.now = output.pending_taps["A"]
    output.start_due_taps()
    assert output.last_error is not None
    assert "bridge failed" in output.last_error


def test_neutral_records_send_error_without_raising():
    output, sender, _ = make_output()
    sender.fail_with = OSError("pipe closed")
    output.neutral()
    assert output.last_error is not None
    assert "pipe closed" in output.last_error


def test_last_error_is_none_without_failure():
    output, _, _ = make_output()
    output.apply(action(held=pad("A")))
    assert output.last_error is None


# --- タイマースレッド ---


def test_timer_thread_releases_tap():
    sender = FakeSender()
    output = PadOutput(sender)
    try:
        output.apply(action(taps={"A"}))
        deadline = time.monotonic() + WAIT_TIMEOUT
        while len(sender.reports) < 2:
            if time.monotonic() >= deadline:
                pytest.fail(f"tap was not released within {WAIT_TIMEOUT} seconds")
            time.sleep(POLL_INTERVAL)
        assert sender.reports == [report("A"), NEUTRAL_REPORT]
    finally:
        output.close()


def test_timer_thread_starts_pending_tap_and_releases_it():
    sender = FakeSender()
    output = PadOutput(sender)
    try:
        output.apply(action(held=pad("A")))
        output.apply(action(taps={"A"}))
        deadline = time.monotonic() + WAIT_TIMEOUT
        while len(sender.reports) < 4:
            if time.monotonic() >= deadline:
                pytest.fail(f"tap was not started within {WAIT_TIMEOUT} seconds")
            time.sleep(POLL_INTERVAL)
        assert sender.reports == [
            report("A"),
            NEUTRAL_REPORT,
            report("A"),
            NEUTRAL_REPORT,
        ]
    finally:
        output.close()


def test_close_without_timer_thread_does_nothing():
    output, _, _ = make_output()
    output.close()
