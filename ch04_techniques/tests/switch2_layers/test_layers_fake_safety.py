from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES
from switch2_layers.system_screen.screen_judgment import judge_system_screen
from switch2_layers.system_screen.system_screen_references import (
    SYSTEM_SCREEN_CHOICE_NAMES,
)


def test_fake_returns_zero_for_every_question_without_usage():
    answer = FakeSafetyChecker().check(b"not even a jpeg")
    assert list(answer.probabilities) == list(SAFETY_QUESTION_NAMES)
    assert all(value == 0.0 for value in answer.probabilities.values())
    assert answer.usage is None


def test_fake_answers_that_the_frame_is_no_system_screen():
    answer = FakeSafetyChecker().check(b"not even a jpeg")
    assert list(answer.system_screen) == list(SYSTEM_SCREEN_CHOICE_NAMES)
    assert answer.system_screen["none"] == 1.0
    assert all(
        value == 0.0 for name, value in answer.system_screen.items() if name != "none"
    )
    assert judge_system_screen(answer.system_screen) is None
