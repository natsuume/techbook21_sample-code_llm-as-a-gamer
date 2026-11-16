from switch2_layers.middle.system_screen_instructions import (
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_types import SystemGoal


def test_developer_instructions_is_a_non_empty_string():
    assert isinstance(SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS, str)
    assert SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS.strip()


def test_goal_text_is_non_empty_for_each_goal():
    for goal in SystemGoal:
        text = goal_text(goal)
        assert isinstance(text, str)
        assert text.strip()


def test_goal_texts_differ_by_goal():
    texts = [goal_text(goal) for goal in SystemGoal]
    assert len(set(texts)) == len(texts)


def test_goal_text_mentions_done():
    # どの目的の手順にも、done を真にする条件を書く
    for goal in SystemGoal:
        assert "done" in goal_text(goal)
