import socket

import pytest

from switch2_remote import daemon
from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.controller_bridge import BridgeStartError
from switch2_remote.hardware_env import COM_PORT_ENV, DEVICE_FPS_ENV


@pytest.fixture(autouse=True)
def without_hardware_env(monkeypatch):
    """どのテストも、開発者の環境の SWITCH2_COM_PORT・SWITCH2_DEVICE_FPS で既定値が変わらないよう、消してから動かす。"""
    monkeypatch.delenv(COM_PORT_ENV, raising=False)
    monkeypatch.delenv(DEVICE_FPS_ENV, raising=False)


class Recorder:
    """偽物の生成と close の順序を記録する。"""

    def __init__(self):
        self.events: list[str] = []


@pytest.fixture
def recorder(monkeypatch):
    """daemon.main が使う Bridge / BridgeSupervisor / Controller / Capture / ソケット / serve_connection を偽物に差し替える。

    偽の BridgeSupervisor は、受け取った start_bridge を本物と同じくコンストラクターで 1 回呼ぶ。
    偽の Bridge と Capture は本物と同じ引数だけを受け取り、ほかの引数を渡すと TypeError になる。
    偽の Bridge の port_name は、渡されなければ None のまま残す (daemon.main が明示的に渡したかを確かめるため。
    本物の既定値は DEFAULT_COM_PORT)。
    rec.bridge_error を設定すると、偽の Bridge はその例外を送出する (起動の失敗)。
    """
    rec = Recorder()
    rec.bridge_error = None

    class FakeBridge:
        def __init__(self, port_name=None):
            if rec.bridge_error is not None:
                raise rec.bridge_error
            rec.events.append("bridge created")
            rec.bridge = self
            self.port_name = port_name

    class FakeSupervisor:
        def __init__(self, start_bridge, reset_radio):
            self.start_bridge = start_bridge
            self.reset_radio = reset_radio
            self.on_recovered = None
            self.bridge = start_bridge()
            rec.events.append("supervisor created")
            rec.supervisor = self

        def close(self):
            rec.events.append("supervisor closed")

    class FakeController:
        def __init__(self, bridge):
            rec.events.append("controller created")
            rec.controller = self
            self.bridge = bridge

        def reset_state(self):
            rec.events.append("controller reset")

        def close(self):
            rec.events.append("controller closed")

    class FakeCapture:
        def __init__(self, ffmpeg_fps, process_fps, device_fps):
            rec.events.append("capture created")
            rec.capture = self
            self.rates = (ffmpeg_fps, process_fps)
            self.device_fps = device_fps

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
    monkeypatch.setattr(daemon, "BridgeSupervisor", FakeSupervisor)
    monkeypatch.setattr(daemon, "Controller", FakeController)
    monkeypatch.setattr(daemon, "Capture", FakeCapture)
    monkeypatch.setattr(daemon.socket, "socket", FakeServer)
    monkeypatch.setattr(daemon, "serve_connection", fake_serve_connection)
    return rec


def test_main_passes_controller_built_from_supervisor(recorder):
    daemon.main([])
    capture, controller = recorder.served
    assert controller is recorder.controller
    assert controller.bridge is recorder.supervisor
    assert capture is recorder.capture


def test_main_builds_supervisor_with_bridge_and_radio_reset(recorder):
    daemon.main([])
    supervisor = recorder.supervisor
    assert supervisor.bridge is recorder.bridge
    assert supervisor.reset_radio is reset_bluetooth_radio


@pytest.mark.parametrize(
    "argv, port_name",
    [([], "COM3"), (["--com-port", "COM4"], "COM4"), (["--com-port", "com4"], "COM4")],
)
def test_main_starts_every_bridge_with_the_com_port(recorder, argv, port_name):
    daemon.main(argv)
    first = recorder.bridge
    assert first.port_name == port_name
    # 復旧で起動し直すブリッジも、最初のブリッジと同じ COM ポートで起動する
    restarted = recorder.supervisor.start_bridge()
    assert restarted is recorder.bridge
    assert restarted is not first
    assert restarted.port_name == port_name


def test_main_resets_controller_state_on_recovery(recorder):
    daemon.main([])
    assert recorder.supervisor.on_recovered == recorder.controller.reset_state


def test_main_exits_with_message_when_first_bridge_fails(recorder):
    recorder.bridge_error = BridgeStartError("controller bridge failed: fatal port")
    with pytest.raises(SystemExit) as excinfo:
        daemon.main([])
    assert excinfo.value.code == "controller bridge failed: fatal port"
    assert recorder.events == []


def test_main_closes_controller_supervisor_capture_in_order(recorder):
    daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == [
        "controller closed",
        "supervisor closed",
        "capture closed",
        "server closed",
    ]


def test_main_closes_only_created_objects_when_capture_fails(recorder, monkeypatch):
    def failing_capture(ffmpeg_fps, process_fps, device_fps):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(daemon, "Capture", failing_capture)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["controller closed", "supervisor closed"]


def test_main_closes_supervisor_when_controller_fails(recorder, monkeypatch):
    def failing_controller(bridge):
        raise RuntimeError("controller failed")

    monkeypatch.setattr(daemon, "Controller", failing_controller)
    with pytest.raises(RuntimeError, match="controller failed"):
        daemon.main([])
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["supervisor closed"]


def test_main_passes_default_rates_to_capture(recorder):
    daemon.main([])
    assert recorder.capture.rates == (30, 10)


def test_main_passes_given_rates_to_capture(recorder):
    daemon.main(["--ffmpeg-fps", "60", "--process-fps", "12"])
    assert recorder.capture.rates == (60, 12)


def test_main_passes_default_device_fps_to_capture(recorder):
    daemon.main([])
    assert recorder.capture.device_fps == 60


def test_main_passes_given_device_fps_to_capture(recorder):
    daemon.main(["--device-fps", "30"])
    assert recorder.capture.device_fps == 30
    assert recorder.capture.rates == (30, 10)


def test_main_reads_command_line_when_argv_is_none(recorder, monkeypatch):
    monkeypatch.setattr("sys.argv", ["switch2-remote-daemon", "--process-fps", "5"])
    daemon.main()
    assert recorder.capture.rates == (30, 5)


def test_main_rejects_invalid_arguments_before_starting_anything(recorder):
    with pytest.raises(SystemExit):
        daemon.main(["--process-fps", "40"])
    assert recorder.events == []


def test_parse_args_uses_defaults():
    # 起動引数は device_fps と ffmpeg_fps と process_fps と com_port の 4 つだけ
    assert vars(daemon.parse_args([])) == {
        "device_fps": 60,
        "ffmpeg_fps": 30,
        "process_fps": 10,
        "com_port": "COM3",
    }


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--com-port", "COM3"], "COM3"),
        (["--com-port", "COM4"], "COM4"),
        (["--com-port", "com4"], "COM4"),
        (["--com-port", "COM10"], "COM10"),
    ],
)
def test_parse_args_accepts_com_port_in_upper_case(argv, expected):
    assert daemon.parse_args(argv).com_port == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--com-port", "COM0"],
        ["--com-port", "COM04"],
        ["--com-port", "4"],
        ["--com-port", "COM4;"],
        ["--com-port", "COM 4"],
        ["--com-port", ""],
        # - で始まる値も、= でつなげば --com-port の値として渡る
        ["--com-port=-PortName"],
    ],
)
def test_parse_args_rejects_invalid_com_port(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(argv)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err
    assert "--com-port" in err
    assert "invalid COM port" in err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--device-fps", "60"], 60),
        (["--device-fps", "30"], 30),
    ],
)
def test_parse_args_accepts_device_fps_choices(argv, expected):
    assert daemon.parse_args(argv).device_fps == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--device-fps", "45"],
        ["--device-fps", "120"],
        ["--device-fps", "0"],
        ["--device-fps", "abc"],
    ],
)
def test_parse_args_rejects_device_fps_outside_choices(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(argv)
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--device-fps", "30", "--ffmpeg-fps", "30"], (30, 30)),
        (["--device-fps", "30", "--ffmpeg-fps", "12", "--process-fps", "12"], (30, 12)),
    ],
)
def test_parse_args_accepts_ffmpeg_fps_up_to_device_fps(argv, expected):
    args = daemon.parse_args(argv)
    assert (args.device_fps, args.ffmpeg_fps) == expected


def test_parse_args_rejects_ffmpeg_fps_above_device_fps(capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(["--device-fps", "30", "--ffmpeg-fps", "31"])
    assert excinfo.value.code == 2
    assert "--ffmpeg-fps (31) must not exceed --device-fps (30)" in (
        capsys.readouterr().err
    )


# --- 環境変数による既定値 (SWITCH2_COM_PORT・SWITCH2_DEVICE_FPS) ---


@pytest.mark.parametrize(
    "environ",
    [
        {},
        # .env.sample の空の行をそのまま写したときの値
        {"SWITCH2_COM_PORT": "", "SWITCH2_DEVICE_FPS": ""},
    ],
)
def test_parse_args_without_arguments_and_environ_uses_com3_and_60(environ):
    args = daemon.parse_args([], environ=environ)
    assert (args.com_port, args.device_fps) == ("COM3", 60)


def test_parse_args_takes_defaults_from_environ():
    environ = {"SWITCH2_COM_PORT": "com4", "SWITCH2_DEVICE_FPS": "30"}
    assert vars(daemon.parse_args([], environ=environ)) == {
        "device_fps": 30,
        "ffmpeg_fps": 30,
        "process_fps": 10,
        "com_port": "COM4",
    }


def test_parse_args_prefers_arguments_to_environ():
    environ = {"SWITCH2_COM_PORT": "COM4", "SWITCH2_DEVICE_FPS": "30"}
    args = daemon.parse_args(
        ["--com-port", "COM5", "--device-fps", "60"], environ=environ
    )
    assert (args.com_port, args.device_fps) == ("COM5", 60)


@pytest.mark.parametrize(
    "argv, environ, expected",
    [
        (["--com-port", "COM4"], {"SWITCH2_COM_PORT": "COM0"}, ("COM4", 60)),
        (["--device-fps", "30"], {"SWITCH2_DEVICE_FPS": "45"}, ("COM3", 30)),
        (
            ["--com-port", "COM4", "--device-fps", "30"],
            {"SWITCH2_COM_PORT": "-PortName", "SWITCH2_DEVICE_FPS": "030"},
            ("COM4", 30),
        ),
    ],
)
def test_parse_args_does_not_read_environ_of_given_arguments(argv, environ, expected):
    # 起動引数を指定したときは環境変数を読まないため、環境変数の値が誤っていてもよい
    args = daemon.parse_args(argv, environ=environ)
    assert (args.com_port, args.device_fps) == expected


@pytest.mark.parametrize(
    "environ, message",
    [
        (
            {"SWITCH2_COM_PORT": "COM0"},
            "SWITCH2_COM_PORT must be COM1, COM2, ...: 'COM0'",
        ),
        (
            {"SWITCH2_COM_PORT": "-PortName"},
            "SWITCH2_COM_PORT must be COM1, COM2, ...: '-PortName'",
        ),
        (
            {"SWITCH2_DEVICE_FPS": "45"},
            "SWITCH2_DEVICE_FPS must be one of 30, 60: '45'",
        ),
        (
            {"SWITCH2_DEVICE_FPS": " 30"},
            "SWITCH2_DEVICE_FPS must be one of 30, 60: ' 30'",
        ),
    ],
)
def test_parse_args_rejects_invalid_environ_without_arguments(environ, message, capsys):
    # 環境変数の誤りは、起動引数の誤りと同じく使い方とともに表示して終了する
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args([], environ=environ)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err
    assert message in err


def test_parse_args_checks_ffmpeg_fps_against_device_fps_from_environ(capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(["--ffmpeg-fps", "31"], environ={"SWITCH2_DEVICE_FPS": "30"})
    assert excinfo.value.code == 2
    assert "--ffmpeg-fps (31) must not exceed --device-fps (30)" in (
        capsys.readouterr().err
    )


def test_parse_args_checks_ffmpeg_fps_against_device_fps_from_arguments():
    # --device-fps を指定したときは、環境変数の値ではなく --device-fps の値で比べる
    args = daemon.parse_args(
        ["--device-fps", "60", "--ffmpeg-fps", "60"],
        environ={"SWITCH2_DEVICE_FPS": "30"},
    )
    assert (args.device_fps, args.ffmpeg_fps) == (60, 60)


@pytest.mark.parametrize(
    "argv",
    [
        ["--buffer-seconds", "5"],
        ["--output-size", "1280x720"],
        ["--keep-sending-neutral"],
    ],
)
def test_parse_args_rejects_unknown_options(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        daemon.parse_args(argv)
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--ffmpeg-fps", "1", "--process-fps", "1"], (1, 1)),
        (["--ffmpeg-fps", "60", "--process-fps", "60"], (60, 60)),
        (["--ffmpeg-fps", "12", "--process-fps", "12"], (12, 12)),
    ],
)
def test_parse_args_accepts_range(argv, expected):
    args = daemon.parse_args(argv)
    assert (args.ffmpeg_fps, args.process_fps) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--ffmpeg-fps", "0"],
        ["--ffmpeg-fps", "61"],
        ["--ffmpeg-fps", "abc"],
        ["--process-fps", "0"],
        ["--process-fps", "1.5"],
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
