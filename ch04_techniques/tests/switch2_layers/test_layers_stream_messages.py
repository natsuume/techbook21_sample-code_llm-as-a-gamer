import json

import pytest

from switch2_layers.orchestrator.stream_messages import (
    assistant_usage_tokens,
    compact_metadata,
    is_compact_boundary,
    user_message_line,
)

TEXT_BLOCK = {"type": "text", "text": "## 時刻\nt=12.345"}
IMAGE_BLOCK = {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/4AAQ"},
}
# 実際の stream-json の assistant の行 (2026-11-16、Claude Code 2.1.325) の形
ASSISTANT_LINE = {
    "type": "assistant",
    "message": {
        "model": "claude-opus-5-5",
        "role": "assistant",
        "content": [{"type": "text", "text": "OK"}],
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 3375,
            "cache_read_input_tokens": 4437,
            "output_tokens": 7,
        },
    },
    "parent_tool_use_id": None,
}
COMPACT_LINE = {
    "type": "system",
    "subtype": "compact_boundary",
    "compact_metadata": {"trigger": "auto", "pre_tokens": 967_123},
}


# --- 標準入力に送る行 ---


def test_user_message_line_is_one_json_line_with_the_content():
    line = user_message_line([TEXT_BLOCK, IMAGE_BLOCK])
    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    assert json.loads(line) == {
        "type": "user",
        "message": {"role": "user", "content": [TEXT_BLOCK, IMAGE_BLOCK]},
        "parent_tool_use_id": None,
    }


def test_user_message_line_keeps_non_ascii_text():
    line = user_message_line([TEXT_BLOCK])
    assert "時刻" in line


def test_user_message_line_rejects_empty_content():
    with pytest.raises(ValueError):
        user_message_line([])


# --- 使用量 ---


def test_usage_is_input_plus_cache_read_plus_cache_creation():
    assert assistant_usage_tokens(ASSISTANT_LINE) == 2 + 4437 + 3375


def test_missing_and_non_integer_usage_values_count_as_zero():
    line = {
        "type": "assistant",
        "message": {
            "content": [],
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": True,
                "cache_creation_input_tokens": "100",
            },
        },
    }
    assert assistant_usage_tokens(line) == 5


@pytest.mark.parametrize(
    "data",
    [
        {"type": "result", "usage": {"input_tokens": 100}},
        {"type": "assistant", "message": {"content": []}},
        {"type": "assistant", "message": {"content": [], "usage": None}},
        {"type": "assistant", "message": "text"},
        {"type": "system", "subtype": "init"},
        "not an object",
        None,
    ],
)
def test_lines_without_assistant_usage_give_none(data):
    assert assistant_usage_tokens(data) is None


# --- 自動の要約の印 ---


def test_compact_boundary_is_recognized():
    assert is_compact_boundary(COMPACT_LINE) is True
    assert compact_metadata(COMPACT_LINE) == {"trigger": "auto", "pre_tokens": 967_123}


@pytest.mark.parametrize(
    "data",
    [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "subtype": "compact_boundary"},
        "compact_boundary",
        None,
    ],
)
def test_other_lines_are_not_compact_boundaries(data):
    assert is_compact_boundary(data) is False


def test_compact_boundary_without_metadata():
    assert compact_metadata({"type": "system", "subtype": "compact_boundary"}) is None
    assert compact_metadata("x") is None
