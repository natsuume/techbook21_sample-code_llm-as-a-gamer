import pytest

from switch2_layers.surface.task_answer_streak import (
    TASK_ANSWER_STREAK,
    TaskAnswerDecision,
    TaskAnswerStreak,
)

THRESHOLD = 0.5
NOTHING = TaskAnswerDecision(done=False, cancel=False)
DONE = TaskAnswerDecision(done=True, cancel=False)
CANCEL = TaskAnswerDecision(done=False, cancel=True)


def observe_all(
    streak: TaskAnswerStreak,
    answers: list[tuple[str | None, float | None, float | None]],
) -> list[TaskAnswerDecision]:
    """answers の (task_id, done_p, cancel_p) を順に数え、決めたものを順に返す。"""
    return [
        streak.observe(task_id, done_p, cancel_p, THRESHOLD)
        for task_id, done_p, cancel_p in answers
    ]


def test_streak_is_two():
    assert TASK_ANSWER_STREAK == 2


def test_done_after_two_requests_in_a_row():
    decisions = observe_all(
        TaskAnswerStreak(), [("task-001", 0.9, 0.1), ("task-001", 0.8, 0.0)]
    )
    assert decisions == [NOTHING, DONE]


def test_cancel_after_two_requests_in_a_row():
    decisions = observe_all(
        TaskAnswerStreak(), [("task-001", 0.1, 0.7), ("task-001", 0.2, 0.51)]
    )
    assert decisions == [NOTHING, CANCEL]


def test_probability_equal_to_the_threshold_does_not_exceed():
    decisions = observe_all(
        TaskAnswerStreak(),
        [("task-001", 0.5, 0.5), ("task-001", 0.5, 0.5), ("task-001", 0.9, 0.9)],
    )
    assert decisions == [NOTHING, NOTHING, NOTHING]


def test_request_below_the_threshold_restarts_the_count():
    decisions = observe_all(
        TaskAnswerStreak(),
        [
            ("task-001", 0.9, 0.0),
            ("task-001", 0.3, 0.0),
            ("task-001", 0.9, 0.0),
            ("task-001", 0.9, 0.0),
        ],
    )
    assert decisions == [NOTHING, NOTHING, NOTHING, DONE]


@pytest.mark.parametrize("missing", ["done", "cancel"])
def test_missing_probability_restarts_the_count(missing):
    streak = TaskAnswerStreak()
    if missing == "done":
        answers = [
            ("task-001", 0.9, 0.0),
            ("task-001", None, 0.0),
            ("task-001", 0.9, 0.0),
        ]
    else:
        answers = [
            ("task-001", 0.0, 0.9),
            ("task-001", 0.0, None),
            ("task-001", 0.0, 0.9),
        ]
    assert observe_all(streak, answers) == [NOTHING, NOTHING, NOTHING]


def test_another_head_task_restarts_the_count():
    decisions = observe_all(
        TaskAnswerStreak(),
        [("task-001", 0.9, 0.9), ("task-002", 0.9, 0.0), ("task-002", 0.9, 0.0)],
    )
    # task-002 になった要求から数え直す
    assert decisions == [NOTHING, NOTHING, DONE]


def test_no_head_task_counts_nothing_and_restarts():
    decisions = observe_all(
        TaskAnswerStreak(),
        [
            ("task-001", 0.9, 0.0),
            (None, 0.9, 0.9),
            # None を挟んだので、同じ ID でも数え直す
            ("task-001", 0.9, 0.0),
            ("task-001", 0.9, 0.0),
        ],
    )
    assert decisions == [NOTHING, NOTHING, NOTHING, DONE]


def test_count_restarts_after_it_is_established():
    decisions = observe_all(
        TaskAnswerStreak(),
        [
            ("task-001", 0.9, 0.0),
            ("task-001", 0.9, 0.0),
            ("task-001", 0.9, 0.0),
            ("task-001", 0.9, 0.0),
        ],
    )
    assert decisions == [NOTHING, DONE, NOTHING, DONE]


def test_done_wins_when_both_are_established():
    decisions = observe_all(
        TaskAnswerStreak(),
        [
            ("task-001", 0.9, 0.9),
            ("task-001", 0.9, 0.9),
            # 両方の数を 0 に戻したので、次の要求では成立しない
            ("task-001", 0.1, 0.9),
            ("task-001", 0.1, 0.9),
        ],
    )
    assert decisions == [NOTHING, DONE, NOTHING, CANCEL]


def test_only_the_established_count_restarts():
    decisions = observe_all(
        TaskAnswerStreak(),
        [
            ("task-001", 0.9, 0.1),
            # done が成立し、cancel の数 (1) は残る
            ("task-001", 0.9, 0.9),
            ("task-001", 0.1, 0.9),
        ],
    )
    assert decisions == [NOTHING, DONE, CANCEL]


def test_threshold_is_given_per_request():
    streak = TaskAnswerStreak()
    assert streak.observe("task-001", 0.7, 0.0, 0.8) == NOTHING
    assert streak.observe("task-001", 0.9, 0.0, 0.8) == NOTHING
    assert streak.observe("task-001", 0.9, 0.0, 0.8) == DONE
