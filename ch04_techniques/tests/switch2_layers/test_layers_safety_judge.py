"""止めるかの判定 (純粋な関数とクラス)。しきい値ちょうど・続けて 2 回・失敗の回数・照合の確率の確かめの境界を確かめる。"""

import inspect
import math

import pytest

import switch2_layers.safety.safety_judge as safety_judge_module
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
    validate_system_screen_probabilities,
)
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
)

THRESHOLD = 0.7
ALL_ZERO = {name: 0.0 for name in SAFETY_QUESTION_NAMES}
NO_SCREEN = {name: 0.0 for name in SYSTEM_SCREEN_CHOICE_NAMES} | {"none": 1.0}


def probabilities(**values: float) -> dict[str, float]:
    """すべての問いを 0 にし、values の問いだけをその確率にした答え。"""
    return {**ALL_ZERO, **values}


# --- 理由の文字列 ---


def test_reason_texts_follow_the_issue():
    assert unsafe_screen_reason("online") == "unsafe screen: online"
    assert unsafe_screen_at_start_reason("purchase") == (
        "unsafe screen at start: purchase"
    )
    assert safety_failure_reason(3) == "safety check failed 3 times in a row"
    assert REASON_SAFETY_FAILED_AT_START == "safety check failed at start"
    assert UNSAFE_STREAK_TO_STOP == 2


# --- --allow-home は判定に使わない ---


def test_judge_has_no_allow_home_exemption():
    assert not hasattr(safety_judge_module, "watched_names")
    for function in (names_over_threshold, start_stop_reason, MonitorJudge):
        assert "allow_home" not in inspect.signature(function).parameters


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


# --- 照合の確率の確かめ ---


def test_validate_system_screen_orders_by_choice_and_ignores_unknown_names():
    value = {"unknown": 3.0, **dict(reversed(NO_SCREEN.items())), "home_menu": 1}
    result = validate_system_screen_probabilities(value)
    assert list(result) == list(SYSTEM_SCREEN_CHOICE_NAMES)
    assert result["home_menu"] == 1.0
    assert type(result["home_menu"]) is float
    assert result["none"] == 1.0


def test_validate_system_screen_accepts_the_edges_of_the_range():
    value = {**NO_SCREEN, "none": 0, "controllers": 1.0}
    result = validate_system_screen_probabilities(value)
    assert result["none"] == 0.0
    assert result["controllers"] == 1.0


@pytest.mark.parametrize(
    "broken",
    [
        {"user_select": None},
        {"user_select": "0.5"},
        {"user_select": True},
        {"none": -0.01},
        {"software_close": 1.01},
        {"controllers": math.nan},
        {"controller_order": math.inf},
    ],
)
def test_validate_system_screen_rejects_broken_values(broken):
    with pytest.raises(ValueError):
        validate_system_screen_probabilities({**NO_SCREEN, **broken})


@pytest.mark.parametrize("missing", SYSTEM_SCREEN_CHOICE_NAMES)
def test_validate_system_screen_rejects_a_missing_choice(missing):
    value = dict(NO_SCREEN)
    del value[missing]
    with pytest.raises(ValueError):
        validate_system_screen_probabilities(value)


def test_validate_system_screen_rejects_an_empty_answer():
    with pytest.raises(ValueError):
        validate_system_screen_probabilities({})


# --- しきい値を超えた問い ---


def test_exactly_the_threshold_is_not_over():
    assert names_over_threshold(probabilities(online=0.7), THRESHOLD) == ()


def test_just_over_the_threshold_is_over():
    assert names_over_threshold(probabilities(online=0.7001), THRESHOLD) == ("online",)


def test_names_over_threshold_are_in_question_order():
    values = probabilities(system_settings=0.9, online=0.8, purchase=0.95)
    assert names_over_threshold(values, THRESHOLD) == (
        "online",
        "purchase",
        "system_settings",
    )


# --- 実行を始めるときの確かめ (1 回で決める) ---


def test_start_stops_at_once_when_a_question_is_over():
    reason = start_stop_reason(probabilities(online=0.71), THRESHOLD)
    assert reason == "unsafe screen at start: online"


def test_start_does_not_stop_at_exactly_the_threshold():
    assert start_stop_reason(probabilities(online=0.7), THRESHOLD) is None


def test_start_does_not_stop_when_every_question_is_low():
    assert start_stop_reason(ALL_ZERO, THRESHOLD) is None


def test_start_reason_names_the_first_question_in_order():
    values = probabilities(system_settings=0.9, purchase=0.9)
    assert start_stop_reason(values, THRESHOLD) == ("unsafe screen at start: purchase")


def test_start_failure_stops():
    assert start_stop_reason(None, THRESHOLD) == "safety check failed at start"


@pytest.mark.parametrize("name", ["online", "purchase", "system_settings"])
def test_start_stops_at_each_question(name):
    reason = start_stop_reason(probabilities(**{name: 0.99}), THRESHOLD)
    assert reason == f"unsafe screen at start: {name}"


# --- 監視: 同じ問いが続けて 2 回 ---


def judge(max_errors: int = 3) -> MonitorJudge:
    return MonitorJudge(threshold=THRESHOLD, max_errors=max_errors)


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
    both = probabilities(system_settings=0.9, purchase=0.9)
    assert monitor.record_answer(both) is None
    assert monitor.record_answer(both) == "unsafe screen: purchase"


@pytest.mark.parametrize("name", ["online", "purchase", "system_settings"])
def test_monitor_stops_at_each_question(name):
    monitor = judge()
    assert monitor.record_answer(probabilities(**{name: 0.99})) is None
    assert monitor.record_answer(probabilities(**{name: 0.99})) == (
        f"unsafe screen: {name}"
    )


def test_monitor_streak_of_an_unknown_name_is_zero():
    monitor = judge()
    monitor.record_answer(probabilities(online=0.9))
    assert monitor.streak("home_menu") == 0


# --- 監視: 失敗した確かめをはさむとき (答えのあった確かめだけを続けて数える) ---


def test_monitor_stops_when_over_then_failure_then_over():
    monitor = judge(max_errors=3)
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.record_failure() is None
    # 失敗した確かめは、続けて超えた回数を消さない
    assert monitor.streak("online") == 1
    assert monitor.record_answer(probabilities(online=0.9)) == "unsafe screen: online"


def test_monitor_stops_when_over_and_failure_alternate():
    # 答えのたびに失敗の回数は 0 に戻るため、交互では失敗の回数では止まらない。続けて超えた回数で止まる
    monitor = judge(max_errors=2)
    reasons: list[str | None] = []
    for _ in range(5):
        reasons.append(monitor.record_answer(probabilities(online=0.9)))
        if reasons[-1] is not None:
            break
        reasons.append(monitor.record_failure())
        if reasons[-1] is not None:
            break
    assert reasons == [None, None, "unsafe screen: online"]


def test_monitor_answer_below_the_threshold_after_a_failure_starts_counting_again():
    monitor = judge(max_errors=3)
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.record_failure() is None
    # 答えのあった確かめで超えなければ、続けて超えた回数は 0 に戻る
    assert monitor.record_answer(probabilities(online=0.7)) is None
    assert monitor.streak("online") == 0
    assert monitor.record_answer(probabilities(online=0.9)) is None
    assert monitor.streak("online") == 1


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
