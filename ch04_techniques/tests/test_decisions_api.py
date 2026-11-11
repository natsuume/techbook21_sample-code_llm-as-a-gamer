import base64
import json

import httpx
import pytest

from switch2_agent.decision_models.base import Decision, DecisionModelError
from switch2_agent.decision_models.decisions_api import (
    API_KEY_ENV,
    DECISIONS_URL,
    DEFAULT_DECISIONS_MODEL,
    DecisionsApiModel,
    build_decisions_headers,
    build_decisions_payload,
    jpeg_data_url,
    parse_decisions_response,
)
from switch2_agent.decision_request import DecisionRequest, LabeledImage
from switch2_agent.questions import Question

IMAGE_ONE = b"jpeg-image-one"
IMAGE_TWO = b"jpeg-image-two"
NAMES = ["press_A", "tap_B", "advisor"]


def sample_request() -> DecisionRequest:
    return DecisionRequest(
        instructions="あなたはコントローラーの判断をする。",
        state_text="## 戦略\n右へ進む",
        images=[
            LabeledImage("過去の場面 1/1 (3.00 秒前)", IMAGE_ONE, 1.0),
            LabeledImage("直近 1/1 (0.10 秒前)", IMAGE_TWO, 3.9),
        ],
        questions=[
            Question(
                "press_A", "今、A ボタン を押した状態にするべきか。", "press", "A"
            ),
            Question("tap_B", "今、B ボタン を 1 回だけ短く押すべきか。", "tap", "B"),
            Question(
                "advisor", "今、advisor に戦略の更新を求めるべきか。", "advisor", ""
            ),
        ],
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decision_body(answers: list[dict]) -> dict:
    """API リファレンスの応答の例 (model・answers・usage) と同じ形の本文を返す。"""
    return {
        "model": "gpt-6-luna",
        "answers": answers,
        "usage": {
            "input_tokens": 42,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 42,
        },
    }


def predicate(name: str, probability: object) -> dict:
    return {"type": "predicate", "name": name, "probability": probability}


ALL_ANSWERS = [
    predicate("press_A", 0.95),
    predicate("tap_B", 0.1),
    predicate("advisor", 0.0),
]


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- 定数とヘッダー ---


def test_constants_follow_report():
    assert DECISIONS_URL == "https://api.openai.com/v1/decisions"
    assert DEFAULT_DECISIONS_MODEL == "gpt-6-luna"
    assert API_KEY_ENV == "OPENAI_API_KEY"


def test_headers_use_bearer_token_and_json():
    assert build_decisions_headers("sk-test") == {
        "Authorization": "Bearer sk-test",
        "Content-Type": "application/json",
    }


def test_jpeg_data_url():
    assert jpeg_data_url(IMAGE_ONE) == f"data:image/jpeg;base64,{b64(IMAGE_ONE)}"


# --- payload ---


def test_payload_has_model_input_and_questions():
    payload = build_decisions_payload(sample_request())
    assert set(payload) == {"model", "input", "questions"}
    assert payload["model"] == "gpt-6-luna"


def test_payload_input_is_one_user_message_with_text_and_labeled_images():
    request = sample_request()
    payload = build_decisions_payload(request)
    assert len(payload["input"]) == 1
    message = payload["input"][0]
    assert message["role"] == "user"
    assert message["content"] == [
        {"type": "input_text", "text": request.instructions},
        {"type": "input_text", "text": request.state_text},
        {"type": "input_text", "text": "過去の場面 1/1 (3.00 秒前)"},
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64(IMAGE_ONE)}",
        },
        {"type": "input_text", "text": "直近 1/1 (0.10 秒前)"},
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64(IMAGE_TWO)}",
        },
    ]


def test_payload_questions_are_predicates_with_name_and_instructions():
    request = sample_request()
    payload = build_decisions_payload(request)
    assert payload["questions"] == [
        {"type": "predicate", "name": q.name, "instructions": q.text}
        for q in request.questions
    ]


def test_payload_contains_each_image_once():
    text = json.dumps(build_decisions_payload(sample_request()), ensure_ascii=False)
    assert text.count(b64(IMAGE_ONE)) == 1
    assert text.count(b64(IMAGE_TWO)) == 1


def test_payload_uses_given_model():
    assert build_decisions_payload(sample_request(), model="other")["model"] == "other"


# --- 応答の解釈 ---


def test_parse_reads_probabilities_by_name():
    decision = parse_decisions_response(decision_body(ALL_ANSWERS), NAMES)
    assert decision == Decision(
        probabilities={"press_A": 0.95, "tap_B": 0.1, "advisor": 0.0}, refusals=0
    )


def test_parse_matches_answers_by_name_not_order():
    answers = list(reversed(ALL_ANSWERS))
    decision = parse_decisions_response(decision_body(answers), NAMES)
    assert decision.probabilities == {"press_A": 0.95, "tap_B": 0.1, "advisor": 0.0}


def test_parse_ignores_answers_not_requested():
    answers = [*ALL_ANSWERS, predicate("extra", 0.5)]
    decision = parse_decisions_response(decision_body(answers), NAMES)
    assert set(decision.probabilities) == set(NAMES)


def test_parse_treats_refusal_as_zero_and_counts_it():
    answers = [
        predicate("press_A", 0.95),
        {"type": "refusal", "name": "tap_B"},
        {"type": "refusal", "name": "advisor"},
    ]
    decision = parse_decisions_response(decision_body(answers), NAMES)
    assert decision.probabilities == {"press_A": 0.95, "tap_B": 0.0, "advisor": 0.0}
    assert decision.refusals == 2


def test_parse_rejects_missing_answer():
    answers = [predicate("press_A", 0.95), predicate("tap_B", 0.1)]
    with pytest.raises(DecisionModelError):
        parse_decisions_response(decision_body(answers), NAMES)


@pytest.mark.parametrize("probability", [1.2, -0.1, "high", None, True])
def test_parse_rejects_invalid_probability(probability):
    answers = [predicate("press_A", probability), *ALL_ANSWERS[1:]]
    with pytest.raises(DecisionModelError):
        parse_decisions_response(decision_body(answers), NAMES)


@pytest.mark.parametrize(
    "body",
    [
        {"model": "gpt-6-luna"},
        {"answers": {"press_A": 0.5}},
        {"answers": ["press_A"]},
        {"answers": [{"type": "choice", "name": "press_A", "choice": "yes"}]},
    ],
)
def test_parse_rejects_malformed_body(body):
    with pytest.raises(DecisionModelError):
        parse_decisions_response(body, ["press_A"])


# --- HTTP ---


def test_decide_posts_payload_and_returns_decision():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=decision_body(ALL_ANSWERS))

    model = DecisionsApiModel("sk-test", client=mock_client(handler))
    request = sample_request()
    decision = model.decide(request)
    assert decision == Decision(
        probabilities={"press_A": 0.95, "tap_B": 0.1, "advisor": 0.0}, refusals=0
    )
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == DECISIONS_URL
    assert sent.headers["authorization"] == "Bearer sk-test"
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == build_decisions_payload(request)


def test_decide_counts_refusals():
    answers = [
        predicate("press_A", 0.95),
        {"type": "refusal", "name": "tap_B"},
        ALL_ANSWERS[2],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=decision_body(answers))

    decision = DecisionsApiModel("sk-test", client=mock_client(handler)).decide(
        sample_request()
    )
    assert decision.refusals == 1
    assert decision.probabilities["tap_B"] == 0.0


def test_decide_uses_given_model_and_timeout():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=decision_body(ALL_ANSWERS))

    model = DecisionsApiModel(
        "sk-test", client=mock_client(handler), timeout_s=3.5, model="other-model"
    )
    model.decide(sample_request())
    assert json.loads(requests[0].content)["model"] == "other-model"
    assert requests[0].extensions["timeout"]["read"] == 3.5


def test_decide_raises_decision_model_error_on_http_500_with_body_head():
    body = "E" * 500 + "TAIL"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=body)

    model = DecisionsApiModel("sk-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError) as excinfo:
        model.decide(sample_request())
    message = str(excinfo.value)
    assert "500" in message
    assert "E" * 500 in message
    assert "TAIL" not in message


def test_decide_raises_decision_model_error_on_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    model = DecisionsApiModel("sk-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError):
        model.decide(sample_request())


def test_decide_raises_decision_model_error_on_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    model = DecisionsApiModel("sk-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError):
        model.decide(sample_request())


def test_decide_raises_decision_model_error_on_missing_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=decision_body(ALL_ANSWERS[:2]))

    model = DecisionsApiModel("sk-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError):
        model.decide(sample_request())
