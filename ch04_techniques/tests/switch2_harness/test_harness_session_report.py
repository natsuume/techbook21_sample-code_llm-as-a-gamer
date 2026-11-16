import json

import numpy as np
import pytest

from switch2_harness.event_log import CommandEvent
from switch2_harness.session_report import (
    AgentSummary,
    IntervalStats,
    SessionSummary,
    format_summary,
    result_messages_from_lines,
    summarize_agent,
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


# 成功した look は 100 / 104 / 107 / 110 / 116 秒の 5 回で、画像は 黒 黒 白 白 白。
# 組 (100,104): 変化なし・間に成功した press あり
# 組 (104,107): 変化あり・間に成功した press あり
# 組 (107,110): 変化なし・間の press は失敗 (成功した press なし)
# 組 (110,116): 変化なし・間に成功した press あり (111 秒の失敗した look は組に入らない)
EVENTS = [
    other(99.0, "run"),
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
        agent=None,
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


def test_summarize_session_includes_agent_summary():
    results = [
        {"type": "result", "session_id": "s1", "total_cost_usd": 0.5, "num_turns": 2}
    ]
    summary = summarize_session([], load_fake_frame, results)
    assert summary.agent == AgentSummary(
        launches=1, total_cost_usd=0.5, num_turns=2, duration_ms=0
    )


def test_summarize_agent_without_results_is_none():
    assert summarize_agent([]) is None


def test_summarize_agent_sums_last_cost_of_each_session():
    results = [
        {
            "type": "result",
            "session_id": "s1",
            "total_cost_usd": 0.5,
            "num_turns": 3,
            "duration_ms": 1000,
        },
        {
            "type": "result",
            "session_id": "s1",
            "total_cost_usd": 1.25,
            "num_turns": 2,
            "duration_ms": 2000,
        },
        {
            "type": "result",
            "session_id": "s2",
            "total_cost_usd": 0.25,
            "num_turns": 1,
            "duration_ms": 500,
        },
    ]
    assert summarize_agent(results) == AgentSummary(
        launches=3,
        total_cost_usd=pytest.approx(1.5),
        num_turns=6,
        duration_ms=3500,
    )


def test_summarize_agent_takes_the_last_value_not_the_largest():
    results = [
        {"type": "result", "session_id": "s1", "total_cost_usd": 2.0},
        {"type": "result", "session_id": "s1", "total_cost_usd": 1.0},
    ]
    agent = summarize_agent(results)
    assert agent is not None
    assert agent.total_cost_usd == pytest.approx(1.0)


def test_summarize_agent_treats_missing_values_as_zero():
    results = [
        {"type": "result", "session_id": "s1"},
        {"type": "result", "session_id": "s2", "total_cost_usd": 0.75},
    ]
    assert summarize_agent(results) == AgentSummary(
        launches=2, total_cost_usd=pytest.approx(0.75), num_turns=0, duration_ms=0
    )


def test_summarize_agent_treats_non_numbers_as_zero():
    results = [
        {
            "type": "result",
            "session_id": "s1",
            "total_cost_usd": None,
            "num_turns": "3",
            "duration_ms": True,
        }
    ]
    assert summarize_agent(results) == AgentSummary(
        launches=1, total_cost_usd=0.0, num_turns=0, duration_ms=0
    )


def test_summarize_agent_groups_results_without_session_id():
    results = [
        {"type": "result", "total_cost_usd": 0.5},
        {"type": "result", "total_cost_usd": 0.75},
        {"type": "result", "session_id": "s1", "total_cost_usd": 0.25},
    ]
    agent = summarize_agent(results)
    assert agent is not None
    assert agent.total_cost_usd == pytest.approx(1.0)


def test_result_messages_from_lines_picks_result_objects():
    first = {"type": "result", "session_id": "s1", "total_cost_usd": 0.5}
    second = {"type": "result", "session_id": "s2", "total_cost_usd": 0.25}
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
        json.dumps(first),
        "",
        "not json",
        json.dumps([1, 2]),
        json.dumps({"type": "assistant", "session_id": "s2"}),
        json.dumps(second),
        '{"type": "result", "session_id": "s3", "total_co',
    ]
    assert result_messages_from_lines(lines) == [first, second]


def test_summary_to_dict_uses_field_names():
    summary = summarize_session(
        [], load_fake_frame, [{"type": "result", "session_id": "s1"}]
    )
    data = summary_to_dict(summary)
    assert data == {
        "duration_s": 0.0,
        "command_counts": {},
        "error_count": 0,
        "look_interval": None,
        "static_pairs": 0,
        "look_pairs": 0,
        "longest_static_s": 0.0,
        "unchanged_after_press": 0,
        "pressed_pairs": 0,
        "agent": {
            "launches": 1,
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
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


def test_format_summary_writes_eight_lines():
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
            launches=3, total_cost_usd=1.5, num_turns=6, duration_ms=3000
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
        "エージェント: 起動 3 回 / 費用 1.5000 USD / ターン 6 / 時間 3.0 秒"
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
