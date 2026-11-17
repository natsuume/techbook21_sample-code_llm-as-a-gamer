"""cli のテスト。

デーモンへの送信と claude の起動がありうるサブコマンドは、fake を渡した run_cli で確かめる。
main (本番の依存) を呼ぶのは、デーモンにも claude にも触れない init と、依存の中身の確認だけにする。
"""

import json
import os
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from harness_fakes import (
    FAKE_FEATURES,
    FAKE_MULTI_FRAME,
    FAKE_STAGNATION_ALERT,
    FakeClock,
    FakeDaemon,
    FakeProcessStarter,
    FakeSleep,
    ImageWritingDaemon,
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
from switch2_harness.features import (
    FEATURES,
    MULTI_FRAME_FEATURE,
    STAGNATION_ALERT_FEATURE,
)
from switch2_harness.instructions import read_package_template
from switch2_harness.workspace_pin import PINNED_WORKSPACE_ENV

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
            deadline_clock=self.clock,
            now=lambda: datetime(2026, 11, 16, 21, 0, 5, tzinfo=JST),
            sleep=FakeSleep(self.clock),
            start_process=self.starter,
            environ={"PATH": "/usr/bin"},
            read_template=read_fake_template,
            available=FAKE_FEATURES,
        )

    def run(self, *argv: str) -> int:
        return run_cli(list(argv), self.deps)

    def pin(self, value: str) -> None:
        """環境に、エージェント用のサブコマンドを固定する環境変数を入れる (run が起動した claude の中と同じ)。"""
        environ = {**self.deps.environ, PINNED_WORKSPACE_ENV: value}
        self.deps = replace(self.deps, environ=environ)


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """init を使わずに作った作業ディレクトリー (harness.json・records/・frames/)。"""
    return make_workspace(tmp_path / "play")


def make_workspace(root: Path, features: Sequence[str] = ()) -> Path:
    """init を使わずに root に作業ディレクトリー (harness.json・records/・frames/) を作る。

    harness.json の features は、features の名前にする。
    """
    paths = make_paths(root)
    paths.harness_file.write_text(
        json.dumps(
            {
                "game": "ゲーム A",
                "goal": "できるだけ先へ進める",
                "features": list(features),
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


def test_press_home_in_a_quoted_argument_is_rejected(workspace, harness, capsys):
    argv = ["press", "tap", "A HOME", "--workspace", str(workspace)]
    assert harness.run(*argv) == 1
    assert capsys.readouterr().err == HOME_ERROR + "\n"
    assert harness.daemon.sent == []


def test_press_sends_quoted_argument_split_on_spaces(workspace, harness):
    argv = ["press", "tap", "RIGHT RIGHT", "--workspace", str(workspace)]
    assert harness.run(*argv) == 0
    assert harness.daemon.sent == [["tap", "RIGHT", "RIGHT"]]


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


# --- 行動履歴 (action-history) ---

HISTORY_HEADER = "直近の行動 (古い順、最大 20 件。秒数はこの撮影の何秒前か):"


def write_frames_on_shot(harness: Harness, values: list[int]) -> None:
    """harness の送信を、shot のたびに PNG を書いてから fake のデーモンに送るものに替える。

    PNG は、すべての画素が values の値 (shot のたびに順に 1 つずつ使う) のグレースケールの画像にする。
    """
    daemon = harness.daemon
    remaining = iter(values)

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        if args[0] == "shot":
            image = np.full((90, 160), next(remaining), dtype=np.uint8)
            assert cv2.imwrite(args[1], image)
        return daemon(args, timeout_s=timeout_s)

    harness.deps = replace(harness.deps, send=send)


@pytest.mark.parametrize(("second_value", "change"), [(255, "あり"), (0, "なし")])
def test_look_prints_the_history_with_action_history(
    tmp_path, harness, capsys, second_value, change
):
    directory = tmp_path / "play"
    argv = ["init", str(directory), "--game", "G", "--enable", "action-history"]
    assert harness.run(*argv) == 0
    write_frames_on_shot(harness, [0, second_value])
    workspace = ["--workspace", str(directory)]
    capsys.readouterr()
    # 1 回目の look を 1000 秒、press を 1010 秒、2 回目の look を 1020 秒に送る
    assert harness.run("look", *workspace) == 0
    first_frame = directory / "frames" / "000001.png"
    assert capsys.readouterr().out == f"ok {first_frame}\n直近の行動: なし\n"
    harness.clock.advance(9.5)
    assert harness.run("press", "tap", "A", *workspace) == 0
    harness.clock.advance(9.5)
    capsys.readouterr()
    assert harness.run("look", *workspace) == 0
    second_frame = directory / "frames" / "000002.png"
    assert capsys.readouterr().out == (
        f"ok {second_frame}\n"
        f"{HISTORY_HEADER}\n"
        "- 20 秒前 look 000001.png\n"
        f"- 10 秒前 press tap A: 画面の変化 {change}\n"
    )


def test_look_prints_no_history_without_features(tmp_path, harness, capsys):
    directory = tmp_path / "play"
    assert harness.run("init", str(directory), "--game", "G") == 0
    workspace = ["--workspace", str(directory)]
    assert harness.run("look", *workspace) == 0
    assert harness.run("press", "tap", "A", *workspace) == 0
    capsys.readouterr()
    assert harness.run("look", *workspace) == 0
    assert capsys.readouterr().out == f"ok {directory / 'frames' / '000002.png'}\n"


# --- 停滞の知らせ (stagnation-alert) ---

# 停滞の知らせの要素を含む、cli のテスト用の要素
FEATURES_WITH_STAGNATION_ALERT = (*FAKE_FEATURES, FAKE_STAGNATION_ALERT)


def write_last_new_screen_time(workspace: Path, last_new_time: float) -> None:
    """records/screen-samples/run-001/last-new-screen.json に、最後の新しい画面の時刻 last_new_time を書く。"""
    run_dir = workspace / "records" / "screen-samples" / "run-001"
    run_dir.mkdir(parents=True)
    state = {"index": 1, "time": last_new_time, "file": "000001.png"}
    (run_dir / "last-new-screen.json").write_text(json.dumps(state), encoding="utf-8")


def test_look_prints_the_stagnation_alert_when_enabled(tmp_path, harness, capsys):
    workspace = make_workspace(tmp_path / "play", [STAGNATION_ALERT_FEATURE])
    harness.deps = replace(harness.deps, available=FEATURES_WITH_STAGNATION_ALERT)
    # look の時刻 (1000 秒) の 300 秒前に、最後の新しい画面があった
    write_last_new_screen_time(workspace, 700.0)
    assert harness.run("look", "--workspace", str(workspace)) == 0
    frame_path = workspace / "frames" / "000001.png"
    assert capsys.readouterr().out == (
        f"ok {frame_path}\n停滞の知らせ: 新しい画面が 300 秒出ていません\n"
    )


def test_look_prints_no_stagnation_alert_when_disabled(tmp_path, harness, capsys):
    workspace = make_workspace(tmp_path / "play")
    harness.deps = replace(harness.deps, available=FEATURES_WITH_STAGNATION_ALERT)
    write_last_new_screen_time(workspace, 700.0)
    assert harness.run("look", "--workspace", str(workspace)) == 0
    frame_path = workspace / "frames" / "000001.png"
    assert capsys.readouterr().out == f"ok {frame_path}\n"


# --- 複数フレーム (multi-frame) ---

# 複数フレームの要素を含む、cli のテスト用の要素
FEATURES_WITH_MULTI_FRAME = (*FAKE_FEATURES, FAKE_MULTI_FRAME)


def test_look_prints_the_multi_frame_line_when_enabled(tmp_path, harness, capsys):
    workspace = make_workspace(tmp_path / "play", [MULTI_FRAME_FEATURE])
    # 撮影のたびに画像を書く送信と、時計を進める sleep に替える
    sleep = FakeSleep(harness.clock)
    harness.deps = replace(
        harness.deps,
        send=ImageWritingDaemon(harness.clock, step_s=0.05),
        sleep=sleep,
        available=FEATURES_WITH_MULTI_FRAME,
    )
    assert harness.run("look", "--workspace", str(workspace)) == 0
    frame_path = workspace / "frames" / "000001.png"
    tile_path = workspace / "multi-frames" / "000001.png"
    assert capsys.readouterr().out == f"ok {frame_path}\n複数フレーム: {tile_path}\n"
    # 撮影の間の待ちには deps.sleep を使う
    assert len(sleep.calls) == 5


def test_look_prints_no_multi_frame_line_when_disabled(tmp_path, harness, capsys):
    workspace = make_workspace(tmp_path / "play")
    harness.deps = replace(harness.deps, available=FEATURES_WITH_MULTI_FRAME)
    assert harness.run("look", "--workspace", str(workspace)) == 0
    frame_path = workspace / "frames" / "000001.png"
    assert capsys.readouterr().out == f"ok {frame_path}\n"
    assert harness.daemon.sent == [["shot", str(frame_path)]]


# --- メモファイル (notes) ---


def test_init_with_notes_writes_the_initial_notes(tmp_path, harness):
    # 本物の要素の一覧とテンプレートで init する
    harness.deps = replace(
        harness.deps, read_template=read_package_template, available=FEATURES
    )
    directory = tmp_path / "play"
    assert harness.run("init", str(directory), "--game", "G", "--enable", "notes") == 0
    notes = directory / "notes.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8") == read_package_template(
        "notes-initial.md"
    )


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
        "2.5000",
    ]


def test_run_zero_minutes_fails(workspace, harness, capsys):
    assert harness.run("run", "--minutes", "0", "--workspace", str(workspace)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error --minutes must be greater than 0: ")
    assert harness.starter.launches == []
    assert harness.daemon.sent == []


@pytest.mark.parametrize("budget", ["0", "-2", "nan", "inf"])
def test_run_invalid_max_budget_usd_fails(workspace, harness, capsys, budget):
    argv = ["run", "--minutes", "1", "--max-budget-usd", budget]
    assert harness.run(*argv, "--workspace", str(workspace)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(
        "error --max-budget-usd must be a positive finite number: "
    )
    assert harness.starter.launches == []
    assert harness.daemon.sent == []


# --- main と本番の依存 ---


def test_production_dependencies():
    deps = production_dependencies()
    assert deps.send is send_command
    assert deps.clock is time.time
    # run の期限も壁時計で決める (WSL2 では time.monotonic が実時間より遅く進むため)
    assert deps.deadline_clock is time.time
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


# --- エージェント用のサブコマンドの作業ディレクトリーの固定 ---

AGENT_ARGVS = [
    ["look"],
    ["press", "tap", "A"],
    ["status"],
    ["finish"],
]


def events_file(workspace: Path) -> Path:
    return workspace / "records" / "events.jsonl"


@pytest.mark.parametrize("argv", AGENT_ARGVS)
def test_pinned_command_uses_the_pinned_workspace_without_option(
    tmp_path, workspace, harness, monkeypatch, argv
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    harness.pin(str(workspace))
    assert harness.run(*argv) == 0
    assert harness.daemon.sent
    assert events_file(workspace).exists()


def test_pinned_look_shoots_into_the_pinned_workspace(
    tmp_path, workspace, harness, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    harness.pin(str(workspace))
    assert harness.run("look") == 0
    assert harness.daemon.sent == [["shot", str(workspace / "frames" / "000001.png")]]


@pytest.mark.parametrize("argv", AGENT_ARGVS)
def test_pinned_command_rejects_another_workspace(
    tmp_path, workspace, harness, capsys, argv
):
    other = make_workspace(tmp_path / "other")
    harness.pin(str(workspace))
    assert harness.run(*argv, "--workspace", str(other)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == f"error this command is pinned to the workspace: {workspace}\n"
    )
    assert harness.daemon.sent == []
    assert not events_file(workspace).exists()
    assert not events_file(other).exists()


@pytest.mark.parametrize(
    ("cwd", "written"),
    [(".", "play"), (".", "play/"), (".", "./play/"), ("sub", "../play")],
)
def test_pinned_command_accepts_the_same_path_written_differently(
    tmp_path, workspace, harness, monkeypatch, cwd, written
):
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / cwd)
    harness.pin(str(workspace))
    assert harness.run("status", "--workspace", written) == 0
    assert harness.daemon.sent == [["status"]]


def test_pinned_command_accepts_a_symlink_to_the_workspace(
    tmp_path, workspace, harness
):
    link = tmp_path / "link"
    link.symlink_to(workspace)
    harness.pin(str(workspace))
    assert harness.run("status", "--workspace", str(link)) == 0
    assert harness.daemon.sent == [["status"]]


@pytest.mark.parametrize("argv", [["look"], ["status", "--workspace", "play"]])
def test_empty_pin_is_rejected(tmp_path, workspace, harness, capsys, monkeypatch, argv):
    monkeypatch.chdir(tmp_path)
    harness.pin("")
    assert harness.run(*argv) == 1
    assert (
        capsys.readouterr().err == "error this command is pinned to the workspace: \n"
    )
    assert harness.daemon.sent == []
    assert not events_file(workspace).exists()


def test_report_ignores_the_pin(tmp_path, workspace, harness, capsys):
    other = make_workspace(tmp_path / "other")
    harness.pin(str(workspace))
    assert harness.run("report", "--json", "--workspace", str(other)) == 0
    assert json.loads(capsys.readouterr().out)["command_counts"] == {}


def test_run_ignores_the_pin(tmp_path, workspace, harness):
    other = make_workspace(tmp_path / "other")
    harness.pin(str(workspace))
    assert harness.run("run", "--minutes", "1", "--workspace", str(other)) == 0
    [launch] = harness.starter.launches
    assert launch.cwd == other
    assert launch.env[PINNED_WORKSPACE_ENV] == str(other)


def test_init_ignores_the_pin(tmp_path, workspace, harness):
    harness.pin(str(workspace))
    assert harness.run("init", str(tmp_path / "new"), "--game", "G") == 0
    assert (tmp_path / "new" / "harness.json").exists()


def test_run_interrupted_by_ctrl_c_exits_with_1(workspace, harness, capsys):
    # 3 回目の待ちの間に Ctrl-C を受けたことにする
    harness.deps = replace(
        harness.deps, sleep=FakeSleep(harness.clock, interrupt_at_call=3)
    )
    assert harness.run("run", "--minutes", "1", "--workspace", str(workspace)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error run interrupted\n"
    assert harness.daemon.sent == [["up", "ALL"]]
