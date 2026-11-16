from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.orchestrator.action_executor import ActionExecutor
from switch2_layers.orchestrator.decision_types import PressAction, WaitAction
from switch2_layers.records.operation_log import OperationEvent
from switch2_layers.run_clock import RunClock


class FakeMonotonic:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeSender:
    """送られたレポートを数える ReportSender。"""

    def __init__(self):
        self.reports: list[bytes] = []

    def set_report(self, report: bytes) -> str:
        self.reports.append(bytes(report))
        return "ack"


def make_executor():
    """タイマースレッドを起動しない PadOutput の上の ActionExecutor と、PadOutput・時計・OperationEvent のリストを返す。"""
    monotonic = FakeMonotonic(10.0)
    events: list[OperationEvent] = []
    pad_output = PadOutput(
        FakeSender(),
        frozenset(BUTTON_ORDER),
        clock=monotonic,
        on_operation=events.append,
        start_timer=False,
    )
    executor = ActionExecutor(pad_output, RunClock(monotonic, origin=0.0))
    return executor, pad_output, monotonic, events


def pressed_monitor_events(events: list[OperationEvent]) -> list[OperationEvent]:
    """監視の列が送った、ボタンを押す操作の行。"""
    return [e for e in events if e.source == "monitor" and e.action.held]


def test_not_stopped_before_sending_while_the_output_runs():
    executor, _, _, events = make_executor()
    executor.start([PressAction(buttons=("A",))], "monitor")
    executor.advance()
    assert len(pressed_monitor_events(events)) == 1
    assert executor.stopped_before_sending is False
    assert executor.output_stopped is False


def test_stopped_before_sending_when_the_output_was_stopped_before_start():
    executor, pad_output, _, events = make_executor()
    pad_output.stop_output()
    executor.start([WaitAction(seconds=0.5), PressAction(buttons=("A",))], "monitor")
    assert executor.owner is None
    executor.advance()
    assert executor.stopped_before_sending is True
    assert pressed_monitor_events(events) == []


def test_stopped_before_sending_when_the_first_press_is_refused():
    executor, pad_output, _, events = make_executor()
    executor.start([PressAction(buttons=("A",))], "monitor")
    # start の後、最初の押下を送る前に別のスレッドが出力を止めた
    pad_output.stop_output()
    executor.advance()
    assert executor.stopped_before_sending is True
    assert executor.owner is None
    assert pressed_monitor_events(events) == []


def test_not_stopped_before_sending_when_the_output_stops_after_the_first_press():
    executor, pad_output, _, events = make_executor()
    executor.start(
        [PressAction(buttons=("A",)), PressAction(buttons=("B",))], "monitor"
    )
    executor.advance()
    # 最初の押下を送った直後に、別のスレッドが出力を止めた。送った列は始めた列として扱う
    pad_output.stop_output()
    assert executor.output_stopped is True
    assert executor.stopped_before_sending is False
    assert len(pressed_monitor_events(events)) == 1


def test_not_stopped_before_sending_when_the_output_stops_after_a_wait_first_list_starts():
    executor, pad_output, monotonic, events = make_executor()
    executor.start([WaitAction(seconds=0.5), PressAction(buttons=("A",))], "monitor")
    executor.advance()
    # 列を受け付けた後に止まった。送ろうとするまでは、送る前に止まったとは分からない
    pad_output.stop_output()
    assert executor.stopped_before_sending is False
    monotonic.now += 1.0
    executor.advance()
    assert executor.stopped_before_sending is True
    assert executor.owner is None
    assert pressed_monitor_events(events) == []


def test_start_resets_whether_a_press_was_sent():
    executor, pad_output, monotonic, _ = make_executor()
    executor.start([PressAction(buttons=("A",))], "monitor")
    executor.advance()
    pad_output.stop_output()
    assert executor.stopped_before_sending is False
    monotonic.now += 1.0
    executor.start([PressAction(buttons=("B",))], "monitor")
    executor.advance()
    assert executor.stopped_before_sending is True
