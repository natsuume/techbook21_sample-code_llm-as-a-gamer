import re

from switch2_layers.safety.safety_questions import (
    ALLOW_HOME_EXEMPT_NAMES,
    FRAME_STATE_KEY,
    SAFETY_INSTRUCTIONS,
    SAFETY_QUESTION_NAMES,
    SAFETY_QUESTION_TEXTS,
    build_safety_questions,
    build_safety_state,
)
from switch2_layers.surface.surface_questions import QuestionKind, QuestionType

ID_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def test_question_names_follow_the_issue():
    assert SAFETY_QUESTION_NAMES == (
        "home_menu",
        "user_select",
        "online",
        "purchase",
        "system_settings",
    )
    assert all(ID_PATTERN.fullmatch(name) for name in SAFETY_QUESTION_NAMES)
    assert set(SAFETY_QUESTION_TEXTS) == set(SAFETY_QUESTION_NAMES)


def test_allow_home_exempts_only_home_menu_and_user_select():
    assert ALLOW_HOME_EXEMPT_NAMES == frozenset({"home_menu", "user_select"})


def test_questions_are_nouls_in_order():
    questions = build_safety_questions()
    assert [question.name for question in questions] == list(SAFETY_QUESTION_NAMES)
    for question in questions:
        assert question.question_type is QuestionType.NOUL
        assert question.kind is QuestionKind.SAFETY
        assert question.target == question.name
        assert question.choices == ()
        assert question.instructions == SAFETY_QUESTION_TEXTS[question.name]


def test_question_texts_point_to_the_frame_by_state_path():
    # 画像の参照文字列ではなく、state の中のパスで画像を指す (参照文字列は書くたびに同じ画像が足されるため)
    for text in SAFETY_QUESTION_TEXTS.values():
        assert f"`{FRAME_STATE_KEY}`" in text
    assert f"`{FRAME_STATE_KEY}`" in SAFETY_INSTRUCTIONS


def test_question_texts_cover_the_screens_of_the_issue():
    texts = SAFETY_QUESTION_TEXTS
    assert "HOME メニュー" in texts["home_menu"]
    assert "ユーザー" in texts["user_select"]
    for phrase in ["ロビー", "対戦", "マッチング", "ほかのプレイヤー", "通報", "投稿"]:
        assert phrase in texts["online"], phrase
    for phrase in ["ショップ", "購入", "課金"]:
        assert phrase in texts["purchase"], phrase
    for phrase in ["本体の設定", "セーブデータの削除"]:
        assert phrase in texts["system_settings"], phrase


def test_state_has_instructions_and_one_image_reference():
    state = build_safety_state("ref-1")
    assert list(state) == ["instructions", FRAME_STATE_KEY]
    assert state["instructions"] == SAFETY_INSTRUCTIONS
    assert state[FRAME_STATE_KEY] == "ref-1"
