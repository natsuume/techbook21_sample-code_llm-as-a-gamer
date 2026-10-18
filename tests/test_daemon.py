import socket

import pytest

from switch2_remote import daemon


class Recorder:
    """偽物の生成と close の順序を記録する。"""

    def __init__(self):
        self.events: list[str] = []


@pytest.fixture
def recorder(monkeypatch):
    """daemon.main が使う Bridge / Controller / Capture / ソケット / serve_connection を偽物に差し替える。"""
    rec = Recorder()

    class FakeBridge:
        def __init__(self):
            rec.events.append("bridge created")
            rec.bridge = self

        def close(self):
            rec.events.append("bridge closed")

    class FakeController:
        def __init__(self, bridge):
            rec.events.append("controller created")
            rec.controller = self
            self.bridge = bridge

        def close(self):
            rec.events.append("controller closed")

    class FakeCapture:
        def __init__(
            self, ffmpeg_fps, process_fps, buffer_seconds, output_width, output_height
        ):
            rec.events.append("capture created")
            rec.capture = self
            self.rates = (ffmpeg_fps, process_fps, buffer_seconds)
            self.output_size = (output_width, output_height)

        def close(self):
            rec.events.append("capture closed")

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeServer:
        def __init__(self, family, kind):
            rec.events.append("server created")

        def bind(self, address):
            pass

        def listen(self, backlog):
            pass

        def accept(self):
            return FakeConnection(), None

        def close(self):
            rec.events.append("server closed")

    def fake_serve_connection(conn, capture, controller):
        rec.served = (capture, controller)
        return True  # quit を受けたものとして終了させる

    monkeypatch.setattr(daemon, "Bridge", FakeBridge)
    monkeypatch.setattr(daemon, "Controller", FakeController)
    monkeypatch.setattr(daemon, "Capture", FakeCapture)
    monkeypatch.setattr(daemon.socket, "socket", FakeServer)
    monkeypatch.setattr(daemon, "serve_connection", fake_serve_connection)
    return rec


def test_main_passes_controller_built_from_bridge(recorder):
    daemon.main([])
    capture, controller = recorder.served
    assert controller is recorder.controller
    assert controller.bridge is recorder.bridge
    assert capture is recorder.capture


def test_main_closes_controller_bridge_capture_in_order(recorder):
    daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == [
        "controller closed",
        "bridge closed",
        "capture closed",
        "server closed",
    ]


def test_main_closes_only_created_objects_when_capture_fails(recorder, monkeypatch):
    def failing_capture(
        ffmpeg_fps, process_fps, buffer_seconds, output_width, output_height
    ):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(daemon, "Capture", failing_capture)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["controller closed", "bridge closed"]


def test_main_closes_bridge_when_controller_fails(recorder, monkeypatch):
    def failing_controller(bridge):
        raise RuntimeError("controller failed")

    monkeypatch.setattr(daemon, "Controller", failing_controller)
    with pytest.raises(RuntimeError, match="controller failed"):
        daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["bridge closed"]


def test_main_passes_default_rates_to_capture(recorder):
    daemon.main([])
    assert recorder.capture.rates == (30, 10, 5)
    assert recorder.capture.output_size == (1280, 720)


def test_main_passes_given_output_size_to_capture(recorder):
    daemon.main(["--output-size", "640x360"])
    assert recorder.capture.output_size == (640, 360)


def test_main_passes_given_rates_to_capture(recorder):
    daemon.main(["--ffmpeg-fps", "60", "--process-fps", "12", "--buffer-seconds", "10"])
    assert recorder.capture.rates == (60, 12, 10)


def test_main_reads_command_line_when_argv_is_none(recorder, monkeypatch):
    monkeypatch.setattr("sys.argv", ["switch2-remote-daemon", "--process-fps", "5"])
    daemon.main()
    assert recorder.capture.rates == (30, 5, 5)


def test_main_rejects_invalid_arguments_before_starting_anything(recorder):
    with pytest.raises(SystemExit):
        daemon.main(["--process-fps", "40"])
    assert recorder.events == []


def test_parse_args_uses_defaults():
    args = daemon.parse_args([])
    assert (args.ffmpeg_fps, args.process_fps, args.buffer_seconds) == (30, 10, 5)
    assert args.output_size == (1280, 720)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1280x720", (1280, 720)),
        ("640x360", (640, 360)),
        ("2x2", (2, 2)),
        ("1280x2", (1280, 2)),
    ],
)
def test_parse_args_accepts_output_size(text, expected):
    assert daemon.parse_args(["--output-size", text]).output_size == expected


@pytest.mark.parametrize(
    "text",
    [
        "641x360",
        "640x361",
        "0x360",
        "640x0",
        "1282x720",
        "1280x722",
        "1920x1080",
        "640",
        "640x",
        "x360",
        "640*360",
        "640X360",
        "-640x360",
        "640x360x2",
        " 640x360",
        "640.0x360",
        "６４０x360",
    ],
)
def test_parse_args_rejects_invalid_output_size(text, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(["--output-size", text])
    assert excinfo.value.code == 2
    assert "--output-size" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--ffmpeg-fps", "1", "--process-fps", "1"], (1, 1, 5)),
        (["--ffmpeg-fps", "60", "--process-fps", "60"], (60, 60, 5)),
        (["--ffmpeg-fps", "12", "--process-fps", "12"], (12, 12, 5)),
        (["--buffer-seconds", "1"], (30, 10, 1)),
        (["--buffer-seconds", "60"], (30, 10, 60)),
    ],
)
def test_parse_args_accepts_range(argv, expected):
    args = daemon.parse_args(argv)
    assert (args.ffmpeg_fps, args.process_fps, args.buffer_seconds) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--ffmpeg-fps", "0"],
        ["--ffmpeg-fps", "61"],
        ["--ffmpeg-fps", "abc"],
        ["--process-fps", "0"],
        ["--process-fps", "1.5"],
        ["--buffer-seconds", "0"],
        ["--buffer-seconds", "61"],
    ],
)
def test_parse_args_rejects_out_of_range_or_non_integer(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(argv)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--ffmpeg-fps", "30", "--process-fps", "31"],
        ["--process-fps", "40"],
        # --process-fps の既定値 10 も --ffmpeg-fps 以下でなければならない
        ["--ffmpeg-fps", "5"],
    ],
)
def test_parse_args_rejects_process_fps_above_ffmpeg_fps(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(argv)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_serve_connection_passes_controller_to_handle_command(monkeypatch):
    calls = []

    def fake_handle_command(request, capture, controller):
        calls.append((request, capture, controller))
        return "ok fake"

    monkeypatch.setattr(daemon, "handle_command", fake_handle_command)
    capture, controller = object(), object()
    server_side, client_side = socket.socketpair()
    with server_side, client_side:
        client_side.sendall(b"status\n")
        assert daemon.serve_connection(server_side, capture, controller) is False
        reply = client_side.makefile("r", encoding="utf-8").readline()
    assert calls == [("status\n", capture, controller)]
    assert reply == "ok fake\n"


def test_serve_connection_replies_error_with_exception_message(monkeypatch):
    def fake_handle_command(request, capture, controller):
        raise ValueError("already pressed: B")

    monkeypatch.setattr(daemon, "handle_command", fake_handle_command)
    server_side, client_side = socket.socketpair()
    with server_side, client_side:
        client_side.sendall(b"tap B\n")
        assert daemon.serve_connection(server_side, object(), object()) is False
        reply = client_side.makefile("r", encoding="utf-8").readline()
    assert reply == "error already pressed: B\n"


def test_serve_connection_returns_true_on_quit():
    server_side, client_side = socket.socketpair()
    with server_side, client_side:
        client_side.sendall(b"quit\n")
        assert daemon.serve_connection(server_side, object(), object()) is True
        assert client_side.makefile("r", encoding="utf-8").readline() == "bye\n"


def test_parse_args_explains_default_process_fps_above_ffmpeg_fps(capsys):
    with pytest.raises(SystemExit):
        daemon.parse_args(["--ffmpeg-fps", "5"])
    err = capsys.readouterr().err
    assert "--process-fps (10) must not exceed --ffmpeg-fps (5)" in err
    assert "--process-fps defaults to 10" in err
