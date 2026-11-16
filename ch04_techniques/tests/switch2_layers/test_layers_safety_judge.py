"""止めるかの判定 (純粋な関数とクラス)。しきい値ちょうど・続けて 2 回・失敗の回数・--allow-home の境界を確かめる。"""

import math

import pytest

from switch2_layers.safety.safety_judge import (
    REASON_SAFETY_FAILED_AT_START,
    UNSAFE_STREAK_TO_STOP,
    MonitorJudge,
    names_over_threshold,
    next_check_wait_s,
    safety_failure_reason,
    start_stop_reason,
    unsafe_screen_at_start_reason,
    unsafe_screen_reason,
    validate_probabilities,
    watched_names,
)
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES

THRESHOLD = 0.7
ALL_ZERO = {name: 0.0 for name in SAFETY_QUESTION_NAMES}


def probabilities(**values: float) -> dict[str, float]:
    """すべての問いを 0 にし、values の問いだけをその確率にした答え。"""
    return {**ALL_ZERO, **values}


# --- 理由の文字列 ---


def test_reason_texts_follow_the_issue():
    assert unsafe_screen_reason("online") == "unsafe screen: online"
    assert unsafe_screen_at_start_reason("home_menu") == (
        "unsafe screen at start: home_menu"
    )
    assert safety_failure_reason(3) == "safety check failed 3 times in a row"
    assert REASON_SAFETY_FAILED_AT_START == "safety check failed at start"
    assert UNSAFE_STREAK_TO_STOP == 2


# --- 止める対象の問い ---


def test_watched_names_without_allow_home_are_all_questions():
    assert watched_names(False) == SAFETY_QUESTION_NAMES


def test_watched_names_with_allow_home_drop_home_menu_and_user_select():
    assert watched_names(True) == ("online", "purchase", "system_settings")


# --- 答えの確率の確かめ ---


def test_validate_probabilities_orders_by_question_and_ignores_unknown_names():
    value = {"system_settings": 0.5, "unknown": 2.0, **ALL_ZERO, "online": 1}
    result = validate_probabilities(value)
    assert list(result) == list(SAFETY_QUESTION_NAMES)
    assert result["online"] == 1.0
    assert type(result["online"]) is float


@pytest.mark.parametrize(
    "broken",
    [
        {"online": None},
        {"online": "0.5"},
        {"online": True},
        {"online": -0.01},
        {"online": 1.01},
        {"online": math.nan},
        {"online": math.inf},
    ],
)
def test_validate_probabilities_rejects_broken_values(broken):
    with pytest.raises(ValueError):
        validate_probabilities({**ALL_ZERO, **broken})


def test_validate_probabilities_rejects_missing_names():
    value = dict(ALL_ZERO)
    del value["purchase"]
    with pytest.raises(ValueError):
        validate_probabilities(value)


# --- しきい値を超えた問い ---


def test_exactly_the_threshold_is_not_over():
    assert names_over_threshold(probabilities(online=0.7), THRESHOLD, False) == ()


def test_just_over_the_threshold_is_over():
    assert names_over_threshold(probabilities(online=0.7001), THRESHOLD, False) == (
        "online",
    )


def test_names_over_threshold_are_in_question_order():
    values = probabilities(system_settings=0.9, home_menu=0.8, purchase=0.95)
    assert names_over_threshold(values, THRESHOLD, False) == (
        "home_menu",
        "purchase",
        "system_settings",
    )


def test_allow_home_ignores_home_menu_and_user_select_only():
    values = probabilities(home_menu=0.99, user_select=0.99, online=0.99)
    assert names_over_threshold(values, THRESHOLD, True) == ("online",)


# --- 実行を始めるときの確かめ (1 回で決める) ---


def test_start_stops_at_once_when_a_question_is_over():
    reason = start_stop_reason(probabilities(home_menu=0.71), THRESHOLD, False)
    assert reason == "unsafe screen at start: home_menu"


def test_start_does_not_stop_at_exactly_the_threshold():
    assert start_stop_reason(probabilities(home_menu=0.7), THRESHOLD, False) is None


def test_start_does_not_stop_when_every_question_is_low():
    assert start_stop_reason(ALL_ZERO, THRESHOLD, False) is None


def test_start_reason_names_the_first_question_in_order():
    values = probabilities(purchase=0.9, user_select=0.9)
    assert start_stop_reason(values, THRESHOLD, False) == (
        "unsafe screen at start: user_select"
    )


def test_start_failure_stops():
    assert start_stop_reason(None, THRESHOLD, False) == "safety check failed at start"
    assert start_stop_reason(None, THRESHOLD, True) == "safety check failed at start"


@pytest.mark.parametrize("name", ["home_menu", "user_select"])
def test_start_with_allow_home_does_not_stop_at_home_menu_or_user_select(name):
    assert start_stop_reason(probabilities(**{name: 0.99}), THRESHOLD, True) is None


@pytest.mark.parametrize("name", ["online", "purchase", "system_settings"])
def test_start_with_allow_home_still_stops_at_other_screens(name):
    reason = start_stop_reason(probabilities(**{name: 0.99}), THRESHOLD, True)
    assert reason == f"unsafe screen at start: {name}"


# --- 監視: 同じ問いが続けて 2 回 ---


def judge(allow_home: bool = False, max_errors: int = 3) -> MonitorJudge:
    return MonitorJudge(
        threshold=THRESHOLD, allow_home=allow_home, max_errors=max_errors
    )


def test_monitor_stops_when_the_same_question_is_over_twice_in_a_row():
    monitor = judge()
    assert monitor.record_answer(probabilities(online=0.8)) is None
    assert monitor.streak("online") == 1
    assert monitor.record_answer(probabilities(online=0.8)) == "unsafe screen: online"


def test_monitor_does_not_stop_on_one_unsafe_answer():
    monitor = judge()
    assert monitor.record_answer(probabilities(online=0.99)) is None
    assert monitor.record_answer(ALL_ZERO) is None
    assert monitor.streak("online") == 0
    assert monitor.record_answer(probabilities(online=0.99)) is None


def test_monitor_does_not_count_exactly_the_threshold():
    monitor = judge()
    assert monitor.record_answer(probabilities(purchase=0.7)) is None
    assert monitor.record_answer(probabilities(purchase=0.7)) is None
    assert monitor.streak("purchase") == 0


def test_monitor_counts_each_question_separately():
    # 違う問いが 1 回ずつ超えても、同じ問いが続けて超えたことにはならない
    monitor = judge()
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.record_answer(probabilities(purchase=0.9)) is None
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.record_answer(probabilities(online=0.9, purchase=0.9)) == (
        "unsafe screen: online"
    )


def test_monitor_reason_names_the_first_question_in_order():
    monitor = judge()
    both = probabilities(system_settings=0.9, home_menu=0.9)
    assert monitor.record_answer(both) is None
    assert monitor.record_answer(both) == "unsafe screen: home_menu"


@pytest.mark.parametrize("name", ["home_menu", "user_select"])
def test_monitor_with_allow_home_does_not_stop_at_home_menu_or_user_select(name):
    monitor = judge(allow_home=True)
    for _ in range(5):
        assert monitor.record_answer(probabilities(**{name: 0.99})) is None
    assert monitor.streak(name) == 0


@pytest.mark.parametrize("name", ["online", "purchase", "system_settings"])
def test_monitor_with_allow_home_still_stops_at_other_screens(name):
    monitor = judge(allow_home=True)
    assert monitor.record_answer(probabilities(**{name: 0.99})) is None
    assert monitor.record_answer(probabilities(**{name: 0.99})) == (
        f"unsafe screen: {name}"
    )


# --- 監視: 失敗の回数 ---


def test_monitor_stops_after_max_errors_failures_in_a_row():
    monitor = judge(max_errors=3)
    assert monitor.record_failure() is None
    assert monitor.record_failure() is None
    assert monitor.consecutive_errors == 2
    assert monitor.record_failure() == "safety check failed 3 times in a row"


def test_monitor_success_resets_the_failure_count():
    monitor = judge(max_errors=3)
    assert monitor.record_failure() is None
    assert monitor.record_failure() is None
    assert monitor.record_answer(ALL_ZERO) is None
    assert monitor.consecutive_errors == 0
    assert monitor.record_failure() is None
    assert monitor.record_failure() is None
    assert monitor.record_failure() == "safety check failed 3 times in a row"


def test_monitor_with_one_max_error_stops_at_the_first_failure():
    assert judge(max_errors=1).record_failure() == (
        "safety check failed 1 times in a row"
    )


def test_monitor_unsafe_answer_also_resets_the_failure_count():
    monitor = judge(max_errors=2)
    assert monitor.record_failure() is None
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.consecutive_errors == 0
    assert monitor.record_failure() is None


# --- 監視の間隔 ---


@pytest.mark.parametrize(
    "t_sent, t_received, expected",
    [
        (10.0, 10.3, 0.7),  # 応答が速ければ、送ってから 1 秒後まで待つ
        (10.0, 11.0, 0.0),  # ちょうど 1 秒かかったら待たない
        (10.0, 11.5, 0.0),  # 1 秒より遅ければ待たずに次を送る
    ],
)
def test_next_check_wait_keeps_the_interval_from_the_previous_send(
    t_sent, t_received, expected
):
    assert next_check_wait_s(1.0, t_sent, t_received) == pytest.approx(expected)
