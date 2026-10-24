import pytest

from switch2_remote.commands import (
    handle_command,
    parse_fps,
    parse_hold_ms,
    parse_shot,
)
from switch2_remote.controller_state import PadState


def pad(*buttons: str, **sticks: tuple[float, float]) -> PadState:
    """ボタン名とスティック (LS=(x, y) など) から PadState を作る。"""
    return PadState(frozenset(buttons), dict(sticks))


class FakeBridge:
    """bridge_supervisor.BridgeSupervisor の代わり。status に使う is_alive・state・restarts だけを持つ。"""

    def __init__(self, alive: bool = True, state: str = "ok", restarts: int = 0):
        self.alive = alive
        self.state = state
        self.restarts = restarts

    def is_alive(self) -> bool:
        return self.alive


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
    """capture.Capture の代わりに、撮影と処理レートの変更の要求を記録する偽物。"""

    def __init__(self, ffmpeg_fps: int = 30):
        self.ffmpeg_fps = ffmpeg_fps
        self.shots: list[str] = []
        self.process_fps_changes: list[int] = []

    def shot(self, path: str) -> str:
        self.shots.append(path)
        return f"ok {path}"

    def set_process_fps(self, fps: int) -> str:
        self.process_fps_changes.append(fps)
        return f"ok fps={fps}"

    def status(self) -> str:
        return (
            "ok frames=10 buffered=5 latest_age=0.05s ffmpeg_fps=30 process_fps=10 "
            "ffmpeg_drops=0 ended=False"
        )


@pytest.mark.parametrize(
    "text, expected", [("1", 1), ("500", 500), ("5000", 5000), ("60000", 60000)]
)
def test_parse_hold_ms_accepts_range(text, expected):
    assert parse_hold_ms(text) == expected


@pytest.mark.parametrize("text", ["0", "60001", "-1", "abc", "1.5"])
def test_parse_hold_ms_rejects_out_of_range_or_non_integer(text):
    with pytest.raises(ValueError):
        parse_hold_ms(text)


def test_parse_shot_returns_path():
    assert parse_shot("shot /tmp/screen.png\n") == "/tmp/screen.png"


def test_parse_shot_keeps_spaces_in_path():
    assert parse_shot("shot /tmp/my screen.png\n") == "/tmp/my screen.png"


@pytest.mark.parametrize(
    "request_line", ["shot screen.png\n", "shot 5 /tmp/screen.png\n"]
)
def test_parse_shot_rejects_relative_path(request_line):
    with pytest.raises(ValueError, match="shot path must be absolute"):
        parse_shot(request_line)


@pytest.mark.parametrize("request_line", ["shot\n", "shot   \n"])
def test_parse_shot_rejects_missing_path(request_line):
    with pytest.raises(ValueError, match="usage: shot ABSOLUTE_PATH"):
        parse_shot(request_line)


@pytest.mark.parametrize(
    "text, max_fps, expected",
    [("1", 30, 1), ("10", 30, 10), ("30", 30, 30), ("60", 60, 60)],
)
def test_parse_fps_accepts_range(text, max_fps, expected):
    assert parse_fps(text, max_fps) == expected


@pytest.mark.parametrize("text", ["0", "31", "45", "-1", "abc", "1.5", ""])
def test_parse_fps_rejects_out_of_range_or_non_integer_with_range(text):
    with pytest.raises(ValueError, match=r"fps must be 1\.\.30: "):
        parse_fps(text, 30)


def test_parse_fps_reports_current_range_and_value():
    with pytest.raises(ValueError) as excinfo:
        parse_fps("45", 30)
    assert str(excinfo.value) == "fps must be 1..30: 45"


def test_parse_fps_uses_given_max_fps():
    with pytest.raises(ValueError, match=r"fps must be 1\.\.12: 13"):
        parse_fps("13", 12)


def test_handle_command_tap_parses_each_chord():
    controller = FakeController()
    reply = handle_command("tap right a+b ls:0.5,-1\n", FakeCapture(), controller)
    assert controller.calls == [
        ("tap", [pad("RIGHT"), pad("A", "B"), pad(LS=(0.5, -1.0))])
    ]
    assert reply == "ok tap (fake)"


def test_handle_command_tap_accepts_stick_click_buttons():
    controller = FakeController()
    handle_command("tap l3 R3+RS:1,0\n", FakeCapture(), controller)
    assert controller.calls == [("tap", [pad("L3"), pad("R3", RS=(1.0, 0.0))])]


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


def test_handle_command_shot_passes_path():
    capture = FakeCapture()
    reply = handle_command("shot /tmp/my screen.png\n", capture, FakeController())
    assert capture.shots == ["/tmp/my screen.png"]
    assert reply == "ok /tmp/my screen.png"


@pytest.mark.parametrize("request_line", ["shot screen.png\n", "shot\n"])
def test_handle_command_shot_rejects_invalid_path_before_shooting(request_line):
    capture = FakeCapture()
    with pytest.raises(ValueError):
        handle_command(request_line, capture, FakeController())
    assert capture.shots == []


def test_handle_command_fps_changes_process_fps():
    capture = FakeCapture()
    reply = handle_command("fps 12\n", capture, FakeController())
    assert capture.process_fps_changes == [12]
    assert reply == "ok fps=12"


def test_handle_command_fps_uses_capture_ffmpeg_fps_as_max():
    capture = FakeCapture(ffmpeg_fps=60)
    handle_command("fps 45\n", capture, FakeController())
    assert capture.process_fps_changes == [45]


@pytest.mark.parametrize("request_line", ["fps 0\n", "fps 31\n", "fps abc\n"])
def test_handle_command_fps_rejects_invalid_value_before_changing(request_line):
    capture = FakeCapture(ffmpeg_fps=30)
    with pytest.raises(ValueError, match=r"fps must be 1\.\.30: "):
        handle_command(request_line, capture, FakeController())
    assert capture.process_fps_changes == []


def test_handle_command_status_includes_bridge_and_controller_state():
    reply = handle_command("status\n", FakeCapture(), FakeController())
    assert reply == (
        "ok frames=10 buffered=5 latest_age=0.05s ffmpeg_fps=30 process_fps=10 "
        "ffmpeg_drops=0 ended=False bridge_alive=True bridge_state=ok "
        "bridge_restarts=0 held=A holds=B+RIGHT:1200ms last_error=none"
    )


def test_handle_command_status_shows_recovering_bridge_and_restarts():
    controller = FakeController()
    controller.bridge = FakeBridge(alive=False, state="recovering", restarts=2)
    reply = handle_command("status\n", FakeCapture(), controller)
    assert reply == (
        "ok frames=10 buffered=5 latest_age=0.05s ffmpeg_fps=30 process_fps=10 "
        "ffmpeg_drops=0 ended=False bridge_alive=False bridge_state=recovering "
        "bridge_restarts=2 held=A holds=B+RIGHT:1200ms last_error=none"
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
        "fps\n",
        "fps 10 20\n",
    ],
)
def test_handle_command_rejects_unknown_or_malformed_command(request_line):
    controller = FakeController()
    reply = handle_command(request_line, FakeCapture(), controller)
    assert reply == f"error unknown command: {request_line.strip()}"
    assert controller.calls == []
