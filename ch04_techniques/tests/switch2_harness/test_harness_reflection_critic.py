import base64
import json
import signal
import threading
from collections.abc import Sequence
from typing import Any

import pytest
from harness_fakes import (
    FakeAgentProcess,
    FakeClock,
    FakeCriticStarter,
    ProcessPlan,
    make_paths,
)

from switch2_harness.agent_launcher import CLAUDE_SETTINGS_JSON, build_claude_env
from switch2_harness.event_log import CommandEvent, append_event, read_events
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.reflection import REFLECTION_INTERVAL_S
from switch2_harness.reflection_critic import (
    CRITIC_EFFORT,
    CRITIC_MODEL,
    CRITIC_STDERR_FILE_NAME,
    CRITIC_TIMEOUT_S,
    POLL_INTERVAL_S,
    REFLECTIONS_DIR_NAME,
    REFLECTIONS_INDEX_FILE_NAME,
    STOP_GRACE_S,
    STOP_JOIN_TIMEOUT_S,
    CriticSetting,
    StartCritic,
    build_critic_args,
    critic_output_file_name,
    next_critic_index,
    reflect_once,
    reflecting_with_critic,
    reflections_dir,
    run_reflections,
    stop_critic,
    write_notes,
)
from switch2_harness.workspace import WorkspacePaths

RUN_START = 1000.0

# 1 回目の批評役を起動する時刻 (run の開始から 5 分)。1 回目の区間は RUN_START からこの時刻まで
FIRST_TICK = RUN_START + 300.0

GAME = "ゲーム A"
GOAL = "ボスを倒す"

# 批評役への指示のテンプレートの代わり
CRITIC_TEMPLATES = {
    "reflection-critic.md": "あなたは批評役です。\n\n- ゲーム名: $game\n- 目標: $goal\n",
}
CRITIC_INSTRUCTIONS = (
    "あなたは批評役です。\n\n- ゲーム名: ゲーム A\n- 目標: ボスを倒す\n"
)

# 起動した人の環境変数の代わり。CLAUDECODE は親のセッションの変数で、批評役には渡さない
BASE_ENV = {"PATH": "/usr/bin", "HOME": "/home/player", "CLAUDECODE": "1"}

NOTES = "# メモ\n\n## 操作方法\n\n- A で決定\n\n## 振り返り\n"

HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)
ALERT_LINE = "停滞の知らせ: 新しい画面が 135 秒出ていません"

INIT_LINE = json.dumps({"type": "system", "subtype": "init", "session_id": "c1"})


def read_critic_template(file_name: str) -> str:
    """read_template の fake。CRITIC_TEMPLATES から本文を返す。"""
    return CRITIC_TEMPLATES[file_name]


def assistant_line(text: str) -> str:
    """プレイ中のエージェントの、text の発言 1 つの stream-json の行を返す。"""
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        ensure_ascii=False,
    )


# 前の回までに読んだ発言の行 (agent_log_offset より前にある)
OLD_AGENT_LINE = assistant_line("前の回の発言")

# 今回の回に読む agent.jsonl の行
AGENT_LINES = [
    json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": " 右へ進みます\n"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "uv run switch2-harness press tap RIGHT"},
                    },
                ]
            },
        },
        ensure_ascii=False,
    ),
    json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok tap"}
                ]
            },
        }
    ),
    assistant_line("扉が開きました。中に入ります"),
]


def success_lines(critique: str) -> tuple[str, ...]:
    """critique を result に入れた、成功した批評役の stream-json の行を返す。"""
    result = {"type": "result", "subtype": "success", "is_error": False}
    return (INIT_LINE, json.dumps({**result, "result": critique}, ensure_ascii=False))


def critic_plan(
    output_lines: Sequence[str], runs_for_s: float | None = 11.0
) -> ProcessPlan:
    """批評役の fake のふるまい。起動から runs_for_s 秒で自分で終わり (None なら終わらない)、SIGTERM でも止まる。"""
    return ProcessPlan(
        runs_for_s=runs_for_s,
        stops_on=frozenset({signal.SIGTERM}),
        output_lines=tuple(output_lines),
    )


class FakeStopWait:
    """reflection_critic の wait の fake。

    受け取った秒数を waits に控えて時計をその秒数進め、進めた後の時計が stop_at 以上なら True (止める合図) を返す。
    stop_at が None なら合図はしない。時計は戻らないので、一度 True を返した後は True を返し続ける
    (本番の threading.Event.wait と同じ)。呼ばれた回数が max_calls を超えたら AssertionError にし、
    ループの誤りでテストが止まらなくなるのを防ぐ。
    """

    def __init__(
        self, clock: FakeClock, stop_at: float | None = None, max_calls: int = 10_000
    ) -> None:
        self.clock = clock
        self.stop_at = stop_at
        self.max_calls = max_calls
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if len(self.waits) > self.max_calls:
            raise AssertionError("wait was called too many times")
        self.clock.advance(seconds)
        return self.stop_at is not None and self.clock.now >= self.stop_at


def make_setting(
    paths: WorkspacePaths, start_critic: StartCritic, clock: FakeClock
) -> CriticSetting:
    return CriticSetting(
        paths=paths,
        game=GAME,
        goal=GOAL,
        base_env=BASE_ENV,
        start_critic=start_critic,
        clock=clock,
        read_template=read_critic_template,
    )


def png_bytes(number: int) -> bytes:
    """番号 number の撮影のファイルの中身の代わり (base64 にして渡すだけなので、PNG でなくてよい)。"""
    return f"png {number}".encode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def write_frame(paths: WorkspacePaths, number: int) -> str:
    """frames/ に番号 number の撮影のファイルを書き、その frame (例: "frames/000001.png") を返す。"""
    frame = f"frames/{number:06d}.png"
    (paths.root / frame).write_bytes(png_bytes(number))
    return frame


def look_event(time: float, frame: str) -> CommandEvent:
    return CommandEvent(time, "look", (), "ok", True, 0.2, frame)


def write_notes_file(paths: WorkspacePaths, text: str = NOTES) -> None:
    (paths.root / "notes.md").write_text(text, encoding="utf-8")


def read_notes_file(paths: WorkspacePaths) -> str:
    return (paths.root / "notes.md").read_text(encoding="utf-8")


def prepare_workspace(paths: WorkspacePaths) -> int:
    """1 回目の区間 (1000〜1300 秒) の前後と中の記録・撮影、agent.jsonl、notes.md を用意する。

    前の回までに読んだ発言の行の終わりのバイト位置 (reflect_once に渡す agent_log_offset) を返す。
    """
    for number in range(1, 6):
        write_frame(paths, number)
    for event in [
        look_event(990.0, "frames/000001.png"),
        look_event(1000.0, "frames/000002.png"),
        CommandEvent(1012.0, "press", ("tap", "A"), "ok tap A", True, 0.4),
        CommandEvent(1015.0, "look", (), "error no frame yet", False, 0.2),
        look_event(1075.9, "frames/000003.png"),
        CommandEvent(
            1075.9,
            "stagnation-alert",
            ("135",),
            ALERT_LINE,
            True,
            0.0,
            "frames/000003.png",
        ),
        CommandEvent(1080.0, "press", ("tap", "HOME"), HOME_ERROR, False, 0.0),
        CommandEvent(1090.0, "status", (), "ok frames=10 held=none", True, 0.3),
        look_event(1300.0, "frames/000004.png"),
        look_event(1300.5, "frames/000005.png"),
    ]:
        append_event(paths.events_file, event)
    old = OLD_AGENT_LINE + "\n"
    paths.agent_log_file.write_text(
        old + "".join(line + "\n" for line in AGENT_LINES), encoding="utf-8"
    )
    write_notes_file(paths)
    return len(old.encode("utf-8"))


def image_block(data: str) -> dict[str, object]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


# prepare_workspace の記録で、1 回目の批評役に渡すユーザーのメッセージの content
EXPECTED_CONTENT = [
    {"type": "text", "text": CRITIC_INSTRUCTIONS},
    {"type": "text", "text": "## 直近 5 分の撮影 (古い順)"},
    {"type": "text", "text": "撮影 1: 000002.png (0:00)"},
    image_block(b64(png_bytes(2))),
    {"type": "text", "text": "撮影 2: 000003.png (1:15)"},
    image_block(b64(png_bytes(3))),
    {"type": "text", "text": "撮影 3: 000004.png (5:00)"},
    image_block(b64(png_bytes(4))),
    {
        "type": "text",
        "text": (
            "## 直近 5 分の行動 (時刻は 5 分の始まりからの経過)\n"
            "\n"
            "- 0:00 look 000002.png\n"
            "- 0:12 press tap A: ok tap A\n"
            "- 0:15 look: error no frame yet\n"
            "- 1:15 look 000003.png\n"
            f"- 1:20 press tap HOME: {HOME_ERROR}\n"
            "- 5:00 look 000004.png\n"
            "\n"
            "## プレイヤーの発言 (古い順)\n"
            "\n"
            "右へ進みます\n"
            "\n"
            "扉が開きました。中に入ります\n"
            "\n"
            "## notes.md\n"
            "\n"
            "# メモ\n"
            "\n"
            "## 操作方法\n"
            "\n"
            "- A で決定\n"
            "\n"
            "## 振り返り"
        ),
    },
]


def critic_content(input_text: str) -> list[dict[str, Any]]:
    """批評役の標準入力の 1 行から、ユーザーのメッセージの content を取り出す。"""
    return json.loads(input_text)["message"]["content"]


def image_data(input_text: str) -> list[str]:
    """批評役の標準入力の 1 行から、画像の base64 の文字列を順に取り出す。"""
    return [
        block["source"]["data"]
        for block in critic_content(input_text)
        if block["type"] == "image"
    ]


def read_reflection_rows(paths: WorkspacePaths) -> list[dict[str, Any]]:
    index_file = paths.records_dir / "reflections" / "reflections.jsonl"
    return [json.loads(line) for line in index_file.read_text("utf-8").splitlines()]


def test_constants():
    assert CRITIC_MODEL == "claude-opus-5-5"
    assert CRITIC_EFFORT == "medium"
    assert CRITIC_TIMEOUT_S == 240.0
    # 批評役は、次の回の起動の時刻より前に止める
    assert CRITIC_TIMEOUT_S < REFLECTION_INTERVAL_S
    assert POLL_INTERVAL_S == 1.0
    assert STOP_GRACE_S == 5.0
    assert STOP_JOIN_TIMEOUT_S == 15.0
    assert REFLECTIONS_DIR_NAME == "reflections"
    assert REFLECTIONS_INDEX_FILE_NAME == "reflections.jsonl"
    assert CRITIC_STDERR_FILE_NAME == "critic-stderr.log"


def test_reflections_dir(tmp_path):
    paths = make_paths(tmp_path / "play")
    assert reflections_dir(paths) == paths.records_dir / "reflections"


# --- build_critic_args ---


def test_build_critic_args():
    assert build_critic_args() == [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--disallowedTools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--restricted",
        "--settings",
        CLAUDE_SETTINGS_JSON,
        "--no-session-persistence",
        "--model",
        "claude-opus-5-5",
        "--effort",
        "medium",
    ]


# --- critic_output_file_name / next_critic_index ---


@pytest.mark.parametrize(
    ("index", "name"),
    [(1, "critic-001.jsonl"), (12, "critic-012.jsonl"), (1234, "critic-1234.jsonl")],
)
def test_critic_output_file_name(index, name):
    assert critic_output_file_name(index) == name


def test_next_critic_index_without_the_directory(tmp_path):
    assert next_critic_index(tmp_path / "reflections") == 1


def test_next_critic_index_ignores_other_files(tmp_path):
    for name in [
        "reflections.jsonl",
        "critic-stderr.log",
        "critic-abc.jsonl",
        "critic-001.json",
    ]:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert next_critic_index(tmp_path) == 1


def test_next_critic_index_uses_the_number_after_the_largest(tmp_path):
    for name in ["critic-001.jsonl", "critic-007.jsonl", "critic-stderr.log"]:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert next_critic_index(tmp_path) == 8


# --- write_notes ---


def test_write_notes_replaces_the_notes(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# 古いメモ\n", encoding="utf-8")
    write_notes(notes, "# メモ\n\n## 振り返り\n\n- 進んでいない\n")
    assert (
        notes.read_text(encoding="utf-8") == "# メモ\n\n## 振り返り\n\n- 進んでいない\n"
    )
    # 一時ファイル (.tmp) は置き換えで無くなり、notes.md だけが残る
    assert sorted(path.name for path in tmp_path.iterdir()) == ["notes.md"]


# --- stop_critic ---


def test_stop_critic_kills_a_critic_that_ignores_sigterm():
    clock = FakeClock(FIRST_TICK)
    process = FakeAgentProcess(
        clock, ProcessPlan(runs_for_s=None, stops_on=frozenset())
    )
    stop_critic(process, FakeStopWait(clock))
    signals = [call[1] for call in process.calls if call[0] == "signal"]
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.poll() is not None
    # SIGKILL は、SIGTERM から STOP_GRACE_S 秒待っても終わらなかったときに送る
    assert clock.now - FIRST_TICK >= STOP_GRACE_S


# --- reflect_once ---


def test_reflect_once_writes_the_critique_to_the_notes(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    events_before = read_events(paths.events_file)
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(
        clock, [critic_plan(success_lines("- 同じ場所を回っています\n"))]
    )
    setting = make_setting(paths, starter, clock)
    agent_log_to = reflect_once(setting, 1, RUN_START, offset, FakeStopWait(clock))
    size = paths.agent_log_file.stat().st_size
    assert agent_log_to == size
    assert read_notes_file(paths) == (
        "# メモ\n"
        "\n"
        "## 操作方法\n"
        "\n"
        "- A で決定\n"
        "\n"
        "## 振り返り\n"
        "\n"
        "(run の開始から 5 分の時点の批評)\n"
        "\n"
        "- 同じ場所を回っています\n"
    )
    events = read_events(paths.events_file)
    assert events[:-1] == events_before
    reflection = events[-1]
    assert (reflection.command, reflection.args, reflection.reply) == (
        "reflection",
        ("1",),
        "ok notes.md",
    )
    assert reflection.ok is True
    assert reflection.frame is None
    # 批評役が終わった (起動から 11 秒たった) 後に書き、elapsed_s は起動から書き終えるまでの秒数
    assert reflection.time >= FIRST_TICK + 11.0
    assert reflection.elapsed_s == pytest.approx(reflection.time - FIRST_TICK)
    [row] = read_reflection_rows(paths)
    elapsed_s = row.pop("elapsed_s")
    assert row == {
        "index": 1,
        "tick": 1,
        "time": FIRST_TICK,
        "window_start": RUN_START,
        "frames": ["frames/000002.png", "frames/000003.png", "frames/000004.png"],
        "agent_log_from": offset,
        "agent_log_to": size,
        "status": "written",
        "critique": "- 同じ場所を回っています",
    }
    assert elapsed_s >= 11.0


def test_reflect_once_gives_the_critic_the_recent_records(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, offset, FakeStopWait(clock)
    )
    [launch] = starter.launches
    assert launch.args == build_critic_args()
    assert launch.cwd == paths.root
    # プレイ中のエージェントと同じく、CLAUDE.md を読ませず、親のセッションの変数を渡さない
    assert launch.env == build_claude_env(BASE_ENV, paths.root)
    assert launch.env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert "CLAUDECODE" not in launch.env
    directory = paths.records_dir / "reflections"
    assert launch.stdout_path == directory / "critic-001.jsonl"
    assert launch.stderr_path == directory / "critic-stderr.log"
    # 標準入力には、末尾に改行のある 1 行の JSON を書く
    assert launch.input_text.endswith("\n")
    assert launch.input_text.count("\n") == 1
    # 区間の中の撮影 (成功した look)・行動 (look と press)・前の回より後の発言・notes.md を渡す
    assert json.loads(launch.input_text) == {
        "type": "user",
        "message": {"role": "user", "content": EXPECTED_CONTENT},
    }


def test_reflect_once_gives_at_most_eight_evenly_spaced_frames(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    # 区間 (1000〜1300 秒) の前と後の撮影と、区間の中の 20 秒ごとの 15 枚 (1000〜1280 秒、番号 2〜16)
    looks = [
        (990.0, 1),
        *[(RUN_START + 20.0 * i, i + 2) for i in range(15)],
        (1300.5, 17),
    ]
    for time, number in looks:
        append_event(paths.events_file, look_event(time, write_frame(paths, number)))
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, 0, FakeStopWait(clock)
    )
    # 等間隔の目標 1000 + 40k 秒 (k = 0〜7) にちょうど撮影がある (番号 2, 4, ..., 16)
    assert image_data(starter.launches[0].input_text) == [
        b64(png_bytes(2 + 2 * k)) for k in range(8)
    ]


def test_reflect_once_skips_frames_whose_files_are_missing(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    (paths.frames_dir / "000003.png").unlink()
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, offset, FakeStopWait(clock)
    )
    assert image_data(starter.launches[0].input_text) == [
        b64(png_bytes(2)),
        b64(png_bytes(4)),
    ]


def test_reflect_once_reads_the_agent_log_up_to_its_size_at_the_start(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    size_at_start = paths.agent_log_file.stat().st_size

    def append_agent_line(launch: int) -> None:
        # 批評役が動いている間に、エージェントが発言を書き足す
        with paths.agent_log_file.open("a", encoding="utf-8") as log:
            log.write(assistant_line("批評の間の発言") + "\n")

    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(
        clock,
        [critic_plan(success_lines("- 批評"))],
        on_start=append_agent_line,
    )
    setting = make_setting(paths, starter, clock)
    agent_log_to = reflect_once(setting, 1, RUN_START, offset, FakeStopWait(clock))
    # 書き足された行は次の回に回す
    assert agent_log_to == size_at_start
    statements = critic_content(starter.launches[0].input_text)[-1]["text"]
    assert "右へ進みます" in statements
    assert "前の回の発言" not in statements
    assert "批評の間の発言" not in statements
    [row] = read_reflection_rows(paths)
    assert (row["agent_log_from"], row["agent_log_to"]) == (offset, size_at_start)


def test_reflect_once_keeps_notes_written_while_the_critic_runs(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)

    def edit_notes(launch: int) -> None:
        # 批評役が動いている間に、エージェントが notes.md の別の節を書き換える
        text = read_notes_file(paths).replace(
            "- A で決定\n", "- A で決定\n- B で戻る\n"
        )
        write_notes_file(paths, text)

    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(
        clock,
        [critic_plan(success_lines("- 同じ場所を回っています"))],
        on_start=edit_notes,
    )
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, offset, FakeStopWait(clock)
    )
    assert read_notes_file(paths) == (
        "# メモ\n"
        "\n"
        "## 操作方法\n"
        "\n"
        "- A で決定\n"
        "- B で戻る\n"
        "\n"
        "## 振り返り\n"
        "\n"
        "(run の開始から 5 分の時点の批評)\n"
        "\n"
        "- 同じ場所を回っています\n"
    )


def test_reflect_once_numbers_after_the_critics_of_earlier_runs(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    directory = paths.records_dir / "reflections"
    directory.mkdir()
    (directory / "critic-001.jsonl").write_text("", encoding="utf-8")
    (directory / "critic-002.jsonl").write_text("", encoding="utf-8")
    earlier_row = {"tick": 2, "time": 600.0, "status": "skipped"}
    (directory / "reflections.jsonl").write_text(
        json.dumps(earlier_row) + "\n", encoding="utf-8"
    )
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, offset, FakeStopWait(clock)
    )
    assert starter.launches[0].stdout_path == directory / "critic-003.jsonl"
    assert read_events(paths.events_file)[-1].args == ("3",)
    rows = read_reflection_rows(paths)
    # 前の行に続けて追記する
    assert rows[0] == earlier_row
    assert (rows[1]["index"], rows[1]["tick"], rows[1]["status"]) == (3, 1, "written")


def test_reflect_once_records_a_failed_critique_without_writing(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    events_before = paths.events_file.read_bytes()
    error_result = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
    }
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(
        clock, [critic_plan([INIT_LINE, json.dumps(error_result)])]
    )
    setting = make_setting(paths, starter, clock)
    agent_log_to = reflect_once(setting, 1, RUN_START, offset, FakeStopWait(clock))
    assert agent_log_to == paths.agent_log_file.stat().st_size
    assert read_notes_file(paths) == NOTES
    assert paths.events_file.read_bytes() == events_before
    [row] = read_reflection_rows(paths)
    assert (row["index"], row["tick"], row["status"]) == (1, 1, "failed")
    assert row["critique"] is None


def test_reflect_once_stops_a_critic_that_runs_too_long(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    events_before = paths.events_file.read_bytes()
    clock = FakeClock(FIRST_TICK)
    # 批評を出力しても、自分では終わらない
    starter = FakeCriticStarter(
        clock, [critic_plan(success_lines("- 書かれない批評"), runs_for_s=None)]
    )
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, offset, FakeStopWait(clock)
    )
    [process] = starter.processes
    assert process.calls[0] == ("signal", signal.SIGTERM)
    assert ("signal", signal.SIGKILL) not in process.calls
    assert process.poll() is not None
    assert clock.now - FIRST_TICK >= CRITIC_TIMEOUT_S
    # 時間切れの回は批評を取り出さない
    assert read_notes_file(paths) == NOTES
    assert paths.events_file.read_bytes() == events_before
    [row] = read_reflection_rows(paths)
    assert (row["tick"], row["status"], row["critique"]) == (1, "timeout", None)
    assert row["elapsed_s"] >= CRITIC_TIMEOUT_S


def test_reflect_once_stops_the_critic_on_the_stop_signal(tmp_path):
    paths = make_paths(tmp_path / "play")
    offset = prepare_workspace(paths)
    events_before = paths.events_file.read_bytes()
    clock = FakeClock(FIRST_TICK)
    starter = FakeCriticStarter(
        clock, [critic_plan(success_lines("- 書かれない批評"), runs_for_s=11.0)]
    )
    # 批評役が動いている間 (起動から 5 秒) に、止める合図をする
    wait = FakeStopWait(clock, stop_at=FIRST_TICK + 5.0)
    reflect_once(make_setting(paths, starter, clock), 1, RUN_START, offset, wait)
    [process] = starter.processes
    assert process.calls[0] == ("signal", signal.SIGTERM)
    assert process.poll() is not None
    assert clock.now < FIRST_TICK + 11.0
    # 止めた回は批評を取り出さない
    assert read_notes_file(paths) == NOTES
    assert paths.events_file.read_bytes() == events_before
    [row] = read_reflection_rows(paths)
    assert (row["tick"], row["status"], row["critique"]) == (1, "stopped", None)


# --- run_reflections ---


def test_run_reflections_launches_the_critic_every_five_minutes(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    # 前の run の発言
    paths.agent_log_file.write_text(OLD_AGENT_LINE + "\n", encoding="utf-8")
    size_at_run_start = paths.agent_log_file.stat().st_size

    def agent_speaks(launch: int) -> None:
        # 批評役が動いている間に、エージェントが発言する
        with paths.agent_log_file.open("a", encoding="utf-8") as log:
            log.write(assistant_line(f"{launch} 回目の批評の間の発言") + "\n")

    clock = FakeClock(RUN_START)
    starter = FakeCriticStarter(
        clock,
        [
            critic_plan(success_lines("- 1 回目の批評")),
            critic_plan(success_lines("- 2 回目の批評")),
        ],
        on_start=agent_speaks,
    )
    # 11 分たったら止める合図をする
    wait = FakeStopWait(clock, stop_at=RUN_START + 660.0)
    run_reflections(make_setting(paths, starter, clock), wait)
    assert len(starter.launches) == 2
    rows = read_reflection_rows(paths)
    assert [(row["index"], row["tick"], row["status"]) for row in rows] == [
        (1, 1, "written"),
        (2, 2, "written"),
    ]
    # run の開始から 5 分と 10 分の時点で起動する
    assert RUN_START + 300.0 <= rows[0]["time"] < RUN_START + 300.0 + POLL_INTERVAL_S
    assert RUN_START + 600.0 <= rows[1]["time"] < RUN_START + 600.0 + POLL_INTERVAL_S
    # 発言は、run の開始の時点の agent.jsonl の後から読み、前の回の続きから読む
    assert rows[0]["agent_log_from"] == size_at_run_start
    assert rows[1]["agent_log_from"] == rows[0]["agent_log_to"]
    assert "前の回の発言" not in starter.launches[0].input_text
    assert "1 回目の批評の間の発言" in starter.launches[1].input_text
    # 2 回目の批評役には、1 回目 (5 分) の批評を書いた notes.md を渡し、2 回目 (10 分) の批評に置き換える
    assert "(run の開始から 5 分の時点の批評)" in starter.launches[1].input_text
    assert read_notes_file(paths) == (
        "# メモ\n"
        "\n"
        "## 操作方法\n"
        "\n"
        "- A で決定\n"
        "\n"
        "## 振り返り\n"
        "\n"
        "(run の開始から 10 分の時点の批評)\n"
        "\n"
        "- 2 回目の批評\n"
    )
    reflections = [
        event
        for event in read_events(paths.events_file)
        if event.command == "reflection"
    ]
    assert [event.args for event in reflections] == [("1",), ("2",)]


def test_run_reflections_skips_a_tick_that_passed_while_the_critic_ran(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    clock = FakeClock(RUN_START)

    def overrun(launch: int) -> None:
        # 1 回目の批評役が動いている間に、2 回目の起動の時刻 (10 分) を過ぎる
        if launch == 1:
            clock.advance(350.0)

    starter = FakeCriticStarter(
        clock,
        [
            critic_plan(success_lines("- 1 回目の批評"), runs_for_s=None),
            critic_plan(success_lines("- 3 回目の批評")),
        ],
        on_start=overrun,
    )
    # 16 分たったら止める合図をする
    wait = FakeStopWait(clock, stop_at=RUN_START + 960.0)
    run_reflections(make_setting(paths, starter, clock), wait)
    assert len(starter.launches) == 2
    rows = read_reflection_rows(paths)
    assert [row["status"] for row in rows] == ["timeout", "skipped", "written"]
    assert rows[1] == {"tick": 2, "time": RUN_START + 600.0, "status": "skipped"}
    assert rows[2]["tick"] == 3
    assert RUN_START + 900.0 <= rows[2]["time"] < RUN_START + 900.0 + POLL_INTERVAL_S
    assert read_notes_file(paths).endswith(
        "## 振り返り\n\n(run の開始から 15 分の時点の批評)\n\n- 3 回目の批評\n"
    )


def test_run_reflections_ends_on_the_stop_signal_before_the_first_tick(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    clock = FakeClock(RUN_START)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    # 1 分たったら止める合図をする
    wait = FakeStopWait(clock, stop_at=RUN_START + 60.0)
    run_reflections(make_setting(paths, starter, clock), wait)
    assert starter.launches == []
    assert read_notes_file(paths) == NOTES
    assert not (paths.records_dir / "reflections" / "reflections.jsonl").exists()


# --- reflecting_with_critic ---

CONFIG = HarnessConfig(
    game=GAME,
    goal=GOAL,
    features=("notes", "reflection"),
    created_at="2026-11-16T21:00:05+09:00",
)


def new_threads(before: set[threading.Thread]) -> set[threading.Thread]:
    """before に無い、動いているスレッドを返す。"""
    return set(threading.enumerate()) - before


def test_reflecting_with_critic_runs_in_a_thread_until_exit(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    # 進まない時計なので、5 分たたず、批評役は起動しない
    clock = FakeClock(RUN_START)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    before = set(threading.enumerate())
    with reflecting_with_critic(
        paths,
        CONFIG,
        clock=clock,
        base_env=BASE_ENV,
        start_critic=starter,
        read_template=read_critic_template,
    ):
        started = new_threads(before)
        assert started
        assert all(thread.daemon for thread in started)
    # with を抜けると、裏のスレッドは止まっている
    assert new_threads(before) == set()
    assert starter.launches == []


class ErrorInRun(Exception):
    """with の中 (run の途中) で起きた例外の代わり。"""


def test_reflecting_with_critic_stops_the_thread_when_an_exception_leaves(tmp_path):
    paths = make_paths(tmp_path / "play")
    write_notes_file(paths)
    clock = FakeClock(RUN_START)
    starter = FakeCriticStarter(clock, [critic_plan(success_lines("- 批評"))])
    reflecting = reflecting_with_critic(
        paths,
        CONFIG,
        clock=clock,
        base_env=BASE_ENV,
        start_critic=starter,
        read_template=read_critic_template,
    )
    before = set(threading.enumerate())
    # with の中の例外は、そのまま外に出る
    with pytest.raises(ErrorInRun), reflecting:
        raise ErrorInRun
    assert new_threads(before) == set()
    assert starter.launches == []
