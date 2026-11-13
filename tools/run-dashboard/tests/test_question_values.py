import random

import pytest
from switch2_agent.action_decision import Thresholds, decide_action
from switch2_agent.questions import Choice, Question, build_questions
from synthetic_runs import make_decision, neutral_answers

from run_dashboard.question_values import (
    adopted_answers,
    adopted_count,
    header_question_list,
    is_old_format,
    question_value,
    questions_for_decision,
    request_adopted,
    request_question_values,
)

QUESTION_NAMES = [question.name for question in build_questions()]
BUTTON_A_ITEM = {
    "name": "button_A",
    "text": "今、A ボタンをどうするか。",
    "kind": "button",
    "answer_type": "choice",
    "choices": [
        {"name": "none", "description": "押さない"},
        {
            "name": "hold",
            "description": "押した状態にする。押した状態は次の判断まで続く",
        },
        {"name": "tap", "description": "1 回だけ短く押す"},
    ],
}
STICK_LS_X_ITEM = {
    "name": "stick_LS_x",
    "text": "今、左スティックを左右に倒すか。",
    "kind": "stick_direction",
    "answer_type": "choice",
    "choices": [
        {"name": "none", "description": "左右には倒さない"},
        {"name": "left", "description": "左に倒す"},
        {"name": "right", "description": "右に倒す"},
    ],
}
ADVISOR_STRATEGY_ITEM = {
    "name": "advisor_strategy",
    "text": "advisor の問い",
    "kind": "advisor",
    "answer_type": "probability",
    "choices": [],
}


def question_item(question: Question) -> dict:
    """Question を header の問いの形 (request_log.RequestLog.write_header の要素) にする。"""
    return {
        "name": question.name,
        "text": question.text,
        "kind": question.kind,
        "answer_type": question.answer_type,
        "choices": [
            {"name": choice.name, "description": choice.description}
            for choice in question.choices
        ],
    }


def current_header() -> dict:
    return {
        "event": "header",
        "questions": [question_item(question) for question in build_questions()],
    }


# --- is_old_format ---


def test_header_none_is_not_old_format():
    assert is_old_format(None) is False


def test_current_header_is_not_old_format():
    assert is_old_format(current_header()) is False


def test_header_without_answer_type_is_old_format():
    header = {
        "event": "header",
        "questions": [
            {"name": "press_A", "text": "押した状態にするべきか。", "kind": "press"},
            {"name": "advisor", "text": "advisor の問い", "kind": "advisor"},
        ],
    }
    assert is_old_format(header) is True


def test_header_with_one_question_missing_answer_type_is_old_format():
    header = current_header()
    del header["questions"][0]["answer_type"]
    assert is_old_format(header) is True


def test_header_with_single_advisor_question_is_old_format():
    header = current_header()
    header["questions"] = [
        item for item in header["questions"] if item["kind"] != "advisor"
    ] + [{**ADVISOR_STRATEGY_ITEM, "name": "advisor"}]
    assert is_old_format(header) is True


def test_header_missing_one_advisor_role_question_is_old_format():
    header = current_header()
    header["questions"] = [
        item for item in header["questions"] if item["name"] != "advisor_tips"
    ]
    assert is_old_format(header) is True


@pytest.mark.parametrize("questions", [None, "questions", {"name": "button_A"}])
def test_header_whose_questions_is_not_list_is_old_format(questions):
    assert is_old_format({"event": "header", "questions": questions}) is True


# --- header_question_list ---


def test_question_list_without_header_is_built_from_build_questions():
    items = header_question_list(None)
    assert [item["name"] for item in items] == QUESTION_NAMES
    assert items[0] == BUTTON_A_ITEM
    assert items[-1]["name"] == "advisor_tips"
    assert items[-1]["answer_type"] == "probability"
    assert items[-1]["choices"] == []


def test_question_list_of_header_has_five_keys_in_header_order():
    header = current_header()
    header["questions"][0]["extra"] = "ignored"
    items = header_question_list(header)
    assert items == current_header()["questions"]
    assert set(items[0]) == {"name", "text", "kind", "answer_type", "choices"}


def test_question_list_of_old_format_is_empty():
    header = {
        "event": "header",
        "questions": [{"name": "press_A", "text": "x", "kind": "press"}],
    }
    assert header_question_list(header) == []


# --- questions_for_decision ---


def test_questions_for_decision_takes_target_from_build_questions():
    items = [
        {
            **question_item(build_questions()[QUESTION_NAMES.index("button_UP")]),
            "text": "header の文",
        },
        {
            "name": "unknown_question",
            "text": "x",
            "kind": "button",
            "answer_type": "choice",
            "choices": [],
        },
        ADVISOR_STRATEGY_ITEM,
    ]
    assert questions_for_decision(items) == [
        Question(
            "button_UP",
            "header の文",
            "button",
            "UP",
            "choice",
            (
                Choice("none", "押さない"),
                Choice("hold", "押した状態にする。押した状態は次の判断まで続く"),
                Choice("tap", "1 回だけ短く押す"),
            ),
        ),
        Question(
            "advisor_strategy",
            "advisor の問い",
            "advisor",
            "strategy",
            "probability",
            (),
        ),
    ]


def test_questions_for_decision_of_full_list_equals_build_questions():
    assert questions_for_decision(header_question_list(None)) == build_questions()


# --- question_value / request_question_values ---


def test_question_value_of_button_is_hold_plus_tap():
    choices = {"button_A": {"none": 0.25, "hold": 0.25, "tap": 0.5}}
    assert question_value(BUTTON_A_ITEM, {}, choices) == pytest.approx(0.75)


def test_question_value_of_stick_direction_is_sum_of_two_directions():
    choices = {"stick_LS_x": {"none": 0.5, "left": 0.125, "right": 0.375}}
    assert question_value(STICK_LS_X_ITEM, {}, choices) == pytest.approx(0.5)


def test_question_value_of_probability_question():
    assert question_value(
        ADVISOR_STRATEGY_ITEM, {"advisor_strategy": 0.875}, {}
    ) == pytest.approx(0.875)


@pytest.mark.parametrize(
    "item, probabilities, choices",
    [
        (BUTTON_A_ITEM, {}, None),
        (BUTTON_A_ITEM, {}, {}),
        (BUTTON_A_ITEM, {}, {"button_A": {"none": 0.5, "hold": 0.5}}),
        (BUTTON_A_ITEM, {}, {"button_A": {"none": 0.5, "hold": "0.25", "tap": 0.25}}),
        (ADVISOR_STRATEGY_ITEM, None, {}),
        (ADVISOR_STRATEGY_ITEM, {}, {}),
        (ADVISOR_STRATEGY_ITEM, {"advisor_strategy": "0.5"}, {}),
        (ADVISOR_STRATEGY_ITEM, {"advisor_strategy": True}, {}),
    ],
)
def test_question_value_is_none_without_numeric_answer(item, probabilities, choices):
    assert question_value(item, probabilities, choices) is None


def test_request_question_values_in_question_list_order():
    request = {
        "probabilities": {"advisor_strategy": 0.875},
        "choices": {
            "button_A": {"none": 0.125, "hold": 0.75, "tap": 0.125},
            "stick_LS_x": {"none": 0.25, "left": 0.125, "right": 0.625},
        },
    }
    values = request_question_values(
        [BUTTON_A_ITEM, STICK_LS_X_ITEM, ADVISOR_STRATEGY_ITEM], request
    )
    assert values == pytest.approx([0.875, 0.75, 0.875])


def test_request_question_values_is_none_without_response():
    request = {"probabilities": None, "choices": None}
    assert (
        request_question_values([BUTTON_A_ITEM, ADVISOR_STRATEGY_ITEM], request) is None
    )


def test_request_question_values_with_only_probabilities_has_none_for_choice_questions():
    request = {"probabilities": {"advisor_strategy": 0.25}, "choices": None}
    assert request_question_values([BUTTON_A_ITEM, ADVISOR_STRATEGY_ITEM], request) == [
        None,
        0.25,
    ]


# --- adopted_answers / adopted_count ---


def pressed_answers():
    """押下・短押し・十字キーの相反・スティックの方向と同点・advisor の問いを含む答え (probabilities, choices)。"""
    decision = make_decision(
        {
            "button_A": {"none": 0.125, "hold": 0.75, "tap": 0.125},
            "button_B": {"none": 0.25, "hold": 0.125, "tap": 0.625},
            "button_UP": {"none": 0.125, "hold": 0.75, "tap": 0.125},
            "button_DOWN": {"none": 0.25, "hold": 0.25, "tap": 0.5},
            "stick_LS_x": {"none": 0.25, "left": 0.125, "right": 0.625},
            "stick_LS_y": {"none": 0.375, "up": 0.375, "down": 0.25},
            "stick_RS_x": {"none": 0.0, "left": 0.75, "right": 0.25},
        },
        stick_LS_x_at_least_50=0.5,
        advisor_strategy=0.875,
        advisor_tips=0.6,
    )
    return decision.probabilities, decision.choices


def test_adopted_answers_of_each_kind():
    probabilities, choices = pressed_answers()
    adopted = adopted_answers(build_questions(), probabilities, choices, 0.6)
    assert adopted is not None
    assert list(adopted) == QUESTION_NAMES
    expected = {name: None for name in QUESTION_NAMES}
    expected.update(
        {
            # 押下と短押し
            "button_A": "hold",
            "button_B": "tap",
            # UP と DOWN はどちらも押す側が none を上回るが、押す側の合計の低い DOWN を採らない
            "button_UP": "hold",
            "button_DOWN": "rejected",
            # スティックの方向。LS_y は none と up が同点なので倒さない
            "stick_LS_x": "right",
            "stick_RS_x": "left",
            # しきい値 0.6 を超えた strategy だけを求める (tips はしきい値ちょうど)
            "advisor_strategy": "requested",
        }
    )
    assert adopted == expected
    assert adopted["stick_LS_x_at_least_50"] is None
    assert adopted_count(adopted) == 6


def test_opposite_dpad_with_equal_press_side_rejects_both():
    probabilities, choices = neutral_answers()
    choices["button_LEFT"] = {"none": 0.25, "hold": 0.0, "tap": 0.75}
    choices["button_RIGHT"] = {"none": 0.25, "hold": 0.75, "tap": 0.0}
    adopted = adopted_answers(build_questions(), probabilities, choices, 0.5)
    assert adopted is not None
    assert adopted["button_LEFT"] == "rejected"
    assert adopted["button_RIGHT"] == "rejected"
    assert adopted_count(adopted) == 0


def test_stick_direction_tie_between_directions_is_none():
    probabilities, choices = neutral_answers()
    choices["stick_RS_y"] = {"none": 0.0, "up": 0.5, "down": 0.5}
    adopted = adopted_answers(build_questions(), probabilities, choices, 0.5)
    assert adopted is not None
    assert adopted["stick_RS_y"] is None


def test_adopted_answers_is_none_for_missing_probability_answer():
    probabilities, choices = neutral_answers()
    del probabilities["advisor_tips"]
    assert adopted_answers(build_questions(), probabilities, choices, 0.5) is None


def test_adopted_answers_is_none_for_missing_choice():
    probabilities, choices = neutral_answers()
    del choices["button_HOME"]["tap"]
    assert adopted_answers(build_questions(), probabilities, choices, 0.5) is None


@pytest.mark.parametrize(
    "choice_changes, probability_changes",
    [
        # 答えの値が文字列
        ({"button_A": {"none": "0.5", "hold": 0.25, "tap": 0.25}}, {}),
        ({}, {"advisor_tips": "0.75"}),
        # 答えの値が list
        ({"button_UP": {"none": [0.0], "hold": [1.0], "tap": [0.0]}}, {}),
        ({}, {"advisor_strategy": [0.75]}),
        # 答えの値が null
        ({"stick_RS_y": {"none": None, "up": 0.5, "down": 0.5}}, {}),
        # 選択肢の問いの答えが object でない
        ({"stick_LS_x": [0.0, 1.0, 0.0]}, {}),
        ({"button_B": "none hold tap"}, {}),
    ],
)
def test_adopted_answers_is_none_for_answers_of_other_types(
    choice_changes, probability_changes
):
    probabilities, choices = neutral_answers()
    choices.update(choice_changes)
    probabilities.update(probability_changes)
    assert adopted_answers(build_questions(), probabilities, choices, 0.5) is None
    request = {"probabilities": probabilities, "choices": choices}
    assert request_adopted(build_questions(), request, 0.5) is None


def test_adopted_answers_is_none_when_header_choices_differ_from_chapter_4():
    # header のボタンの問いの選択肢が空 (hold と tap が無い)。check_answers は通るが、adopt_buttons が
    # hold の確率を読めない
    header = current_header()
    for item in header["questions"]:
        if item["name"] == "button_A":
            item["choices"] = []
    questions = questions_for_decision(header["questions"])
    probabilities, choices = neutral_answers()
    choices["button_A"] = {}
    assert adopted_answers(questions, probabilities, choices, 0.5) is None


def test_names_that_cannot_be_keys_are_treated_as_missing():
    list_name_item = {**BUTTON_A_ITEM, "name": ["button_A"]}
    list_choice_item = {
        **BUTTON_A_ITEM,
        "choices": [{"name": ["hold"], "description": "押した状態にする"}],
    }
    choices = {"button_A": {"none": 0.0, "hold": 1.0, "tap": 0.0}}
    assert question_value(list_name_item, {}, choices) is None
    assert question_value(list_choice_item, {}, choices) is None
    assert questions_for_decision([list_name_item]) == []
    # name が list の問いを足しても、advisor の役割ごとの 4 問はそろっている
    header = current_header()
    header["questions"].append({**ADVISOR_STRATEGY_ITEM, "name": ["advisor_x"]})
    assert is_old_format(header) is False


def test_adopted_count():
    assert adopted_count(None) is None
    assert adopted_count({}) == 0
    assert adopted_count({"a": "rejected", "b": None}) == 0
    assert (
        adopted_count(
            {
                "a": "hold",
                "b": "tap",
                "c": "left",
                "d": "requested",
                "e": "rejected",
                "f": None,
            }
        )
        == 4
    )


# --- request_adopted ---


def test_request_adopted_is_none_without_response():
    request = {"probabilities": None, "choices": None}
    assert request_adopted(build_questions(), request, 0.5) is None


def test_request_adopted_uses_answers_of_request():
    probabilities, choices = pressed_answers()
    request = {"probabilities": probabilities, "choices": choices}
    assert request_adopted(build_questions(), request, 0.6) == adopted_answers(
        build_questions(), probabilities, choices, 0.6
    )
    assert request_adopted(build_questions(), request, 0.6)["button_DOWN"] == "rejected"


def test_request_adopted_with_only_one_object_is_none_for_missing_answers():
    probabilities, _ = neutral_answers()
    request = {"probabilities": probabilities, "choices": None}
    assert request_adopted(build_questions(), request, 0.5) is None


# --- 第4章の decide_action との一致 ---

# 確率の候補。同じ値になりやすいよう少ない値から選び、同点・しきい値ちょうどの場合を多く含める
RANDOM_VALUES = (0.0, 0.125, 0.25, 0.5, 0.75, 1.0)


def random_answers(rng: random.Random):
    probabilities: dict[str, float] = {}
    choices: dict[str, dict[str, float]] = {}
    for question in build_questions():
        if question.answer_type == "choice":
            choices[question.name] = {
                choice.name: rng.choice(RANDOM_VALUES) for choice in question.choices
            }
        else:
            probabilities[question.name] = rng.choice(RANDOM_VALUES)
    return probabilities, choices


def adopted_targets(
    questions: list[Question], adopted: dict, kind: str, values: set[str]
) -> set[str]:
    """kind の問いのうち、adopted の値が values のどれかの問いの target の集合を返す。"""
    return {
        question.target
        for question in questions
        if question.kind == kind and adopted[question.name] in values
    }


def test_adopted_answers_match_decide_action_for_random_answers():
    rng = random.Random(20261114)
    questions = build_questions()
    for _ in range(50):
        probabilities, choices = random_answers(rng)
        threshold = rng.choice((0.25, 0.5, 0.75))
        action = decide_action(probabilities, choices, questions, Thresholds(threshold))
        adopted = adopted_answers(questions, probabilities, choices, threshold)
        assert adopted is not None
        held = adopted_targets(questions, adopted, "button", {"hold"})
        tapped = adopted_targets(questions, adopted, "button", {"tap"})
        axes = adopted_targets(
            questions, adopted, "stick_direction", {"left", "right", "up", "down"}
        )
        roles = adopted_targets(questions, adopted, "advisor", {"requested"})
        assert held == action.held.buttons
        assert tapped == action.taps
        assert axes == {
            f"{stick}_{axis}"
            for stick, (x, y) in action.held.sticks.items()
            for axis, value in (("x", x), ("y", y))
            if value != 0.0
        }
        assert roles == action.advisor_requested
        assert adopted_count(adopted) == len(held) + len(tapped) + len(axes) + len(
            roles
        )
