from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from run_dashboard.cli import (
    DEFAULT_PORT,
    DEFAULT_RUNS_DIR,
    HOST,
    PORT_MAX,
    PORT_MIN,
    main,
    parse_args,
)


class FakeUvicornRun:
    """uvicorn.run の代わり。呼ばれた引数を calls に記録し、サーバーは起動しない。"""

    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


@pytest.fixture
def fake_run(monkeypatch) -> FakeUvicornRun:
    fake = FakeUvicornRun()
    monkeypatch.setattr(uvicorn, "run", fake)
    return fake


def test_constants():
    assert HOST == "127.0.0.1"
    assert DEFAULT_PORT == 8765
    assert DEFAULT_RUNS_DIR == "../../ch04_techniques/runs"
    assert (PORT_MIN, PORT_MAX) == (1, 65535)


# --- parse_args ---


def test_parse_args_defaults():
    args = parse_args([])
    assert args.runs_dir == Path("../../ch04_techniques/runs")
    assert args.port == 8765


def test_parse_args_reads_runs_dir_and_port():
    args = parse_args(["--runs-dir", "some/runs", "--port", "9000"])
    assert args.runs_dir == Path("some/runs")
    assert args.port == 9000


@pytest.mark.parametrize("port", ["1", "65535"])
def test_parse_args_accepts_port_bounds(port):
    assert parse_args(["--port", port]).port == int(port)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "80.5"])
def test_parse_args_rejects_port_out_of_range(port, capsys):
    with pytest.raises(SystemExit) as error:
        parse_args(["--port", port])
    assert error.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_parse_args_has_no_option_for_listen_address(capsys):
    with pytest.raises(SystemExit) as error:
        parse_args(["--host", "0.0.0.0"])
    assert error.value.code == 2


# --- main ---


def test_main_exits_with_message_when_runs_dir_is_missing(tmp_path, fake_run):
    missing = tmp_path / "missing"
    with pytest.raises(SystemExit) as error:
        main(["--runs-dir", str(missing)])
    message = str(error.value)
    assert str(missing) in message
    assert "--runs-dir" in message
    assert fake_run.calls == []


def test_main_exits_with_message_when_runs_dir_is_a_file(tmp_path, fake_run):
    file = tmp_path / "runs.txt"
    file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["--runs-dir", str(file)])
    assert str(file) in str(error.value)
    assert fake_run.calls == []


def test_main_runs_uvicorn_on_localhost_with_given_port(tmp_path, fake_run):
    main(["--runs-dir", str(tmp_path), "--port", "9000"])
    assert len(fake_run.calls) == 1
    args, kwargs = fake_run.calls[0]
    assert len(args) == 1
    assert isinstance(args[0], FastAPI)
    assert kwargs == {"host": "127.0.0.1", "port": 9000}


def test_main_uses_default_port_and_runs_dir_relative_to_cwd(
    tmp_path, monkeypatch, fake_run
):
    (tmp_path / "runs").mkdir()
    monkeypatch.chdir(tmp_path)
    main(["--runs-dir", "runs"])
    assert len(fake_run.calls) == 1
    _, kwargs = fake_run.calls[0]
    assert kwargs == {"host": "127.0.0.1", "port": 8765}
