import pytest

from switch2_harness.agent_kind import (
    AGENT_KINDS,
    CLAUDE_AGENT,
    CODEX_AGENT,
    DEFAULT_AGENT,
    default_model,
    instructions_file_name,
    parse_agent_kind,
)


def test_agent_kinds_and_default():
    assert AGENT_KINDS == ("claude", "codex")
    assert DEFAULT_AGENT == CLAUDE_AGENT == "claude"
    assert CODEX_AGENT == "codex"


def test_default_models():
    assert default_model("claude") == "claude-opus-5-5"
    assert default_model("codex") == "gpt-6.2-sol"


def test_instructions_file_names():
    assert instructions_file_name("claude") == "CLAUDE.md"
    assert instructions_file_name("codex") == "AGENTS.md"


@pytest.mark.parametrize("value", ["claude", "codex"])
def test_parse_agent_kind_accepts_known_kinds(value):
    assert parse_agent_kind(value) == value


@pytest.mark.parametrize("value", ["gemini", "", None, 1, "Claude"])
def test_parse_agent_kind_rejects_others(value):
    with pytest.raises(ValueError) as error:
        parse_agent_kind(value)
    assert str(error.value) == f"unknown agent: {value} (available: claude, codex)"
