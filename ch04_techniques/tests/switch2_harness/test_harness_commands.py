import json
import math
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
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

from switch2_harness.agent_launcher import (
    LaunchOptions,
    build_claude_args,
    first_prompt,
)
from switch2_harness.commands import (
    RUN_INTERRUPTED_MESSAGE,
    CommandResult,
    handle_finish,
    handle_init,
    handle_look,
    handle_press,
    handle_report,
    handle_run,
    handle_status,
    is_error_reply,
    release_not_confirmed_message,
)
from switch2_harness.event_log import CommandEvent, append_event, read_events
from switch2_harness.features import BASE_AGENT_COMMANDS
from switch2_harness.signal_guard import RunTerminated
from switch2_harness.workspace import WorkspacePaths

JST = timezone(timedelta(hours=9))
START_TIME = 1000.0
HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def fixed_now() -> datetime:
    return datetime(2026, 11, 16, 21, 0, 5, 123456, tzinfo=JST)


def write_harness_json(paths: WorkspacePaths, features: list[str]) -> None:
    paths.harness_file.write_text(
        json.dumps(
            {
                "game": "ゲーム A",
                "goal": "ボスを倒す",
                "features": features,
                "created_at": "2026-11-16T21:00:05+09:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_TIME)


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    return make_paths(tmp_path / "play")


# --- is_error_reply ---


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ("error no frame yet", True),
        ("error", True),
        ("ok tap A", False),
        ("", False),
        ("ok error", False),
    ],
)
def test_is_error_reply(reply, error):
    assert is_error_reply(reply) is error


# --- init ---


def test_init_creates_the_workspace(tmp_path):
    directory = tmp_path / "plays" / "one"
    result = handle_init(
        directory,
        "ゲーム A",
        "ボスを倒す",
        [],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {directory} features=none",)
    )
    assert json.loads((directory / "harness.json").read_text(encoding="utf-8")) == {
        "game": "ゲーム A",
        "goal": "ボスを倒す",
        "features": [],
        "created_at": "2026-11-16T21:00:05+09:00",
    }
    assert (directory / "CLAUDE.md").read_text(encoding="utf-8") == (
        "# 基本 ゲーム A\n\n目標: ボスを倒す\n"
    )
    assert (directory / "records").is_dir()
    assert (directory / "frames").is_dir()


def test_init_enables_features_in_available_order(tmp_path):
    directory = tmp_path / "play"
    result = handle_init(
        directory,
        "G",
        "X",
        ["memo-files", "action-history"],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert result.stdout_lines == (
        f"ok {directory} features=action-history,memo-files",
    )
    harness = json.loads((directory / "harness.json").read_text(encoding="utf-8"))
    assert harness["features"] == ["action-history", "memo-files"]
    assert (directory / "CLAUDE.md").read_text(encoding="utf-8") == (
        "# 基本 G\n\n目標: X\n\n## 行動履歴 (G)\n\n## メモ\n\nX を目指す\n"
    )


def test_init_prints_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = handle_init(
        "plays/one",
        "G",
        "X",
        [],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert result.stdout_lines == (f"ok {tmp_path / 'plays' / 'one'} features=none",)


def test_init_rejects_unknown_feature_without_creating_anything(tmp_path):
    directory = tmp_path / "play"
    result = handle_init(
        directory,
        "G",
        "X",
        ["zoom"],
        now=fixed_now,
        read_template=read_fake_template,
        available=(),
    )
    assert result == CommandResult(
        exit_code=1,
        stderr_lines=("error unknown feature: zoom (available: none)",),
    )
    assert not directory.exists()


def test_init_rejects_non_empty_directory(tmp_path):
    directory = tmp_path / "play"
    directory.mkdir()
    (directory / "memo.txt").write_text("keep", encoding="utf-8")
    result = handle_init(
        directory,
        "G",
        "X",
        [],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 1
    assert result.stdout_lines == ()
    assert result.stderr_lines == (f"error directory is not empty: {directory}",)
    assert os.listdir(directory) == ["memo.txt"]


# --- look ---


def test_look_shoots_the_next_frame_and_records_it(paths, clock):
    daemon = FakeDaemon(clock)
    result = handle_look(paths, send=daemon, clock=clock)
    frame_path = paths.frames_dir / "000001.png"
    assert daemon.sent == [["shot", str(frame_path)]]
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))
    assert read_events(paths.events_file) == [
        CommandEvent(
            time=START_TIME,
            command="look",
            args=(),
            reply=f"ok {frame_path}",
            ok=True,
            elapsed_s=0.5,
            frame="frames/000001.png",
        )
    ]


def test_look_uses_the_number_after_existing_frames(paths, clock):
    (paths.frames_dir / "000001.png").write_bytes(b"")
    (paths.frames_dir / "000002.png").write_bytes(b"")
    daemon = FakeDaemon(clock)
    handle_look(paths, send=daemon, clock=clock)
    assert daemon.sent == [["shot", str(paths.frames_dir / "000003.png")]]
    assert read_events(paths.events_file)[0].frame == "frames/000003.png"


def test_look_error_reply_fails_and_records_no_frame(paths, clock):
    daemon = FakeDaemon(clock, {"shot": "error no frame yet"})
    result = handle_look(paths, send=daemon, clock=clock)
    assert result == CommandResult(exit_code=1, stdout_lines=("error no frame yet",))
    [event] = read_events(paths.events_file)
    assert event.ok is False
    assert event.frame is None
    assert event.reply == "error no frame yet"


def test_look_error_reply_removes_the_reserved_frame(paths, clock):
    daemon = FakeDaemon(clock, {"shot": "error no frame yet"})
    handle_look(paths, send=daemon, clock=clock)
    assert list(paths.frames_dir.iterdir()) == []


def test_look_reserves_the_frame_before_shooting(paths, clock):
    reserved: list[bool] = []

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        # shot を送る時点で、パスはもう確保されている (空のファイルがある)
        reserved.append(Path(args[1]).is_file())
        return f"ok {args[1]}"

    handle_look(paths, send=send, clock=clock)
    assert reserved == [True]
    assert (paths.frames_dir / "000001.png").is_file()


def test_overlapping_looks_get_different_frames(paths, clock):
    sent: list[list[str]] = []

    def send(args: list[str], *, timeout_s: float | None = None) -> str:
        sent.append(list(args))
        if len(sent) == 1:
            # 1 回目の shot の応答を待つ間に、もう 1 つの look が動く
            handle_look(paths, send=FakeDaemon(clock), clock=clock)
        return f"ok {args[1]}"

    handle_look(paths, send=send, clock=clock)
    frames = sorted(event.frame for event in read_events(paths.events_file))
    assert frames == ["frames/000001.png", "frames/000002.png"]


def test_report_ignores_frames_of_failed_looks(paths, clock):
    handle_look(
        paths, send=FakeDaemon(clock, {"shot": "error no frame yet"}), clock=clock
    )
    handle_look(
        paths, send=FakeDaemon(clock, {"shot": "error no frame yet"}), clock=clock
    )
    result = handle_report(paths, as_json=True)
    assert result.exit_code == 0
    assert json.loads(result.stdout_lines[0])["command_counts"] == {"look": 2}


# --- press ---


def test_press_sends_action_and_args(paths, clock):
    daemon = FakeDaemon(clock)
    result = handle_press(paths, "tap", ["B+RIGHT"], send=daemon, clock=clock)
    assert daemon.sent == [["tap", "B+RIGHT"]]
    assert result == CommandResult(exit_code=0, stdout_lines=("ok tap B+RIGHT",))
    assert read_events(paths.events_file) == [
        CommandEvent(
            time=START_TIME,
            command="press",
            args=("tap", "B+RIGHT"),
            reply="ok tap B+RIGHT",
            ok=True,
            elapsed_s=0.5,
            frame=None,
        )
    ]


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["RIGHT", "RIGHT", "A"]),
        ("hold", ["A", "500"]),
        ("down", ["LS:0,1"]),
        ("up", ["ALL"]),
    ],
)
def test_press_sends_args_unchanged(paths, clock, action, args):
    daemon = FakeDaemon(clock)
    handle_press(paths, action, args, send=daemon, clock=clock)
    assert daemon.sent == [[action, *args]]


@pytest.mark.parametrize(
    ("action", "args", "tokens"),
    [
        ("tap", ["RIGHT RIGHT"], ["RIGHT", "RIGHT"]),
        ("tap", ["  A ", "B+RIGHT"], ["A", "B+RIGHT"]),
        ("hold", ["A 500"], ["A", "500"]),
        ("down", ["LS:0,1  A"], ["LS:0,1", "A"]),
    ],
)
def test_press_sends_args_split_on_spaces(paths, clock, action, args, tokens):
    daemon = FakeDaemon(clock)
    handle_press(paths, action, args, send=daemon, clock=clock)
    assert daemon.sent == [[action, *tokens]]
    assert read_events(paths.events_file)[0].args == (action, *tokens)


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("tap", ["HOME"]),
        ("tap", ["home"]),
        ("tap", ["B+HOME"]),
        ("hold", ["HOME", "500"]),
        ("down", ["HOME"]),
        ("up", ["HOME"]),
    ],
)
def test_press_rejects_home_without_sending(paths, clock, action, args):
    daemon = FakeDaemon(clock)
    result = handle_press(paths, action, args, send=daemon, clock=clock)
    assert daemon.sent == []
    assert result == CommandResult(exit_code=1, stderr_lines=(HOME_ERROR,))
    assert read_events(paths.events_file) == [
        CommandEvent(
            time=START_TIME,
            command="press",
            args=(action, *args),
            reply=HOME_ERROR,
            ok=False,
            elapsed_s=0.0,
            frame=None,
        )
    ]


@pytest.mark.parametrize(
    ("action", "args", "tokens"),
    [
        ("tap", ["A HOME"], ("A", "HOME")),
        ("down", ["B+HOME"], ("B+HOME",)),
        ("down", ["LS:0,1 B+HOME"], ("LS:0,1", "B+HOME")),
        ("hold", ["HOME 500"], ("HOME", "500")),
        ("up", ["LS HOME"], ("LS", "HOME")),
    ],
)
def test_press_rejects_home_hidden_behind_spaces(paths, clock, action, args, tokens):
    daemon = FakeDaemon(clock)
    result = handle_press(paths, action, args, send=daemon, clock=clock)
    assert daemon.sent == []
    assert result == CommandResult(exit_code=1, stderr_lines=(HOME_ERROR,))
    [event] = read_events(paths.events_file)
    assert event.args == (action, *tokens)
    assert event.reply == HOME_ERROR
    assert event.ok is False


def test_press_rejects_action_with_spaces(paths, clock):
    daemon = FakeDaemon(clock)
    result = handle_press(paths, "tap A", ["HOME"], send=daemon, clock=clock)
    assert daemon.sent == []
    assert result == CommandResult(
        exit_code=1,
        stderr_lines=("error press accepts tap / hold / down / up: tap A",),
    )


def test_press_rejects_other_actions_without_sending(paths, clock):
    daemon = FakeDaemon(clock)
    result = handle_press(paths, "shot", ["/tmp/x.png"], send=daemon, clock=clock)
    message = "error press accepts tap / hold / down / up: shot"
    assert daemon.sent == []
    assert result == CommandResult(exit_code=1, stderr_lines=(message,))
    [event] = read_events(paths.events_file)
    assert event.args == ("shot", "/tmp/x.png")
    assert event.reply == message
    assert event.ok is False


def test_press_daemon_error_fails(paths, clock):
    daemon = FakeDaemon(clock, {"tap": "error A is already held"})
    result = handle_press(paths, "tap", ["A"], send=daemon, clock=clock)
    assert result == CommandResult(
        exit_code=1, stdout_lines=("error A is already held",)
    )
    assert read_events(paths.events_file)[0].ok is False


# --- status ---


def test_status_sends_status(paths, clock):
    daemon = FakeDaemon(clock, {"status": "ok frames=10 held=none"})
    result = handle_status(paths, send=daemon, clock=clock)
    assert daemon.sent == [["status"]]
    assert result == CommandResult(
        exit_code=0, stdout_lines=("ok frames=10 held=none",)
    )
    assert read_events(paths.events_file) == [
        CommandEvent(
            time=START_TIME,
            command="status",
            args=(),
            reply="ok frames=10 held=none",
            ok=True,
            elapsed_s=0.5,
            frame=None,
        )
    ]


def test_status_error_fails(paths, clock):
    daemon = FakeDaemon(clock, {"status": "error cannot connect to daemon: x"})
    result = handle_status(paths, send=daemon, clock=clock)
    assert result.exit_code == 1
    assert result.stdout_lines == ("error cannot connect to daemon: x",)


# --- finish ---


def test_finish_releases_then_presses_home(paths, clock):
    daemon = FakeDaemon(clock)
    result = handle_finish(paths, send=daemon, clock=clock)
    assert daemon.sent == [["up", "ALL"], ["tap", "HOME"]]
    assert result == CommandResult(
        exit_code=0, stdout_lines=("ok held=none", "ok tap HOME")
    )
    assert read_events(paths.events_file) == [
        CommandEvent(START_TIME, "finish", ("up", "ALL"), "ok held=none", True, 0.5),
        CommandEvent(
            START_TIME + 0.5, "finish", ("tap", "HOME"), "ok tap HOME", True, 0.5
        ),
    ]


def test_finish_presses_home_even_if_release_fails(paths, clock):
    daemon = FakeDaemon(clock, {"up": "error bridge is recovering"})
    result = handle_finish(paths, send=daemon, clock=clock)
    assert daemon.sent == [["up", "ALL"], ["tap", "HOME"]]
    assert result == CommandResult(
        exit_code=1, stdout_lines=("error bridge is recovering", "ok tap HOME")
    )
    assert [event.ok for event in read_events(paths.events_file)] == [False, True]


def test_finish_fails_when_home_fails(paths, clock):
    daemon = FakeDaemon(clock, {"tap": "error bridge is recovering"})
    result = handle_finish(paths, send=daemon, clock=clock)
    assert result.exit_code == 1
    assert result.stdout_lines == ("ok held=none", "error bridge is recovering")


# --- report ---


def write_png(path: Path, value: int) -> None:
    assert cv2.imwrite(str(path), np.full((90, 160), value, dtype=np.uint8))


def prepare_report_records(paths: WorkspacePaths) -> None:
    write_png(paths.frames_dir / "000001.png", 0)
    write_png(paths.frames_dir / "000002.png", 0)
    write_png(paths.frames_dir / "000003.png", 255)
    for event in [
        CommandEvent(99.0, "run", ("start",), "launched claude launch=1", True, 0.0),
        CommandEvent(100.0, "look", (), "ok", True, 0.2, "frames/000001.png"),
        CommandEvent(102.0, "press", ("tap", "A"), "ok tap A", True, 0.4),
        CommandEvent(
            103.0,
            "run",
            ("resume", "s1"),
            "launched claude launch=2 resume=s1",
            True,
            0.0,
        ),
        CommandEvent(104.0, "look", (), "ok", True, 0.2, "frames/000002.png"),
        CommandEvent(107.0, "look", (), "ok", True, 0.2, "frames/000003.png"),
    ]:
        append_event(paths.events_file, event)
    paths.agent_log_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "system", "session_id": "s1"}),
                json.dumps({"type": "assistant", "message": {"id": "m1"}}),
                json.dumps({"type": "assistant", "message": {"id": "m1"}}),
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "s1",
                        "total_cost_usd": 0.5,
                        "num_turns": 3,
                        "duration_ms": 1000,
                    }
                ),
                json.dumps({"type": "assistant", "message": {"id": "m2"}}),
                json.dumps(
                    {
                        "type": "result",
                        "session_id": "s1",
                        "total_cost_usd": 0.75,
                        "num_turns": 2,
                        "duration_ms": 500,
                    }
                ),
                json.dumps({"type": "assistant", "message": {"id": "m3"}}),
                '{"type": "result", "sess',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_report_json(paths):
    prepare_report_records(paths)
    result = handle_report(paths, as_json=True)
    assert result.exit_code == 0
    assert len(result.stdout_lines) == 1
    assert json.loads(result.stdout_lines[0]) == {
        "duration_s": 8.0,
        "command_counts": {"run": 2, "look": 3, "press": 1},
        "error_count": 0,
        "look_interval": {"median_s": 3.5, "mean_s": 3.5, "max_s": 4.0},
        "static_pairs": 1,
        "look_pairs": 2,
        "longest_static_s": 4.0,
        "unchanged_after_press": 1,
        "pressed_pairs": 1,
        "agent": {
            "launches": 2,
            "results": 2,
            "total_cost_usd": 0.75,
            "num_turns": 5,
            "duration_ms": 1500,
            "responses": 3,
        },
    }


def test_report_text(paths):
    prepare_report_records(paths)
    result = handle_report(paths, as_json=False)
    assert result.exit_code == 0
    assert result.stdout_lines == (
        "記録の時間: 8.0 秒",
        "コマンドの回数: run=2 look=3 press=1",
        "error の数: 0",
        "look の間隔: 中央値 3.5 秒 / 平均 3.5 秒 / 最大 4.0 秒",
        "変化なしの look の組: 1 / 2",
        "変化なしが続いた最長の時間: 4.0 秒",
        "press の後も変化なしの組: 1 / 1",
        "エージェント: 起動 2 回 / 応答 3 回 / result 2 回",
        "result を出した起動の分: 費用 0.7500 USD / ターン 5 / 時間 1.5 秒",
    )


def test_report_reads_agent_log_with_a_line_cut_in_a_character(paths):
    # 日本語の文字の途中 (3 バイトの文字の 2 バイト目まで) で切れた行の後に、正しい result の行がある
    broken = '{"type": "assistant", "message": {"text": "日本'.encode()[:-1]
    valid = json.dumps(
        {
            "type": "result",
            "session_id": "s1",
            "total_cost_usd": 0.25,
            "num_turns": 4,
            "duration_ms": 2000,
        }
    ).encode()
    paths.agent_log_file.write_bytes(broken + b"\n" + valid + b"\n")
    result = handle_report(paths, as_json=True)
    assert result.exit_code == 0
    agent = json.loads(result.stdout_lines[0])["agent"]
    assert agent == {
        "launches": 0,
        "results": 1,
        "total_cost_usd": 0.25,
        "num_turns": 4,
        "duration_ms": 2000,
        "responses": 0,
    }


def test_report_keeps_the_cost_before_a_crashed_result(paths):
    # 同じセッションで 0.4 の後にクラッシュで 0.0 の result が来ても、費用は 0.4 のまま
    paths.agent_log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {"type": "result", "session_id": "s1", "total_cost_usd": 0.4}
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "session_id": "s1",
                        "total_cost_usd": 0.0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = handle_report(paths, as_json=True)
    agent = json.loads(result.stdout_lines[0])["agent"]
    assert agent["results"] == 2
    assert agent["total_cost_usd"] == pytest.approx(0.4)


def test_report_of_a_new_workspace(paths):
    result = handle_report(paths, as_json=False)
    assert result.exit_code == 0
    assert result.stdout_lines[0] == "記録の時間: 0.0 秒"
    assert result.stdout_lines[-1] == "エージェント: 記録なし"
    assert len(result.stdout_lines) == 8


# --- run ---


def make_run_parts(clock: FakeClock, plans: list[ProcessPlan]):
    return FakeProcessStarter(clock, plans), FakeDaemon(clock), FakeSleep(clock)


def test_run_launches_and_reports_the_outcome(paths, clock):
    write_harness_json(paths, [])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={"PATH": "/usr/bin"},
        available=FAKE_FEATURES,
    )
    assert result == CommandResult(
        exit_code=0,
        stdout_lines=(
            f"ok run finished reason=deadline launches=1 log={paths.agent_log_file}",
        ),
    )
    [launch] = starter.launches
    assert launch.args == build_claude_args(
        first_prompt("ゲーム A"),
        BASE_AGENT_COMMANDS,
        paths.instructions_file,
        LaunchOptions(),
    )
    assert daemon.sent == [["up", "ALL"]]
    events = read_events(paths.events_file)
    assert [event.command for event in events] == ["run", "run", "run"]
    assert [event.args[0] for event in events] == ["start", "stop", "release"]


def test_run_allows_commands_of_enabled_features(paths, clock):
    write_harness_json(paths, ["memo-files"])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    args = starter.launches[0].args
    assert "Bash(uv run switch2-harness note *)" in args
    assert "Bash(uv run switch2-harness notes *)" in args
    assert "Bash(uv run switch2-harness history *)" not in args


def test_run_reports_budget_exhausted(paths, clock):
    write_harness_json(paths, [])
    plan = ProcessPlan(
        runs_for_s=10.0,
        output_lines=(
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_max_budget_usd",
                    "session_id": "s1",
                }
            ),
        ),
    )
    starter, daemon, sleep = make_run_parts(clock, [plan])
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(max_budget_usd=1.0),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    expected = (
        f"ok run finished reason=budget_exhausted launches=1 log={paths.agent_log_file}"
    )
    assert result == CommandResult(exit_code=0, stdout_lines=(expected,))
    assert len(starter.launches) == 1
    assert daemon.sent == [["up", "ALL"]]


def test_run_reports_an_unconfirmed_release(paths, clock):
    write_harness_json(paths, [])
    starter = FakeProcessStarter(clock, [ProcessPlan(runs_for_s=None)])
    daemon = FakeDaemon(clock, {"up": "error bridge is recovering"})
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=FakeSleep(clock),
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    finished = f"ok run finished reason=deadline launches=1 log={paths.agent_log_file}"
    assert result == CommandResult(
        exit_code=1,
        stdout_lines=(finished,),
        stderr_lines=(
            "error up ALL after the run was not confirmed: error bridge is recovering",
        ),
    )


def test_release_not_confirmed_message():
    assert (
        release_not_confirmed_message("error no reply from daemon")
        == "error up ALL after the run was not confirmed: error no reply from daemon"
    )


@pytest.mark.parametrize("minutes", [0.0, -5.0])
def test_run_rejects_non_positive_minutes(paths, clock, minutes):
    write_harness_json(paths, [])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    result = handle_run(
        paths,
        minutes,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 1
    assert result.stdout_lines == ()
    assert len(result.stderr_lines) == 1
    assert result.stderr_lines[0].startswith("error --minutes must be greater than 0: ")
    assert starter.launches == []
    assert daemon.sent == []


def test_run_rejects_unknown_feature_in_harness_json(paths, clock):
    write_harness_json(paths, ["zoom"])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 1
    assert result.stderr_lines[0].startswith("error unknown feature: zoom")
    assert starter.launches == []
    assert daemon.sent == []


@pytest.mark.parametrize(
    ("budget", "shown"),
    [(0.0, "0.0"), (-1.0, "-1.0"), (math.nan, "nan"), (math.inf, "inf")],
)
def test_run_rejects_invalid_max_budget_usd(paths, clock, budget, shown):
    write_harness_json(paths, [])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(max_budget_usd=budget),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result == CommandResult(
        exit_code=1,
        stderr_lines=(
            f"error --max-budget-usd must be a positive finite number: {shown}",
        ),
    )
    assert starter.launches == []
    assert daemon.sent == []


def test_run_terminated_by_signal_reports_an_error_after_cleanup(paths, clock):
    write_harness_json(paths, [])
    starter, daemon, _ = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        # 3 回目の待ちの間に SIGTERM を受けたことにする
        sleep_calls.append(seconds)
        if len(sleep_calls) == 3:
            raise RunTerminated(signal.SIGTERM)
        clock.advance(seconds)

    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result == CommandResult(
        exit_code=1, stderr_lines=("error run terminated by signal",)
    )
    assert starter.processes[0].calls == [("signal", signal.SIGINT), ("wait", 20.0)]
    assert daemon.sent == [["up", "ALL"]]
    assert [event.args[0] for event in read_events(paths.events_file)] == [
        "start",
        "release",
    ]


def test_run_interrupted_by_ctrl_c_reports_an_error_after_cleanup(paths, clock):
    write_harness_json(paths, [])
    starter, daemon, _ = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        # 3 回目の待ちの間に Ctrl-C を受けたことにする
        sleep_calls.append(seconds)
        if len(sleep_calls) == 3:
            raise KeyboardInterrupt
        clock.advance(seconds)

    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        monotonic=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result == CommandResult(exit_code=1, stderr_lines=("error run interrupted",))
    assert RUN_INTERRUPTED_MESSAGE == "error run interrupted"
    assert starter.processes[0].calls == [("signal", signal.SIGINT), ("wait", 20.0)]
    assert daemon.sent == [["up", "ALL"]]
    assert [event.args[0] for event in read_events(paths.events_file)] == [
        "start",
        "release",
    ]


def test_run_uses_the_monotonic_clock_for_the_deadline(paths):
    write_harness_json(paths, [])
    wall = FakeClock(START_TIME)
    mono = FakeClock(50.0)
    starter = FakeProcessStarter(mono, [ProcessPlan(runs_for_s=None)])
    sleep = FakeSleep(mono)
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=wall,
        monotonic=mono,
        sleep=sleep,
        send=FakeDaemon(mono),
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 0
    assert len(sleep.calls) == 60
    assert {event.time for event in read_events(paths.events_file)} == {START_TIME}
