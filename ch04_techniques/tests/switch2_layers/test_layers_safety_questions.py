import re

import pytest

import switch2_layers.safety.safety_questions as safety_questions_module
from switch2_layers.safety.safety_questions import (
    FRAME_STATE_KEY,
    REFERENCES_STATE_KEY,
    SAFETY_INSTRUCTIONS,
    SAFETY_QUESTION_NAMES,
    SAFETY_QUESTION_TEXTS,
    SYSTEM_SCREEN_QUESTION_NAME,
    SYSTEM_SCREEN_QUESTION_TEXT,
    build_safety_questions,
    build_safety_state,
)
from switch2_layers.surface.surface_questions import QuestionKind, QuestionType
from switch2_layers.system_screen.system_screen_references import (
    NONE_CHOICE,
    NONE_CHOICE_DESCRIPTION,
    SYSTEM_SCREEN_CHOICE_NAMES,
    SYSTEM_SCREEN_NAMES,
    SYSTEM_SCREEN_REFERENCES,
)

ID_PATTERN = re.compile(r"[A-Za-z0-9_]+")
REFERENCES = {name: f"ref-{name}" for name in SYSTEM_SCREEN_NAMES}


# --- 問いの名前 ---


def test_question_names_follow_the_issue():
    assert SAFETY_QUESTION_NAMES == ("online", "purchase", "system_settings")
    assert SYSTEM_SCREEN_QUESTION_NAME == "system_screen"
    names = [*SAFETY_QUESTION_NAMES, SYSTEM_SCREEN_QUESTION_NAME]
    assert all(ID_PATTERN.fullmatch(name) for name in names)
    assert set(SAFETY_QUESTION_TEXTS) == set(SAFETY_QUESTION_NAMES)


def test_home_menu_and_user_select_are_not_safety_questions():
    # HOME メニューとユーザー選択は、止める問いではなく照合の選択肢で見分ける
    assert "home_menu" not in SAFETY_QUESTION_NAMES
    assert "user_select" not in SAFETY_QUESTION_NAMES
    assert not hasattr(safety_questions_module, "ALLOW_HOME_EXEMPT_NAMES")


# --- 問い ---


def test_questions_are_three_nouls_then_the_system_screen_choice():
    questions = build_safety_questions()
    assert [question.name for question in questions] == [
        "online",
        "purchase",
        "system_settings",
        "system_screen",
    ]
    for question in questions[:3]:
        assert question.question_type is QuestionType.NOUL
        assert question.kind is QuestionKind.SAFETY
        assert question.target == question.name
        assert question.choices == ()
        assert question.instructions == SAFETY_QUESTION_TEXTS[question.name]


def test_system_screen_question_is_a_choice_of_none_and_the_references():
    system_screen = build_safety_questions()[-1]
    assert system_screen.name == SYSTEM_SCREEN_QUESTION_NAME
    assert system_screen.target == SYSTEM_SCREEN_QUESTION_NAME
    assert system_screen.kind is QuestionKind.SAFETY
    assert system_screen.question_type is QuestionType.CHOICE
    assert system_screen.instructions == SYSTEM_SCREEN_QUESTION_TEXT
    assert [choice.name for choice in system_screen.choices] == list(
        SYSTEM_SCREEN_CHOICE_NAMES
    )
    # none を先頭に置き、見本の名前を見本の表の順に並べる
    assert system_screen.choices[0].name == NONE_CHOICE
    assert system_screen.choices[0].description == NONE_CHOICE_DESCRIPTION
    for choice, reference in zip(
        system_screen.choices[1:], SYSTEM_SCREEN_REFERENCES, strict=True
    ):
        assert choice.name == reference.name
        assert choice.description == reference.description


def test_question_texts_point_to_the_images_by_state_path():
    # 画像の参照文字列ではなく、state の中のパスで画像を指す (参照文字列は書くたびに同じ画像が足されるため)
    for text in SAFETY_QUESTION_TEXTS.values():
        assert f"`{FRAME_STATE_KEY}`" in text
    assert f"`{FRAME_STATE_KEY}`" in SYSTEM_SCREEN_QUESTION_TEXT
    assert f"`{REFERENCES_STATE_KEY}`" in SYSTEM_SCREEN_QUESTION_TEXT
    for reference in SYSTEM_SCREEN_REFERENCES:
        assert f"`{REFERENCES_STATE_KEY}.{reference.name}`" in reference.description


def test_instructions_explain_the_frame_and_the_references():
    assert f"`{FRAME_STATE_KEY}`" in SAFETY_INSTRUCTIONS
    assert f"`{REFERENCES_STATE_KEY}`" in SAFETY_INSTRUCTIONS
    # references は、本体の画面の一部を切り出した見本であることを書く
    assert "本体の画面の見本" in SAFETY_INSTRUCTIONS
    assert "本体の画面の一部を切り出したもの" in SAFETY_INSTRUCTIONS


def test_question_texts_cover_the_screens_of_the_issue():
    texts = SAFETY_QUESTION_TEXTS
    for phrase in ["ロビー", "対戦", "マッチング", "ほかのプレイヤー", "通報", "投稿"]:
        assert phrase in texts["online"], phrase
    for phrase in ["ショップ", "購入", "課金"]:
        assert phrase in texts["purchase"], phrase
    for phrase in ["本体の設定", "セーブデータの削除"]:
        assert phrase in texts["system_settings"], phrase


# --- state ---


def test_state_has_instructions_frame_and_references_in_order():
    state = build_safety_state("ref-frame", REFERENCES)
    assert list(state) == ["instructions", FRAME_STATE_KEY, REFERENCES_STATE_KEY]
    assert state["instructions"] == SAFETY_INSTRUCTIONS
    assert state[FRAME_STATE_KEY] == "ref-frame"
    assert state[REFERENCES_STATE_KEY] == REFERENCES
    assert list(state[REFERENCES_STATE_KEY]) == list(SYSTEM_SCREEN_NAMES)


def test_state_orders_references_by_the_reference_table():
    state = build_safety_state("ref-frame", dict(reversed(REFERENCES.items())))
    assert list(state[REFERENCES_STATE_KEY]) == list(SYSTEM_SCREEN_NAMES)


def test_state_has_each_image_reference_once():
    state = build_safety_state("ref-frame", REFERENCES)
    values = [state[FRAME_STATE_KEY], *state[REFERENCES_STATE_KEY].values()]
    assert sorted(values) == sorted(["ref-frame", *REFERENCES.values()])


@pytest.mark.parametrize(
    "references",
    [
        {name: ref for name, ref in REFERENCES.items() if name != "software_close"},
        {**REFERENCES, "unknown": "ref-unknown"},
        {},
    ],
    ids=["missing_name", "unknown_name", "empty"],
)
def test_state_with_other_reference_names_is_an_error(references):
    with pytest.raises(ValueError):
        build_safety_state("ref-frame", references)
