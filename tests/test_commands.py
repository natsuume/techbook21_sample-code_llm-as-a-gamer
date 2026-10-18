import pytest

from switch2_remote.commands import handle_command, parse_hold_ms, parse_shot
from switch2_remote.controller_state import PadState


def pad(*buttons: str, **sticks: tuple[float, float]) -> PadState:
    """ボタン名とスティック (LS=(x, y) など) から PadState を作る。"""
    return PadState(frozenset(buttons), dict(sticks))


class FakeBridge:
    """controller_bridge.Bridge の代わり。status の bridge_alive に使う is_alive だけを持つ。"""

    def is_alive(self) -> bool:
        return True


class FakeController:
    """controller.Controller の代わりに、呼ばれた操作と引数を記録する偽物。"""

    def __init__(self):
        self.bridge = FakeBridge()
        self.calls: list[tuple] = []

    def tap(self, chords) -> str:
        self.calls.append(("tap", list(chords)))
        return "ok tap (fake)"

    def hold(self, chord, ms) -> str:
        self.calls.append(("hold", chord, ms))
        return "ok hold (fake)"

    def down(self, chords) -> str:
        self.calls.append(("down", list(chords)))
        return "ok held=(fake)"

    def up(self, targets) -> str:
        self.calls.append(("up", targets))
        return "ok held=(fake)"

    def up_all(self) -> str:
        self.calls.append(("up_all",))
        return "ok held=none"

    def status_text(self) -> str:
        return "held=A holds=B+RIGHT:1200ms last_error=none"


class FakeCapture:
    """capture.Capture の代わりに、撮影の要求を記録する偽物。"""

    def __init__(self):
        self.shots: list[tuple[str, float]] = []

    def shot(self, path: str, timeout: float) -> str:
        self.shots.append((path, timeout))
        return f"ok {path} settled=True waited=0.30s"

    def status(self) -> str:
        return "ok frames=10 ffmpeg_drops=0 ended=False"


@pytest.mark.parametrize(
    "text, expected", [("1", 1), ("500", 500), ("5000", 5000), ("60000", 60000)]
)
def test_parse_hold_ms_accepts_range(text, expected):
    assert parse_hold_ms(text) == expected


@pytest.mark.parametrize("text", ["0", "60001", "-1", "abc", "1.5"])
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


def test_handle_command_tap_parses_each_chord():
    controller = FakeController()
    reply = handle_command("tap right a+b ls:0.5,-1\n", FakeCapture(), controller)
    assert controller.calls == [
        ("tap", [pad("RIGHT"), pad("A", "B"), pad(LS=(0.5, -1.0))])
    ]
    assert reply == "ok tap (fake)"


@pytest.mark.parametrize(
    "request_line", ["tap RIGHT FOO\n", "tap A+\n", "tap LR\n", "tap UP+DOWN\n"]
)
def test_handle_command_tap_rejects_invalid_chord_before_sending(request_line):
    controller = FakeController()
    with pytest.raises(ValueError):
        handle_command(request_line, FakeCapture(), controller)
    assert controller.calls == []


def test_handle_command_hold_passes_chord_and_duration():
    controller = FakeController()
    reply = handle_command("hold b+right 2000\n", FakeCapture(), controller)
    assert controller.calls == [("hold", pad("B", "RIGHT"), 2000)]
    assert reply == "ok hold (fake)"


def test_handle_command_hold_accepts_max_duration():
    controller = FakeController()
    handle_command("hold A 60000\n", FakeCapture(), controller)
    assert controller.calls == [("hold", pad("A"), 60000)]


@pytest.mark.parametrize(
    "request_line", ["hold A 0\n", "hold A 60001\n", "hold A 1.5\n", "hold FOO 100\n"]
)
def test_handle_command_hold_rejects_invalid_arguments(request_line):
    controller = FakeController()
    with pytest.raises(ValueError):
        handle_command(request_line, FakeCapture(), controller)
    assert controller.calls == []


def test_handle_command_down_passes_chords():
    controller = FakeController()
    reply = handle_command("down a LS:0,1\n", FakeCapture(), controller)
    assert controller.calls == [("down", [pad("A"), pad(LS=(0.0, 1.0))])]
    assert reply == "ok held=(fake)"


def test_handle_command_up_passes_targets():
    controller = FakeController()
    reply = handle_command("up a+ls B\n", FakeCapture(), controller)
    assert controller.calls == [("up", frozenset({"A", "LS", "B"}))]
    assert reply == "ok held=(fake)"


@pytest.mark.parametrize("request_line", ["up ALL\n", "up all\n"])
def test_handle_command_up_all_releases_everything(request_line):
    controller = FakeController()
    reply = handle_command(request_line, FakeCapture(), controller)
    assert controller.calls == [("up_all",)]
    assert reply == "ok held=none"


@pytest.mark.parametrize("request_line", ["up FOO\n", "up LS:0,1\n", "up A+\n"])
def test_handle_command_up_rejects_invalid_target(request_line):
    controller = FakeController()
    with pytest.raises(ValueError):
        handle_command(request_line, FakeCapture(), controller)
    assert controller.calls == []


def test_handle_command_shot_passes_path_and_timeout():
    capture = FakeCapture()
    reply = handle_command("shot 3.0 /tmp/my screen.png\n", capture, FakeController())
    assert capture.shots == [("/tmp/my screen.png", 3.0)]
    assert reply == "ok /tmp/my screen.png settled=True waited=0.30s"


def test_handle_command_status_includes_bridge_and_controller_state():
    reply = handle_command("status\n", FakeCapture(), FakeController())
    assert reply == (
        "ok frames=10 ffmpeg_drops=0 ended=False bridge_alive=True "
        "held=A holds=B+RIGHT:1200ms last_error=none"
    )


@pytest.mark.parametrize(
    "request_line",
    [
        "jump\n",
        "tap\n",
        "hold A\n",
        "hold A 100 200\n",
        "down\n",
        "up\n",
    ],
)
def test_handle_command_rejects_unknown_or_malformed_command(request_line):
    controller = FakeController()
    reply = handle_command(request_line, FakeCapture(), controller)
    assert reply == f"error unknown command: {request_line.strip()}"
    assert controller.calls == []
