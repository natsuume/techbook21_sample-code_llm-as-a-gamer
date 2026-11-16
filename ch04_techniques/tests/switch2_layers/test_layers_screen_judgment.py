"""照合の結果の決め方 (judge_system_screen) と、最新の照合の結果 (LatestSystemScreen) を確かめる。

しきい値ちょうど・確率の並び・none がいちばん高いとき・frame_t が同じか古い結果・待ちの境界を確かめる。
"""

import threading
import time

import pytest

from switch2_layers.system_screen.screen_judgment import (
    SYSTEM_SCREEN_THRESHOLD,
    LatestSystemScreen,
    SystemScreenJudgment,
    judge_system_screen,
)
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
    SYSTEM_SCREEN_NAMES,
)

# 待つテストで、届かないことを確かめるときに待たせる秒数
SHORT_WAIT_S = 0.2
# 待つテストが終わらないときに、テストを終わらせるための上限 (秒)
LONG_WAIT_S = 5.0


def choices(**values: float) -> dict[str, float]:
    """すべての選択肢を 0 にし、values の選択肢だけをその確率にした照合の確率 (選択肢の順)。"""
    return {**dict.fromkeys(SYSTEM_SCREEN_CHOICE_NAMES, 0.0), **values}


def reversed_order(values: dict[str, float]) -> dict[str, float]:
    """values と同じ中身で、並びだけを逆にした dict。"""
    return dict(reversed(values.items()))


def judgment(frame_t: float, screen: str | None = None) -> SystemScreenJudgment:
    return SystemScreenJudgment(
        frame_t=frame_t,
        t_received=frame_t + 0.3,
        screen=screen,
        probabilities=choices(none=1.0),
    )


# --- 照合の結果の決め方 ---


def test_threshold_follows_the_issue():
    assert SYSTEM_SCREEN_THRESHOLD == 0.5


@pytest.mark.parametrize("name", SYSTEM_SCREEN_NAMES)
def test_a_reference_over_the_threshold_is_the_result(name):
    assert judge_system_screen(choices(none=0.02, **{name: 0.97})) == name


def test_none_highest_is_no_screen():
    assert judge_system_screen(choices(none=0.71, home_menu=0.29)) is None


def test_exactly_the_threshold_is_not_over():
    assert judge_system_screen(choices(none=0.3, controllers=0.5)) is None


def test_just_over_the_threshold_is_over():
    assert judge_system_screen(choices(none=0.3, controllers=0.5001)) == "controllers"


def test_highest_reference_at_or_below_the_threshold_is_no_screen():
    values = choices(none=0.2, controller_order=0.45, controllers=0.35)
    assert judge_system_screen(values) is None


def test_tie_between_references_picks_the_earlier_choice():
    # 確率の合計は確かめない。並びは選択肢の順 (probabilities の並びではない)
    values = choices(home_menu=0.6, controllers=0.6)
    assert judge_system_screen(values) == "home_menu"
    assert judge_system_screen(reversed_order(values)) == "home_menu"


def test_tie_between_later_references_picks_the_earlier_choice():
    values = choices(user_select=0.7, software_close=0.7)
    assert judge_system_screen(reversed_order(values)) == "user_select"


def test_tie_with_none_picks_none():
    # none は先頭の選択肢なので、同じ確率の見本より先に選ばれる
    values = choices(none=0.6, home_menu=0.6)
    assert judge_system_screen(values) is None
    assert judge_system_screen(reversed_order(values)) is None


def test_names_outside_the_choices_are_ignored():
    values = {**choices(none=0.1, user_select=0.9), "unknown": 1.0}
    assert judge_system_screen(values) == "user_select"


def test_missing_choice_is_an_error():
    values = choices(home_menu=0.9)
    del values["software_close"]
    with pytest.raises(ValueError):
        judge_system_screen(values)


# --- 最新の照合の結果 ---


def test_latest_is_none_before_any_update():
    assert LatestSystemScreen().latest() is None


def test_latest_returns_the_received_judgment():
    latest = LatestSystemScreen()
    first = judgment(1.0, "home_menu")
    latest.update(first)
    assert latest.latest() == first


def test_newer_frame_replaces_the_judgment():
    latest = LatestSystemScreen()
    latest.update(judgment(1.0, "home_menu"))
    newer = judgment(2.0, None)
    latest.update(newer)
    assert latest.latest() == newer


def test_same_frame_does_not_replace_the_judgment():
    latest = LatestSystemScreen()
    first = judgment(2.0, "home_menu")
    latest.update(first)
    latest.update(judgment(2.0, "controllers"))
    assert latest.latest() == first


def test_older_frame_does_not_replace_the_judgment():
    latest = LatestSystemScreen()
    current = judgment(2.0, "controllers")
    latest.update(current)
    latest.update(judgment(1.5, "home_menu"))
    assert latest.latest() == current


def test_updates_from_many_threads_keep_the_newest_frame():
    latest = LatestSystemScreen()
    frame_times = [float(t) for t in range(200)]

    def update_newest_last(times: list[float]) -> None:
        for frame_t in times:
            latest.update(judgment(frame_t))

    def update_newest_first(times: list[float]) -> None:
        update_newest_last(list(reversed(times)))

    # 8 つのスレッドが、時刻を分けて、新しい順と古い順を混ぜて渡す
    threads = [
        threading.Thread(
            target=update_newest_first if i % 2 else update_newest_last,
            args=(frame_times[i::8],),
        )
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(LONG_WAIT_S)
    result = latest.latest()
    assert result is not None
    assert result.frame_t == max(frame_times)


# --- 新しい結果を待つ ---


def test_wait_newer_returns_at_once_when_a_newer_judgment_is_held():
    latest = LatestSystemScreen()
    held = judgment(3.0, "user_select")
    latest.update(held)
    started = time.monotonic()
    assert latest.wait_newer(2.9, LONG_WAIT_S) == held
    assert time.monotonic() - started < 1.0


def test_wait_newer_does_not_count_the_same_frame_as_newer():
    latest = LatestSystemScreen()
    latest.update(judgment(3.0, "user_select"))
    started = time.monotonic()
    assert latest.wait_newer(3.0, SHORT_WAIT_S) is None
    assert time.monotonic() - started >= SHORT_WAIT_S * 0.9


def test_wait_newer_returns_none_when_nothing_arrives():
    latest = LatestSystemScreen()
    started = time.monotonic()
    assert latest.wait_newer(0.0, SHORT_WAIT_S) is None
    elapsed = time.monotonic() - started
    assert SHORT_WAIT_S * 0.9 <= elapsed < LONG_WAIT_S


@pytest.mark.parametrize("timeout_s", [0.0, -1.0])
def test_wait_newer_without_time_returns_at_once_without_a_newer_judgment(timeout_s):
    latest = LatestSystemScreen()
    latest.update(judgment(1.0))
    started = time.monotonic()
    assert latest.wait_newer(1.0, timeout_s) is None
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize("timeout_s", [0.0, -1.0])
def test_wait_newer_without_time_returns_a_held_newer_judgment(timeout_s):
    latest = LatestSystemScreen()
    held = judgment(1.0, "controller_order")
    latest.update(held)
    assert latest.wait_newer(0.5, timeout_s) == held


def test_wait_newer_returns_a_judgment_that_arrives_while_waiting():
    latest = LatestSystemScreen()
    latest.update(judgment(1.0))
    arriving = judgment(2.0, "software_close")
    timer = threading.Timer(0.1, latest.update, args=(arriving,))
    timer.start()
    try:
        started = time.monotonic()
        assert latest.wait_newer(1.0, LONG_WAIT_S) == arriving
        assert time.monotonic() - started < LONG_WAIT_S
    finally:
        timer.cancel()


def test_wait_newer_keeps_waiting_through_an_older_judgment():
    # 待つ間に届いた結果の frame_t が after_frame_t 以下なら、新しい結果として返さない
    latest = LatestSystemScreen()
    timer = threading.Timer(0.05, latest.update, args=(judgment(3.0, "home_menu"),))
    timer.start()
    try:
        assert latest.wait_newer(5.0, SHORT_WAIT_S) is None
    finally:
        timer.cancel()
    # 結果を持っていなかったので、届いた結果は持つ
    held = latest.latest()
    assert held is not None
    assert held.frame_t == 3.0


def test_wait_newer_from_many_threads_all_receive_the_new_judgment():
    latest = LatestSystemScreen()
    results: list[SystemScreenJudgment | None] = []
    lock = threading.Lock()

    def wait() -> None:
        result = latest.wait_newer(0.0, LONG_WAIT_S)
        with lock:
            results.append(result)

    waiters = [threading.Thread(target=wait) for _ in range(4)]
    for waiter in waiters:
        waiter.start()
    arriving = judgment(1.0, "controllers")
    time.sleep(0.05)
    latest.update(arriving)
    for waiter in waiters:
        waiter.join(LONG_WAIT_S)
    assert results == [arriving] * 4
