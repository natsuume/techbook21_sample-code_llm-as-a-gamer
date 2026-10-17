import pytest

from switch2_remote import client
from switch2_remote.client import build_request


def test_build_request_joins_arguments_into_one_line():
    assert build_request(["tap", "RIGHT", "RIGHT", "A"]) == "tap RIGHT RIGHT A\n"


def test_build_request_passes_other_commands_through():
    assert build_request(["status"]) == "status\n"
    assert build_request(["hold", "A", "500"]) == "hold A 500\n"
    assert build_request(["quit"]) == "quit\n"


def test_build_request_reorders_shot_with_default_timeout():
    assert build_request(["shot", "/tmp/screen.png"]) == "shot 5.0 /tmp/screen.png\n"


def test_build_request_reorders_shot_with_timeout():
    assert build_request(["shot", "/tmp/screen.png", "3"]) == (
        "shot 3.0 /tmp/screen.png\n"
    )


def test_build_request_makes_shot_path_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert build_request(["shot", "screen.png"]) == (
        f"shot 5.0 {tmp_path / 'screen.png'}\n"
    )


def test_build_request_keeps_spaces_in_shot_path():
    assert build_request(["shot", "/tmp/my screen.png"]) == (
        "shot 5.0 /tmp/my screen.png\n"
    )


def test_build_request_rejects_non_numeric_shot_timeout():
    with pytest.raises(ValueError):
        build_request(["shot", "/tmp/screen.png", "soon"])


@pytest.mark.parametrize("arg", ["A\nquit", "A\r"])
def test_build_request_rejects_line_breaks(arg):
    with pytest.raises(ValueError, match="arguments must not contain line breaks"):
        build_request(["tap", arg])


def test_main_without_arguments_prints_usage_and_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(client.sys, "argv", ["switch2-remote"])
    assert client.main() == 2
    assert "使い方" in capsys.readouterr().err
