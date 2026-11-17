import json

import pytest

from switch2_harness.event_log import CommandEvent
from switch2_harness.reflection import (
    ACTION_COMMANDS,
    CRITIC_INSTRUCTION_FILE,
    MAX_CRITIC_FRAMES,
    NOTICE_LINE,
    REFLECTION_COMMAND,
    REFLECTION_INTERVAL_S,
    REFLECTION_NOTICE_COMMAND,
    REFLECTION_SECTION_HEADING,
    CriticFrame,
    assistant_texts,
    build_critic_content,
    build_notice_event,
    build_reflection_event,
    critic_input_line,
    critique_from_result,
    format_action_lines,
    format_offset,
    next_tick_index,
    notice_pending,
    replace_reflection_section,
    section_body,
    select_frames,
    window_actions,
)

HOME_ERROR = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def make_event(
    time: float,
    command: str,
    args: tuple[str, ...] = (),
    reply: str = "ok",
    ok: bool = True,
    frame: str | None = None,
) -> CommandEvent:
    return CommandEvent(
        time=time,
        command=command,
        args=args,
        reply=reply,
        ok=ok,
        elapsed_s=0.5,
        frame=frame,
    )


def looks_at(times: list[float]) -> list[tuple[float, str]]:
    """時刻 times の撮影 (時刻, frame) を返す。frame は 1 から数えた番号のファイル (例: "frames/000001.png")。"""
    return [(time, f"frames/{i:06d}.png") for i, time in enumerate(times, start=1)]


def test_constants():
    assert REFLECTION_INTERVAL_S == 300.0
    assert MAX_CRITIC_FRAMES == 8
    assert REFLECTION_SECTION_HEADING == "## 振り返り"
    assert REFLECTION_COMMAND == "reflection"
    assert REFLECTION_NOTICE_COMMAND == "reflection-notice"
    assert NOTICE_LINE == "振り返り: notes.md の「振り返り」の節を書き換えました"
    assert CRITIC_INSTRUCTION_FILE == "reflection-critic.md"
    assert ACTION_COMMANDS == ("look", "press")


# --- format_offset ---


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0:00"), (75.9, "1:15"), (300, "5:00"), (-3, "0:00"), (605, "10:05")],
)
def test_format_offset(seconds, text):
    assert format_offset(seconds) == text


# --- window_actions ---


def test_window_actions_keeps_looks_and_presses_in_the_window_in_order():
    before = make_event(999.0, "look", frame="frames/000001.png")
    first_look = make_event(1000.0, "look", frame="frames/000002.png")
    press = make_event(1010.0, "press", ("tap", "A"), "ok tap A")
    status = make_event(1020.0, "status", reply="ok frames=10 held=none")
    failed_look = make_event(1030.0, "look", reply="error no frame yet", ok=False)
    rejected = make_event(1040.0, "press", ("tap", "HOME"), HOME_ERROR, ok=False)
    alert = make_event(1040.0, "stagnation-alert", ("135",), frame="frames/000002.png")
    reflection = make_event(1050.0, "reflection", ("1",), "ok notes.md")
    last_look = make_event(1300.0, "look", frame="frames/000003.png")
    after = make_event(1300.5, "look", frame="frames/000004.png")
    events = [
        before,
        first_look,
        press,
        status,
        failed_look,
        rejected,
        alert,
        reflection,
        last_look,
        after,
    ]
    # 区間の両端の時刻の行を含め、失敗した行も含める
    assert window_actions(events, 1000.0, 1300.0) == [
        first_look,
        press,
        failed_look,
        rejected,
        last_look,
    ]


def test_window_actions_without_events():
    assert window_actions([], 1000.0, 1300.0) == []


# --- select_frames ---


def test_select_frames_returns_every_look_up_to_max_count():
    looks = looks_at([0.0, 5.0, 7.0])
    assert select_frames(looks, max_count=4) == looks
    eight = looks_at([float(i) for i in range(8)])
    assert select_frames(eight) == eight


def test_select_frames_picks_evenly_spaced_looks():
    # 時刻 0, 10, ..., 90 の 10 枚から 4 枚なら、目標 0, 30, 60, 90 で 0, 30, 60, 90 の撮影
    looks = looks_at([float(t) for t in range(0, 100, 10)])
    assert select_frames(looks, max_count=4) == [
        (0.0, "frames/000001.png"),
        (30.0, "frames/000004.png"),
        (60.0, "frames/000007.png"),
        (90.0, "frames/000010.png"),
    ]


def test_select_frames_prefers_the_earlier_look_on_a_tie():
    # 時刻 0, 1, ..., 9 の 10 枚から 3 枚なら、目標は 0, 4.5, 9。4.5 は 4 と 5 から同じ差なので前の 4
    looks = looks_at([float(t) for t in range(10)])
    assert select_frames(looks, max_count=3) == [
        (0.0, "frames/000001.png"),
        (4.0, "frames/000005.png"),
        (9.0, "frames/000010.png"),
    ]


def test_select_frames_always_includes_the_first_and_the_last_look():
    times = [0.0, 3.0, 4.0, 11.0, 12.0, 30.0, 31.0, 47.0, 58.0, 59.0, 61.0, 62.5]
    looks = looks_at(times)
    selected = select_frames(looks, max_count=5)
    assert selected[0] == looks[0]
    assert selected[-1] == looks[-1]
    assert len(selected) <= 5
    assert selected == sorted(selected)


def test_select_frames_merges_looks_chosen_for_several_targets():
    # 0〜8 秒に 9 枚、100 秒に 1 枚。目標 0, 14.3, 28.6, 42.9, 57.1, 71.4, 85.7, 100 のうち、
    # 14.3〜42.9 は 8 秒の撮影、57.1〜100 は 100 秒の撮影が選ばれ、3 枚にまとまる
    looks = looks_at([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 100.0])
    assert select_frames(looks, max_count=8) == [
        (0.0, "frames/000001.png"),
        (8.0, "frames/000009.png"),
        (100.0, "frames/000010.png"),
    ]


def test_select_frames_without_looks():
    assert select_frames([]) == []


# --- format_action_lines ---


def test_format_action_lines():
    actions = [
        make_event(1012.0, "look", reply="ok", frame="frames/000123.png"),
        make_event(1015.0, "press", ("tap", "A"), "ok tap A"),
        make_event(
            1020.0, "press", ("tap", "HOME"), "error HOME is not allowed", False
        ),
        make_event(1075.9, "look", reply="error no frame yet", ok=False),
        make_event(1080.0, "press", ("hold", "A", "500"), "ok hold A 500"),
    ]
    assert format_action_lines(actions, 1000.0) == [
        "- 0:12 look 000123.png",
        "- 0:15 press tap A: ok tap A",
        "- 0:20 press tap HOME: error HOME is not allowed",
        "- 1:15 look: error no frame yet",
        "- 1:20 press hold A 500: ok hold A 500",
    ]


def test_format_action_lines_without_actions():
    assert format_action_lines([], 1000.0) == []


# --- assistant_texts ---


def assistant_line(*content: object) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": list(content)}},
        ensure_ascii=False,
    )


def test_assistant_texts_returns_stripped_texts_in_order():
    tool_use = {
        "type": "tool_use",
        "id": "t1",
        "name": "Bash",
        "input": {"command": "uv run switch2-harness press tap RIGHT"},
    }
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
        assistant_line({"type": "text", "text": " 右へ進みます\n"}, tool_use),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                    ]
                },
            }
        ),
        assistant_line(
            {"type": "text", "text": "扉が開きました"},
            {"type": "text", "text": "中に入ります"},
        ),
    ]
    assert assistant_texts(lines) == ["右へ進みます", "扉が開きました", "中に入ります"]


@pytest.mark.parametrize(
    "line",
    [
        "",
        '{"type": "assistant", "message": {"content": [{"type": "te',
        "[1, 2]",
        json.dumps(
            {"type": "user", "message": {"content": [{"type": "text", "text": "x"}]}}
        ),
        json.dumps({"type": "assistant", "message": {"content": "文字列"}}),
        json.dumps({"type": "assistant", "message": "文字列"}),
        assistant_line({"type": "text", "text": " \n "}),
        assistant_line({"type": "text", "text": 5}),
        assistant_line("text"),
    ],
    ids=[
        "empty",
        "broken-json",
        "not-an-object",
        "not-assistant",
        "content-is-not-a-list",
        "message-is-not-an-object",
        "blank-text",
        "text-is-not-a-string",
        "block-is-not-an-object",
    ],
)
def test_assistant_texts_skips_lines_of_other_shapes(line):
    valid = assistant_line({"type": "text", "text": "左へ進みます"})
    assert assistant_texts([line, valid]) == ["左へ進みます"]


# --- build_critic_content / critic_input_line ---

SHOTS_HEADER = "## 直近 5 分の撮影 (古い順)"
ACTIONS_HEADER = "## 直近 5 分の行動 (時刻は 5 分の始まりからの経過)"
STATEMENTS_HEADER = "## プレイヤーの発言 (古い順)"


def image_block(data: str) -> dict[str, object]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def test_build_critic_content():
    frames = [
        CriticFrame(file_name="000002.png", offset_s=0.0, png_base64="QUJD"),
        CriticFrame(file_name="000005.png", offset_s=75.9, png_base64="REVG"),
    ]
    content = build_critic_content(
        "批評の指示",
        frames,
        ["- 0:00 look 000002.png", "- 0:12 press tap A: ok tap A"],
        ["右へ進みます", "扉が開きました"],
        "# メモ\n\n## 振り返り\n",
    )
    assert content == [
        {"type": "text", "text": "批評の指示"},
        {"type": "text", "text": SHOTS_HEADER},
        {"type": "text", "text": "撮影 1: 000002.png (0:00)"},
        image_block("QUJD"),
        {"type": "text", "text": "撮影 2: 000005.png (1:15)"},
        image_block("REVG"),
        {
            "type": "text",
            "text": (
                f"{ACTIONS_HEADER}\n"
                "\n"
                "- 0:00 look 000002.png\n"
                "- 0:12 press tap A: ok tap A\n"
                "\n"
                f"{STATEMENTS_HEADER}\n"
                "\n"
                "右へ進みます\n"
                "\n"
                "扉が開きました\n"
                "\n"
                "## notes.md\n"
                "\n"
                "# メモ\n"
                "\n"
                "## 振り返り"
            ),
        },
    ]


def test_build_critic_content_without_frames_actions_statements_and_notes():
    content = build_critic_content("批評の指示", [], [], [], "  \n")
    assert content == [
        {"type": "text", "text": "批評の指示"},
        {"type": "text", "text": f"{SHOTS_HEADER}\n\n(なし)"},
        {
            "type": "text",
            "text": (
                f"{ACTIONS_HEADER}\n\n(なし)\n\n"
                f"{STATEMENTS_HEADER}\n\n(なし)\n\n"
                "## notes.md\n\n(空)"
            ),
        },
    ]


def test_critic_input_line():
    content: list[dict[str, object]] = [
        {"type": "text", "text": "批評の指示\n2 行目"},
        image_block("QUJD"),
    ]
    line = critic_input_line(content)
    message = {"type": "user", "message": {"role": "user", "content": content}}
    assert line == json.dumps(message, ensure_ascii=False) + "\n"
    # 1 行 (改行は末尾の 1 つだけ) で、日本語はそのまま書く
    assert line.count("\n") == 1
    assert "批評の指示" in line
    assert json.loads(line) == message


# --- critique_from_result ---


def result_line(**fields: object) -> str:
    return json.dumps({"type": "result", **fields}, ensure_ascii=False)


SUCCESS = result_line(subtype="success", is_error=False, result="\n- 進んでいない\n")


@pytest.mark.parametrize(
    ("lines", "critique"),
    [
        (
            [json.dumps({"type": "system", "subtype": "init"}), SUCCESS],
            "- 進んでいない",
        ),
        (
            [result_line(subtype="error_during_execution", is_error=True, result="")],
            None,
        ),
        ([json.dumps({"type": "system", "subtype": "init"})], None),
        ([], None),
        (
            [result_line(subtype="success", is_error=True, result="- 進んでいない")],
            None,
        ),
        ([result_line(subtype="success", is_error=False, result=" \n ")], None),
        ([result_line(subtype="success", is_error=False, result=5)], None),
        (['{"type": "result", "sub', "not json", "[1]", SUCCESS], "- 進んでいない"),
        (
            [SUCCESS, result_line(subtype="error_max_turns", is_error=True)],
            None,
        ),
        (
            [result_line(subtype="error_during_execution", is_error=True), SUCCESS],
            "- 進んでいない",
        ),
    ],
    ids=[
        "success",
        "error-result",
        "no-result",
        "no-lines",
        "is-error",
        "blank-result",
        "result-is-not-a-string",
        "skips-unreadable-lines",
        "last-result-is-an-error",
        "last-result-is-a-success",
    ],
)
def test_critique_from_result(lines, critique):
    assert critique_from_result(lines) == critique


# --- section_body ---


def test_section_body():
    assert section_body(10, "- 進んでいない\n") == (
        "(run の開始から 10 分の時点の批評)\n\n- 進んでいない"
    )


def test_section_body_strips_the_critique():
    assert section_body(5, "\n  - 同じ場所\n- 次は北へ  \n") == (
        "(run の開始から 5 分の時点の批評)\n\n- 同じ場所\n- 次は北へ"
    )


# --- replace_reflection_section ---

BODY = "(run の開始から 10 分の時点の批評)\n\n- 進んでいない"


@pytest.mark.parametrize(
    ("notes", "body", "expected"),
    [
        (
            "# メモ\n\n## 操作方法\n\n- A\n\n## 振り返り\n",
            "前",
            "# メモ\n\n## 操作方法\n\n- A\n\n## 振り返り\n\n前\n",
        ),
        (
            "# メモ\n\n## 振り返り\n\n(run の開始から 5 分の時点の批評)\n\n- 古い批評\n",
            BODY,
            f"# メモ\n\n## 振り返り\n\n{BODY}\n",
        ),
        (
            "# メモ\n\n## 振り返り\n\n- 古い批評\n\n## 今の状況と次にやること\n\n- 北へ\n",
            BODY,
            f"# メモ\n\n## 振り返り\n\n{BODY}\n\n## 今の状況と次にやること\n\n- 北へ\n",
        ),
        (
            "## 振り返り\n- 古い批評\n## 今の状況と次にやること\n",
            "新",
            "## 振り返り\n\n新\n\n## 今の状況と次にやること\n",
        ),
        (
            "## 振り返り\n\n- 古い批評\n\n# 別のメモ\n",
            "新",
            "## 振り返り\n\n新\n\n# 別のメモ\n",
        ),
        (
            "# メモ\n\n## 振り返り  \n\n- 古い批評\n",
            "新",
            "# メモ\n\n## 振り返り\n\n新\n",
        ),
        (
            "# メモ\n\n## 操作方法\n\n- A\n",
            "前",
            "# メモ\n\n## 操作方法\n\n- A\n\n## 振り返り\n\n前\n",
        ),
        ("# メモ", "前", "# メモ\n\n## 振り返り\n\n前\n"),
        ("", "前", "## 振り返り\n\n前\n"),
    ],
    ids=[
        "section-at-the-end",
        "old-critique-is-removed",
        "followed-by-a-section",
        "followed-by-a-section-without-blank-lines",
        "followed-by-a-top-heading",
        "heading-with-trailing-spaces",
        "no-section",
        "no-section-without-a-trailing-newline",
        "empty-notes",
    ],
)
def test_replace_reflection_section(notes, body, expected):
    assert replace_reflection_section(notes, body) == expected


# --- next_tick_index ---


@pytest.mark.parametrize(
    ("run_start", "now", "last_index", "expected"),
    [
        (0.0, 10.0, 0, 1),
        (0.0, 310.0, 1, 2),
        (0.0, 650.0, 1, 3),
        (0.0, 599.9, 1, 2),
        (0.0, 600.0, 1, 3),
        (1000.0, 1010.0, 0, 1),
        (0.0, 10.0, 2, 3),
    ],
    ids=[
        "first",
        "next",
        "skips-a-passed-tick",
        "just-before-a-tick",
        "exactly-at-a-tick",
        "run-start-is-not-zero",
        "after-the-last-index",
    ],
)
def test_next_tick_index(run_start, now, last_index, expected):
    assert next_tick_index(run_start, now, last_index) == expected


def test_next_tick_index_with_another_interval():
    # 60 秒ごとなら、130 秒の時点で 1 回目 (60) と 2 回目 (120) は過ぎている
    assert next_tick_index(0.0, 130.0, 0, interval_s=60.0) == 3


# --- notice_pending ---


@pytest.mark.parametrize(
    ("commands", "pending"),
    [
        ([], False),
        (["look", "press"], False),
        (["reflection-notice", "look"], False),
        (["look", "reflection", "press"], True),
        (["reflection", "look", "reflection-notice", "look"], False),
        (["reflection", "look", "reflection-notice", "reflection", "look"], True),
    ],
    ids=[
        "no-events",
        "no-reflection",
        "only-a-notice",
        "reflection-without-a-notice",
        "notice-after-the-reflection",
        "reflection-after-the-notice",
    ],
)
def test_notice_pending(commands, pending):
    events = [make_event(1000.0 + i, command) for i, command in enumerate(commands)]
    assert notice_pending(events) is pending


def test_notice_pending_compares_positions_not_times():
    # 知らせの行は、時刻は早いが、リストでは振り返りの行より後ろにある
    events = [
        make_event(2000.0, "reflection", ("1",), "ok notes.md"),
        make_event(1000.0, "reflection-notice", reply=NOTICE_LINE),
    ]
    assert notice_pending(events) is False


# --- build_notice_event / build_reflection_event ---


def test_build_notice_event():
    assert build_notice_event(1000.5, "frames/000003.png") == CommandEvent(
        time=1000.5,
        command="reflection-notice",
        args=(),
        reply="振り返り: notes.md の「振り返り」の節を書き換えました",
        ok=True,
        elapsed_s=0.0,
        frame="frames/000003.png",
    )


def test_build_reflection_event():
    assert build_reflection_event(1311.0, 3, 11.0) == CommandEvent(
        time=1311.0,
        command="reflection",
        args=("3",),
        reply="ok notes.md",
        ok=True,
        elapsed_s=11.0,
        frame=None,
    )
