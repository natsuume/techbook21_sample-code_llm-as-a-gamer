"""cli のテスト。

デーモンへの送信と claude の起動がありうるサブコマンドは、fake を渡した run_cli で確かめる。
main (本番の依存) を呼ぶのは、デーモンにも claude にも触れない init と、依存の中身の確認だけにする。
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from harness_fakes import (
    FAKE_FEATURES,
    FakeClock,
    FakeDaemon,
    FakeProcessStarter,
    FakeSleep,
    ProcessPlan,
    make_paths,
    read_fake_template,
)

from switch2_harness.agent_launcher import DEFAULT_MODEL, start_claude_process
from switch2_harness.cli import (
    PROG,
    CliDependencies,
    build_parser,
    main,
    production_dependencies,
    run_cli,
)
from switch2_harness.daemon_client import send_command
from switch2_harness.features import FEATURES
from switch2_harness.instructions import read_package_template

JST = timezone(timedelta(hours=9))
HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


class Harness:
    """fake の依存と、それを渡した run_cli。"""

    def __init__(self) -> None:
        self.clock = FakeClock(1000.0)
        self.daemon = FakeDaemon(self.clock)
        self.starter = FakeProcessStarter(self.clock, [ProcessPlan(runs_for_s=None)])
        self.deps = CliDependencies(
            send=self.daemon,
            clock=self.clock,
            now=lambda: datetime(2026, 11, 16, 21, 0, 5, tzinfo=JST),
            sleep=FakeSleep(self.clock),
            start_process=self.starter,
            environ={"PATH": "/usr/bin"},
            read_template=read_fake_template,
            available=FAKE_FEATURES,
        )

    def run(self, *argv: str) -> int:
        return run_cli(list(argv), self.deps)


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """init を使わずに作った作業ディレクトリー (harness.json・records/・frames/)。"""
    paths = make_paths(tmp_path / "play")
    paths.harness_file.write_text(
        json.dumps(
            {
                "game": "ゲーム A",
                "goal": "できるだけ先へ進める",
                "features": [],
                "created_at": "2026-11-16T21:00:05+09:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return paths.root


# --- parser ---


def test_parser_prog():
    assert build_parser().prog == PROG == "switch2-harness"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["init", "plays/x"],
        ["press"],
        ["press", "tap"],
        ["run"],
        ["run", "--minutes", "abc"],
    ],
)
def test_usage_errors_exit_with_2(harness, argv):
    with pytest.raises(SystemExit) as error:
        harness.run(*argv)
    assert error.value.code == 2
    assert harness.daemon.sent == []
    assert harness.starter.launches == []


# --- init ---


def test_init_uses_default_goal(tmp_path, harness, capsys):
    directory = tmp_path / "play"
    assert harness.run("init", str(directory), "--game", "ゲーム A") == 0
    assert capsys.readouterr().out == f"ok {directory} features=none\n"
    harness_json = json.loads((directory / "harness.json").read_text("utf-8"))
    assert harness_json == {
        "game": "ゲーム A",
        "goal": "できるだけ先へ進める",
        "features": [],
        "created_at": "2026-11-16T21:00:05+09:00",
    }


@pytest.mark.parametrize(
    "enable_args",
    [
        ["--enable", "memo-files", "action-history"],
        ["--enable", "memo-files", "--enable", "action-history"],
    ],
)
def test_init_with_goal_and_features(tmp_path, harness, capsys, enable_args):
    directory = tmp_path / "play"
    argv = ["init", str(directory), "--game", "G", "--goal", "X", *enable_args]
    assert harness.run(*argv) == 0
    assert capsys.readouterr().out == (
        f"ok {directory} features=action-history,memo-files\n"
    )
    harness_json = json.loads((directory / "harness.json").read_text("utf-8"))
    assert harness_json["goal"] == "X"
    assert harness_json["features"] == ["action-history", "memo-files"]


def test_init_unknown_feature_fails(tmp_path, harness, capsys):
    directory = tmp_path / "play"
    assert harness.run("init", str(directory), "--game", "G", "--enable", "zoom") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error unknown feature: zoom (available: action-history, memo-files)\n"
    )
    assert not directory.exists()


def test_init_non_empty_directory_fails(tmp_path, harness, capsys):
    directory = tmp_path / "play"
    directory.mkdir()
    (directory / "memo.txt").write_text("keep", encoding="utf-8")
    assert harness.run("init", str(directory), "--game", "G") == 1
    assert capsys.readouterr().err.startswith("error ")


# --- 作業ディレクトリーの確認 ---


@pytest.mark.parametrize(
    "argv",
    [
        ["look"],
        ["press", "tap", "A"],
        ["status"],
        ["finish"],
        ["report"],
        ["run", "--minutes", "1"],
    ],
)
def test_commands_outside_a_workspace_fail(tmp_path, harness, capsys, argv):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert harness.run(*argv, "--workspace", str(empty)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"error not a harness workspace (harness.json not found): {empty}\n"
    )
    assert harness.daemon.sent == []
    assert harness.starter.launches == []


# --- look / press / status / finish ---


def test_look_with_workspace(workspace, harness, capsys):
    assert harness.run("look", "--workspace", str(workspace)) == 0
    frame_path = workspace / "frames" / "000001.png"
    assert harness.daemon.sent == [["shot", str(frame_path)]]
    assert capsys.readouterr().out == f"ok {frame_path}\n"


def test_workspace_defaults_to_current_directory(workspace, harness, monkeypatch):
    monkeypatch.chdir(workspace)
    assert harness.run("look") == 0
    assert harness.daemon.sent == [["shot", str(workspace / "frames" / "000001.png")]]


@pytest.mark.parametrize(
    ("argv", "sent"),
    [
        (["press", "tap", "B+RIGHT"], ["tap", "B+RIGHT"]),
        (["press", "tap", "RIGHT", "RIGHT", "A"], ["tap", "RIGHT", "RIGHT", "A"]),
        (["press", "hold", "A", "500"], ["hold", "A", "500"]),
        (["press", "down", "LS:-1,0"], ["down", "LS:-1,0"]),
        (["press", "up", "ALL"], ["up", "ALL"]),
    ],
)
def test_press_dispatch(workspace, harness, argv, sent):
    assert harness.run(*argv, "--workspace", str(workspace)) == 0
    assert harness.daemon.sent == [sent]


def test_press_home_is_rejected(workspace, harness, capsys):
    assert harness.run("press", "tap", "HOME", "--workspace", str(workspace)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == HOME_ERROR + "\n"
    assert harness.daemon.sent == []


def test_status_dispatch(workspace, harness, capsys):
    assert harness.run("status", "--workspace", str(workspace)) == 0
    assert harness.daemon.sent == [["status"]]
    assert capsys.readouterr().out == "ok status\n"


def test_finish_dispatch(workspace, harness, capsys):
    assert harness.run("finish", "--workspace", str(workspace)) == 0
    assert harness.daemon.sent == [["up", "ALL"], ["tap", "HOME"]]
    assert capsys.readouterr().out == "ok held=none\nok tap HOME\n"


def test_daemon_error_exits_with_1(workspace, harness, capsys):
    harness.daemon.overrides["status"] = "error cannot connect to daemon: refused"
    assert harness.run("status", "--workspace", str(workspace)) == 1
    assert capsys.readouterr().out == "error cannot connect to daemon: refused\n"


# --- report ---


def test_report_json(workspace, harness, capsys):
    harness.run("status", "--workspace", str(workspace))
    capsys.readouterr()
    assert harness.run("report", "--json", "--workspace", str(workspace)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["command_counts"] == {"status": 1}
    assert summary["agent"] is None


def test_report_text(workspace, harness, capsys):
    assert harness.run("report", "--workspace", str(workspace)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 8
    assert lines[0] == "記録の時間: 0.0 秒"


# --- run ---


def test_run_dispatch_with_defaults(workspace, harness, capsys):
    assert harness.run("run", "--minutes", "1", "--workspace", str(workspace)) == 0
    log = workspace / "records" / "agent.jsonl"
    assert capsys.readouterr().out == (
        f"ok run finished reason=deadline launches=1 log={log}\n"
    )
    [launch] = harness.starter.launches
    assert launch.args[launch.args.index("--model") + 1] == DEFAULT_MODEL
    assert "--effort" not in launch.args
    assert "--max-budget-usd" not in launch.args
    assert launch.cwd == workspace
    assert launch.env["PATH"] == "/usr/bin"


def test_run_dispatch_with_options(workspace, harness):
    argv = [
        "run",
        "--minutes",
        "0.5",
        "--model",
        "m",
        "--effort",
        "high",
        "--max-budget-usd",
        "2.5",
        "--workspace",
        str(workspace),
    ]
    assert harness.run(*argv) == 0
    [launch] = harness.starter.launches
    assert launch.args[-6:] == [
        "--model",
        "m",
        "--effort",
        "high",
        "--max-budget-usd",
        "2.5",
    ]


def test_run_zero_minutes_fails(workspace, harness, capsys):
    assert harness.run("run", "--minutes", "0", "--workspace", str(workspace)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error --minutes must be greater than 0: ")
    assert harness.starter.launches == []
    assert harness.daemon.sent == []


# --- main と本番の依存 ---


def test_production_dependencies():
    deps = production_dependencies()
    assert deps.send is send_command
    assert deps.clock is time.time
    assert deps.sleep is time.sleep
    assert deps.start_process is start_claude_process
    assert deps.environ is os.environ
    assert deps.read_template is read_package_template
    assert deps.available is FEATURES
    assert deps.now().utcoffset() is not None


def test_main_init_uses_the_package_template(tmp_path, capsys):
    directory = tmp_path / "play"
    assert main(["init", str(directory), "--game", "ゲーム A"]) == 0
    assert capsys.readouterr().out == f"ok {directory} features=none\n"
    instructions = (directory / "CLAUDE.md").read_text(encoding="utf-8")
    assert "- ゲーム名: ゲーム A\n" in instructions
    assert "- 目標: できるだけ先へ進める\n" in instructions
    created_at = json.loads((directory / "harness.json").read_text("utf-8"))[
        "created_at"
    ]
    assert datetime.fromisoformat(created_at).utcoffset() is not None
    assert len(created_at) == len("2026-11-16T21:00:05+09:00")
