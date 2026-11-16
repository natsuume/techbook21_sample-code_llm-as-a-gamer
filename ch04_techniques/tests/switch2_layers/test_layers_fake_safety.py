from switch2_layers.safety.fake_safety import FakeSafetyChecker
from switch2_layers.safety.safety_questions import SAFETY_QUESTION_NAMES


def test_fake_returns_zero_for_every_question_without_usage():
    answer = FakeSafetyChecker().check(b"not even a jpeg")
    assert list(answer.probabilities) == list(SAFETY_QUESTION_NAMES)
    assert all(value == 0.0 for value in answer.probabilities.values())
    assert answer.usage is None
