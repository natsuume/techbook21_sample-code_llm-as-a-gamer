import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.orchestrator_instructions import (
    HANDOFF_NOTE,
    handoff_request_text,
    orchestrator_instructions,
)
from switch2_layers.safety.safety_rules import SAFETY_RULES


def test_instructions_contain_the_safety_rules_verbatim():
    assert SAFETY_RULES in orchestrator_instructions()


def test_instructions_can_be_passed_as_the_appended_system_prompt():
    text = orchestrator_instructions()
    assert text
    assert not text.startswith("-")


@pytest.mark.parametrize(
    "word",
    [
        "```json",
        '"actions"',
        '"macros"',
        '"monitor"',
        '"press"',
        '"wait"',
        '"procedure"',
        '"macro"',
        '"buttons"',
        '"sticks"',
        '"seconds"',
        '"left"',
        '"right"',
        '"set"',
        '"delete"',
        '"create"',
        '"stop"',
        '"cases"',
        '"condition"',
        '"repeat"',
        '"every"',
        '"once"',
    ],
)
def test_instructions_describe_the_json_form(word):
    assert word in orchestrator_instructions()


@pytest.mark.parametrize(
    "word",
    ["tap_repeat", "walk", "button", "count", "interval_seconds", "direction"],
)
def test_instructions_describe_the_code_procedures(word):
    assert word in orchestrator_instructions()


def test_instructions_list_every_button_name_and_the_hold_limit():
    text = orchestrator_instructions()
    for button in BUTTON_ORDER:
        assert button in text
    assert f"{MAX_HOLD_SECONDS:g}" in text


def test_instructions_describe_skills_notes_and_old_frames():
    text = orchestrator_instructions()
    assert "notes/skills/game-" in text
    assert "SKILL.md" in text
    assert "name" in text
    assert "description" in text
    assert ".claude/skills/" in text
    assert "notes/" in text
    assert "frames/interval/" in text


def test_handoff_request_asks_for_the_handoff_note_and_json_without_actions():
    text = handoff_request_text()
    assert f"notes/{HANDOFF_NOTE}" in text
    assert HANDOFF_NOTE == "handoff.md"
    assert "```json" in text
    assert "actions" in text
    for item in ("目標", "分かったこと", "効かなかったこと", "監視", "マクロ"):
        assert item in text
