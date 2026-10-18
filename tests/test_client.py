import os

import pytest

from switch2_remote import client
from switch2_remote.client import build_request


def test_build_request_joins_arguments_into_one_line():
    assert build_request(["tap", "RIGHT", "RIGHT", "A"]) == "tap RIGHT RIGHT A\n"


def test_build_request_passes_other_commands_through():
    assert build_request(["status"]) == "status\n"
    assert build_request(["hold", "A", "500"]) == "hold A 500\n"
    assert build_request(["quit"]) == "quit\n"


def test_build_request_passes_chords_and_sticks_through():
    assert build_request(["tap", "L+R", "a+b"]) == "tap L+R a+b\n"
    assert build_request(["hold", "B+RIGHT", "2000"]) == "hold B+RIGHT 2000\n"
    assert build_request(["down", "LS:0.5,-1+A"]) == "down LS:0.5,-1+A\n"


def test_build_request_sends_shot_with_absolute_path():
    assert build_request(["shot", "/tmp/screen.png"]) == "shot /tmp/screen.png\n"


def test_build_request_makes_shot_path_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert build_request(["shot", "screen.png"]) == (
        f"shot {tmp_path / 'screen.png'}\n"
    )


def test_build_request_keeps_spaces_in_shot_path():
    assert build_request(["shot", "/tmp/my screen.png"]) == (
        "shot /tmp/my screen.png\n"
    )


@pytest.mark.parametrize(
    "args", [["shot"], ["shot", "/tmp/a.png", "5"], ["shot", "/tmp/a.png", "b.png"]]
)
def test_build_request_rejects_shot_without_exactly_one_path(args):
    with pytest.raises(ValueError, match="usage: shot PATH"):
        build_request(args)


@pytest.mark.parametrize(
    "argv", [["switch2-remote", "shot"], ["switch2-remote", "shot", "/tmp/a.png", "5"]]
)
def test_main_does_not_send_shot_without_exactly_one_path(monkeypatch, capsys, argv):
    def fail_if_connecting(*args):
        raise AssertionError("must not connect to daemon")

    monkeypatch.setattr(client.socket, "socket", fail_if_connecting)
    monkeypatch.setattr(client.sys, "argv", argv)
    assert client.main() == 1
    assert capsys.readouterr().err == "error usage: shot PATH\n"


def test_build_request_passes_fps_through():
    assert build_request(["fps", "12"]) == "fps 12\n"


@pytest.mark.parametrize("arg", ["A\nquit", "A\r"])
def test_build_request_rejects_line_breaks(arg):
    with pytest.raises(ValueError, match="arguments must not contain line breaks"):
        build_request(["tap", arg])


def test_main_without_arguments_prints_usage_and_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(client.sys, "argv", ["switch2-remote"])
    assert client.main() == 2
    assert "使い方" in capsys.readouterr().err


def test_main_reports_error_when_daemon_is_not_running(monkeypatch, capsys):
    # 誰も待ち受けていない抽象名前空間のソケット名に向ける
    monkeypatch.setattr(
        client, "SOCKET_ADDRESS", f"\0switch2-remote-test-absent-{os.getpid()}"
    )
    monkeypatch.setattr(client.sys, "argv", ["switch2-remote", "status"])
    assert client.main() == 1
    assert capsys.readouterr().err.startswith("error cannot connect to daemon")
