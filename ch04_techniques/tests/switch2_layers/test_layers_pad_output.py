import math
import time

import pytest
from switch2_remote.controller_state import BUTTON_ORDER, NEUTRAL_REPORT

from switch2_layers.buttons import FilteredAction
from switch2_layers.hardware.pad_output import (
    MAX_HOLD_SECONDS,
    TAP_PRESS_S,
    TAP_RELEASE_GAP_S,
    OutputStoppedError,
    PadOutput,
    normalize_hold_seconds,
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


class SlowSender(FakeSender):
    """送るたびに偽の時計を delay 秒進めてから戻る ReportSender (送信が戻った時刻を確かめるため)。"""

    def __init__(self, clock: FakeMonotonic, delay: float):
        super().__init__()
        self.clock = clock
        self.delay = delay

    def set_report(self, report: bytes) -> str:
        ack = super().set_report(report)
        self.clock.now += self.delay
        return ack


def make_output(allowed=ALLOWED_WITHOUT_HOME):
    """タイマースレッドを起動しない PadOutput と、偽の送信先・時計・受け取った OperationEvent のリストを返す。"""
    sender = FakeSender()
    clock = FakeMonotonic(0.0)
    events: list[OperationEvent] = []
    output = PadOutput(
        sender, allowed, clock=clock, on_operation=events.append, start_timer=False
    )
    return output, sender, clock, events


def make_slow_output(delay: float):
    """make_output と同じものを、送るたびに時計が delay 秒進む送信先 (SlowSender) で返す。"""
    clock = FakeMonotonic(0.0)
    sender = SlowSender(clock, delay)
    events: list[OperationEvent] = []
    output = PadOutput(
        sender,
        ALLOWED_WITHOUT_HOME,
        clock=clock,
        on_operation=events.append,
        start_timer=False,
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


# --- 押されていたボタンの短押し (いったん離してから押す) ---


def test_tap_press_and_release_gap_constants():
    assert TAP_RELEASE_GAP_S == 0.1


def test_tap_of_button_not_pressed_before_starts_immediately():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"B"})))
    clock.now = 1.0
    output.apply(PadAction(held=frozenset({"B"}), taps=frozenset({"A"})))
    # 直前のレポートで押されていなかった A は、この apply のレポートですぐ押す
    assert sender.reports[-1][0] & A_BIT
    assert sender.reports[-1][0] & B_BIT
    count = len(sender.reports)
    clock.now = 2.0
    output.start_due_taps()
    assert len(sender.reports) == count


def test_held_to_tap_releases_first_then_taps_after_gap():
    output, sender, clock, events = make_output()
    output.apply(PadAction(held=frozenset({"A", "B"})))
    assert sender.reports[-1][0] & A_BIT
    clock.now = 1.0
    output.apply(
        PadAction(held=frozenset({"B"}), taps=frozenset({"A"}), left_stick=(1.0, 0.0))
    )
    # 1. まず A を離したレポートを送る (B とスティックは、この apply の値)
    release = sender.reports[-1]
    assert release[0] & A_BIT == 0
    assert release[0] & B_BIT
    assert release[3] == 255
    count = len(sender.reports)
    # 2. 離す間隔 (0.1 秒) の前は押さない
    clock.now = 1.0 + TAP_RELEASE_GAP_S - 0.01
    output.start_due_taps()
    assert len(sender.reports) == count
    # 3. 離す間隔の後に A を押したレポートを送る
    clock.now = 1.0 + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert len(sender.reports) == count + 1
    press = sender.reports[-1]
    assert press[0] & A_BIT
    assert press[0] & B_BIT
    assert press[3] == 255
    # 4. 押してから TAP_PRESS_S 後に離す
    pressed_at = clock.now
    clock.now = pressed_at + TAP_PRESS_S - 0.01
    output.release_due_taps()
    assert len(sender.reports) == count + 1
    clock.now = pressed_at + TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1][0] & A_BIT == 0
    assert sender.reports[-1][0] & B_BIT
    # タイマースレッドが送るレポートでは on_operation を呼ばない
    assert [event.source for event in events] == ["apply", "apply"]


def test_tap_again_during_tap_releases_then_taps_again():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT
    # 短押しの最中に、同じボタンの短押しをもう一度求める
    clock.now = 0.05
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1] == NEUTRAL_REPORT
    count = len(sender.reports)
    # 前の短押しの離す時刻 (0.12) になっても、もう離してあるので送らない
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert len(sender.reports) == count
    clock.now = 0.05 + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert sender.reports[-1][0] & A_BIT
    clock.now = 0.05 + TAP_RELEASE_GAP_S + TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1] == NEUTRAL_REPORT


def test_waiting_tap_is_cancelled_when_button_is_held_again():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"})))
    clock.now = 1.0
    output.apply(PadAction(taps=frozenset({"A"})))
    clock.now = 1.05
    output.apply(PadAction(held=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT
    count = len(sender.reports)
    clock.now = 2.0
    output.start_due_taps()
    output.release_due_taps()
    # 押し続けに戻ったので、待っていた短押しは始めない
    assert len(sender.reports) == count


def test_waiting_tap_is_not_rescheduled_by_later_apply():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"})))
    clock.now = 1.0
    output.apply(PadAction(taps=frozenset({"A"})))
    clock.now = 1.05
    output.apply(PadAction(taps=frozenset({"A"})))
    # 始める時刻は 1.0 + 0.1 のまま (1.05 + 0.1 に延ばさない)
    clock.now = 1.0 + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert sender.reports[-1][0] & A_BIT


def test_tap_right_after_timer_release_waits_for_gap():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(taps=frozenset({"A"})))
    # タイマーが TAP_PRESS_S の時点で A を離す (最後に離した時刻 = TAP_PRESS_S)
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1] == NEUTRAL_REPORT
    # 離してから TAP_RELEASE_GAP_S たつ前に、同じボタンの短押しが来る
    clock.now = TAP_PRESS_S + 0.03
    output.apply(PadAction(taps=frozenset({"A"})))
    # すぐには押さない
    assert sender.reports[-1][0] & A_BIT == 0
    count = len(sender.reports)
    clock.now = TAP_PRESS_S + TAP_RELEASE_GAP_S - 0.01
    output.start_due_taps()
    assert len(sender.reports) == count
    # 「離した時刻 + 間隔」に押す
    clock.now = TAP_PRESS_S + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert sender.reports[-1][0] & A_BIT


def test_tap_after_release_from_held_waits_for_gap():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"})))
    clock.now = 1.0
    # A を押し続けから外す (最後に離した時刻 = 1.0)
    output.apply(PadAction())
    clock.now = 1.05
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT == 0
    clock.now = 1.0 + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert sender.reports[-1][0] & A_BIT


def test_tap_long_after_release_starts_immediately():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(taps=frozenset({"A"})))
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    # 離してから TAP_RELEASE_GAP_S 以上たっていれば、この apply のレポートですぐ押す
    clock.now = TAP_PRESS_S + TAP_RELEASE_GAP_S
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT


@pytest.mark.parametrize("stop", ["neutral", "stop_output"])
def test_neutral_and_stop_cancel_waiting_taps(stop):
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"})))
    clock.now = 1.0
    output.apply(PadAction(taps=frozenset({"A"})))
    getattr(output, stop)()
    assert sender.reports[-1] == NEUTRAL_REPORT
    count = len(sender.reports)
    clock.now = 2.0
    output.start_due_taps()
    output.release_due_taps()
    assert len(sender.reports) == count


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


# --- 秒数を指定した押し続け ---


def test_max_hold_seconds_constant():
    assert MAX_HOLD_SECONDS == 60.0


def test_normalize_hold_seconds_drops_invalid_clamps_and_ignores_buttons_not_held():
    held = frozenset({"A", "B", "X", "Y", "L", "ZL", "ZR", "R"})
    seconds = {
        "A": 0.3,
        "B": 0.0,
        "X": -1.0,
        "Y": math.nan,
        "L": math.inf,
        "ZL": -math.inf,
        "ZR": 120.0,
        "UP": 0.5,
    }
    before = dict(seconds)
    assert normalize_hold_seconds(held, seconds) == (
        # 秒数の無い R は残す。秒数が有限の正の値でない B・X・Y・L・ZL は外す
        frozenset({"A", "ZR", "R"}),
        # 上限を超えた ZR は上限にする。held に無い UP の秒数は捨てる
        {"A": 0.3, "ZR": MAX_HOLD_SECONDS},
    )
    assert seconds == before


def test_timed_hold_is_released_at_send_return_plus_seconds():
    output, sender, clock, events = make_slow_output(delay=0.05)
    output.apply(
        PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}, left_stick=(1.0, 0.0))
    )
    assert sender.reports[-1][0] & A_BIT
    # 押すレポートの送信が戻った時刻 (0.05) + 0.3 秒に離す
    sent_at = clock.now
    clock.now = sent_at + 0.3 - 0.01
    output.release_due_holds()
    assert len(sender.reports) == 1
    clock.now = sent_at + 0.3
    output.release_due_holds()
    assert len(sender.reports) == 2
    assert sender.reports[-1][0] & A_BIT == 0
    # スティックは次の apply まで倒したまま
    assert sender.reports[-1][3] == 255
    # 期限で離すレポートでは on_operation を呼ばない
    assert [event.source for event in events] == ["apply"]


def test_hold_without_seconds_stays_until_next_apply():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A", "B"}), hold_seconds={"A": 0.3}))
    clock.now = 10.0
    output.release_due_holds()
    # 秒数のある A だけを離し、秒数の無い B は押したまま
    assert sender.reports[-1][0] & A_BIT == 0
    assert sender.reports[-1][0] & B_BIT
    count = len(sender.reports)
    clock.now = 20.0
    output.release_due_holds()
    assert len(sender.reports) == count


def test_holding_again_before_deadline_replaces_deadline_without_release():
    output, sender, clock, _ = make_slow_output(delay=0.05)
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    first_deadline = clock.now + 0.3
    clock.now = 0.2
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 1.0}))
    # 期限は「この apply の送信が戻った時刻 + 新しい秒数」
    new_deadline = clock.now + 1.0
    # 離さずに押し続ける
    assert all(report[0] & A_BIT for report in sender.reports)
    count = len(sender.reports)
    clock.now = first_deadline
    output.release_due_holds()
    assert len(sender.reports) == count
    clock.now = new_deadline - 0.01
    output.release_due_holds()
    assert len(sender.reports) == count
    clock.now = new_deadline
    output.release_due_holds()
    assert sender.reports[-1][0] & A_BIT == 0


def test_holding_again_without_seconds_removes_deadline():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.2
    output.apply(PadAction(held=frozenset({"A"})))
    count = len(sender.reports)
    clock.now = 10.0
    output.release_due_holds()
    # 次の apply まで押し続ける
    assert len(sender.reports) == count
    assert sender.reports[-1][0] & A_BIT


def test_tap_right_after_deadline_release_waits_for_gap():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.3
    output.release_due_holds()
    assert sender.reports[-1][0] & A_BIT == 0
    # 期限で離してから TAP_RELEASE_GAP_S たつ前に、同じボタンの短押しが来る
    clock.now = 0.33
    output.apply(PadAction(taps=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT == 0
    count = len(sender.reports)
    clock.now = 0.3 + TAP_RELEASE_GAP_S - 0.01
    output.start_due_taps()
    assert len(sender.reports) == count
    clock.now = 0.3 + TAP_RELEASE_GAP_S
    output.start_due_taps()
    assert sender.reports[-1][0] & A_BIT


def test_timed_hold_right_after_deadline_release_waits_for_gap():
    output, sender, clock, events = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.3
    output.release_due_holds()
    # 期限で離してから TAP_RELEASE_GAP_S たつ前に、同じボタンの押し続けが来る (例外にしない)
    clock.now = 0.33
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    assert sender.reports[-1][0] & A_BIT == 0
    count = len(sender.reports)
    clock.now = 0.3 + TAP_RELEASE_GAP_S - 0.01
    output.start_due_holds()
    assert len(sender.reports) == count
    started_at = 0.3 + TAP_RELEASE_GAP_S
    clock.now = started_at
    output.start_due_holds()
    assert len(sender.reports) == count + 1
    assert sender.reports[-1][0] & A_BIT
    # 押し始めたレポートの送信が戻った時刻 + 秒数で離す
    clock.now = started_at + 0.3 - 0.01
    output.release_due_holds()
    assert len(sender.reports) == count + 1
    clock.now = started_at + 0.3
    output.release_due_holds()
    assert sender.reports[-1][0] & A_BIT == 0
    # タイマーが送るレポートでは on_operation を呼ばない
    assert [event.source for event in events] == ["apply", "apply"]


def test_untimed_hold_right_after_deadline_release_waits_for_gap_then_stays():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.3
    output.release_due_holds()
    clock.now = 0.33
    output.apply(PadAction(held=frozenset({"A"})))
    assert sender.reports[-1][0] & A_BIT == 0
    clock.now = 0.3 + TAP_RELEASE_GAP_S
    output.start_due_holds()
    assert sender.reports[-1][0] & A_BIT
    count = len(sender.reports)
    clock.now = 100.0
    output.release_due_holds()
    assert len(sender.reports) == count


def test_waiting_hold_is_cancelled_when_next_apply_does_not_hold():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.3
    output.release_due_holds()
    clock.now = 0.33
    output.apply(PadAction(held=frozenset({"A"})))
    clock.now = 0.35
    output.apply(PadAction())
    count = len(sender.reports)
    clock.now = 1.0
    output.start_due_holds()
    # 押し続けから外れたので、待っていた押し続けは始めない
    assert len(sender.reports) == count
    assert sender.reports[-1] == NEUTRAL_REPORT


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_hold_with_invalid_seconds_is_removed_without_error(seconds):
    output, sender, _, events = make_output()
    result = output.apply(
        PadAction(held=frozenset({"A", "B"}), hold_seconds={"A": seconds})
    )
    # 例外にせず、A を押さずに外す。外したボタンは blocked_buttons に入れない
    assert result.blocked_buttons == ()
    assert result.action.held == frozenset({"B"})
    assert sender.reports[-1][0] & A_BIT == 0
    assert sender.reports[-1][0] & B_BIT
    assert events[-1].action.held == frozenset({"B"})


def test_hold_seconds_over_limit_are_treated_as_limit():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 120.0}))
    clock.now = MAX_HOLD_SECONDS - 0.01
    output.release_due_holds()
    assert sender.reports[-1][0] & A_BIT
    clock.now = MAX_HOLD_SECONDS
    output.release_due_holds()
    assert sender.reports[-1][0] & A_BIT == 0


def test_seconds_of_buttons_not_held_are_ignored():
    output, sender, clock, _ = make_output()
    output.apply(
        PadAction(held=frozenset({"B"}), taps=frozenset({"A"}), hold_seconds={"A": 1.0})
    )
    # A は秒数に依らず短押しのまま
    clock.now = TAP_PRESS_S
    output.release_due_taps()
    assert sender.reports[-1][0] & A_BIT == 0
    assert sender.reports[-1][0] & B_BIT
    count = len(sender.reports)
    clock.now = 5.0
    output.release_due_holds()
    assert len(sender.reports) == count


@pytest.mark.parametrize("stop", ["neutral", "stop_output"])
def test_neutral_and_stop_release_timed_holds(stop):
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    getattr(output, stop)()
    assert sender.reports[-1] == NEUTRAL_REPORT
    count = len(sender.reports)
    clock.now = 1.0
    output.start_due_holds()
    output.release_due_holds()
    assert len(sender.reports) == count


@pytest.mark.parametrize("stop", ["neutral", "stop_output"])
def test_neutral_and_stop_cancel_waiting_holds(stop):
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    clock.now = 0.3
    output.release_due_holds()
    clock.now = 0.33
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    getattr(output, stop)()
    count = len(sender.reports)
    clock.now = 1.0
    output.start_due_holds()
    output.release_due_holds()
    assert len(sender.reports) == count


def test_conflicting_directions_with_hold_seconds_are_both_removed():
    output, sender, clock, _ = make_output()
    result = output.apply(
        PadAction(
            held=frozenset({"UP", "DOWN", "A"}),
            hold_seconds={"UP": 0.3, "DOWN": 0.3},
        )
    )
    assert result.action.held == frozenset({"A"})
    assert result.blocked_buttons == ()
    assert sender.reports[-1] != NEUTRAL_REPORT
    assert sender.reports[-1][0] & A_BIT
    count = len(sender.reports)
    clock.now = 1.0
    output.release_due_holds()
    # 外した方向の期限は無いので、何も送らない
    assert len(sender.reports) == count


def test_deadline_release_send_failure_is_not_raised():
    output, sender, clock, _ = make_output()
    output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.3}))
    sender.fail_with = RuntimeError("bridge failed")
    clock.now = 0.3
    output.release_due_holds()
    assert "bridge failed" in (output.last_error or "")


def wait_until(condition, timeout_s: float = 2.0) -> bool:
    """condition() が真になるまで最大 timeout_s 秒待ち、真になったかを返す。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_timer_thread_releases_timed_hold_and_starts_waiting_hold():
    sender = FakeSender()
    output = PadOutput(sender, ALLOWED_WITHOUT_HOME)
    try:
        output.apply(PadAction(held=frozenset({"A"}), hold_seconds={"A": 0.05}))
        assert sender.reports[-1][0] & A_BIT
        # タイマースレッドが期限で離す
        assert wait_until(lambda: sender.reports[-1][0] & A_BIT == 0)
        # 離した直後の押し続けも、タイマースレッドが間をあけてから始める
        output.apply(PadAction(held=frozenset({"A"})))
        assert wait_until(lambda: sender.reports[-1][0] & A_BIT != 0)
    finally:
        output.close()
