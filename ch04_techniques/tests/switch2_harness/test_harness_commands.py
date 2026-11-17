import json
import math
import os
import signal
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from harness_fakes import (
    FAKE_FEATURES,
    FAKE_REFLECTION,
    FAKE_STAGNATION_ALERT,
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
from switch2_harness.features import (
    BASE_AGENT_COMMANDS,
    REFLECTION_FEATURE,
    STAGNATION_ALERT_FEATURE,
    Feature,
)
from switch2_harness.harness_config import HarnessConfig
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


def test_init_writes_initial_files_of_enabled_features_unsubstituted(tmp_path):
    directory = tmp_path / "play"
    result = handle_init(
        directory,
        "G",
        "X",
        ["memo-files"],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 0
    memo = directory / "memo.md"
    assert memo.is_file()
    assert memo.read_text(encoding="utf-8") == "# メモ $goal\n"


def test_init_without_features_creates_only_the_base_files(tmp_path):
    directory = tmp_path / "play"
    handle_init(
        directory,
        "G",
        "X",
        [],
        now=fixed_now,
        read_template=read_fake_template,
        available=FAKE_FEATURES,
    )
    assert sorted(os.listdir(directory)) == [
        "CLAUDE.md",
        "frames",
        "harness.json",
        "records",
    ]


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


# 併用する要素 (requires) と、ファイルの末尾に足す内容 (file_appends) のある要素を含む、テスト用の要素
FEATURES_WITH_REFLECTION = (*FAKE_FEATURES, FAKE_REFLECTION)


def test_init_appends_to_the_initial_file_of_the_required_feature(tmp_path):
    directory = tmp_path / "play"
    result = handle_init(
        directory,
        "G",
        "X",
        [REFLECTION_FEATURE, "memo-files"],
        now=fixed_now,
        read_template=read_fake_template,
        available=FEATURES_WITH_REFLECTION,
    )
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {directory} features=memo-files,reflection",)
    )
    # memo.md の最初の内容 "# メモ $goal\n" の後に、空行を 1 つ挟んで "## 振り返り $goal\n" を足す (置換はしない)
    assert (directory / "memo.md").read_text(encoding="utf-8") == (
        "# メモ $goal\n\n## 振り返り $goal\n"
    )


def test_init_rejects_a_feature_without_its_required_feature(tmp_path):
    directory = tmp_path / "play"
    result = handle_init(
        directory,
        "G",
        "X",
        [REFLECTION_FEATURE],
        now=fixed_now,
        read_template=read_fake_template,
        available=FEATURES_WITH_REFLECTION,
    )
    assert result == CommandResult(
        exit_code=1, stderr_lines=("error feature reflection requires memo-files",)
    )
    assert not directory.exists()


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


# --- look の行動履歴 ---

HISTORY_HEADER = "直近の行動 (古い順、最大 20 件。秒数はこの撮影の何秒前か):"


class RecordingFramesChanged:
    """frames_changed の fake。呼ばれた (前, 後) の組を calls に記録し、changed を返す。"""

    def __init__(self, changed: bool) -> None:
        self.changed = changed
        self.calls: list[tuple[str, str]] = []

    def __call__(self, before: str, after: str) -> bool:
        self.calls.append((before, after))
        return self.changed


def record_earlier_actions(paths: WorkspacePaths) -> None:
    """20 秒前の look (frames/000001.png) と 10 秒前の press を events.jsonl に記録する。

    次の look が frames/000002.png に保存するよう、frames/000001.png の場所も埋めておく。
    """
    (paths.frames_dir / "000001.png").write_bytes(b"")
    append_event(
        paths.events_file,
        CommandEvent(START_TIME - 20, "look", (), "ok", True, 0.2, "frames/000001.png"),
    )
    append_event(
        paths.events_file,
        CommandEvent(START_TIME - 10, "press", ("tap", "A"), "ok tap A", True, 0.4),
    )


def test_look_with_frames_changed_prints_the_history_after_the_reply(paths, clock):
    record_earlier_actions(paths)
    frames_changed = RecordingFramesChanged(changed=True)
    result = handle_look(
        paths, send=FakeDaemon(clock), clock=clock, frames_changed=frames_changed
    )
    frame_path = paths.frames_dir / "000002.png"
    assert result == CommandResult(
        exit_code=0,
        stdout_lines=(
            f"ok {frame_path}",
            HISTORY_HEADER,
            "- 20 秒前 look 000001.png",
            "- 10 秒前 press tap A: 画面の変化 あり",
        ),
    )
    assert frames_changed.calls == [("frames/000001.png", "frames/000002.png")]


def test_look_with_frames_changed_prints_no_actions_in_a_new_workspace(paths, clock):
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        frames_changed=RecordingFramesChanged(changed=True),
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {frame_path}", "直近の行動: なし")
    )


def test_failed_look_with_frames_changed_prints_only_the_reply(paths, clock):
    record_earlier_actions(paths)
    frames_changed = RecordingFramesChanged(changed=True)
    daemon = FakeDaemon(clock, {"shot": "error no frame yet"})
    result = handle_look(paths, send=daemon, clock=clock, frames_changed=frames_changed)
    assert result == CommandResult(exit_code=1, stdout_lines=("error no frame yet",))
    assert frames_changed.calls == []


def test_look_without_frames_changed_prints_only_the_reply(paths, clock):
    record_earlier_actions(paths)
    result = handle_look(paths, send=FakeDaemon(clock), clock=clock)
    frame_path = paths.frames_dir / "000002.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))


# --- look の停滞の知らせ ---

ALERT_LINE_135 = "停滞の知らせ: 新しい画面が 135 秒出ていません"


class RecordingLastNewScreenTime:
    """last_new_screen_time の fake。呼ばれた回数を calls に数え、value を返す。"""

    def __init__(self, value: float | None) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float | None:
        self.calls += 1
        return self.value


def test_look_prints_no_alert_without_a_last_new_screen(paths, clock):
    last_new_screen_time = RecordingLastNewScreenTime(None)
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        last_new_screen_time=last_new_screen_time,
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))
    assert [event.command for event in read_events(paths.events_file)] == ["look"]
    assert last_new_screen_time.calls == 1


def test_look_prints_and_records_the_alert_after_120_seconds(paths, clock):
    # look のイベントの time (送信の前の START_TIME) の 135.7 秒前。
    # 送信の後の時計 (START_TIME + 0.5) で計ると 136 秒になる
    last_new_screen_time = RecordingLastNewScreenTime(START_TIME - 135.7)
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        last_new_screen_time=last_new_screen_time,
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {frame_path}", ALERT_LINE_135)
    )
    assert read_events(paths.events_file) == [
        CommandEvent(
            time=START_TIME,
            command="look",
            args=(),
            reply=f"ok {frame_path}",
            ok=True,
            elapsed_s=0.5,
            frame="frames/000001.png",
        ),
        CommandEvent(
            time=START_TIME,
            command="stagnation-alert",
            args=("135",),
            reply=ALERT_LINE_135,
            ok=True,
            elapsed_s=0.0,
            frame="frames/000001.png",
        ),
    ]
    assert last_new_screen_time.calls == 1


def test_look_prints_no_alert_before_120_seconds(paths, clock):
    # look のイベントの time の 119.5 秒前。送信の後の時計で計ると 120 秒になる
    last_new_screen_time = RecordingLastNewScreenTime(START_TIME - 119.5)
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        last_new_screen_time=last_new_screen_time,
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))
    assert [event.command for event in read_events(paths.events_file)] == ["look"]


def test_failed_look_prints_no_alert_and_does_not_read_the_last_new_screen(
    paths, clock
):
    last_new_screen_time = RecordingLastNewScreenTime(START_TIME - 500.0)
    daemon = FakeDaemon(clock, {"shot": "error no frame yet"})
    result = handle_look(
        paths, send=daemon, clock=clock, last_new_screen_time=last_new_screen_time
    )
    assert result == CommandResult(exit_code=1, stdout_lines=("error no frame yet",))
    assert [event.command for event in read_events(paths.events_file)] == ["look"]
    assert last_new_screen_time.calls == 0


def test_look_prints_the_alert_before_the_history(paths, clock):
    record_earlier_actions(paths)
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        frames_changed=RecordingFramesChanged(changed=True),
        last_new_screen_time=RecordingLastNewScreenTime(START_TIME - 135.7),
    )
    frame_path = paths.frames_dir / "000002.png"
    assert result == CommandResult(
        exit_code=0,
        stdout_lines=(
            f"ok {frame_path}",
            ALERT_LINE_135,
            HISTORY_HEADER,
            "- 20 秒前 look 000001.png",
            "- 10 秒前 press tap A: 画面の変化 あり",
        ),
    )


# --- look の振り返りの知らせ ---

REFLECTION_NOTICE_LINE = "振り返り: notes.md の「振り返り」の節を書き換えました"


def record_reflection(paths: WorkspacePaths, time: float) -> CommandEvent:
    """批評を notes.md に書いたことの記録 (command が reflection の行) を events.jsonl に足し、足した行を返す。"""
    event = CommandEvent(time, "reflection", ("1",), "ok notes.md", True, 11.0)
    append_event(paths.events_file, event)
    return event


def notice_event(frame: str) -> CommandEvent:
    """START_TIME の look が知らせを出したときに記録する行。"""
    return CommandEvent(
        time=START_TIME,
        command="reflection-notice",
        args=(),
        reply=REFLECTION_NOTICE_LINE,
        ok=True,
        elapsed_s=0.0,
        frame=frame,
    )


def test_look_prints_and_records_the_reflection_notice_once(paths, clock):
    reflection = record_reflection(paths, START_TIME - 30.0)
    result = handle_look(
        paths, send=FakeDaemon(clock), clock=clock, reflection_notice=True
    )
    first_frame = paths.frames_dir / "000001.png"
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {first_frame}", REFLECTION_NOTICE_LINE)
    )
    assert read_events(paths.events_file) == [
        reflection,
        CommandEvent(
            time=START_TIME,
            command="look",
            args=(),
            reply=f"ok {first_frame}",
            ok=True,
            elapsed_s=0.5,
            frame="frames/000001.png",
        ),
        notice_event("frames/000001.png"),
    ]
    # 次の look は、新しい批評が書かれるまで知らせを出さない
    result = handle_look(
        paths, send=FakeDaemon(clock), clock=clock, reflection_notice=True
    )
    second_frame = paths.frames_dir / "000002.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {second_frame}",))
    assert [event.command for event in read_events(paths.events_file)] == [
        "reflection",
        "look",
        "reflection-notice",
        "look",
    ]


def test_look_prints_no_reflection_notice_without_a_reflection(paths, clock):
    result = handle_look(
        paths, send=FakeDaemon(clock), clock=clock, reflection_notice=True
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))
    assert [event.command for event in read_events(paths.events_file)] == ["look"]


def test_failed_look_leaves_the_reflection_notice_to_the_next_look(paths, clock):
    record_reflection(paths, START_TIME - 30.0)
    failing = FakeDaemon(clock, {"shot": "error no frame yet"})
    result = handle_look(paths, send=failing, clock=clock, reflection_notice=True)
    assert result == CommandResult(exit_code=1, stdout_lines=("error no frame yet",))
    assert [event.command for event in read_events(paths.events_file)] == [
        "reflection",
        "look",
    ]
    result = handle_look(
        paths, send=FakeDaemon(clock), clock=clock, reflection_notice=True
    )
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(
        exit_code=0, stdout_lines=(f"ok {frame_path}", REFLECTION_NOTICE_LINE)
    )
    events = read_events(paths.events_file)
    assert [event.command for event in events] == [
        "reflection",
        "look",
        "look",
        "reflection-notice",
    ]
    assert events[-1].frame == "frames/000001.png"
    assert events[-1].time == events[-2].time


def test_look_prints_the_reflection_notice_after_the_alert_and_before_the_history(
    paths, clock
):
    record_earlier_actions(paths)
    record_reflection(paths, START_TIME - 5.0)
    result = handle_look(
        paths,
        send=FakeDaemon(clock),
        clock=clock,
        frames_changed=RecordingFramesChanged(changed=True),
        last_new_screen_time=RecordingLastNewScreenTime(START_TIME - 135.7),
        reflection_notice=True,
    )
    frame_path = paths.frames_dir / "000002.png"
    assert result == CommandResult(
        exit_code=0,
        stdout_lines=(
            f"ok {frame_path}",
            ALERT_LINE_135,
            REFLECTION_NOTICE_LINE,
            HISTORY_HEADER,
            "- 20 秒前 look 000001.png",
            "- 10 秒前 press tap A: 画面の変化 あり",
        ),
    )
    # 知らせの行は、look の行と停滞の知らせの行の後に記録する
    events = read_events(paths.events_file)
    assert [event.command for event in events[-3:]] == [
        "look",
        "stagnation-alert",
        "reflection-notice",
    ]
    assert events[-1] == notice_event("frames/000002.png")


def test_look_without_reflection_notice_ignores_reflections(paths, clock):
    record_reflection(paths, START_TIME - 30.0)
    result = handle_look(paths, send=FakeDaemon(clock), clock=clock)
    frame_path = paths.frames_dir / "000001.png"
    assert result == CommandResult(exit_code=0, stdout_lines=(f"ok {frame_path}",))
    assert [event.command for event in read_events(paths.events_file)] == [
        "reflection",
        "look",
    ]


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
        deadline_clock=clock,
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
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    args = starter.launches[0].args
    assert "Bash(uv run switch2-harness note *)" in args
    assert "Bash(uv run switch2-harness notes *)" in args
    assert "Bash(uv run switch2-harness history *)" not in args


def test_run_allows_writing_files_of_enabled_features(paths, clock):
    write_harness_json(paths, ["memo-files"])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FAKE_FEATURES,
    )
    args = starter.launches[0].args
    assert args[args.index("--tools") + 1] == "Bash,Read,Write,Edit"
    assert "Edit(./memo.md)" in args


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
        deadline_clock=clock,
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
        deadline_clock=clock,
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
        deadline_clock=clock,
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
        deadline_clock=clock,
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
        deadline_clock=clock,
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
        deadline_clock=clock,
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
        deadline_clock=clock,
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


def test_run_uses_the_deadline_clock_for_the_deadline(paths):
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
        deadline_clock=mono,
        sleep=sleep,
        send=FakeDaemon(mono),
        base_env={},
        available=FAKE_FEATURES,
    )
    assert result.exit_code == 0
    assert len(sleep.calls) == 60
    assert {event.time for event in read_events(paths.events_file)} == {START_TIME}


# --- run の画面の記録 (停滞の知らせ) ---

# 停滞の知らせの要素を含む、run のテスト用の要素
FEATURES_WITH_STAGNATION_ALERT = (*FAKE_FEATURES, FAKE_STAGNATION_ALERT)


class FakeScreenRecording:
    """screen_recording の fake。呼ばれた paths と、記録の開始 (enter)・終了 (exit) の順を記録する。

    開始のときに起動済みのプロセスの数を launches_at_enter に、終了のときにデーモンに送った引数を sent_at_exit に控え、
    記録が最初の起動の前に始まり、後片付けの up ALL の後に止まることを確かめられるようにする。
    with の中で起きた例外は握りつぶさず、そのまま外に出す。
    """

    def __init__(self, starter: FakeProcessStarter, daemon: FakeDaemon) -> None:
        self.starter = starter
        self.daemon = daemon
        self.calls: list[WorkspacePaths] = []
        self.order: list[str] = []
        self.launches_at_enter: int | None = None
        self.sent_at_exit: list[list[str]] | None = None

    def __call__(self, paths: WorkspacePaths) -> AbstractContextManager[object]:
        self.calls.append(paths)
        return self._recording()

    @contextmanager
    def _recording(self) -> Iterator[None]:
        self.order.append("enter")
        self.launches_at_enter = len(self.starter.launches)
        try:
            yield
        finally:
            self.order.append("exit")
            self.sent_at_exit = [list(args) for args in self.daemon.sent]


def test_run_with_stagnation_alert_records_screens_around_the_agent(paths, clock):
    write_harness_json(paths, [STAGNATION_ALERT_FEATURE])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    recording = FakeScreenRecording(starter, daemon)
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FEATURES_WITH_STAGNATION_ALERT,
        screen_recording=recording,
    )
    assert result.exit_code == 0
    assert recording.calls == [paths]
    assert recording.order == ["enter", "exit"]
    # 記録は最初の起動の前に始まり、後片付けの up ALL を送った後に止まる
    assert recording.launches_at_enter == 0
    assert len(starter.launches) == 1
    assert recording.sent_at_exit == [["up", "ALL"]]


def test_run_without_stagnation_alert_does_not_record_screens(paths, clock):
    write_harness_json(paths, [])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    recording = FakeScreenRecording(starter, daemon)
    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FEATURES_WITH_STAGNATION_ALERT,
        screen_recording=recording,
    )
    assert result.exit_code == 0
    assert recording.calls == []


@pytest.mark.parametrize(
    ("interruption", "message"),
    [
        (RunTerminated(signal.SIGTERM), "error run terminated by signal"),
        (KeyboardInterrupt(), "error run interrupted"),
    ],
    ids=["sigterm", "ctrl-c"],
)
def test_run_stops_recording_screens_when_interrupted(
    paths, clock, interruption, message
):
    write_harness_json(paths, [STAGNATION_ALERT_FEATURE])
    starter, daemon, _ = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    recording = FakeScreenRecording(starter, daemon)
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        # 3 回目の待ちの間に SIGTERM か Ctrl-C を受けたことにする
        sleep_calls.append(seconds)
        if len(sleep_calls) == 3:
            raise interruption
        clock.advance(seconds)

    result = handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=FEATURES_WITH_STAGNATION_ALERT,
        screen_recording=recording,
    )
    assert result == CommandResult(exit_code=1, stderr_lines=(message,))
    assert recording.order == ["enter", "exit"]
    assert recording.sent_at_exit == [["up", "ALL"]]


# --- run の振り返り ---


class FakeReflecting:
    """reflecting の fake。呼ばれた (paths, config) と、振り返りの開始 (enter)・終了 (exit) の順を記録する。

    開始のときに起動済みのプロセスの数を launches_at_enter に、終了のときにデーモンに送った引数を sent_at_exit に控え、
    振り返りが最初の起動の前に始まり、後片付けの up ALL の後に止まることを確かめられるようにする。
    recording を渡すと、開始と終了のときの画面の記録の順 (FakeScreenRecording.order) も控え、
    振り返りが画面の記録の内側で動くことを確かめられるようにする。
    with の中で起きた例外は握りつぶさず、そのまま外に出す。
    """

    def __init__(
        self,
        starter: FakeProcessStarter,
        daemon: FakeDaemon,
        recording: FakeScreenRecording | None = None,
    ) -> None:
        self.starter = starter
        self.daemon = daemon
        self.recording = recording
        self.calls: list[tuple[WorkspacePaths, HarnessConfig]] = []
        self.order: list[str] = []
        self.launches_at_enter: int | None = None
        self.sent_at_exit: list[list[str]] | None = None
        self.recording_order_at_enter: list[str] | None = None
        self.recording_order_at_exit: list[str] | None = None

    def __call__(
        self, paths: WorkspacePaths, config: HarnessConfig
    ) -> AbstractContextManager[object]:
        self.calls.append((paths, config))
        return self._reflecting()

    @contextmanager
    def _reflecting(self) -> Iterator[None]:
        self.order.append("enter")
        self.launches_at_enter = len(self.starter.launches)
        if self.recording is not None:
            self.recording_order_at_enter = list(self.recording.order)
        try:
            yield
        finally:
            self.order.append("exit")
            self.sent_at_exit = [list(args) for args in self.daemon.sent]
            if self.recording is not None:
                self.recording_order_at_exit = list(self.recording.order)


def run_with_fakes(
    paths: WorkspacePaths,
    clock: FakeClock,
    starter: FakeProcessStarter,
    daemon: FakeDaemon,
    sleep: Callable[[float], None],
    available: tuple[Feature, ...],
    reflecting: FakeReflecting,
    screen_recording: FakeScreenRecording | None = None,
) -> CommandResult:
    """1 分の run を、fake の部品と reflecting で動かす。"""
    return handle_run(
        paths,
        1.0,
        LaunchOptions(),
        start_process=starter,
        clock=clock,
        deadline_clock=clock,
        sleep=sleep,
        send=daemon,
        base_env={},
        available=available,
        screen_recording=screen_recording,
        reflecting=reflecting,
    )


def test_run_with_reflection_reflects_around_the_agent(paths, clock):
    write_harness_json(paths, ["memo-files", REFLECTION_FEATURE])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    reflecting = FakeReflecting(starter, daemon)
    result = run_with_fakes(
        paths, clock, starter, daemon, sleep, FEATURES_WITH_REFLECTION, reflecting
    )
    assert result.exit_code == 0
    config = HarnessConfig(
        game="ゲーム A",
        goal="ボスを倒す",
        features=("memo-files", "reflection"),
        created_at="2026-11-16T21:00:05+09:00",
    )
    assert reflecting.calls == [(paths, config)]
    assert reflecting.order == ["enter", "exit"]
    # 振り返りは最初の起動の前に始まり、後片付けの up ALL を送った後に止まる
    assert reflecting.launches_at_enter == 0
    assert len(starter.launches) == 1
    assert reflecting.sent_at_exit == [["up", "ALL"]]


def test_run_without_reflection_does_not_reflect(paths, clock):
    write_harness_json(paths, ["memo-files"])
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    reflecting = FakeReflecting(starter, daemon)
    result = run_with_fakes(
        paths, clock, starter, daemon, sleep, FEATURES_WITH_REFLECTION, reflecting
    )
    assert result.exit_code == 0
    assert reflecting.calls == []


@pytest.mark.parametrize(
    ("interruption", "message"),
    [
        (RunTerminated(signal.SIGTERM), "error run terminated by signal"),
        (KeyboardInterrupt(), "error run interrupted"),
    ],
    ids=["sigterm", "ctrl-c"],
)
def test_run_stops_reflecting_when_interrupted(paths, clock, interruption, message):
    write_harness_json(paths, ["memo-files", REFLECTION_FEATURE])
    starter, daemon, _ = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    reflecting = FakeReflecting(starter, daemon)
    sleep_calls: list[float] = []

    def sleep(seconds: float) -> None:
        # 3 回目の待ちの間に SIGTERM か Ctrl-C を受けたことにする
        sleep_calls.append(seconds)
        if len(sleep_calls) == 3:
            raise interruption
        clock.advance(seconds)

    result = run_with_fakes(
        paths, clock, starter, daemon, sleep, FEATURES_WITH_REFLECTION, reflecting
    )
    assert result == CommandResult(exit_code=1, stderr_lines=(message,))
    assert reflecting.order == ["enter", "exit"]
    assert reflecting.sent_at_exit == [["up", "ALL"]]


def test_run_with_stagnation_alert_and_reflection_enters_both(paths, clock):
    write_harness_json(
        paths, ["memo-files", STAGNATION_ALERT_FEATURE, REFLECTION_FEATURE]
    )
    starter, daemon, sleep = make_run_parts(clock, [ProcessPlan(runs_for_s=None)])
    recording = FakeScreenRecording(starter, daemon)
    reflecting = FakeReflecting(starter, daemon, recording)
    available = (*FAKE_FEATURES, FAKE_STAGNATION_ALERT, FAKE_REFLECTION)
    result = run_with_fakes(
        paths,
        clock,
        starter,
        daemon,
        sleep,
        available,
        reflecting,
        screen_recording=recording,
    )
    assert result.exit_code == 0
    assert recording.order == ["enter", "exit"]
    assert reflecting.order == ["enter", "exit"]
    # 振り返りは画面の記録の内側で動く (画面の記録が始まった後に始まり、止まる前に止まる)
    assert reflecting.recording_order_at_enter == ["enter"]
    assert reflecting.recording_order_at_exit == ["enter"]
    assert reflecting.launches_at_enter == 0
    assert reflecting.sent_at_exit == [["up", "ALL"]]
