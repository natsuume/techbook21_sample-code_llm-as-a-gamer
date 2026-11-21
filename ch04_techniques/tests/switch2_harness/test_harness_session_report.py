import json

import numpy as np
import pytest
from harness_fakes import (
    codex_command_line,
    codex_message_line,
    codex_thread_line,
    codex_turn_completed_line,
)

from switch2_harness.event_log import CommandEvent
from switch2_harness.session_report import (
    AgentSummary,
    CodexAgentSummary,
    IntervalStats,
    SessionSummary,
    agent_messages_from_lines,
    count_launches,
    count_responses,
    format_summary,
    summarize_agent,
    summarize_codex_agent,
    summarize_session,
    summary_to_dict,
)

BLACK = np.zeros((90, 160), dtype=np.uint8)
WHITE = np.full((90, 160), 255, dtype=np.uint8)


def look(time: float, frame: str | None, ok: bool = True) -> CommandEvent:
    reply = f"ok /w/{frame}" if ok else "error no frame yet"
    return CommandEvent(time, "look", (), reply, ok, 0.2, frame if ok else None)


def press(time: float, ok: bool = True) -> CommandEvent:
    reply = "ok tap A" if ok else "error HOME cannot be pressed with press."
    return CommandEvent(time, "press", ("tap", "A"), reply, ok, 0.4)


def other(time: float, command: str) -> CommandEvent:
    return CommandEvent(time, command, (), "ok", True, 0.1)


def run_event(time: float, *args: str) -> CommandEvent:
    return CommandEvent(time, "run", args, "run event", True, 0.0)


def assistant_line(message_id: str | None) -> str:
    message: dict[str, object] = {"role": "assistant", "content": []}
    if message_id is not None:
        message["id"] = message_id
    return json.dumps({"type": "assistant", "message": message})


def result_line(**values: object) -> str:
    return json.dumps({"type": "result", **values})


NO_AGENT_OUTPUT = AgentSummary(
    launches=1,
    results=0,
    total_cost_usd=0.0,
    num_turns=0,
    duration_ms=0,
    responses=0,
)

# 成功した look は 100 / 104 / 107 / 110 / 116 秒の 5 回で、画像は 黒 黒 白 白 白。
# 組 (100,104): 変化なし・間に成功した press あり
# 組 (104,107): 変化あり・間に成功した press あり
# 組 (107,110): 変化なし・間の press は失敗 (成功した press なし)
# 組 (110,116): 変化なし・間に成功した press あり (111 秒の失敗した look は組に入らない)
EVENTS = [
    run_event(99.0, "start"),
    look(100.0, "frames/000001.png"),
    press(102.0),
    look(104.0, "frames/000002.png"),
    other(105.0, "status"),
    press(106.0),
    look(107.0, "frames/000003.png"),
    press(108.0, ok=False),
    look(110.0, "frames/000004.png"),
    look(111.0, None, ok=False),
    press(112.0),
    look(116.0, "frames/000005.png"),
    other(120.0, "finish"),
]
FRAMES = {
    "frames/000001.png": BLACK,
    "frames/000002.png": BLACK,
    "frames/000003.png": WHITE,
    "frames/000004.png": WHITE,
    "frames/000005.png": WHITE,
}


def load_fake_frame(frame: str) -> np.ndarray:
    return FRAMES[frame]


# --- 画面と操作の集計 ---


def test_summarize_session_counts_every_item():
    summary = summarize_session(EVENTS, load_fake_frame, [])
    assert summary == SessionSummary(
        duration_s=21.0,
        command_counts={"run": 1, "look": 6, "press": 4, "status": 1, "finish": 1},
        error_count=2,
        look_interval=IntervalStats(median_s=3.5, mean_s=4.0, max_s=6.0),
        static_pairs=3,
        look_pairs=4,
        longest_static_s=9.0,
        unchanged_after_press=2,
        pressed_pairs=3,
        agent=NO_AGENT_OUTPUT,
    )


def test_command_counts_keep_first_appearance_order():
    summary = summarize_session(EVENTS, load_fake_frame, [])
    assert list(summary.command_counts) == ["run", "look", "press", "status", "finish"]


def test_summarize_session_reads_only_successful_look_frames():
    requested: set[str] = set()

    def load_frame(frame: str) -> np.ndarray:
        requested.add(frame)
        return FRAMES[frame]

    summarize_session(EVENTS, load_frame, [])
    assert requested == set(FRAMES)


def test_summarize_session_sorts_events_by_time():
    shuffled = list(reversed(EVENTS))
    assert summarize_session(shuffled, load_fake_frame, []) == summarize_session(
        EVENTS, load_fake_frame, []
    )


def test_summarize_session_without_events():
    summary = summarize_session([], load_fake_frame, [])
    assert summary == SessionSummary(
        duration_s=0.0,
        command_counts={},
        error_count=0,
        look_interval=None,
        static_pairs=0,
        look_pairs=0,
        longest_static_s=0.0,
        unchanged_after_press=0,
        pressed_pairs=0,
        agent=None,
    )


def test_one_look_has_no_interval_and_no_pairs():
    summary = summarize_session(
        [look(10.0, "frames/000001.png"), other(12.0, "status")],
        load_fake_frame,
        [],
    )
    assert summary.duration_s == 2.0
    assert summary.look_interval is None
    assert summary.look_pairs == 0
    assert summary.longest_static_s == 0.0


def test_failed_looks_do_not_count_for_intervals():
    summary = summarize_session(
        [look(10.0, "frames/000001.png"), look(11.0, None, ok=False)],
        load_fake_frame,
        [],
    )
    assert summary.look_interval is None
    assert summary.look_pairs == 0


def test_longest_static_spans_consecutive_static_pairs():
    events = [
        look(10.0, "frames/000003.png"),
        look(13.0, "frames/000004.png"),
        look(20.0, "frames/000005.png"),
    ]
    summary = summarize_session(events, load_fake_frame, [])
    assert summary.static_pairs == 2
    assert summary.longest_static_s == 10.0
    assert summary.look_interval == IntervalStats(median_s=5.0, mean_s=5.0, max_s=7.0)


def test_changed_pairs_have_no_static_span():
    events = [look(10.0, "frames/000002.png"), look(12.0, "frames/000003.png")]
    summary = summarize_session(events, load_fake_frame, [])
    assert summary.static_pairs == 0
    assert summary.longest_static_s == 0.0


# --- エージェントの集計 ---


def test_summarize_session_passes_events_and_agent_lines():
    lines = [
        assistant_line("m1"),
        result_line(session_id="s1", total_cost_usd=0.5, num_turns=2),
    ]
    summary = summarize_session([run_event(1.0, "start")], load_fake_frame, lines)
    assert summary.agent == AgentSummary(
        launches=1,
        results=1,
        total_cost_usd=0.5,
        num_turns=2,
        duration_ms=0,
        responses=1,
    )


def test_summarize_agent_is_none_without_run_events_and_agent_messages():
    assert summarize_agent([], []) is None
    lines = [json.dumps({"type": "system", "session_id": "s1"}), "not json"]
    assert summarize_agent([look(1.0, "frames/000001.png")], lines) is None


def test_summarize_agent_counts_launch_that_wrote_no_result():
    events = [
        run_event(1.0, "start"),
        run_event(61.0, "stop", "deadline"),
        run_event(61.5, "release"),
    ]
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
        assistant_line("m1"),
        assistant_line("m1"),
        assistant_line("m2"),
        '{"type": "assistant", "message": {"id": "m3", "cont',
    ]
    assert summarize_agent(events, lines) == AgentSummary(
        launches=1,
        results=0,
        total_cost_usd=0.0,
        num_turns=0,
        duration_ms=0,
        responses=2,
    )


def test_summarize_agent_counts_launches_from_start_and_resume_events():
    events = [
        run_event(1.0, "start"),
        run_event(10.0, "resume", "s1"),
        run_event(20.0, "resume", "s1"),
        run_event(61.0, "stop", "deadline"),
        run_event(61.5, "release"),
    ]
    agent = summarize_agent(events, [result_line(session_id="s1")])
    assert agent is not None
    assert agent.launches == 3
    assert agent.results == 1


def test_summarize_agent_with_run_events_only():
    assert summarize_agent([run_event(1.0, "start")], []) == NO_AGENT_OUTPUT


def test_count_launches():
    events = [
        run_event(1.0, "start"),
        run_event(2.0, "resume", "s1"),
        run_event(3.0, "stop", "deadline"),
        run_event(4.0, "release"),
        run_event(5.0),
        CommandEvent(6.0, "press", ("start",), "ok", True, 0.1),
    ]
    assert count_launches(events) == 2


def test_count_responses_merges_lines_with_the_same_message_id():
    messages = [
        {"type": "assistant", "message": {"id": "m1", "content": ["text"]}},
        {"type": "assistant", "message": {"id": "m1", "content": ["tool_use"]}},
        {"type": "assistant", "message": {"id": "m2"}},
        {"type": "user", "message": {"id": "m3"}},
        {"type": "result", "session_id": "s1"},
    ]
    assert count_responses(messages) == 2


def test_count_responses_counts_lines_without_id_one_by_one():
    messages = [
        {"type": "assistant", "message": {"content": []}},
        {"type": "assistant", "message": {"content": []}},
        {"type": "assistant"},
        {"type": "assistant", "message": "text"},
        {"type": "assistant", "message": {"id": 5}},
        {"type": "assistant", "message": {"id": "m1"}},
    ]
    assert count_responses(messages) == 6


def test_summarize_agent_sums_largest_cost_of_each_session():
    lines = [
        result_line(session_id="s1", total_cost_usd=0.5, num_turns=3, duration_ms=1000),
        result_line(
            session_id="s1", total_cost_usd=1.25, num_turns=2, duration_ms=2000
        ),
        result_line(session_id="s2", total_cost_usd=0.25, num_turns=1, duration_ms=500),
    ]
    assert summarize_agent([], lines) == AgentSummary(
        launches=0,
        results=3,
        total_cost_usd=pytest.approx(1.5),
        num_turns=6,
        duration_ms=3500,
        responses=0,
    )


def test_summarize_agent_takes_the_largest_value_not_the_last():
    lines = [
        result_line(session_id="s1", total_cost_usd=2.0),
        result_line(session_id="s1", total_cost_usd=1.0),
    ]
    agent = summarize_agent([], lines)
    assert agent is not None
    assert agent.total_cost_usd == pytest.approx(2.0)


def test_summarize_agent_keeps_the_cost_before_a_crashed_result():
    # クラッシュで終わった result は total_cost_usd が 0 になることがある
    lines = [
        result_line(session_id="s1", total_cost_usd=0.4),
        result_line(
            session_id="s1", subtype="error_during_execution", total_cost_usd=0.0
        ),
        result_line(session_id="s2", total_cost_usd=0.1),
    ]
    agent = summarize_agent([], lines)
    assert agent is not None
    assert agent.results == 3
    assert agent.total_cost_usd == pytest.approx(0.5)


def test_summarize_agent_treats_missing_values_as_zero():
    lines = [
        result_line(session_id="s1"),
        result_line(session_id="s2", total_cost_usd=0.75),
    ]
    agent = summarize_agent([], lines)
    assert agent is not None
    assert agent.results == 2
    assert agent.total_cost_usd == pytest.approx(0.75)
    assert agent.num_turns == 0
    assert agent.duration_ms == 0


def test_summarize_agent_treats_non_numbers_as_zero():
    lines = [
        result_line(
            session_id="s1", total_cost_usd=None, num_turns="3", duration_ms=True
        )
    ]
    agent = summarize_agent([], lines)
    assert agent is not None
    assert agent.total_cost_usd == 0.0
    assert agent.num_turns == 0
    assert agent.duration_ms == 0


def test_summarize_agent_groups_results_without_session_id():
    lines = [
        result_line(total_cost_usd=0.5),
        result_line(total_cost_usd=0.75),
        result_line(session_id="s1", total_cost_usd=0.25),
    ]
    agent = summarize_agent([], lines)
    assert agent is not None
    assert agent.total_cost_usd == pytest.approx(1.0)


def test_agent_messages_from_lines_picks_json_objects():
    system = {"type": "system", "subtype": "init", "session_id": "s1"}
    result = {"type": "result", "session_id": "s1", "total_cost_usd": 0.5}
    assistant = {"type": "assistant", "message": {"id": "m1"}}
    lines = [
        json.dumps(system),
        json.dumps(result),
        "",
        "not json",
        json.dumps([1, 2]),
        json.dumps(assistant),
        '{"type": "result", "session_id": "s3", "total_co',
    ]
    assert agent_messages_from_lines(lines) == [system, result, assistant]


# --- 辞書とテキスト ---


def test_summary_to_dict_uses_field_names():
    summary = summarize_session(
        [run_event(1.0, "start")],
        load_fake_frame,
        [assistant_line("m1"), result_line(session_id="s1")],
    )
    data = summary_to_dict(summary)
    assert data == {
        "duration_s": 0.0,
        "command_counts": {"run": 1},
        "error_count": 0,
        "look_interval": None,
        "static_pairs": 0,
        "look_pairs": 0,
        "longest_static_s": 0.0,
        "unchanged_after_press": 0,
        "pressed_pairs": 0,
        "agent": {
            "launches": 1,
            "results": 1,
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "responses": 1,
        },
    }
    json.dumps(data)


def test_summary_to_dict_nests_interval_stats():
    summary = summarize_session(EVENTS, load_fake_frame, [])
    assert summary_to_dict(summary)["look_interval"] == {
        "median_s": 3.5,
        "mean_s": 4.0,
        "max_s": 6.0,
    }


def test_format_summary_writes_nine_lines():
    summary = SessionSummary(
        duration_s=21.0,
        command_counts={"run": 1, "look": 6, "press": 4},
        error_count=2,
        look_interval=IntervalStats(median_s=3.5, mean_s=4.0, max_s=6.0),
        static_pairs=3,
        look_pairs=4,
        longest_static_s=9.0,
        unchanged_after_press=2,
        pressed_pairs=3,
        agent=AgentSummary(
            launches=4,
            results=3,
            total_cost_usd=1.5,
            num_turns=6,
            duration_ms=3000,
            responses=12,
        ),
    )
    assert format_summary(summary) == (
        "記録の時間: 21.0 秒\n"
        "コマンドの回数: run=1 look=6 press=4\n"
        "error の数: 2\n"
        "look の間隔: 中央値 3.5 秒 / 平均 4.0 秒 / 最大 6.0 秒\n"
        "変化なしの look の組: 3 / 4\n"
        "変化なしが続いた最長の時間: 9.0 秒\n"
        "press の後も変化なしの組: 2 / 3\n"
        "エージェント: 起動 4 回 / 応答 12 回 / result 3 回\n"
        "result を出した起動の分: 費用 1.5000 USD / ターン 6 / 時間 3.0 秒"
    )


def test_format_summary_without_looks_and_agent():
    summary = SessionSummary(
        duration_s=0.0,
        command_counts={},
        error_count=0,
        look_interval=None,
        static_pairs=0,
        look_pairs=0,
        longest_static_s=0.0,
        unchanged_after_press=0,
        pressed_pairs=0,
        agent=None,
    )
    assert format_summary(summary) == (
        "記録の時間: 0.0 秒\n"
        "コマンドの回数: なし\n"
        "error の数: 0\n"
        "look の間隔: なし (成功した look が 2 回未満)\n"
        "変化なしの look の組: 0 / 0\n"
        "変化なしが続いた最長の時間: 0.0 秒\n"
        "press の後も変化なしの組: 0 / 0\n"
        "エージェント: 記録なし"
    )


# --- Codex の作業ディレクトリーの集計 ---


def codex_lines() -> list[str]:
    """2 つのスレッドの、codex exec --json の出力の行 (t1 は resume で続けた 2 回の起動)。"""
    return [
        "WARNING: not json",
        codex_thread_line("t1"),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {"type": "item.completed", "item": {"type": "error", "message": "warn"}}
        ),
        codex_message_line("進みます"),
        codex_command_line("uv run switch2-harness look"),
        codex_turn_completed_line(30313, 19968, 145, 1),
        codex_thread_line("t1"),
        codex_message_line("続けます"),
        codex_turn_completed_line(41218, 30080, 150, 2),
        codex_thread_line("t2"),
        codex_message_line("期限で止まる起動"),
    ]


def test_summarize_session_for_codex_reads_the_codex_log():
    events = [run_event(1.0, "start"), run_event(10.0, "resume", "t1")]
    summary = summarize_session(events, load_fake_frame, codex_lines(), agent="codex")
    assert summary.agent == CodexAgentSummary(
        launches=2,
        results=2,
        total_cost_usd=None,
        num_turns=None,
        duration_ms=None,
        responses=4,
        input_tokens=41218,
        cached_input_tokens=30080,
        output_tokens=150,
        reasoning_output_tokens=2,
    )


def test_summarize_codex_agent_sums_the_threads():
    lines = [
        codex_thread_line("t1"),
        codex_turn_completed_line(100, 10, 5, 1),
        codex_thread_line("t2"),
        codex_turn_completed_line(50, 0, 3, 0),
    ]
    agent = summarize_codex_agent([], lines)
    assert agent is not None
    assert (agent.input_tokens, agent.cached_input_tokens) == (150, 10)
    assert (agent.output_tokens, agent.reasoning_output_tokens) == (8, 1)


def test_summarize_codex_agent_is_none_without_runs_and_output():
    assert summarize_codex_agent([], []) is None
    assert (
        summarize_codex_agent(
            [look(1.0, "frames/000001.png")], [codex_thread_line("t")]
        )
        is None
    )
    assert summarize_codex_agent([run_event(1.0, "start")], []) == CodexAgentSummary(
        launches=1,
        results=0,
        total_cost_usd=None,
        num_turns=None,
        duration_ms=None,
        responses=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_output_tokens=0,
    )


def test_summary_to_dict_for_codex_has_the_claude_keys_and_tokens():
    summary = summarize_session(
        [run_event(1.0, "start")], load_fake_frame, codex_lines(), agent="codex"
    )
    agent = summary_to_dict(summary)["agent"]
    assert agent == {
        "launches": 1,
        "results": 2,
        "total_cost_usd": None,
        "num_turns": None,
        "duration_ms": None,
        "responses": 4,
        "input_tokens": 41218,
        "cached_input_tokens": 30080,
        "output_tokens": 150,
        "reasoning_output_tokens": 2,
    }
    json.dumps(summary_to_dict(summary))


def test_summarize_session_defaults_to_claude():
    lines = [assistant_line("m1"), result_line(session_id="s1", total_cost_usd=0.5)]
    events = [run_event(1.0, "start")]
    assert summarize_session(events, load_fake_frame, lines) == summarize_session(
        events, load_fake_frame, lines, agent="claude"
    )
    assert isinstance(
        summarize_session(events, load_fake_frame, lines).agent, AgentSummary
    )


def test_format_summary_for_codex_writes_none_for_missing_items():
    summary = SessionSummary(
        duration_s=21.0,
        command_counts={"run": 1},
        error_count=0,
        look_interval=None,
        static_pairs=0,
        look_pairs=0,
        longest_static_s=0.0,
        unchanged_after_press=0,
        pressed_pairs=0,
        agent=CodexAgentSummary(
            launches=4,
            results=3,
            total_cost_usd=None,
            num_turns=None,
            duration_ms=None,
            responses=12,
            input_tokens=219297,
            cached_input_tokens=188416,
            output_tokens=889,
            reasoning_output_tokens=0,
        ),
    )
    lines = format_summary(summary).split("\n")
    assert len(lines) == 9
    assert lines[7] == "エージェント: 起動 4 回 / 応答 12 回 / turn.completed 3 回"
    assert lines[8] == (
        "turn.completed を出した起動の分: 費用 なし / ターン なし / 時間 なし / "
        "入力 219297 トークン (キャッシュ 188416) / 出力 889 トークン (推論 0)"
    )
