import threading

import pytest
from switch2_remote.controller_state import NEUTRAL_REPORT

from switch2_layers.buttons import ALL_BUTTONS, FilteredAction, allowed_buttons
from switch2_layers.hardware.pad_output import OutputStoppedError, PadOutput
from switch2_layers.pad_action import PadAction
from switch2_layers.records.operation_log import OperationEvent
from switch2_layers.surface.surface_output_gate import (
    SurfaceOutputBlockedError,
    SurfaceOutputGate,
)

HOME_BIT = 0x10  # レポートの b2


class FakeSender:
    """ブリッジの偽物。送られたレポートを記録する。"""

    def __init__(self):
        self.reports: list[bytes] = []

    def set_report(self, report: bytes) -> str:
        self.reports.append(bytes(report))
        return "ack"


class Phase:
    """段階の偽物。is_play は、ロックを持っているときに呼ばれたかを記録する。"""

    def __init__(self, lock: threading.Lock, play: bool = True):
        self.lock = lock
        self.play = play
        self.locked_on_call: list[bool] = []

    def is_play(self) -> bool:
        self.locked_on_call.append(self.lock.locked())
        return self.play


def make_gate(play: bool = True):
    sender = FakeSender()
    events: list[OperationEvent] = []
    # PadOutput は HOME を含む 17 個を押せるように作る (app と同じ)
    pad_output = PadOutput(
        sender, ALL_BUTTONS, on_operation=events.append, start_timer=False
    )
    lock = threading.Lock()
    phase = Phase(lock, play)
    gate = SurfaceOutputGate(pad_output, is_play=phase.is_play, lock=lock)
    return gate, sender, events, phase


def test_allowed_buttons_are_the_16_without_home():
    gate, _, _, _ = make_gate()
    assert gate.allowed_buttons == allowed_buttons()
    assert "HOME" not in gate.allowed_buttons
    assert len(gate.allowed_buttons) == 16


def test_apply_in_play_removes_home_and_sends_as_apply():
    gate, sender, events, phase = make_gate()
    result = gate.apply(
        PadAction(held=frozenset({"A", "HOME"}), taps=frozenset({"HOME"}))
    )
    assert result == FilteredAction(
        action=PadAction(held=frozenset({"A"})), blocked_buttons=("HOME",)
    )
    assert len(sender.reports) == 1
    assert sender.reports[0][1] & HOME_BIT == 0
    assert [event.source for event in events] == ["apply"]
    assert events[0].action == PadAction(held=frozenset({"A"}))
    # play かの確かめは、段階を変える処理と同じロックの中で行う
    assert phase.locked_on_call == [True]


def test_apply_outside_play_sends_nothing():
    gate, sender, events, _ = make_gate(play=False)
    with pytest.raises(SurfaceOutputBlockedError):
        gate.apply(PadAction(held=frozenset({"A"})))
    assert sender.reports == []
    assert events == []


def test_blocked_error_is_handled_like_a_stopped_output():
    assert issubclass(SurfaceOutputBlockedError, OutputStoppedError)


def test_neutral_in_play_sends_a_neutral_report():
    gate, sender, events, phase = make_gate()
    gate.neutral()
    assert sender.reports == [NEUTRAL_REPORT]
    assert [event.source for event in events] == ["neutral"]
    assert phase.locked_on_call == [True]


def test_neutral_outside_play_does_nothing():
    gate, sender, events, _ = make_gate(play=False)
    gate.neutral()
    assert sender.reports == []
    assert events == []


def test_gate_follows_the_current_phase():
    gate, sender, _, phase = make_gate(play=False)
    with pytest.raises(SurfaceOutputBlockedError):
        gate.apply(PadAction(taps=frozenset({"B"})))
    phase.play = True
    gate.apply(PadAction(taps=frozenset({"B"})))
    assert len(sender.reports) == 1
