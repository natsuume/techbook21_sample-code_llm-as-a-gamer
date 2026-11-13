import re
from collections import Counter

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_agent.questions import (
    BUTTON_LABELS,
    HOLD_CHOICE,
    NONE_CHOICE,
    STICK_AXIS_DIRECTIONS,
    STICK_STRENGTH_LEVELS,
    TAP_CHOICE,
    Choice,
    build_questions,
)

ADVISOR_TEXT = (
    "今、advisor (戦略を考える上位のモデル) に戦略の更新を求めるべきか。"
    "今の戦略が画面の状況に合わないとき、新しい場面に入ったとき、"
    "同じ失敗を繰り返しているときに真にする。"
)
BUTTON_CHOICES = (
    Choice("none", "押さない"),
    Choice("hold", "押した状態にする。押した状態は次の判断まで続く"),
    Choice("tap", "1 回だけ短く押す"),
)
X_AXIS_CHOICES = (
    Choice("none", "左右には倒さない"),
    Choice("left", "左に倒す"),
    Choice("right", "右に倒す"),
)
Y_AXIS_CHOICES = (
    Choice("none", "上下には倒さない"),
    Choice("up", "上に倒す"),
    Choice("down", "下に倒す"),
)
# スティックの方向の問いの軸 (問いの順)
STICK_AXES = ["LS_x", "LS_y", "RS_x", "RS_y"]


def questions_by_name() -> dict:
    return {question.name: question for question in build_questions()}


def questions_of_kind(kind: str) -> list:
    return [question for question in build_questions() if question.kind == kind]


# --- 一覧の全体 ---


def test_build_questions_returns_34_questions():
    assert len(build_questions()) == 34


def test_question_names_are_unique():
    names = [question.name for question in build_questions()]
    assert len(set(names)) == len(names)


def test_question_names_use_only_letters_digits_and_underscore():
    for question in build_questions():
        assert re.fullmatch(r"[A-Za-z0-9_]+", question.name), question.name


def test_question_counts_per_kind():
    counts = Counter(question.kind for question in build_questions())
    assert counts == {
        "button": 17,
        "stick_direction": 4,
        "stick_strength": 12,
        "advisor": 1,
    }


def test_questions_are_ordered_buttons_directions_strengths_then_advisor():
    kinds = [question.kind for question in build_questions()]
    assert kinds == (
        ["button"] * 17
        + ["stick_direction"] * 4
        + ["stick_strength"] * 12
        + ["advisor"]
    )


def test_choice_questions_have_choices_starting_with_none():
    questions = build_questions()
    choice_questions = [q for q in questions if q.answer_type == "choice"]
    probability_questions = [q for q in questions if q.answer_type == "probability"]
    # ボタンとスティックの方向の 21 問が選択肢の問い、強さと advisor の 13 問が確率の問い
    assert len(choice_questions) == 21
    assert len(probability_questions) == 13
    for question in choice_questions:
        assert question.choices[0].name == NONE_CHOICE, question.name
        for choice in question.choices:
            assert re.fullmatch(r"[a-z]+", choice.name), choice.name
    for question in probability_questions:
        assert question.choices == (), question.name


# --- ボタンの問い ---


def test_button_questions_follow_button_order():
    buttons = questions_of_kind("button")
    assert [q.name for q in buttons] == [f"button_{b}" for b in BUTTON_ORDER]
    assert [q.target for q in buttons] == list(BUTTON_ORDER)
    assert {q.answer_type for q in buttons} == {"choice"}


def test_button_questions_have_none_hold_tap_choices_in_order():
    buttons = questions_of_kind("button")
    assert len(buttons) == 17
    for question in buttons:
        assert question.choices == BUTTON_CHOICES, question.name
    assert [choice.name for choice in BUTTON_CHOICES] == [
        NONE_CHOICE,
        HOLD_CHOICE,
        TAP_CHOICE,
    ]


def test_button_question_text_uses_button_label():
    # 呼び名と「を」の間に空白を入れない
    by_name = questions_by_name()
    assert by_name["button_A"].text == "今、A ボタンをどうするか。"
    assert by_name["button_UP"].text == "今、十字キーの上をどうするか。"
    assert by_name["button_L3"].text == "今、L3 (左スティックの押し込み)をどうするか。"


# --- スティックの方向の問い ---


def test_stick_direction_questions_are_ls_and_rs_with_x_then_y():
    directions = questions_of_kind("stick_direction")
    assert [q.name for q in directions] == [f"stick_{axis}" for axis in STICK_AXES]
    assert [q.target for q in directions] == STICK_AXES
    assert {q.answer_type for q in directions} == {"choice"}


def test_stick_direction_question_text():
    by_name = questions_by_name()
    assert by_name["stick_LS_x"].text == "今、左スティックを左右に倒すか。"
    assert by_name["stick_LS_y"].text == "今、左スティックを上下に倒すか。"
    assert by_name["stick_RS_x"].text == "今、右スティックを左右に倒すか。"
    assert by_name["stick_RS_y"].text == "今、右スティックを上下に倒すか。"


def test_stick_direction_choices_in_order():
    by_name = questions_by_name()
    # y は「上」が先で、正の向き (up) と負の向き (down) の組の順とは異なる
    assert by_name["stick_LS_x"].choices == X_AXIS_CHOICES
    assert by_name["stick_RS_x"].choices == X_AXIS_CHOICES
    assert by_name["stick_LS_y"].choices == Y_AXIS_CHOICES
    assert by_name["stick_RS_y"].choices == Y_AXIS_CHOICES


# --- スティックの強さの問い ---


def test_stick_strength_questions_are_each_axis_with_50_75_100():
    strengths = questions_of_kind("stick_strength")
    assert [q.name for q in strengths] == [
        f"stick_{axis}_at_least_{level}"
        for axis in STICK_AXES
        for level in (50, 75, 100)
    ]
    assert [q.target for q in strengths] == [
        f"{axis}_{level}" for axis in STICK_AXES for level in (50, 75, 100)
    ]
    assert {q.answer_type for q in strengths} == {"probability"}


def test_stick_strength_question_text():
    by_name = questions_by_name()
    assert by_name["stick_LS_x_at_least_50"].text == (
        "左スティックを左右に倒す場合、傾きを 50% 以上にするべきか。"
    )
    assert by_name["stick_RS_y_at_least_75"].text == (
        "右スティックを上下に倒す場合、傾きを 75% 以上にするべきか。"
    )
    assert by_name["stick_LS_y_at_least_100"].text == (
        "左スティックを上下に倒す場合、傾きをいっぱい (100%) にするべきか。"
    )


# --- advisor の問い ---


def test_advisor_question_is_last_probability_question():
    questions = build_questions()
    advisor = questions[-1]
    assert advisor.name == "advisor"
    assert (advisor.kind, advisor.target) == ("advisor", "")
    assert advisor.text == ADVISOR_TEXT
    assert (advisor.answer_type, advisor.choices) == ("probability", ())


# --- 定数 ---


def test_choice_name_constants():
    assert (NONE_CHOICE, HOLD_CHOICE, TAP_CHOICE) == ("none", "hold", "tap")


def test_stick_axis_directions_are_negative_then_positive():
    assert STICK_AXIS_DIRECTIONS == {"x": ("left", "right"), "y": ("down", "up")}


def test_stick_strength_levels():
    assert STICK_STRENGTH_LEVELS == (50, 75, 100)


def test_choice_holds_name_and_description():
    choice = Choice("none", "押さない")
    assert (choice.name, choice.description) == ("none", "押さない")


def test_button_labels_cover_all_buttons():
    assert set(BUTTON_LABELS) == set(BUTTON_ORDER)


def test_button_labels_examples():
    assert BUTTON_LABELS["A"] == "A ボタン"
    assert BUTTON_LABELS["L3"] == "L3 (左スティックの押し込み)"
    assert BUTTON_LABELS["UP"] == "十字キーの上"
