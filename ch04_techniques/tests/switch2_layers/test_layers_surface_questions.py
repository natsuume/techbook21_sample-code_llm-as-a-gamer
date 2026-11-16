import re

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.buttons import allowed_buttons
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.safety.safety_rules import SAFETY_RULES
from switch2_layers.surface.box_targets import DEFAULT_BOX_TARGETS, BoxTarget
from switch2_layers.surface.surface_questions import (
    ADVISOR_KINDS,
    ADVISOR_QUESTION_TEXTS,
    BUTTON_CHOICES,
    HOLD_LONG_CHOICE,
    HOLD_LONG_SECONDS,
    HOLD_SHORT_CHOICE,
    HOLD_SHORT_SECONDS,
    INFO_LABEL_NAMES,
    NONE_CHOICE,
    SCENE_CHANGE_QUESTION_NAME,
    STICK_DIRECTION_CHOICES,
    SURFACE_INSTRUCTIONS,
    TAP_CHOICE,
    QuestionKind,
    QuestionType,
    box_exists_question_name,
    box_question_name,
    build_advisor_questions,
    build_box_exists_questions,
    build_box_questions,
    build_button_questions,
    build_info_label_questions,
    build_scene_change_question,
    build_stick_direction_questions,
    build_stick_strength_questions,
    build_surface_questions,
    latest_frame_path,
)

ID_PATTERN = re.compile(r"[A-Za-z0-9_]+")
TARGETS = (
    BoxTarget("自機", "赤い帽子の人"),
    BoxTarget("敵", "茶色のきのこ"),
    BoxTarget("目標地点", "旗"),
)


# --- 定数と説明の文 ---


def test_question_lists_follow_the_issue():
    assert ADVISOR_KINDS == (
        DocumentKind.CONTROLS,
        DocumentKind.TIPS,
        DocumentKind.STRATEGY,
        DocumentKind.ADVICE,
    )
    assert DocumentKind.MAP not in ADVISOR_KINDS
    assert set(ADVISOR_QUESTION_TEXTS) == set(ADVISOR_KINDS)
    assert INFO_LABEL_NAMES == ("controls", "tips", "strategy", "stuck", "map")


def test_choices_put_none_first():
    assert [choice.name for choice in BUTTON_CHOICES] == [
        "none",
        "tap",
        "hold_short",
        "hold_long",
    ]
    # スティックの向きは none・負の向き・正の向き
    assert [choice.name for choice in STICK_DIRECTION_CHOICES["x"]] == [
        "none",
        "left",
        "right",
    ]
    assert [choice.name for choice in STICK_DIRECTION_CHOICES["y"]] == [
        "none",
        "down",
        "up",
    ]


def test_button_choice_names_and_hold_seconds_are_constants():
    assert (TAP_CHOICE, HOLD_SHORT_CHOICE, HOLD_LONG_CHOICE) == (
        "tap",
        "hold_short",
        "hold_long",
    )
    assert HOLD_SHORT_SECONDS == 0.3
    assert HOLD_LONG_SECONDS == 1.0


def test_hold_choices_describe_seconds_and_that_holding_again_continues():
    descriptions = {choice.name: choice.description for choice in BUTTON_CHOICES}
    assert "0.3 秒" in descriptions["hold_short"]
    assert "1.0 秒" in descriptions["hold_long"]
    # 次の判断でも同じボタンの押し続けを選ぶと、離さずに続くこと
    for name in ["hold_short", "hold_long"]:
        assert "離さず" in descriptions[name]


def test_instructions_say_that_action_and_other_questions_are_independent():
    assert "独立" in SURFACE_INSTRUCTIONS
    for key in ["`frames`", "`documents`", "`timeline`", "`recent_actions`"]:
        assert key in SURFACE_INSTRUCTIONS


def test_instructions_include_the_safety_rules():
    # 安全のための規則を、要求のたびに state の instructions で渡す
    assert SAFETY_RULES in SURFACE_INSTRUCTIONS


def test_latest_frame_path():
    assert latest_frame_path(5) == "frames[4].image"
    assert latest_frame_path(1) == "frames[0].image"
    with pytest.raises(ValueError):
        latest_frame_path(0)


def test_box_question_names():
    assert box_exists_question_name(1) == "box_exists_1"
    assert box_question_name(3) == "box_3"
    with pytest.raises(ValueError):
        box_question_name(0)
    with pytest.raises(ValueError):
        box_exists_question_name(0)


# --- 操作の問い ---


def test_button_questions_are_only_for_allowed_buttons_in_button_order():
    questions = build_button_questions(allowed_buttons(allow_home=False))
    assert [question.target for question in questions] == [
        button for button in BUTTON_ORDER if button != "HOME"
    ]
    assert len(questions) == 16
    first = questions[0]
    assert first.name == "button_A"
    assert first.instructions == "今、A ボタンをどうするか。"
    assert first.kind is QuestionKind.BUTTON
    assert first.question_type is QuestionType.CHOICE
    assert first.choices == BUTTON_CHOICES


def test_home_question_only_with_allow_home():
    without = build_button_questions(allowed_buttons(allow_home=False))
    with_home = build_button_questions(allowed_buttons(allow_home=True))
    assert "button_HOME" not in [question.name for question in without]
    assert "button_HOME" in [question.name for question in with_home]
    assert len(with_home) == 17


def test_button_questions_ignore_unknown_names():
    questions = build_button_questions({"A", "TURBO"})
    assert [question.name for question in questions] == ["button_A"]


def test_stick_direction_questions():
    questions = build_stick_direction_questions()
    assert [question.name for question in questions] == [
        "stick_LS_x",
        "stick_LS_y",
        "stick_RS_x",
        "stick_RS_y",
    ]
    assert [question.target for question in questions] == [
        "LS_x",
        "LS_y",
        "RS_x",
        "RS_y",
    ]
    assert questions[0].instructions == "今、左スティックを左右に倒すか。"
    assert questions[3].instructions == "今、右スティックを上下に倒すか。"
    assert all(q.question_type is QuestionType.CHOICE for q in questions)
    assert questions[0].choices == STICK_DIRECTION_CHOICES["x"]
    assert questions[1].choices == STICK_DIRECTION_CHOICES["y"]


def test_stick_strength_questions():
    questions = build_stick_strength_questions()
    assert len(questions) == 12
    assert [question.name for question in questions[:3]] == [
        "stick_LS_x_at_least_50",
        "stick_LS_x_at_least_75",
        "stick_LS_x_at_least_100",
    ]
    assert questions[0].target == "LS_x_50"
    assert questions[0].instructions == (
        "左スティックを左右に倒す場合、傾きを 50% 以上にするべきか。"
    )
    assert questions[2].instructions == (
        "左スティックを左右に倒す場合、傾きをいっぱい (100%) にするべきか。"
    )
    assert questions[-1].name == "stick_RS_y_at_least_100"
    assert all(q.question_type is QuestionType.NOUL for q in questions)
    assert all(q.choices == () for q in questions)


# --- ほかの問い ---


def test_advisor_questions_are_the_four_documents():
    questions = build_advisor_questions()
    assert [question.name for question in questions] == [
        "advisor_controls",
        "advisor_tips",
        "advisor_strategy",
        "advisor_advice",
    ]
    assert [question.target for question in questions] == [
        "controls",
        "tips",
        "strategy",
        "advice",
    ]
    for question, kind in zip(questions, ADVISOR_KINDS, strict=True):
        assert question.instructions == ADVISOR_QUESTION_TEXTS[kind]
        assert question.kind is QuestionKind.ADVISOR
        assert question.question_type is QuestionType.NOUL


def test_info_label_questions_refer_to_latest_frame_by_path():
    questions = build_info_label_questions("frames[2].image")
    assert [question.name for question in questions] == [
        "info_controls",
        "info_tips",
        "info_strategy",
        "info_stuck",
        "info_map",
    ]
    assert [question.target for question in questions] == list(INFO_LABEL_NAMES)
    for question in questions:
        assert "`frames[2].image`" in question.instructions
        assert "{frame}" not in question.instructions
        assert question.kind is QuestionKind.INFO_LABEL
        assert question.question_type is QuestionType.NOUL


def test_scene_change_question():
    question = build_scene_change_question()
    assert question.name == SCENE_CHANGE_QUESTION_NAME == "scene_changed"
    assert question.target == ""
    assert question.kind is QuestionKind.SCENE_CHANGE
    assert question.question_type is QuestionType.NOUL
    assert "`frames`" in question.instructions


def test_box_exists_questions_for_every_target():
    questions = build_box_exists_questions(TARGETS, "frames[4].image")
    assert [question.name for question in questions] == [
        "box_exists_1",
        "box_exists_2",
        "box_exists_3",
    ]
    assert [question.target for question in questions] == ["自機", "敵", "目標地点"]
    assert questions[1].instructions == (
        "`frames[4].image` に「敵」(茶色のきのこ) が写っているか。"
    )
    assert all(q.question_type is QuestionType.NOUL for q in questions)
    assert all(q.kind is QuestionKind.BOX_EXISTS for q in questions)


def test_box_questions_only_for_requery_targets_with_the_same_numbers():
    questions = build_box_questions(
        TARGETS, {"目標地点", "自機", "いない対象"}, "frames[0].image"
    )
    assert [question.name for question in questions] == ["box_1", "box_3"]
    assert [question.target for question in questions] == ["自機", "目標地点"]
    assert (
        questions[1].instructions
        == "`frames[0].image` の中の「目標地点」(旗) を囲む box。"
    )
    assert all(q.question_type is QuestionType.BOX for q in questions)
    assert all(q.kind is QuestionKind.BOX for q in questions)


# --- 1 回の要求の問いの一覧 ---


def test_surface_questions_are_in_the_order_of_the_table():
    questions = build_surface_questions(
        allowed_buttons(allow_home=False), TARGETS, {"敵"}, frame_count=5
    )
    kinds = [question.kind for question in questions]
    expected = (
        [QuestionKind.BUTTON] * 16
        + [QuestionKind.STICK_DIRECTION] * 4
        + [QuestionKind.STICK_STRENGTH] * 12
        + [QuestionKind.ADVISOR] * 4
        + [QuestionKind.INFO_LABEL] * 5
        + [QuestionKind.SCENE_CHANGE]
        + [QuestionKind.BOX_EXISTS] * 3
        + [QuestionKind.BOX]
    )
    assert kinds == expected
    assert questions[-1].name == "box_2"
    # 最新のフレームを指す文は、5 枚の要求なら frames[4].image
    assert "`frames[4].image`" in questions[-1].instructions


def test_surface_question_ids_are_unique_and_safe():
    questions = build_surface_questions(
        allowed_buttons(allow_home=True),
        DEFAULT_BOX_TARGETS,
        {target.name for target in DEFAULT_BOX_TARGETS},
        frame_count=3,
    )
    ids = [question.name for question in questions]
    assert len(ids) == len(set(ids))
    assert all(ID_PATTERN.fullmatch(name) for name in ids)
    for question in questions:
        if question.question_type is QuestionType.CHOICE:
            assert question.choices[0].name == NONE_CHOICE
        else:
            assert question.choices == ()


def test_surface_questions_without_requery_have_no_box_question():
    questions = build_surface_questions(
        allowed_buttons(allow_home=False), TARGETS, set(), frame_count=1
    )
    assert not any(q.question_type is QuestionType.BOX for q in questions)
    assert sum(q.kind is QuestionKind.BOX_EXISTS for q in questions) == 3
