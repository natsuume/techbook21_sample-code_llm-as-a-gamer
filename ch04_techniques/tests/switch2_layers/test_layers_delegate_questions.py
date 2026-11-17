"""委任のループが Jev に送る問いと要求の state、答えから選んだ選択肢を決める規則 (orchestrator.delegate_questions)。"""

from switch2_layers.orchestrator.decision_types import DelegateChoice
from switch2_layers.orchestrator.delegate_questions import (
    DELEGATE_INSTRUCTIONS,
    DELEGATE_QUESTION_NAME,
    DELEGATE_QUESTION_TEXT,
    MAX_RECENT_PICKS,
    NONE_OPTION,
    RETURN_CONTROL_OPTION,
    DelegateStateFrame,
    build_delegate_question,
    build_delegate_state,
    choose_option,
    delegate_option_names,
)
from switch2_layers.orchestrator.orchestrator_events import DelegatePick
from switch2_layers.surface.surface_questions import QuestionKind, QuestionType

ATTACK = DelegateChoice(name="attack", description="前の敵を攻撃する")
MOVE_UP = DelegateChoice(name="move_up", description="上に 1 マス移動する")


def test_delegate_options_are_none_then_the_actions_then_return_control():
    assert delegate_option_names([ATTACK, MOVE_UP]) == (
        "none",
        "attack",
        "move_up",
        "return_control",
    )
    assert NONE_OPTION == "none"
    assert RETURN_CONTROL_OPTION == "return_control"


def test_delegate_question_is_one_choice_question_with_the_descriptions():
    question = build_delegate_question([ATTACK, MOVE_UP])
    assert question.name == DELEGATE_QUESTION_NAME
    assert question.kind is QuestionKind.DELEGATE
    assert question.question_type is QuestionType.CHOICE
    assert question.instructions == DELEGATE_QUESTION_TEXT
    assert [choice.name for choice in question.choices] == [
        "none",
        "attack",
        "move_up",
        "return_control",
    ]
    # 行動の説明は、主ループが書いた文をそのまま使う
    assert [choice.description for choice in question.choices[1:3]] == [
        "前の敵を攻撃する",
        "上に 1 マス移動する",
    ]


def test_delegate_state_has_the_role_tasks_cautions_recent_actions_and_frames_in_order():
    state = build_delegate_state(
        ["最重要: HP を 0 にせずに敵をすべて倒す", "敵の攻撃は、可能なら避ける"],
        ["衝撃波は横一列に進む"],
        [DelegatePick("attack", 12.5), DelegatePick("move_up", 13.25)],
        [DelegateStateFrame(12.8, "ref-1"), DelegateStateFrame(13.0, "ref-2")],
    )
    assert list(state) == [
        "instructions",
        "tasks",
        "cautions",
        "recent_actions",
        "frames",
    ]
    assert state == {
        "instructions": DELEGATE_INSTRUCTIONS,
        "tasks": [
            "最重要: HP を 0 にせずに敵をすべて倒す",
            "敵の攻撃は、可能なら避ける",
        ],
        "cautions": ["衝撃波は横一列に進む"],
        "recent_actions": [
            {"name": "attack", "t": "t=12.500"},
            {"name": "move_up", "t": "t=13.250"},
        ],
        "frames": [
            {"t": "t=12.800", "image": "ref-1"},
            {"t": "t=13.000", "image": "ref-2"},
        ],
    }


def test_delegate_state_keeps_only_the_newest_five_recent_actions():
    picks = [DelegatePick(f"action_{index}", float(index)) for index in range(7)]
    state = build_delegate_state(["敵を倒す"], [], picks, [])
    assert MAX_RECENT_PICKS == 5
    assert [row["name"] for row in state["recent_actions"]] == [
        "action_2",
        "action_3",
        "action_4",
        "action_5",
        "action_6",
    ]


def test_delegate_choice_is_the_option_with_the_highest_probability():
    names = delegate_option_names([ATTACK, MOVE_UP])
    probabilities = {
        "none": 0.1,
        "attack": 0.2,
        "move_up": 0.6,
        "return_control": 0.1,
    }
    assert choose_option(probabilities, names) == "move_up"


def test_delegate_choice_of_the_same_probability_is_the_earlier_option():
    names = delegate_option_names([ATTACK, MOVE_UP])
    probabilities = {
        "none": 0.1,
        "attack": 0.4,
        "move_up": 0.4,
        "return_control": 0.1,
    }
    assert choose_option(probabilities, names) == "attack"
