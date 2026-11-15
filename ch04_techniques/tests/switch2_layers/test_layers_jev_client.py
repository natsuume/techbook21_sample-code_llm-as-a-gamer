"""Jev のクライアント。httpx の MockTransport で要求を受け、外部には送らない。"""

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from switch2_layers.surface.jev_client import (
    API_KEY_ENV,
    DEFAULT_JEV_MODEL,
    DEFAULT_TIMEOUT_S,
    IMAGES_HEADER,
    JEV_URL,
    JevBox,
    JevClient,
    JevError,
    JevImage,
    build_assets,
    build_headers,
    build_payload,
    build_question_json,
    needs_images_header,
    new_asset_reference,
    parse_answers,
    read_probability,
)
from switch2_layers.surface.surface_questions import (
    Choice,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)

BUTTON = SurfaceQuestion(
    name="button_A",
    instructions="今、A ボタンをどうするか。",
    kind=QuestionKind.BUTTON,
    target="A",
    question_type=QuestionType.CHOICE,
    choices=(Choice("none", "押さない"), Choice("hold", "押す"), Choice("tap", "短く")),
)
NOUL = SurfaceQuestion(
    name="scene_changed",
    instructions="画面の種類が変わったか。",
    kind=QuestionKind.SCENE_CHANGE,
    target="",
    question_type=QuestionType.NOUL,
)
BOX = SurfaceQuestion(
    name="box_1",
    instructions="`frames[1].image` の中の「自機」(勇者) を囲む box。",
    kind=QuestionKind.BOX,
    target="自機",
    question_type=QuestionType.BOX,
)
QUESTIONS = (BUTTON, NOUL, BOX)
IMAGES = (
    JevImage("11111111-aaaa", b"\xff\xd8first"),
    JevImage("22222222-bbbb", b"\xff\xd8second"),
)
STATE = {
    "instructions": "説明",
    "frames": [
        {"label": "t=1.000 秒 (1.0 秒前)", "image": "11111111-aaaa"},
        {"label": "t=2.000 秒 (0.0 秒前)", "image": "22222222-bbbb"},
    ],
}


def good_answers() -> dict[str, object]:
    return {
        "button_A": {
            "type": "choice",
            "choice": "hold",
            "confidence": 0.7,
            "probabilities": {"none": 0.2, "hold": 0.7, "tap": 0.1},
        },
        "scene_changed": {"type": "noul", "noul": 0.25},
        "box_1": {
            "type": "box",
            "box": {"top": 0.2, "bottom": 0.66, "left": 0.19, "right": 0.68},
            "confidence": None,
        },
    }


def body_with(answers: dict[str, object]) -> dict[str, object]:
    return {
        "model": DEFAULT_JEV_MODEL,
        "answers": answers,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class Recorder:
    """MockTransport の処理。受けた要求を記録し、respond が返す応答を返す。"""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]):
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def client_returning(
    respond: Callable[[httpx.Request], httpx.Response],
) -> tuple[JevClient, Recorder]:
    recorder = Recorder(respond)
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    return JevClient("test-key", client=http), recorder


def json_response(
    body: object, status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body)


# --- 定数 ---


def test_constants_follow_the_issue():
    assert JEV_URL == "https://api.typesafe.ai/v1/systemone"
    assert DEFAULT_JEV_MODEL == "jev-1.14.0"
    assert API_KEY_ENV == "TYPESAFE_API_KEY"
    assert IMAGES_HEADER == "TypeSafe-Danger-Zone-Images"
    assert DEFAULT_TIMEOUT_S == 10.0


def test_new_asset_reference_is_new_each_time():
    first = new_asset_reference()
    second = new_asset_reference()
    assert first != second
    assert len(first) == 36


# --- 要求の形 ---


def test_headers():
    assert build_headers("k", with_images=False) == {
        "Authorization": "Bearer k",
        "Content-Type": "application/json",
    }
    assert build_headers("k", with_images=True)[IMAGES_HEADER] == "true"


def test_images_header_is_needed_for_images_or_box_questions():
    assert needs_images_header([NOUL], IMAGES) is True
    assert needs_images_header([NOUL, BOX], []) is True
    assert needs_images_header([BUTTON, NOUL], []) is False


def test_question_json_by_type():
    assert build_question_json(BUTTON) == {
        "type": "choice",
        "instructions": "今、A ボタンをどうするか。",
        "criteria": {"none": "押さない", "hold": "押す", "tap": "短く"},
    }
    assert list(build_question_json(BUTTON)["criteria"]) == ["none", "hold", "tap"]
    assert build_question_json(NOUL) == {
        "type": "noul",
        "instructions": "画面の種類が変わったか。",
    }
    assert build_question_json(BOX) == {
        "type": "box",
        "instructions": "`frames[1].image` の中の「自機」(勇者) を囲む box。",
    }


def test_assets_are_base64_jpeg():
    assets = build_assets(IMAGES)
    assert list(assets) == ["11111111-aaaa", "22222222-bbbb"]
    assert assets["11111111-aaaa"] == {
        "type": "image",
        "format": "jpg",
        "data_base64": base64.b64encode(b"\xff\xd8first").decode("ascii"),
    }


def test_duplicate_asset_reference_is_an_error():
    with pytest.raises(ValueError):
        build_assets([IMAGES[0], IMAGES[0]])


def test_payload_keys_and_questions_in_order():
    payload = build_payload(STATE, QUESTIONS, IMAGES)
    assert list(payload) == ["model", "state", "questions", "assets"]
    assert payload["model"] == DEFAULT_JEV_MODEL
    assert payload["state"] == STATE
    assert list(payload["questions"]) == ["button_A", "scene_changed", "box_1"]


def test_payload_without_images_has_no_assets():
    payload = build_payload({"instructions": "x"}, [NOUL], [])
    assert "assets" not in payload


def test_duplicate_question_id_is_an_error():
    with pytest.raises(ValueError):
        build_payload(STATE, [NOUL, NOUL], IMAGES)


def test_each_asset_reference_appears_once_outside_assets():
    payload = build_payload(STATE, QUESTIONS, IMAGES)
    without_assets = json.dumps({k: v for k, v in payload.items() if k != "assets"})
    for image in IMAGES:
        assert without_assets.count(image.reference) == 1


# --- 送信 ---


def test_ask_posts_json_with_headers_and_timeout():
    client, recorder = client_returning(json_response(body_with(good_answers())))
    client.ask(STATE, QUESTIONS, IMAGES)
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == JEV_URL
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers[IMAGES_HEADER] == "true"
    assert request.extensions["timeout"]["read"] == DEFAULT_TIMEOUT_S
    assert json.loads(request.content) == build_payload(STATE, QUESTIONS, IMAGES)


def test_ask_without_images_and_box_has_no_images_header():
    client, recorder = client_returning(
        json_response(body_with({"scene_changed": {"type": "noul", "noul": 0.5}}))
    )
    client.ask({"instructions": "x"}, [NOUL], [])
    assert IMAGES_HEADER not in recorder.requests[0].headers


def test_ask_reads_answers():
    client, _ = client_returning(json_response(body_with(good_answers())))
    answers = client.ask(STATE, QUESTIONS, IMAGES)
    assert answers.choices == {"button_A": {"none": 0.2, "hold": 0.7, "tap": 0.1}}
    assert answers.nouls == {"scene_changed": 0.25}
    assert answers.boxes == {
        "box_1": JevBox(top=0.2, bottom=0.66, left=0.19, right=0.68, confidence=None)
    }


def test_http_error_is_jev_error_without_retry():
    client, recorder = client_returning(
        lambda request: httpx.Response(500, text="boom")
    )
    with pytest.raises(JevError, match="500"):
        client.ask(STATE, QUESTIONS, IMAGES)
    assert len(recorder.requests) == 1


def test_transport_error_is_jev_error():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client, recorder = client_returning(fail)
    with pytest.raises(JevError):
        client.ask(STATE, QUESTIONS, IMAGES)
    assert len(recorder.requests) == 1


def test_timeout_is_jev_error():
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = client_returning(time_out)
    with pytest.raises(JevError):
        client.ask(STATE, QUESTIONS, IMAGES)


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b'"text"'])
def test_body_that_is_not_a_json_object_is_jev_error(content):
    client, _ = client_returning(lambda request: httpx.Response(200, content=content))
    with pytest.raises(JevError):
        client.ask(STATE, QUESTIONS, IMAGES)


def test_close_does_not_close_given_client():
    http = httpx.Client(transport=httpx.MockTransport(json_response({})))
    client = JevClient("k", client=http)
    client.close()
    client.close()
    assert not http.is_closed


# --- 応答の読み取りと不正な応答 ---


def test_probability_reading():
    assert read_probability(0, "q") == 0.0
    assert read_probability(1, "q") == 1.0
    assert read_probability(0.25, "q") == 0.25


@pytest.mark.parametrize(
    "value", [None, "0.5", True, -0.01, 1.01, float("nan"), float("inf")]
)
def test_invalid_probability_is_jev_error(value):
    with pytest.raises(JevError):
        read_probability(value, "q")


def test_answers_must_be_an_object():
    with pytest.raises(JevError):
        parse_answers({"answers": []}, QUESTIONS)
    with pytest.raises(JevError):
        parse_answers({}, QUESTIONS)


def test_missing_answer_is_jev_error():
    answers = good_answers()
    del answers["scene_changed"]
    with pytest.raises(JevError, match="scene_changed"):
        parse_answers(body_with(answers), QUESTIONS)


def test_unrequested_answers_and_choices_are_ignored():
    answers = good_answers()
    answers["extra"] = {"type": "noul", "noul": 2.0}
    answers["button_A"]["probabilities"]["extra"] = 5.0
    parsed = parse_answers(body_with(answers), QUESTIONS)
    assert "extra" not in parsed.nouls
    assert parsed.choices["button_A"] == {"none": 0.2, "hold": 0.7, "tap": 0.1}


@pytest.mark.parametrize(
    "name, answer",
    [
        ("scene_changed", {"type": "choice", "probabilities": {}}),
        ("scene_changed", "0.5"),
        ("scene_changed", {"type": "noul", "noul": 1.5}),
        ("scene_changed", {"type": "noul"}),
        ("button_A", {"type": "noul", "noul": 0.5}),
        ("button_A", {"type": "choice", "probabilities": [0.2, 0.7, 0.1]}),
        ("button_A", {"type": "choice", "probabilities": {"none": 0.5, "hold": 0.5}}),
        (
            "button_A",
            {"type": "choice", "probabilities": {"none": 0.5, "hold": "x", "tap": 0.0}},
        ),
        ("box_1", {"type": "noul", "noul": 0.5}),
        ("box_1", {"type": "box", "box": [0.1, 0.2, 0.3, 0.4], "confidence": 0.5}),
        (
            "box_1",
            {
                "type": "box",
                "box": {"top": 0.1, "bottom": 0.2, "left": 0.3},
                "confidence": 0.5,
            },
        ),
        (
            "box_1",
            {
                "type": "box",
                "box": {"top": 0.1, "bottom": 1.2, "left": 0.3, "right": 0.4},
                "confidence": 0.5,
            },
        ),
        (
            "box_1",
            {
                "type": "box",
                "box": {"top": 0.1, "bottom": 0.2, "left": True, "right": 0.4},
                "confidence": 0.5,
            },
        ),
        (
            "box_1",
            {
                "type": "box",
                "box": {"top": 0.1, "bottom": 0.2, "left": 0.3, "right": 0.4},
                "confidence": 1.5,
            },
        ),
        (
            "box_1",
            {
                "type": "box",
                "box": {"top": 0.1, "bottom": 0.2, "left": 0.3, "right": 0.4},
                "confidence": "high",
            },
        ),
    ],
)
def test_malformed_answer_is_jev_error(name, answer):
    answers = good_answers()
    answers[name] = answer
    with pytest.raises(JevError):
        parse_answers(body_with(answers), QUESTIONS)


def test_box_confidence_number_is_read():
    answers = good_answers()
    answers["box_1"]["confidence"] = 0.92
    parsed = parse_answers(body_with(answers), QUESTIONS)
    assert parsed.boxes["box_1"].confidence == 0.92


@pytest.mark.parametrize(
    "edges",
    [
        # 上端が下端より下
        {"top": 0.5, "bottom": 0.2, "left": 0.3, "right": 0.4},
        # 幅が 0
        {"top": 0.1, "bottom": 0.2, "left": 0.4, "right": 0.4},
    ],
)
def test_box_without_area_is_no_usable_box_not_an_error(edges):
    answers = good_answers()
    answers["box_1"] = {"type": "box", "box": edges, "confidence": 0.5}
    parsed = parse_answers(body_with(answers), QUESTIONS)
    assert parsed.boxes["box_1"] is None
    # ほかの問いの答えは読める (要求全体を失敗にしない)
    assert parsed.nouls == {"scene_changed": 0.25}


def test_non_finite_probability_in_body_is_jev_error():
    # Python の json は NaN を読めるため、本文に NaN があっても確率として受け付けないことを確かめる
    content = json.dumps(body_with(good_answers())).replace("0.25", "NaN").encode()
    client, _ = client_returning(lambda request: httpx.Response(200, content=content))
    with pytest.raises(JevError):
        client.ask(STATE, QUESTIONS, IMAGES)
