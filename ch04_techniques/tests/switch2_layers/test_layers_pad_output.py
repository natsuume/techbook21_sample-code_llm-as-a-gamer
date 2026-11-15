import pytest
from switch2_remote.controller_state import BUTTON_ORDER, NEUTRAL_REPORT

from switch2_layers.buttons import FilteredAction
from switch2_layers.hardware.pad_output import (
    TAP_PRESS_S,
    OutputStoppedError,
    PadOutput,
)
from switch2_layers.pad_action import NEUTRAL_ACTION, PadAction
from switch2_layers.records.operation_log import OperationEvent

ALLOWED_WITHOUT_HOME = frozenset(BUTTON_ORDER) - {"HOME"}
ALLOWED_WITH_HOME = frozenset(BUTTON_ORDER)
# レポートのビット (switch2_remote.controller_state.BUTTON_BITS と同じ)
A_BIT = 0x04  # b1
B_BIT = 0x02  # b1
HOME_BIT = 0x10  # b2
# レポートの hat (3 バイト目) の値 (switch2_remote.controller_state.DPAD_HAT と同じ)
HAT_UP = 0
HAT_DOWN = 4
HAT_LEFT = 6


class FakeMonotonic:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeSender:
    """送られたレポートを記録する ReportSender。fail_with を入れると、その例外を出す。"""

    def __init__(self):
        self.reports: list[bytes] = []
        self.fail_with: Exception | None = None

    def set_report(self, report: bytes) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.reports.append(bytes(report))
        return f"ack {bytes(report).hex()} 0"


def make_output(allowed=ALLOWED_WITHOUT_HOME):
    """タイマースレッドを起動しない PadOutput と、偽の送信先・時計・受け取った OperationEvent のリストを返す。"""
    sender = FakeSender()
    clock = FakeMonotonic(0.0)
    events: list[OperationEvent] = []
    output = PadOutput(
        sender, allowed, clock=clock, on_operation=events.append, start_timer=False
    )
    return output, sender, clock, events


def test_tap_press_time():
    assert TAP_PRESS_S == 0.12


def test_creating_sends_nothing():
    _, sender, _, events = make_output()
    assert sender.reports == []
    assert events == []


def test_apply_sends_held_buttons_and_sticks():
    output, sender, _, events = make_output()
    action = PadAction(held=frozenset({"A"}), left_stick=(1.0, 0.0))
    result = output.apply(action)
    assert result == FilteredAction(action=action, blocked_buttons=())
    assert len(sender.reports) == 1
    report = sender.reports[0]
    assert report[0] & A_BIT
    assert report[3] == 255  # LX
    assert report[4] == 128  # LY
    assert events == [
        OperationEvent(source="apply", action=action, ok=True, error=None)
    ]


def test_apply_removes_home_right_before_sending():
    output, sender, _, events = make_output()
    result = output.apply(
        PadAction(held=frozenset({"HOME", "B"}), taps=frozenset({"HOME"}))
    )
    assert result.blocked_buttons == ("HOME",)
    assert result.action == PadAction(held=frozenset({"B"}))
    report = sender.reports[-1]
    assert report[1] & HOME_BIT == 0
    assert report[0] & B_BIT
    assert events[-1].action == PadAction(held=frozenset({"B"}))


def test_apply_sends_home_with_allow_home():
    output, sender, _, _ = make_output(ALLOWED_WITH_HOME)
    result = output.apply(PadAction(held=frozenset({"HOME"})))
    assert result.blocked_buttons == ()
    assert sender.reports[-1][1] & HOME_BIT


def test_tap_is_released_after_tap_press_time():
    output, sender, clock, events = make_output()
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT
    clock.now = TAP_PRESS_S - 0.01
    output.release_due_taps()
    assert len(sender.reports) == 1
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert len(sender.reports) == 2
    assert sender.reports[-1] == NEUTRAL_REPORT
    # 短押しを離すレポートでは on_operation を呼ばない
    assert [event.source for event in events] == ["apply"]


def test_tap_of_held_button_is_ignored():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), taps=frozenset({"A"})))
    clock.now = 1.0
    output.release_due_taps()
    # 押し続けているボタンは短押しにしないため、離すレポートも送らない
    assert len(sender.reports) == 1
    assert sender.reports[-1][0] & A_BIT


def test_conflicting_directions_in_held_are_both_removed():
    output, sender, _, events = make_output()
    result = output.apply(PadAction(held=frozenset({"UP", "DOWN", "LEFT", "A"})))
    sent = PadAction(held=frozenset({"LEFT", "A"}))
    # 例外にせず、相反する方向を両方とも外して送る。外した方向は blocked_buttons に入れない
    assert result == FilteredAction(action=sent, blocked_buttons=())
    report = sender.reports[-1]
    assert report[2] == HAT_LEFT
    assert report[0] & A_BIT
    assert events[-1] == OperationEvent(
        source="apply", action=sent, ok=True, error=None
    )


def test_conflicting_directions_in_taps_are_both_removed():
    output, sender, clock, _ = make_output()
    result = output.apply(
        PadAction(held=frozenset({"UP"}), taps=frozenset({"LEFT", "RIGHT", "B"}))
    )
    assert result.action == PadAction(held=frozenset({"UP"}), taps=frozenset({"B"}))
    assert sender.reports[-1][2] == HAT_UP
    assert sender.reports[-1][0] & B_BIT
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1][2] == HAT_UP
    assert sender.reports[-1][0] & B_BIT == 0


def test_all_directions_conflicting_sends_no_direction():
    output, sender, _, _ = make_output()
    result = output.apply(
        PadAction(held=frozenset({"UP", "DOWN", "LEFT", "RIGHT", "HOME"}))
    )
    assert result == FilteredAction(action=PadAction(), blocked_buttons=("HOME",))
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_tap_direction_overrides_opposite_held_direction_while_tapping():
    output, sender, clock, events = make_output()
    result = output.apply(PadAction(held=frozenset({"UP"}), taps=frozenset({"DOWN"})))
    # held と taps の間の相反する方向は外さず、短押しの間だけ短押しを優先する
    assert result.action == PadAction(held=frozenset({"UP"}), taps=frozenset({"DOWN"}))
    assert sender.reports[-1][2] == HAT_DOWN
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1][2] == HAT_UP
    assert len(events) == 1


def test_stick_values_are_clamped():
    output, sender, _, _ = make_output()
    output.apply(PadAction(left_stick=(1.5, -2.0), right_stick=(float("nan"), 0.0)))
    report = sender.reports[-1]
    assert report[3] == 255  # LX: 1.0
    assert report[4] == 255  # LY: -1.0 (下) は 255
    assert report[5] == 128  # RX: nan は 0.0


def test_neutral_sends_neutral_report_and_clears_state():
    output, sender, clock, events = make_output()
    output.apply(PadAction(held=frozenset({"A"}), taps=frozenset({"B"})))
    output.neutral()
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert events[-1] == OperationEvent(
        source="neutral", action=NEUTRAL_ACTION, ok=True, error=None
    )
    clock.now = 1.0
    count = len(sender.reports)
    output.release_due_taps()
    # 短押しも空にしたので、離すレポートは送らない
    assert len(sender.reports) == count


def test_stop_output_blocks_later_apply():
    output, sender, _, events = make_output()
    output.apply(PadAction(held=frozenset({"A"})))
    output.stop_output()
    assert sender.reports[-1] == NEUTRAL_REPORT
    assert events[-1].source == "stop"
    count = len(sender.reports)
    with pytest.raises(OutputStoppedError):
        output.apply(PadAction(held=frozenset({"B"})))
    assert len(sender.reports) == count
    assert events[-1].source == "stop"
    # 止めた後も中立は送れる
    output.neutral()
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_apply_send_failure_is_reported_and_raised():
    output, sender, _, events = make_output()
    sender.fail_with = RuntimeError("bridge is reconnecting")
    with pytest.raises(RuntimeError, match="reconnecting"):
        output.apply(PadAction(held=frozenset({"A"})))
    assert events[-1].source == "apply"
    assert events[-1].ok is False
    assert "reconnecting" in (events[-1].error or "")


def test_neutral_send_failure_is_not_raised():
    output, sender, _, events = make_output()
    sender.fail_with = RuntimeError("bridge failed")
    output.neutral()
    output.stop_output()
    assert "bridge failed" in (output.last_error or "")
    assert [event.ok for event in events] == [False, False]


def test_close_without_timer():
    output, sender, _, _ = make_output()
    output.close()
    assert sender.reports == []
