import pytest

from switch2_remote import commands
from switch2_remote.commands import (
    handle_command,
    parse_hold_ms,
    parse_shot,
    tap_sequence,
    validate_buttons,
)
from switch2_remote.controller_bridge import UnconfirmedInputError


class FakeBridge:
    """controller_bridge.Bridge の代わりに、押されたボタンを記録する偽物。

    fail_on に指定したボタンを押すと、fail_with の例外を送出する。
    """

    def __init__(self, fail_on: str | None = None, fail_with: Exception | None = None):
        self.taps: list[tuple[str, int]] = []
        self.fail_on = fail_on
        self.fail_with = fail_with

    def tap(self, button: str, hold_ms: int = 120) -> str:
        if button == self.fail_on:
            raise self.fail_with
        self.taps.append((button, hold_ms))
        return f"ack {button} 1 / done {button} 2"

    def is_alive(self) -> bool:
        return True


class FakeCapture:
    """capture.Capture の代わりに、撮影の要求を記録する偽物。"""

    def __init__(self):
        self.shots: list[tuple[str, float]] = []

    def shot(self, path: str, timeout: float) -> str:
        self.shots.append((path, timeout))
        return f"ok {path} settled=True waited=0.30s"

    def status(self) -> str:
        return "ok frames=10 ffmpeg_drops=0 ended=False"


@pytest.fixture(autouse=True)
def no_tap_interval(monkeypatch):
    # ボタン間の待ちはテストでは不要なので省く
    monkeypatch.setattr(commands.time, "sleep", lambda seconds: None)


def test_validate_buttons_uppercases_names():
    assert validate_buttons(["a", "Right", "lr"]) == ["A", "RIGHT", "LR"]


def test_validate_buttons_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown button: FOO BAR"):
        validate_buttons(["A", "foo", "bar"])


@pytest.mark.parametrize("text, expected", [("1", 1), ("500", 500), ("5000", 5000)])
def test_parse_hold_ms_accepts_range(text, expected):
    assert parse_hold_ms(text) == expected


@pytest.mark.parametrize("text", ["0", "5001", "-1", "abc"])
def test_parse_hold_ms_rejects_out_of_range_or_non_integer(text):
    with pytest.raises(ValueError):
        parse_hold_ms(text)


def test_parse_shot_keeps_spaces_in_path():
    assert parse_shot("shot 5.0 /tmp/my screen.png\n") == (5.0, "/tmp/my screen.png")


@pytest.mark.parametrize("timeout", ["0", "-1", "60.1"])
def test_parse_shot_rejects_timeout_out_of_range(timeout):
    with pytest.raises(ValueError, match="shot timeout must be in"):
        parse_shot(f"shot {timeout} /tmp/screen.png")


def test_parse_shot_accepts_max_timeout():
    assert parse_shot("shot 60 /tmp/screen.png") == (60.0, "/tmp/screen.png")


def test_parse_shot_rejects_relative_path():
    with pytest.raises(ValueError, match="shot path must be absolute"):
        parse_shot("shot 5 screen.png")


def test_parse_shot_rejects_missing_arguments():
    with pytest.raises(ValueError, match="usage: shot TIMEOUT ABSOLUTE_PATH"):
        parse_shot("shot 5")


def test_tap_sequence_presses_buttons_in_order():
    bridge = FakeBridge()
    reply = tap_sequence(bridge, ["RIGHT", "A"])
    assert bridge.taps == [("RIGHT", 120), ("A", 120)]
    assert reply == "ack RIGHT 1 / done RIGHT 2 | ack A 1 / done A 2"


def test_tap_sequence_reports_sent_inputs_when_bridge_fails_before_ack():
    bridge = FakeBridge(fail_on="A", fail_with=RuntimeError("bridge exited"))
    reply = tap_sequence(bridge, ["RIGHT", "A", "B"])
    assert bridge.taps == [("RIGHT", 120)]
    assert reply == (
        "error RuntimeError('bridge exited') "
        "(already sent: ack RIGHT 1 / done RIGHT 2; unconfirmed: none)"
    )


def test_tap_sequence_reports_unconfirmed_button_after_ack():
    bridge = FakeBridge(fail_on="RIGHT", fail_with=UnconfirmedInputError("no ack"))
    reply = tap_sequence(bridge, ["RIGHT", "A"])
    assert bridge.taps == []
    assert reply == (
        "error UnconfirmedInputError('no ack') (already sent: none; unconfirmed: RIGHT)"
    )


def test_handle_command_tap_validates_all_buttons_before_sending():
    bridge = FakeBridge()
    with pytest.raises(ValueError):
        handle_command("tap RIGHT FOO\n", FakeCapture(), bridge)
    assert bridge.taps == []


def test_handle_command_hold_passes_duration():
    bridge = FakeBridge()
    reply = handle_command("hold a 500\n", FakeCapture(), bridge)
    assert bridge.taps == [("A", 500)]
    assert reply == "ack A 1 / done A 2"


def test_handle_command_shot_passes_path_and_timeout():
    capture = FakeCapture()
    reply = handle_command("shot 3.0 /tmp/my screen.png\n", capture, FakeBridge())
    assert capture.shots == [("/tmp/my screen.png", 3.0)]
    assert reply == "ok /tmp/my screen.png settled=True waited=0.30s"


def test_handle_command_status_includes_bridge_state():
    reply = handle_command("status\n", FakeCapture(), FakeBridge())
    assert reply == "ok frames=10 ffmpeg_drops=0 ended=False bridge_alive=True"


@pytest.mark.parametrize("request_line", ["jump\n", "tap\n", "hold A\n"])
def test_handle_command_rejects_unknown_or_malformed_command(request_line):
    bridge = FakeBridge()
    reply = handle_command(request_line, FakeCapture(), bridge)
    assert reply == f"error unknown command: {request_line.strip()}"
    assert bridge.taps == []
