"""codex_critic のテストと、CodexCritic で動かした reflect_once のテスト (本物の codex は起動しない)。"""

import json
import signal
from pathlib import Path

from harness_fakes import (
    FakeClock,
    FakeCriticStarter,
    ProcessPlan,
    codex_message_line,
    codex_thread_line,
    codex_turn_completed_line,
    make_paths,
)

from switch2_harness.codex_command import build_codex_critic_args, build_codex_env
from switch2_harness.codex_critic import (
    ATTACHED_IMAGES_NOTE,
    CodexCritic,
    codex_critic_prompt,
)
from switch2_harness.event_log import CommandEvent, append_event, read_events
from switch2_harness.reflection import (
    CriticFrame,
    build_critic_content,
    critic_input_line,
)
from switch2_harness.reflection_critic import (
    CLAUDE_CRITIC,
    CriticRequest,
    CriticSetting,
    build_critic_args,
    reflect_once,
)

CODEX_HOME = Path("/home/player/.cache/switch2-harness/codex-home/play-abc")
RUN_START = 1000.0
FIRST_TICK = RUN_START + 300.0
NOTES = "# メモ\n\n## 振り返り\n"
CRITIC_TEMPLATES = {"reflection-critic.md": "批評役です ($game / $goal)\n"}
BASE_ENV = {"PATH": "/usr/bin", "OPENAI_API_KEY": "secret"}

FRAMES = [
    CriticFrame(file_name="000002.png", offset_s=0.0, png_base64="AAA"),
    CriticFrame(file_name="000003.png", offset_s=75.9, png_base64="BBB"),
]


def make_request(frames=FRAMES) -> CriticRequest:
    return CriticRequest(
        instructions="批評役です\n",
        frames=frames,
        frame_paths=[Path("/w/frames") / frame.file_name for frame in frames],
        action_lines=["- 0:12 press tap A: ok tap A"],
        statements=["右へ進みます"],
        notes_text=NOTES,
    )


def test_codex_critic_prompt_lists_the_attached_frames():
    assert codex_critic_prompt(make_request()) == (
        "批評役です\n"
        "\n"
        "## 直近 5 分の撮影 (古い順)\n"
        "\n"
        "撮影 1: 000002.png (0:00)\n"
        "撮影 2: 000003.png (1:15)\n"
        "\n"
        f"{ATTACHED_IMAGES_NOTE}\n"
        "\n"
        "## 直近 5 分の行動 (時刻は 5 分の始まりからの経過)\n"
        "\n"
        "- 0:12 press tap A: ok tap A\n"
        "\n"
        "## プレイヤーの発言 (古い順)\n"
        "\n"
        "右へ進みます\n"
        "\n"
        "## notes.md\n"
        "\n"
        "# メモ\n"
        "\n"
        "## 振り返り\n"
    )


def test_codex_critic_prompt_without_frames():
    prompt = codex_critic_prompt(make_request(frames=[]))
    assert "## 直近 5 分の撮影 (古い順)\n\n(なし)\n\n## 直近 5 分の行動" in prompt
    assert ATTACHED_IMAGES_NOTE not in prompt


def test_codex_critic_prompt_shares_the_records_text_with_the_claude_critic():
    # 行動・発言・notes.md の節は、Claude Code の批評役の入力と同じ文
    claude_records = build_critic_content(
        "批評役です\n",
        FRAMES,
        ["- 0:12 press tap A: ok tap A"],
        ["右へ進みます"],
        NOTES,
    )[-1]["text"]
    assert codex_critic_prompt(make_request()).endswith(claude_records + "\n")


def test_codex_critic_build_command():
    critic = CodexCritic(CODEX_HOME)
    command = critic.build_command(make_request())
    assert command.args == build_codex_critic_args(
        [Path("/w/frames/000002.png"), Path("/w/frames/000003.png")]
    )
    assert command.input_text == codex_critic_prompt(make_request())
    assert critic.build_env(BASE_ENV, Path("/w")) == build_codex_env(
        BASE_ENV, CODEX_HOME, Path("/w")
    )


def test_claude_critic_build_command_is_the_stream_json_input():
    command = CLAUDE_CRITIC.build_command(make_request())
    assert command.args == build_critic_args()
    assert command.input_text == critic_input_line(
        build_critic_content(
            "批評役です\n",
            FRAMES,
            ["- 0:12 press tap A: ok tap A"],
            ["右へ進みます"],
            NOTES,
        )
    )


def test_codex_critic_reads_statements_and_the_critique():
    critic = CodexCritic(CODEX_HOME)
    agent_lines = [codex_thread_line("t1"), codex_message_line(" 右へ "), "not json"]
    assert critic.statements(agent_lines) == ["右へ"]
    output = [
        codex_thread_line("c1"),
        '{"type": "turn.started"}',
        codex_message_line("- 進んでいない\n"),
        codex_turn_completed_line(10),
    ]
    assert critic.critique(output) == "- 進んでいない"
    assert critic.critique(output[:-1]) is None


# --- reflect_once を CodexCritic で動かす ---


def prepare(tmp_path: Path):
    paths = make_paths(tmp_path / "play")
    for number in (2, 3):
        (paths.frames_dir / f"{number:06d}.png").write_bytes(b"png")
    for event in [
        CommandEvent(1000.0, "look", (), "ok", True, 0.2, "frames/000002.png"),
        CommandEvent(1012.0, "press", ("tap", "A"), "ok tap A", True, 0.4),
        CommandEvent(1075.9, "look", (), "ok", True, 0.2, "frames/000003.png"),
    ]:
        append_event(paths.events_file, event)
    paths.agent_log_file.write_text(
        "\n".join([codex_thread_line("t1"), codex_message_line("右へ進みます"), ""]),
        encoding="utf-8",
    )
    (paths.root / "notes.md").write_text(NOTES, encoding="utf-8")
    return paths


def make_setting(paths, starter, clock) -> CriticSetting:
    return CriticSetting(
        paths=paths,
        game="ゲーム A",
        goal="ボスを倒す",
        base_env=BASE_ENV,
        start_critic=starter,
        clock=clock,
        read_template=CRITIC_TEMPLATES.__getitem__,
        critic=CodexCritic(CODEX_HOME),
    )


def advancing_wait(clock: FakeClock):
    """reflection_critic の wait の fake。時計を進め、止める合図はしない。"""

    def wait(seconds: float) -> bool:
        clock.advance(seconds)
        return False

    return wait


def critic_plan(lines) -> ProcessPlan:
    return ProcessPlan(
        runs_for_s=7.0, stops_on=frozenset({signal.SIGTERM}), output_lines=tuple(lines)
    )


def test_reflect_once_with_the_codex_critic_writes_the_critique(tmp_path):
    paths = prepare(tmp_path)
    clock = FakeClock(FIRST_TICK)
    output = [
        codex_thread_line("c1"),
        '{"type": "turn.started"}',
        codex_message_line("- 同じ場所を回っています"),
        codex_turn_completed_line(10),
    ]
    starter = FakeCriticStarter(clock, [critic_plan(output)])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, 0, advancing_wait(clock)
    )
    [launch] = starter.launches
    assert launch.args == build_codex_critic_args(
        [paths.frames_dir / "000002.png", paths.frames_dir / "000003.png"]
    )
    assert launch.env == build_codex_env(BASE_ENV, CODEX_HOME, paths.root)
    assert "OPENAI_API_KEY" not in launch.env
    assert launch.input_text.startswith("批評役です (ゲーム A / ボスを倒す)\n\n")
    assert "撮影 1: 000002.png (0:00)\n撮影 2: 000003.png (1:15)" in launch.input_text
    # プレイヤーの発言は、Codex の agent.jsonl の agent_message から取る
    assert "## プレイヤーの発言 (古い順)\n\n右へ進みます\n" in launch.input_text
    assert (paths.root / "notes.md").read_text(encoding="utf-8") == (
        "# メモ\n\n## 振り返り\n\n(run の開始から 5 分の時点の批評)\n\n- 同じ場所を回っています\n"
    )
    assert read_events(paths.events_file)[-1].command == "reflection"
    row = json.loads(
        (paths.records_dir / "reflections" / "reflections.jsonl").read_text("utf-8")
    )
    assert row["status"] == "written"
    assert row["frames"] == ["frames/000002.png", "frames/000003.png"]


def test_reflect_once_with_a_failed_codex_critic_keeps_the_notes(tmp_path):
    paths = prepare(tmp_path)
    clock = FakeClock(FIRST_TICK)
    output = [
        codex_thread_line("c1"),
        '{"type": "turn.started"}',
        '{"type": "turn.failed", "error": {"message": "x"}}',
    ]
    starter = FakeCriticStarter(clock, [critic_plan(output)])
    reflect_once(
        make_setting(paths, starter, clock), 1, RUN_START, 0, advancing_wait(clock)
    )
    assert (paths.root / "notes.md").read_text(encoding="utf-8") == NOTES
    row = json.loads(
        (paths.records_dir / "reflections" / "reflections.jsonl").read_text("utf-8")
    )
    assert row["status"] == "failed"
    assert row["critique"] is None
