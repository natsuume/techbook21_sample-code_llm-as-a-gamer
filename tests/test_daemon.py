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
        def __init__(self):
            rec.events.append("capture created")
            rec.capture = self

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
    daemon.main()
    capture, controller = recorder.served
    assert controller is recorder.controller
    assert controller.bridge is recorder.bridge
    assert capture is recorder.capture


def test_main_closes_controller_bridge_capture_in_order(recorder):
    daemon.main()
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == [
        "controller closed",
        "bridge closed",
        "capture closed",
        "server closed",
    ]


def test_main_closes_only_created_objects_when_capture_fails(recorder, monkeypatch):
    def failing_capture():
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(daemon, "Capture", failing_capture)
    with pytest.raises(RuntimeError):
        daemon.main()
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["controller closed", "bridge closed"]


def test_main_closes_bridge_when_controller_fails(recorder, monkeypatch):
    def failing_controller(bridge):
        raise RuntimeError("controller failed")

    monkeypatch.setattr(daemon, "Controller", failing_controller)
    with pytest.raises(RuntimeError):
        daemon.main()
    closes = [event for event in recorder.events if event.endswith("closed")]
    assert closes == ["bridge closed"]


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


def test_serve_connection_returns_true_on_quit():
    server_side, client_side = socket.socketpair()
    with server_side, client_side:
        client_side.sendall(b"quit\n")
        assert daemon.serve_connection(server_side, object(), object()) is True
        assert client_side.makefile("r", encoding="utf-8").readline() == "bye\n"
