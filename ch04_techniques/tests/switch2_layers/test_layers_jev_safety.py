"""JevSafetyChecker を MockTransport の Jev に対して動かす (実際の API は呼ばない)。"""

import base64
import json

import httpx
import pytest

from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_questions import (
    SAFETY_INSTRUCTIONS,
    SAFETY_QUESTION_NAMES,
    SAFETY_QUESTION_TEXTS,
)
from switch2_layers.surface.jev_client import JevClient, JevError

JPEG = b"\xff\xd8\xff\xe0safety-frame"
USAGE = {"input_tokens": 1500, "output_tokens": 5}


class MockJev:
    """Jev の偽物。受け取った要求を記録し、決めた本文 (または HTTP のエラー) を返す。"""

    def __init__(self, body: dict | None = None, status: int = 200):
        self.body = body
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="server error")
        return httpx.Response(200, json=self.body)


def answers_body(values: dict[str, object], usage: object = USAGE) -> dict:
    """values の確率を noul の答えにした応答の本文。"""
    body: dict[str, object] = {
        "model": "jev-1.14.0",
        "answers": {
            name: {"type": "noul", "noul": value} for name, value in values.items()
        },
    }
    if usage is not None:
        body["usage"] = usage
    return body


def checker_for(mock: MockJev) -> JevSafetyChecker:
    client = JevClient(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(mock))
    )
    return JevSafetyChecker(client, make_reference=lambda: "ref-1")


SOME_VALUES = {
    "home_menu": 0.9,
    "user_select": 0.1,
    "online": 0.0,
    "purchase": 0.25,
    "system_settings": 1,
}


def test_check_sends_one_image_and_five_noul_questions():
    mock = MockJev(answers_body(SOME_VALUES))
    checker_for(mock).check(JPEG)
    [request] = mock.requests
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["TypeSafe-Danger-Zone-Images"] == "true"
    payload = json.loads(request.content)
    assert payload["model"] == "jev-1.14.0"
    assert payload["state"] == {"instructions": SAFETY_INSTRUCTIONS, "frame": "ref-1"}
    assert list(payload["questions"]) == list(SAFETY_QUESTION_NAMES)
    for name, question in payload["questions"].items():
        assert question == {"type": "noul", "instructions": SAFETY_QUESTION_TEXTS[name]}
    assert payload["assets"] == {
        "ref-1": {
            "type": "image",
            "format": "jpg",
            "data_base64": base64.b64encode(JPEG).decode("ascii"),
        }
    }


def test_check_returns_probabilities_in_order_and_usage():
    mock = MockJev(answers_body({**dict(reversed(SOME_VALUES.items())), "x": 0.5}))
    answer = checker_for(mock).check(JPEG)
    assert list(answer.probabilities) == list(SAFETY_QUESTION_NAMES)
    assert answer.probabilities == {
        "home_menu": 0.9,
        "user_select": 0.1,
        "online": 0.0,
        "purchase": 0.25,
        "system_settings": 1.0,
    }
    assert answer.usage == USAGE


def test_check_without_usage_returns_none_usage():
    mock = MockJev(answers_body(SOME_VALUES, usage=None))
    assert checker_for(mock).check(JPEG).usage is None


@pytest.mark.parametrize(
    "mock",
    [
        MockJev(status=500),
        MockJev(answers_body({**SOME_VALUES, "online": 1.5})),
        MockJev(answers_body({**SOME_VALUES, "online": "high"})),
        MockJev(
            answers_body({k: v for k, v in SOME_VALUES.items() if k != "purchase"})
        ),
    ],
    ids=["http_error", "out_of_range", "not_a_number", "missing_answer"],
)
def test_check_failures_are_jev_errors_without_retry(mock):
    with pytest.raises(JevError):
        checker_for(mock).check(JPEG)
    assert len(mock.requests) == 1
