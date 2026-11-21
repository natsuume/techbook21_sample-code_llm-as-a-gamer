import json

from harness_fakes import (
    codex_command_line,
    codex_message_line,
    codex_thread_line,
    codex_turn_completed_line,
)

from switch2_harness.codex_log import (
    TokenUsage,
    agent_message_texts,
    count_responses,
    count_turns_completed,
    final_agent_message,
    has_codex_output,
    last_thread_id,
    max_usage_by_thread,
    usage_from,
)

TURN_STARTED = json.dumps({"type": "turn.started"})
WARNING_ITEM = json.dumps(
    {"type": "item.completed", "item": {"type": "error", "message": "deprecated"}}
)
REASONING_ITEM = json.dumps(
    {"type": "item.completed", "item": {"type": "reasoning", "text": "考え中"}}
)
TURN_FAILED = json.dumps({"type": "turn.failed", "error": {"message": "no model"}})
ERROR_EVENT = json.dumps({"type": "error", "message": "no model"})


def test_last_thread_id_returns_the_last_thread_started():
    lines = [
        "WARNING: not json",
        codex_thread_line("t1"),
        codex_message_line("a"),
        codex_thread_line("t2"),
        json.dumps({"type": "item.completed", "thread_id": "not-a-thread-start"}),
        '{"type": "thread.started", "thread_id": "t3"',
    ]
    assert last_thread_id(lines) == "t2"


def test_last_thread_id_without_thread_is_none():
    assert last_thread_id([TURN_STARTED, "", "[1]"]) is None


def test_agent_message_texts_strips_and_skips_empty_messages():
    lines = [
        codex_thread_line("t1"),
        codex_message_line(" 右へ進みます\n"),
        codex_command_line("uv run switch2-harness press tap RIGHT"),
        codex_message_line("  "),
        WARNING_ITEM,
        codex_message_line("扉が開きました"),
        json.dumps(
            {"type": "item.started", "item": {"type": "agent_message", "text": "x"}}
        ),
    ]
    assert agent_message_texts(lines) == ["右へ進みます", "扉が開きました"]


def test_final_agent_message_takes_the_last_message_of_a_completed_turn():
    lines = [
        codex_thread_line("t1"),
        TURN_STARTED,
        WARNING_ITEM,
        codex_message_line("読みます"),
        codex_message_line(" - 進んでいない\n"),
        codex_turn_completed_line(10),
    ]
    assert final_agent_message(lines) == "- 進んでいない"


def test_final_agent_message_is_none_when_the_turn_failed_or_did_not_end():
    failed = [TURN_STARTED, codex_message_line("途中"), ERROR_EVENT, TURN_FAILED]
    assert final_agent_message(failed) is None
    unfinished = [TURN_STARTED, codex_message_line("途中")]
    assert final_agent_message(unfinished) is None
    no_message = [TURN_STARTED, codex_turn_completed_line()]
    assert final_agent_message(no_message) is None


def test_final_agent_message_looks_at_the_last_turn():
    lines = [
        TURN_STARTED,
        codex_message_line("前のターン"),
        codex_turn_completed_line(),
        TURN_STARTED,
        TURN_FAILED,
    ]
    assert final_agent_message(lines) is None


def test_usage_from_reads_the_token_counts():
    line = json.loads(codex_turn_completed_line(100, 40, 7, 3))
    assert usage_from(line) == TokenUsage(100, 40, 7, 3)
    assert usage_from({"type": "turn.completed"}) == TokenUsage()
    bad = {
        "usage": {"input_tokens": "5", "output_tokens": True, "cached_input_tokens": 2}
    }
    assert usage_from(bad) == TokenUsage(0, 2, 0, 0)


def test_max_usage_by_thread_keeps_the_largest_cumulative_value_of_each_thread():
    lines = [
        codex_thread_line("t1"),
        codex_turn_completed_line(30313, 19968, 145, 0),
        # resume で続けた起動の usage は、前の起動の分を含む累計
        codex_thread_line("t1"),
        codex_turn_completed_line(41218, 30080, 150, 2),
        codex_thread_line("t2"),
        codex_turn_completed_line(500, 0, 9, 1),
    ]
    assert max_usage_by_thread(lines) == {
        "t1": TokenUsage(41218, 30080, 150, 2),
        "t2": TokenUsage(500, 0, 9, 1),
    }


def test_max_usage_by_thread_puts_a_turn_before_any_thread_under_none():
    assert max_usage_by_thread([codex_turn_completed_line(5)]) == {
        None: TokenUsage(5, 0, 0, 0)
    }


def test_count_turns_completed():
    lines = [
        TURN_STARTED,
        codex_turn_completed_line(),
        TURN_FAILED,
        codex_turn_completed_line(),
    ]
    assert count_turns_completed(lines) == 2


def test_count_responses_skips_reasoning_and_warnings():
    lines = [
        WARNING_ITEM,
        REASONING_ITEM,
        codex_message_line("a"),
        codex_command_line("ls"),
        json.dumps({"type": "item.completed", "item": {"type": "file_change"}}),
        json.dumps({"type": "item.started", "item": {"type": "command_execution"}}),
    ]
    assert count_responses(lines) == 3


def test_has_codex_output():
    assert has_codex_output([codex_thread_line("t1"), TURN_STARTED]) is False
    assert has_codex_output([codex_turn_completed_line()]) is True
    assert has_codex_output([WARNING_ITEM]) is True
    assert has_codex_output([]) is False
