import dataclasses

import pytest

import switch2_layers.system_screen.run_phase as run_phase_module
from switch2_layers.system_screen.run_phase import (
    CLEAN_UP_DONE_SCREEN,
    CLEAN_UP_TIMEOUT_S,
    DEFAULT_PHASE_TIMINGS,
    ENTER_STREAK,
    EXIT_STREAK,
    NO_EVENT,
    PHASE_REASON_PREPARED,
    PHASE_REASON_PRESS_HOME,
    PHASE_REASON_RECOVERED,
    PHASE_REASON_START_CHECK_PASSED,
    PHASE_REASON_SYSTEM_SCREEN,
    CleanUpResult,
    PhaseChange,
    PhaseEvent,
    PhaseTimings,
    PhaseTracker,
    RunPhase,
)

START_T = 10.0
STOP_REASON = "interrupted"
# 準備・復帰に長くいる時刻 (段階に入ってからの秒数)。準備・復帰には時間切れが無い
LONG_STAY_S = [180.0, 10_000.0]


def changed_to(phase: RunPhase, reason: str) -> PhaseEvent:
    return PhaseEvent(change=PhaseChange(phase, reason))


def judge_many(
    tracker: PhaseTracker, screens: list[str | None], start_t: float
) -> list[PhaseEvent]:
    """screens の照合の結果を、start_t から 0.5 秒ごとの frame_t で順に渡し、結果の一覧を返す。"""
    return [
        tracker.on_judgment(start_t + 0.5 * index, screen)
        for index, screen in enumerate(screens)
    ]


def prepared_tracker() -> PhaseTracker:
    """done の後に None が続いて PLAY に移った (t = 13.0 に移る) PhaseTracker を返す。"""
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    events = judge_many(tracker, [None] * EXIT_STREAK, 12.0)
    assert events[-1] == changed_to(RunPhase.PLAY, PHASE_REASON_PREPARED)
    assert tracker.phase is RunPhase.PLAY
    return tracker


def recovering_tracker() -> PhaseTracker:
    """PLAY から照合の結果で RECOVER に移った PhaseTracker を返す。"""
    tracker = prepared_tracker()
    events = judge_many(tracker, ["home_menu"] * ENTER_STREAK, 20.0)
    assert events[-1] == changed_to(RunPhase.RECOVER, PHASE_REASON_SYSTEM_SCREEN)
    return tracker


def cleaning_tracker() -> PhaseTracker:
    """PLAY から片付けを始めた (t = 30.0) PhaseTracker を返す。"""
    tracker = prepared_tracker()
    assert tracker.begin_clean_up(30.0, STOP_REASON) == changed_to(
        RunPhase.CLEAN_UP, STOP_REASON
    )
    return tracker


# --- 定数と型 ---


def test_constants_follow_the_issue():
    assert ENTER_STREAK == 2
    assert EXIT_STREAK == 3
    assert CLEAN_UP_TIMEOUT_S == 45.0
    assert CLEAN_UP_DONE_SCREEN == "home_menu"
    assert PHASE_REASON_START_CHECK_PASSED == "start check passed"


def test_prepare_and_recover_have_no_timeout_constants():
    # 時間切れは片付けだけにあり、準備と復帰の秒数と止める理由は持たない
    for name in [
        "PREPARE_TIMEOUT_S",
        "RECOVER_TIMEOUT_S",
        "REASON_PREPARATION_TIMED_OUT",
        "REASON_RECOVERY_TIMED_OUT",
    ]:
        assert not hasattr(run_phase_module, name), name


def test_phase_and_clean_up_values():
    assert [phase.value for phase in RunPhase] == [
        "prepare",
        "play",
        "recover",
        "clean_up",
    ]
    assert [result.value for result in CleanUpResult] == [
        "done",
        "timeout",
        "middle_failed",
        "interrupted",
        "unsafe",
        "stopped",
    ]


def test_timings_default_to_the_constants():
    # 秒数は片付けの時間切れだけを持つ
    assert [field.name for field in dataclasses.fields(PhaseTimings)] == [
        "enter_streak",
        "exit_streak",
        "clean_up_timeout_s",
    ]
    assert PhaseTimings() == PhaseTimings(
        enter_streak=ENTER_STREAK,
        exit_streak=EXIT_STREAK,
        clean_up_timeout_s=CLEAN_UP_TIMEOUT_S,
    )
    assert DEFAULT_PHASE_TIMINGS == PhaseTimings()


def test_no_event_has_nothing():
    assert NO_EVENT == PhaseEvent(change=None, stop_reason=None, clean_up_end=None)


# --- prepare ---


def test_starts_in_prepare_and_wants_the_system_middle():
    tracker = PhaseTracker(START_T)
    assert tracker.phase is RunPhase.PREPARE
    assert tracker.phase_started_t == START_T
    assert tracker.wants_system_middle is True


def test_judgments_before_done_do_not_move_to_play():
    tracker = PhaseTracker(START_T)
    events = judge_many(tracker, [None] * (EXIT_STREAK + 2), 11.0)
    assert events == [NO_EVENT] * (EXIT_STREAK + 2)
    assert tracker.phase is RunPhase.PREPARE


def test_done_waits_for_the_game_screen_without_calling_the_middle():
    tracker = PhaseTracker(START_T)
    assert tracker.on_system_output(11.0, done=False) == NO_EVENT
    assert tracker.wants_system_middle is True
    assert tracker.on_system_output(12.0, done=True) == NO_EVENT
    assert tracker.wants_system_middle is False
    assert tracker.phase is RunPhase.PREPARE


def test_none_judgments_after_done_move_to_play():
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    events = judge_many(tracker, [None] * EXIT_STREAK, 12.0)
    assert events[:-1] == [NO_EVENT] * (EXIT_STREAK - 1)
    assert events[-1] == changed_to(RunPhase.PLAY, PHASE_REASON_PREPARED)
    assert tracker.phase is RunPhase.PLAY
    assert tracker.phase_started_t == 13.0
    assert tracker.wants_system_middle is False


def test_system_screen_after_done_cancels_done():
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    judge_many(tracker, [None] * (EXIT_STREAK - 1), 12.0)
    assert tracker.on_judgment(14.0, "user_select") == NO_EVENT
    # done を取り消し、もう一度 middle を呼ぶ
    assert tracker.phase is RunPhase.PREPARE
    assert tracker.wants_system_middle is True
    # done の前の None は数えない
    assert judge_many(tracker, [None] * (EXIT_STREAK + 1), 15.0) == [NO_EVENT] * (
        EXIT_STREAK + 1
    )


def test_second_done_counts_none_from_zero():
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    judge_many(tracker, [None] * (EXIT_STREAK - 1), 12.0)
    tracker.on_judgment(14.0, "home_menu")
    tracker.on_system_output(15.0, done=True)
    events = judge_many(tracker, [None] * EXIT_STREAK, 16.0)
    assert events[:-1] == [NO_EVENT] * (EXIT_STREAK - 1)
    assert events[-1] == changed_to(RunPhase.PLAY, PHASE_REASON_PREPARED)


def test_custom_exit_streak_moves_to_play_sooner():
    tracker = PhaseTracker(START_T, PhaseTimings(exit_streak=1))
    tracker.on_system_output(11.0, done=True)
    assert tracker.on_judgment(11.5, None) == changed_to(
        RunPhase.PLAY, PHASE_REASON_PREPARED
    )


def test_prepare_has_no_timeout():
    # 準備にどれだけいても、check_time は止める理由を返さない
    tracker = PhaseTracker(START_T)
    for stay_s in LONG_STAY_S:
        assert tracker.check_time(START_T + stay_s) == NO_EVENT
    assert tracker.phase is RunPhase.PREPARE
    assert tracker.wants_system_middle is True


def test_prepare_has_no_timeout_while_waiting_after_done():
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    for stay_s in LONG_STAY_S:
        assert tracker.check_time(START_T + stay_s) == NO_EVENT
    assert tracker.phase is RunPhase.PREPARE
    # done の後の待ちは続く
    assert tracker.wants_system_middle is False


# --- play ---


def test_play_does_not_want_the_system_middle_and_has_no_timeout():
    tracker = prepared_tracker()
    assert tracker.wants_system_middle is False
    assert tracker.check_time(13.0 + 10_000.0) == NO_EVENT


def test_system_screens_in_a_row_move_to_recover():
    tracker = prepared_tracker()
    events = judge_many(tracker, ["home_menu"] * ENTER_STREAK, 20.0)
    assert events[:-1] == [NO_EVENT] * (ENTER_STREAK - 1)
    assert events[-1] == changed_to(RunPhase.RECOVER, PHASE_REASON_SYSTEM_SCREEN)
    assert tracker.phase is RunPhase.RECOVER
    assert tracker.phase_started_t == 20.0 + 0.5 * (ENTER_STREAK - 1)
    assert tracker.wants_system_middle is True


def test_any_reference_screen_counts_toward_recover():
    tracker = prepared_tracker()
    events = judge_many(tracker, ["controllers", "software_close"], 20.0)
    assert events[-1] == changed_to(RunPhase.RECOVER, PHASE_REASON_SYSTEM_SCREEN)


def test_none_in_play_resets_the_count():
    tracker = prepared_tracker()
    events = judge_many(tracker, ["home_menu", None, "home_menu"], 20.0)
    assert events == [NO_EVENT, NO_EVENT, NO_EVENT]
    assert tracker.phase is RunPhase.PLAY


def test_press_home_moves_play_to_recover():
    tracker = prepared_tracker()
    assert tracker.on_press_home(20.0) == changed_to(
        RunPhase.RECOVER, PHASE_REASON_PRESS_HOME
    )
    assert tracker.phase is RunPhase.RECOVER
    assert tracker.phase_started_t == 20.0


def test_press_home_outside_play_does_nothing():
    tracker = PhaseTracker(START_T)
    assert tracker.on_press_home(11.0) == NO_EVENT
    assert tracker.phase is RunPhase.PREPARE
    recovering = recovering_tracker()
    assert recovering.on_press_home(30.0) == NO_EVENT
    assert recovering.phase is RunPhase.RECOVER


# --- recover ---


def test_none_judgments_without_sending_move_recover_to_play():
    tracker = recovering_tracker()
    events = judge_many(tracker, [None] * EXIT_STREAK, 25.0)
    assert events[:-1] == [NO_EVENT] * (EXIT_STREAK - 1)
    assert events[-1] == changed_to(RunPhase.PLAY, PHASE_REASON_RECOVERED)
    assert tracker.phase is RunPhase.PLAY


def test_recover_does_not_need_done():
    tracker = recovering_tracker()
    assert tracker.on_system_output(24.0, done=True) == NO_EVENT
    assert tracker.phase is RunPhase.RECOVER
    assert tracker.wants_system_middle is True


def test_judgments_while_sending_do_not_count_and_reset_the_count():
    tracker = recovering_tracker()
    judge_many(tracker, [None] * (EXIT_STREAK - 1), 25.0)
    assert tracker.on_operations_started(26.0) == NO_EVENT
    assert judge_many(tracker, [None] * (EXIT_STREAK + 1), 26.5) == [NO_EVENT] * (
        EXIT_STREAK + 1
    )
    assert tracker.phase is RunPhase.RECOVER
    assert tracker.on_operations_finished(30.0) == NO_EVENT
    # 送り終えた後の結果を、0 から数える
    events = judge_many(tracker, [None] * EXIT_STREAK, 30.5)
    assert events[:-1] == [NO_EVENT] * (EXIT_STREAK - 1)
    assert events[-1] == changed_to(RunPhase.PLAY, PHASE_REASON_RECOVERED)


def test_system_screen_in_recover_resets_the_count():
    tracker = recovering_tracker()
    events = judge_many(tracker, [None, None, "home_menu", None, None], 25.0)
    assert events == [NO_EVENT] * 5
    assert tracker.phase is RunPhase.RECOVER


def test_system_screens_before_recover_do_not_count_in_recover():
    # play の本体の画面の数は、recover に移ったときに数え直す
    tracker = prepared_tracker()
    judge_many(tracker, ["home_menu"] * ENTER_STREAK, 20.0)
    events = judge_many(tracker, [None] * (EXIT_STREAK - 1), 25.0)
    assert events == [NO_EVENT] * (EXIT_STREAK - 1)
    assert tracker.phase is RunPhase.RECOVER


def test_recover_has_no_timeout():
    # 復帰にどれだけいても、check_time は止める理由を返さない
    tracker = recovering_tracker()
    started = tracker.phase_started_t
    for stay_s in LONG_STAY_S:
        assert tracker.check_time(started + stay_s) == NO_EVENT
    assert tracker.phase is RunPhase.RECOVER
    assert tracker.wants_system_middle is True


# --- clean_up ---


def tracker_in(phase: RunPhase) -> PhaseTracker:
    """PREPARE・PLAY・RECOVER のどれかの段階にある PhaseTracker を返す。"""
    if phase is RunPhase.PLAY:
        return prepared_tracker()
    if phase is RunPhase.RECOVER:
        return recovering_tracker()
    return PhaseTracker(START_T)


@pytest.mark.parametrize("phase", [RunPhase.PREPARE, RunPhase.PLAY, RunPhase.RECOVER])
def test_clean_up_can_begin_from_any_phase(phase):
    tracker = tracker_in(phase)
    assert tracker.phase is phase
    assert tracker.begin_clean_up(40.0, STOP_REASON) == changed_to(
        RunPhase.CLEAN_UP, STOP_REASON
    )
    assert tracker.phase is RunPhase.CLEAN_UP
    assert tracker.phase_started_t == 40.0
    assert tracker.wants_system_middle is True


def test_clean_up_waiting_after_done_in_prepare_wants_the_system_middle():
    tracker = PhaseTracker(START_T)
    tracker.on_system_output(11.0, done=True)
    tracker.begin_clean_up(12.0, STOP_REASON)
    assert tracker.wants_system_middle is True


def test_clean_up_twice_does_nothing():
    tracker = cleaning_tracker()
    assert tracker.begin_clean_up(31.0, "middle failed 5 times in a row") == NO_EVENT
    assert tracker.phase_started_t == 30.0


def test_judgments_do_not_change_clean_up():
    tracker = cleaning_tracker()
    events = judge_many(tracker, [None] * EXIT_STREAK + ["home_menu"] * 3, 31.0)
    assert events == [NO_EVENT] * (EXIT_STREAK + 3)
    assert tracker.phase is RunPhase.CLEAN_UP


def test_done_on_the_home_menu_ends_clean_up():
    tracker = cleaning_tracker()
    tracker.on_judgment(31.0, "home_menu")
    assert tracker.on_system_output(32.0, done=True) == PhaseEvent(
        clean_up_end=CleanUpResult.DONE
    )
    assert tracker.wants_system_middle is False


@pytest.mark.parametrize("latest", [None, "software_close", "user_select"])
def test_done_on_another_screen_does_not_end_clean_up(latest):
    tracker = cleaning_tracker()
    tracker.on_judgment(31.0, "home_menu")
    tracker.on_judgment(31.5, latest)
    assert tracker.on_system_output(32.0, done=True) == NO_EVENT
    assert tracker.wants_system_middle is True


def test_not_done_on_the_home_menu_does_not_end_clean_up():
    tracker = cleaning_tracker()
    tracker.on_judgment(31.0, "home_menu")
    assert tracker.on_system_output(32.0, done=False) == NO_EVENT


def test_done_without_any_judgment_in_clean_up_does_not_end_it():
    tracker = cleaning_tracker()
    assert tracker.on_system_output(31.0, done=True) == NO_EVENT


def test_clean_up_times_out():
    tracker = cleaning_tracker()
    assert tracker.check_time(30.0 + CLEAN_UP_TIMEOUT_S - 0.1) == NO_EVENT
    assert tracker.check_time(30.0 + CLEAN_UP_TIMEOUT_S) == PhaseEvent(
        clean_up_end=CleanUpResult.TIMEOUT
    )


def test_one_system_middle_failure_ends_clean_up():
    tracker = cleaning_tracker()
    assert tracker.on_system_middle_failed(31.0) == PhaseEvent(
        clean_up_end=CleanUpResult.MIDDLE_FAILED
    )


@pytest.mark.parametrize("phase", [RunPhase.PREPARE, RunPhase.RECOVER])
def test_system_middle_failure_outside_clean_up_does_nothing(phase):
    tracker = tracker_in(phase)
    assert tracker.on_system_middle_failed(40.0) == NO_EVENT
    assert tracker.phase is phase


def test_nothing_happens_after_clean_up_ends():
    tracker = cleaning_tracker()
    tracker.on_system_middle_failed(31.0)
    tracker.on_judgment(31.5, "home_menu")
    assert tracker.on_system_output(32.0, done=True) == NO_EVENT
    assert tracker.check_time(30.0 + CLEAN_UP_TIMEOUT_S) == NO_EVENT
    assert tracker.on_system_middle_failed(80.0) == NO_EVENT
    assert tracker.wants_system_middle is False
    assert tracker.phase is RunPhase.CLEAN_UP


def test_clean_up_timeout_uses_custom_timings():
    tracker = PhaseTracker(START_T, PhaseTimings(clean_up_timeout_s=0.5))
    tracker.begin_clean_up(11.0, STOP_REASON)
    assert tracker.check_time(11.5) == PhaseEvent(clean_up_end=CleanUpResult.TIMEOUT)
