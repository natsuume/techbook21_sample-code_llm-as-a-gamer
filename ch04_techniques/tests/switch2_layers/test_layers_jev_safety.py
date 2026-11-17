"""JevSafetyChecker を MockTransport の Jev に対して動かす (実際の API は呼ばない)。

見本の画像は偽物のバイト列を渡す (パッケージの見本の画像には依らない)。
"""

import base64
import itertools
import json

import httpx
import pytest

from switch2_layers.safety.jev_safety import JevSafetyChecker
from switch2_layers.safety.safety_questions import (
    SAFETY_INSTRUCTIONS,
    SAFETY_QUESTION_NAMES,
    SAFETY_QUESTION_TEXTS,
    SYSTEM_SCREEN_QUESTION_TEXT,
)
from switch2_layers.surface.jev_client import JevClient, JevError
from switch2_layers.system_screen.system_screen_references import (
    NONE_CHOICE_DESCRIPTION,
    SYSTEM_SCREEN_CHOICE_NAMES,
    SYSTEM_SCREEN_NAMES,
    SYSTEM_SCREEN_REFERENCES,
)

JPEG = b"\xff\xd8\xff\xe0safety-frame"
USAGE = {"input_tokens": 12950, "output_tokens": 8}
REFERENCE_IMAGES = {
    name: (b"\xff\xd8" + f"reference-{name}".encode(),) for name in SYSTEM_SCREEN_NAMES
}
NOUL_VALUES = {"online": 0.0, "purchase": 0.25, "system_settings": 1}
SCREEN_VALUES = {
    "none": 0.02,
    "home_menu": 0.96,
    "controllers": 0.01,
    "controller_order": 0.0,
    "user_select": 0.01,
    "software_close": 0,
    "software_list": 0,
}


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

    def payloads(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


def answers_body(
    nouls: dict[str, object] = NOUL_VALUES,
    screen: dict[str, object] | None = SCREEN_VALUES,
    screen_type: str = "choice",
    usage: object = USAGE,
) -> dict:
    """nouls の確率を noul の答えに、screen の確率を system_screen の choice の答えにした応答の本文。

    screen が None なら system_screen の答えを入れない。
    """
    answers: dict[str, object] = {
        name: {"type": "noul", "noul": value} for name, value in nouls.items()
    }
    if screen is not None:
        answers["system_screen"] = {
            "type": screen_type,
            "choice": "home_menu",
            "confidence": 0.96,
            "probabilities": screen,
        }
    body: dict[str, object] = {"model": "jev-1.14.0", "answers": answers}
    if usage is not None:
        body["usage"] = usage
    return body


def counting_references():
    """呼ぶたびに ref-1、ref-2、... を返す参照文字列の作り方。"""
    counter = itertools.count(1)
    return lambda: f"ref-{next(counter)}"


def checker_for(
    mock: MockJev,
    reference_images: dict[str, tuple[bytes, ...]] = REFERENCE_IMAGES,
) -> JevSafetyChecker:
    client = JevClient(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(mock))
    )
    return JevSafetyChecker(
        client, reference_images, make_reference=counting_references()
    )


def string_values(value: object) -> list[str]:
    """JSON の値の中の文字列を、入れ子の中まで (dict のキーは除く) すべて並べる。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in string_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in string_values(item)]
    return []


def base64_of(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- 要求 ---


def test_check_sends_the_frame_and_the_references_with_four_questions():
    mock = MockJev(answers_body())
    checker_for(mock).check(JPEG)
    [request] = mock.requests
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["TypeSafe-Danger-Zone-Images"] == "true"
    [payload] = mock.payloads()
    assert payload["model"] == "jev-1.14.0"
    # 参照文字列は、今のフレーム、見本の表の順の見本の画像の順に作る
    references = {name: f"ref-{i}" for i, name in enumerate(SYSTEM_SCREEN_NAMES, 2)}
    assert payload["state"] == {
        "instructions": SAFETY_INSTRUCTIONS,
        "frame": "ref-1",
        "references": references,
    }
    assert list(payload["state"]["references"]) == list(SYSTEM_SCREEN_NAMES)
    assert payload["assets"] == {
        "ref-1": {"type": "image", "format": "jpg", "data_base64": base64_of(JPEG)},
        **{
            reference: {
                "type": "image",
                "format": "jpg",
                "data_base64": base64_of(REFERENCE_IMAGES[name][0]),
            }
            for name, reference in references.items()
        },
    }


def test_check_asks_three_nouls_and_the_system_screen_choice():
    mock = MockJev(answers_body())
    checker_for(mock).check(JPEG)
    [payload] = mock.payloads()
    questions = payload["questions"]
    assert list(questions) == [*SAFETY_QUESTION_NAMES, "system_screen"]
    for name in SAFETY_QUESTION_NAMES:
        assert questions[name] == {
            "type": "noul",
            "instructions": SAFETY_QUESTION_TEXTS[name],
        }
    assert questions["system_screen"] == {
        "type": "choice",
        "instructions": SYSTEM_SCREEN_QUESTION_TEXT,
        "criteria": {
            "none": NONE_CHOICE_DESCRIPTION,
            **{
                reference.name: reference.description
                for reference in SYSTEM_SCREEN_REFERENCES
            },
        },
    }
    assert list(questions["system_screen"]["criteria"]) == list(
        SYSTEM_SCREEN_CHOICE_NAMES
    )


def test_each_image_reference_is_placed_once_in_the_state():
    # 参照文字列は書くたびに同じ画像が足されるため、state の中に 1 回ずつだけ置く
    mock = MockJev(answers_body())
    checker_for(mock).check(JPEG)
    [payload] = mock.payloads()
    texts = string_values(payload["state"]) + string_values(payload["questions"])
    for reference in payload["assets"]:
        assert texts.count(reference) == 1, reference
    assert len(payload["assets"]) == 1 + len(SYSTEM_SCREEN_NAMES)


def test_each_check_uses_new_references_for_the_same_images():
    mock = MockJev(answers_body())
    checker = checker_for(mock)
    checker.check(JPEG)
    checker.check(JPEG)
    first, second = mock.payloads()
    assert set(first["assets"]).isdisjoint(second["assets"])
    assert sorted(asset["data_base64"] for asset in first["assets"].values()) == sorted(
        asset["data_base64"] for asset in second["assets"].values()
    )


# --- 答え ---


def test_check_returns_probabilities_system_screen_and_usage_in_order():
    reversed_nouls = {**dict(reversed(NOUL_VALUES.items())), "x": 0.5}
    reversed_screen = {**dict(reversed(SCREEN_VALUES.items())), "unknown": 0.9}
    mock = MockJev(answers_body(reversed_nouls, reversed_screen))
    answer = checker_for(mock).check(JPEG)
    assert list(answer.probabilities) == list(SAFETY_QUESTION_NAMES)
    assert answer.probabilities == {
        "online": 0.0,
        "purchase": 0.25,
        "system_settings": 1.0,
    }
    assert list(answer.system_screen) == list(SYSTEM_SCREEN_CHOICE_NAMES)
    assert answer.system_screen == {
        name: float(value) for name, value in SCREEN_VALUES.items()
    }
    assert answer.usage == USAGE


def test_check_without_usage_returns_none_usage():
    mock = MockJev(answers_body(usage=None))
    assert checker_for(mock).check(JPEG).usage is None


# --- 失敗 ---


def without(values: dict[str, object], name: str) -> dict[str, object]:
    return {key: value for key, value in values.items() if key != name}


@pytest.mark.parametrize(
    "mock",
    [
        MockJev(status=500),
        MockJev(answers_body({**NOUL_VALUES, "online": 1.5})),
        MockJev(answers_body({**NOUL_VALUES, "online": "high"})),
        MockJev(answers_body(without(NOUL_VALUES, "purchase"))),
        MockJev(answers_body(screen=None)),
        MockJev(answers_body(screen=without(SCREEN_VALUES, "software_close"))),
        MockJev(answers_body(screen=without(SCREEN_VALUES, "none"))),
        MockJev(answers_body(screen={**SCREEN_VALUES, "home_menu": 1.2})),
        MockJev(answers_body(screen={**SCREEN_VALUES, "none": -0.1})),
        MockJev(answers_body(screen={**SCREEN_VALUES, "user_select": "low"})),
        MockJev(answers_body(screen=SCREEN_VALUES, screen_type="noul")),
    ],
    ids=[
        "http_error",
        "noul_out_of_range",
        "noul_not_a_number",
        "missing_noul_answer",
        "missing_system_screen_answer",
        "missing_reference_choice",
        "missing_none_choice",
        "choice_over_one",
        "choice_below_zero",
        "choice_not_a_number",
        "system_screen_not_a_choice",
    ],
)
def test_check_failures_are_jev_errors_without_retry(mock):
    with pytest.raises(JevError):
        checker_for(mock).check(JPEG)
    assert len(mock.requests) == 1


@pytest.mark.parametrize(
    "reference_images",
    [
        without(REFERENCE_IMAGES, "software_close"),
        {**REFERENCE_IMAGES, "home_menu": ()},
        {**REFERENCE_IMAGES, "home_menu": (b"one", b"two")},
    ],
    ids=["missing_reference", "no_image", "two_images"],
)
def test_check_with_unusable_reference_images_sends_nothing(reference_images):
    mock = MockJev(answers_body())
    with pytest.raises(ValueError):
        checker_for(mock, reference_images).check(JPEG)
    assert mock.requests == []
