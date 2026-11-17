import re

import pytest
from switch2_remote.controller_state import BUTTON_ORDER

import switch2_layers.surface.surface_questions as surface_questions_module
from switch2_layers.buttons import ALL_BUTTONS, allowed_buttons
from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.safety.safety_rules import SAFETY_RULES
from switch2_layers.surface.box_targets import DEFAULT_BOX_TARGETS, BoxTarget
from switch2_layers.surface.surface_questions import (
    ADVISOR_KINDS,
    ADVISOR_QUESTION_TEXTS,
    ADVISOR_STATUS_RULE_TEXT,
    BUTTON_CHOICES,
    DPAD_DIRECTION_BUTTONS,
    DPAD_DIRECTION_CHOICES,
    DPAD_DIRECTION_QUESTION_NAME,
    DPAD_PRESS_CHOICES,
    DPAD_PRESS_QUESTION_NAME,
    DPAD_TARGET,
    HOLD_CHOICE,
    HOLD_CHOICE_DESCRIPTION,
    INFO_LABEL_NAMES,
    NONE_CHOICE,
    PRESS_CHOICES,
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
    build_dpad_questions,
    build_info_label_questions,
    build_scene_change_question,
    build_stick_direction_questions,
    build_stick_strength_questions,
    build_surface_questions,
    latest_frame_path,
)

ID_PATTERN = re.compile(r"[A-Za-z0-9_]+")
# 十字キーの 4 方向のボタン名
DPAD_BUTTONS = {"UP", "RIGHT", "DOWN", "LEFT"}
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
        DocumentKind.GAME,
    )
    assert DocumentKind.MAP not in ADVISOR_KINDS
    assert set(ADVISOR_QUESTION_TEXTS) == set(ADVISOR_KINDS)
    assert INFO_LABEL_NAMES == ("controls", "tips", "strategy", "stuck", "map")


def test_choices_put_none_first():
    assert [choice.name for choice in BUTTON_CHOICES] == ["none", "tap", "hold"]
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


def test_button_choice_names_are_constants():
    assert (NONE_CHOICE, TAP_CHOICE, HOLD_CHOICE) == ("none", "tap", "hold")
    assert PRESS_CHOICES == ("tap", "hold")


def test_button_choices_describe_tap_and_hold():
    assert [(choice.name, choice.description) for choice in BUTTON_CHOICES] == [
        ("none", "押さない"),
        ("tap", "1 回だけ短く押す"),
        ("hold", HOLD_CHOICE_DESCRIPTION),
    ]


def test_hold_description_says_it_lasts_until_the_next_decision_and_continues():
    assert HOLD_CHOICE_DESCRIPTION == (
        "押した状態にする。押した状態は次の判断まで続く。"
        "次の判断でも hold を選ぶと、離さずに押し続ける"
    )
    # 押し続けの長さは秒数ではなく、次の判断までで決まる
    assert "秒" not in HOLD_CHOICE_DESCRIPTION


@pytest.mark.parametrize(
    "name",
    [
        "HOLD_SHORT_CHOICE",
        "HOLD_LONG_CHOICE",
        "HOLD_SHORT_SECONDS",
        "HOLD_LONG_SECONDS",
        "HOLD_CHOICE_SECONDS",
    ],
)
def test_timed_hold_names_are_removed(name):
    assert not hasattr(surface_questions_module, name)


def test_dpad_direction_choices_follow_the_issue():
    assert [(choice.name, choice.description) for choice in DPAD_DIRECTION_CHOICES] == [
        ("none", "十字キーを押さない"),
        ("up", "上を押す"),
        ("right", "右を押す"),
        ("down", "下を押す"),
        ("left", "左を押す"),
        ("up_right", "右上 (上と右を同時に押す)"),
        ("down_right", "右下 (下と右を同時に押す)"),
        ("down_left", "左下 (下と左を同時に押す)"),
        ("up_left", "左上 (上と左を同時に押す)"),
    ]


def test_dpad_press_choices_are_tap_and_hold_without_none():
    assert [(choice.name, choice.description) for choice in DPAD_PRESS_CHOICES] == [
        ("tap", "1 回だけ短く押す"),
        ("hold", HOLD_CHOICE_DESCRIPTION),
    ]


def test_dpad_direction_buttons_follow_the_issue():
    assert DPAD_DIRECTION_BUTTONS == {
        "none": (),
        "up": ("UP",),
        "right": ("RIGHT",),
        "down": ("DOWN",),
        "left": ("LEFT",),
        "up_right": ("UP", "RIGHT"),
        "down_right": ("DOWN", "RIGHT"),
        "down_left": ("DOWN", "LEFT"),
        "up_left": ("UP", "LEFT"),
    }
    # 並びは方向の問いの選択肢と同じ
    assert list(DPAD_DIRECTION_BUTTONS) == [
        choice.name for choice in DPAD_DIRECTION_CHOICES
    ]


def test_instructions_say_that_action_and_other_questions_are_independent():
    assert "独立" in SURFACE_INSTRUCTIONS
    for key in ["`frames`", "`documents`", "`timeline`", "`recent_actions`"]:
        assert key in SURFACE_INSTRUCTIONS


def test_instructions_include_the_safety_rules():
    # 安全のための規則を、要求のたびに state の instructions で渡す
    assert SAFETY_RULES in SURFACE_INSTRUCTIONS


def test_instructions_explain_progress_state():
    # state のほかのキーと同じく、progress の意味も説明する
    assert "`progress`" in SURFACE_INSTRUCTIONS
    progress_part = SURFACE_INSTRUCTIONS[SURFACE_INSTRUCTIONS.index("`progress`") :]
    progress_part = progress_part[: progress_part.index("\n")]
    assert "画面が変わっていない" in progress_part
    assert "新しい画面が出ていない" in progress_part


def test_instructions_explain_advisor_as_deep_status_per_document_kind():
    assert "`advisor`" in SURFACE_INSTRUCTIONS
    advisor_part = SURFACE_INSTRUCTIONS[SURFACE_INSTRUCTIONS.index("`advisor`") :]
    assert "deep" in advisor_part
    assert "状況" in advisor_part


def test_instructions_list_the_five_parts_of_advisor_status_in_order():
    # advisor の説明 (`advisor` で始まる行) に、状況の文の 5 つの部分を、文の部分と同じ順に書く
    advisor_line = SURFACE_INSTRUCTIONS[SURFACE_INSTRUCTIONS.index("`advisor`") :]
    advisor_line = advisor_line[: advisor_line.index("\n")]
    parts = [
        "実行中のジョブ",
        "保留中の要求",
        "前回の終了",
        "今求めたときの扱い",
        "surface が最後に求めた結果",
    ]
    for part in parts:
        assert part in advisor_line, part
    positions = [advisor_line.index(part) for part in parts]
    assert positions == sorted(positions)


def advisor_rule(kind_value: str) -> str:
    """種類の値 kind_value の advisor 判定の問いの文の末尾に足す共通の文を、定数を使わずに書いたもの。"""
    return (
        f"ただし、`advisor.{kind_value}` に保留中の要求があるとき、"
        "または今求めると捨てられると書かれているときは偽にする。"
    )


def test_advisor_status_rule_text():
    assert ADVISOR_STATUS_RULE_TEXT.format(kind="tips") == advisor_rule("tips")


def test_instructions_describe_the_game_document():
    # documents の説明に、ほかの文書と同じ「名前 (キー)」の形でゲームの性質を足す
    for phrase in [
        "短期タスクリスト (tasks)",
        "攻略情報 (tips)",
        "操作説明 (controls)",
        "詰まったときの助言 (advice)",
        "ゲームの性質 (game)",
    ]:
        assert phrase in SURFACE_INSTRUCTIONS, phrase


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
    questions = build_button_questions(allowed_buttons())
    assert [question.target for question in questions] == [
        button for button in BUTTON_ORDER if button not in DPAD_BUTTONS | {"HOME"}
    ]
    # 十字キーの 4 方向は、ボタンの問いではなく十字キーの 2 問で問う
    assert len(questions) == 12
    first = questions[0]
    assert first.name == "button_A"
    assert first.instructions == "今、A ボタンをどうするか。"
    assert first.kind is QuestionKind.BUTTON
    assert first.question_type is QuestionType.CHOICE
    assert first.choices == BUTTON_CHOICES


def test_home_question_only_when_home_is_allowed():
    # run の surface の押せるボタン (allowed_buttons) には HOME が無いので、HOME の問いは無い
    without = build_button_questions(allowed_buttons())
    with_home = build_button_questions(ALL_BUTTONS)
    assert "button_HOME" not in [question.name for question in without]
    assert "button_HOME" in [question.name for question in with_home]
    assert len(with_home) == 13


def test_button_questions_ignore_unknown_names():
    questions = build_button_questions({"A", "TURBO"})
    assert [question.name for question in questions] == ["button_A"]


def test_button_questions_never_ask_about_dpad_directions():
    questions = build_button_questions({"A", "UP", "RIGHT", "DOWN", "LEFT"})
    assert [question.name for question in questions] == ["button_A"]


def test_dpad_questions():
    questions = build_dpad_questions()
    assert [question.name for question in questions] == [
        DPAD_DIRECTION_QUESTION_NAME,
        DPAD_PRESS_QUESTION_NAME,
    ]
    assert (DPAD_DIRECTION_QUESTION_NAME, DPAD_PRESS_QUESTION_NAME) == (
        "dpad_direction",
        "dpad_press",
    )
    direction, press = questions
    assert direction.kind is QuestionKind.DPAD_DIRECTION
    assert press.kind is QuestionKind.DPAD_PRESS
    assert (QuestionKind.DPAD_DIRECTION, QuestionKind.DPAD_PRESS) == (
        "dpad_direction",
        "dpad_press",
    )
    assert DPAD_TARGET == "dpad"
    assert direction.target == press.target == "dpad"
    assert direction.question_type is QuestionType.CHOICE
    assert press.question_type is QuestionType.CHOICE
    assert direction.choices == DPAD_DIRECTION_CHOICES
    assert press.choices == DPAD_PRESS_CHOICES
    assert direction.instructions == (
        "今、十字キーをどの方向に押すか。斜めは 2 つの方向を同時に押す。"
    )
    assert press.instructions == "十字キーを押す場合、短く押すか、押し続けるか。"


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


def test_advisor_questions_are_the_five_documents():
    questions = build_advisor_questions()
    assert [question.name for question in questions] == [
        "advisor_controls",
        "advisor_tips",
        "advisor_strategy",
        "advisor_advice",
        "advisor_game",
    ]
    assert [question.target for question in questions] == [
        "controls",
        "tips",
        "strategy",
        "advice",
        "game",
    ]
    for question, kind in zip(questions, ADVISOR_KINDS, strict=True):
        # 種類ごとの文の末尾に、間を空けずに共通の文を足す
        assert question.instructions == (
            ADVISOR_QUESTION_TEXTS[kind] + advisor_rule(kind.value)
        )
        assert question.kind is QuestionKind.ADVISOR
        assert question.question_type is QuestionType.NOUL


def test_advisor_question_texts_do_not_contain_the_common_rule():
    # 共通の文は build_advisor_questions が足すので、種類ごとの文には書かない
    for text in ADVISOR_QUESTION_TEXTS.values():
        assert "ただし" not in text
        assert "`advisor." not in text


def test_advisor_rule_is_added_for_a_new_advisor_kind(monkeypatch):
    monkeypatch.setattr(
        surface_questions_module,
        "ADVISOR_KINDS",
        (*ADVISOR_KINDS, DocumentKind.MAP),
    )
    monkeypatch.setattr(
        surface_questions_module,
        "ADVISOR_QUESTION_TEXTS",
        {**ADVISOR_QUESTION_TEXTS, DocumentKind.MAP: "マップの更新を求めるべきか。"},
    )
    questions = build_advisor_questions()
    assert [question.target for question in questions] == [
        "controls",
        "tips",
        "strategy",
        "advice",
        "game",
        "map",
    ]
    assert questions[-1].instructions == (
        "マップの更新を求めるべきか。" + advisor_rule("map")
    )


def test_advisor_game_question_follows_the_issue():
    assert ADVISOR_QUESTION_TEXTS[DocumentKind.GAME] == (
        "今、deep (文書をまとめる上位のモデル) に、ゲームの性質 (`documents.game`) の更新を求めるべきか。"
        "まだ書かれていないゲームの仕組み (入力を待たずに進む部分、入力を待って進む部分、マップの形、"
        "ダメージやゲームオーバーの条件) が画面に出たとき、書かれている内容と画面の様子が合わないときに真にする。"
    )
    [question] = [q for q in build_advisor_questions() if q.target == "game"]
    assert question.name == "advisor_game"
    assert question.instructions == (
        ADVISOR_QUESTION_TEXTS[DocumentKind.GAME] + advisor_rule("game")
    )


def test_info_labels_do_not_include_game():
    # 情報ラベル (info_<名前>) にはゲームの性質を足さない
    assert "game" not in INFO_LABEL_NAMES
    names = [q.name for q in build_info_label_questions("frames[0].image")]
    assert "info_game" not in names


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
        allowed_buttons(), TARGETS, {"敵"}, frame_count=5
    )
    kinds = [question.kind for question in questions]
    expected = (
        [QuestionKind.BUTTON] * 12
        + [QuestionKind.DPAD_DIRECTION, QuestionKind.DPAD_PRESS]
        + [QuestionKind.STICK_DIRECTION] * 4
        + [QuestionKind.STICK_STRENGTH] * 12
        + [QuestionKind.ADVISOR] * 5
        + [QuestionKind.INFO_LABEL] * 5
        + [QuestionKind.SCENE_CHANGE]
        + [QuestionKind.BOX_EXISTS] * 3
        + [QuestionKind.BOX]
    )
    assert kinds == expected
    # 十字キーの 2 問は、ボタンの問いの直後 (スティックの向きの問いの前)
    assert [question.name for question in questions[11:15]] == [
        "button_MINUS",
        "dpad_direction",
        "dpad_press",
        "stick_LS_x",
    ]
    assert questions[-1].name == "box_2"
    # 最新のフレームを指す文は、5 枚の要求なら frames[4].image
    assert "`frames[4].image`" in questions[-1].instructions


def test_surface_question_ids_are_unique_and_safe():
    questions = build_surface_questions(
        ALL_BUTTONS,
        DEFAULT_BOX_TARGETS,
        {target.name for target in DEFAULT_BOX_TARGETS},
        frame_count=3,
    )
    ids = [question.name for question in questions]
    assert len(ids) == len(set(ids))
    assert all(ID_PATTERN.fullmatch(name) for name in ids)
    # 十字キーの押し方の問いは、選択肢の問いの中で 1 つだけ none を持たない (押さないかは方向の問いで決まる)
    assert DPAD_PRESS_QUESTION_NAME in ids
    for question in questions:
        if question.name == DPAD_PRESS_QUESTION_NAME:
            assert NONE_CHOICE not in [choice.name for choice in question.choices]
        elif question.question_type is QuestionType.CHOICE:
            assert question.choices[0].name == NONE_CHOICE
        else:
            assert question.choices == ()


def test_surface_questions_without_requery_have_no_box_question():
    questions = build_surface_questions(
        allowed_buttons(), TARGETS, set(), frame_count=1
    )
    assert not any(q.question_type is QuestionType.BOX for q in questions)
    assert sum(q.kind is QuestionKind.BOX_EXISTS for q in questions) == 3


@pytest.mark.parametrize(
    "allowed", [set(), {"A"}, {"A", "UP", "RIGHT", "DOWN", "LEFT"}]
)
def test_dpad_questions_are_sent_regardless_of_allowed_buttons(allowed):
    questions = build_surface_questions(allowed, TARGETS, set(), frame_count=1)
    names = [question.name for question in questions]
    assert names.count("dpad_direction") == 1
    assert names.count("dpad_press") == 1
    for button in DPAD_BUTTONS:
        assert f"button_{button}" not in names
