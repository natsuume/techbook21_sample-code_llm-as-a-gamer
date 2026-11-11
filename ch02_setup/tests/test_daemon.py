import socket

import pytest

from switch2_remote import daemon
from switch2_remote.bluetooth_radio import reset_bluetooth_radio
from switch2_remote.controller_bridge import BridgeStartError


class Recorder:
    """偽物の生成と close の順序を記録する。"""

    def __init__(self):
        self.events: list[str] = []


@pytest.fixture
def recorder(monkeypatch):
    """daemon.main が使う Bridge / BridgeSupervisor / Controller / Capture / ソケット / serve_connection を偽物に差し替える。

    偽の BridgeSupervisor は、受け取った start_bridge を本物と同じくコンストラクターで 1 回呼ぶ。
    偽の Bridge と Capture は本物と同じ引数だけを受け取り、ほかの引数を渡すと TypeError になる。
    rec.bridge_error を設定すると、偽の Bridge はその例外を送出する (起動の失敗)。
    """
    rec = Recorder()
    rec.bridge_error = None

    class FakeBridge:
        def __init__(self):
            if rec.bridge_error is not None:
                raise rec.bridge_error
            rec.events.append("bridge created")
            rec.bridge = self

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
        def __init__(self, ffmpeg_fps, process_fps):
            rec.events.append("capture created")
            rec.capture = self
            self.rates = (ffmpeg_fps, process_fps)

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


def test_main_starts_every_bridge_without_arguments(recorder):
    daemon.main([])
    first = recorder.bridge
    # 復旧で起動し直すブリッジも、最初のブリッジと同じく引数なしの Bridge() で起動する
    restarted = recorder.supervisor.start_bridge()
    assert restarted is recorder.bridge
    assert restarted is not first


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
    def failing_capture(ffmpeg_fps, process_fps):
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


def test_main_reads_command_line_when_argv_is_none(recorder, monkeypatch):
    monkeypatch.setattr("sys.argv", ["switch2-remote-daemon", "--process-fps", "5"])
    daemon.main()
    assert recorder.capture.rates == (30, 5)


def test_main_rejects_invalid_arguments_before_starting_anything(recorder):
    with pytest.raises(SystemExit):
        daemon.main(["--process-fps", "40"])
    assert recorder.events == []


def test_parse_args_uses_defaults():
    # 起動引数は ffmpeg_fps と process_fps の 2 つだけ
    assert vars(daemon.parse_args([])) == {"ffmpeg_fps": 30, "process_fps": 10}


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
