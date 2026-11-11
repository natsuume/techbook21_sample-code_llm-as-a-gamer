import re
from collections import Counter

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_agent.questions import BUTTON_LABELS, build_questions

ADVISOR_TEXT = (
    "今、advisor (戦略を考える上位のモデル) に戦略の更新を求めるべきか。"
    "今の戦略が画面の状況に合わないとき、新しい場面に入ったとき、"
    "同じ失敗を繰り返しているときに真にする。"
)


def questions_by_name() -> dict:
    return {question.name: question for question in build_questions()}


def test_build_questions_returns_43_questions():
    assert len(build_questions()) == 43


def test_question_names_are_unique():
    names = [question.name for question in build_questions()]
    assert len(set(names)) == len(names)


def test_question_names_use_only_letters_digits_and_underscore():
    for question in build_questions():
        assert re.fullmatch(r"[A-Za-z0-9_]+", question.name), question.name


def test_question_counts_per_kind():
    counts = Counter(question.kind for question in build_questions())
    assert counts == {"press": 17, "tap": 17, "stick": 8, "advisor": 1}


def test_questions_are_ordered_buttons_then_sticks_then_advisor():
    kinds = [question.kind for question in build_questions()]
    assert set(kinds[:34]) == {"press", "tap"}
    assert kinds[34:42] == ["stick"] * 8
    assert kinds[42] == "advisor"


def test_each_button_has_press_and_tap_questions():
    by_name = questions_by_name()
    for button in BUTTON_ORDER:
        press = by_name[f"press_{button}"]
        tap = by_name[f"tap_{button}"]
        assert (press.kind, press.target) == ("press", button)
        assert (tap.kind, tap.target) == ("tap", button)


def test_press_question_text():
    assert questions_by_name()["press_A"].text == (
        "今、A ボタン を押した状態にするべきか。押した状態は次の判断まで続く。"
    )


def test_tap_question_text_uses_button_label():
    assert questions_by_name()["tap_L3"].text == (
        "今、L3 (左スティックの押し込み) を 1 回だけ短く押すべきか。"
    )


def test_stick_questions_cover_both_sticks_and_four_directions_in_order():
    sticks = [q for q in build_questions() if q.kind == "stick"]
    assert [q.name for q in sticks] == [
        "stick_LS_UP",
        "stick_LS_DOWN",
        "stick_LS_LEFT",
        "stick_LS_RIGHT",
        "stick_RS_UP",
        "stick_RS_DOWN",
        "stick_RS_LEFT",
        "stick_RS_RIGHT",
    ]
    assert [q.target for q in sticks] == [q.name.removeprefix("stick_") for q in sticks]


def test_stick_question_text():
    by_name = questions_by_name()
    assert by_name["stick_LS_UP"].text == "今、左スティックを上に倒すべきか。"
    assert by_name["stick_RS_LEFT"].text == "今、右スティックを左に倒すべきか。"


def test_advisor_question():
    advisor = questions_by_name()["advisor"]
    assert (advisor.kind, advisor.target) == ("advisor", "")
    assert advisor.text == ADVISOR_TEXT


def test_button_labels_cover_all_buttons():
    assert set(BUTTON_LABELS) == set(BUTTON_ORDER)


def test_button_labels_examples():
    assert BUTTON_LABELS["A"] == "A ボタン"
    assert BUTTON_LABELS["L3"] == "L3 (左スティックの押し込み)"
    assert BUTTON_LABELS["UP"] == "十字キーの上"
