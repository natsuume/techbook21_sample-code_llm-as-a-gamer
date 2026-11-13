import base64
import json
import uuid
from dataclasses import replace

import httpx
import pytest

from switch2_agent.decision_models.base import Decision, DecisionModelError
from switch2_agent.decision_models.jev import (
    API_KEY_ENV,
    DEFAULT_JEV_MODEL,
    IMAGES_HEADER,
    JEV_URL,
    JevModel,
    build_jev_headers,
    build_jev_payload,
    new_asset_reference,
    parse_jev_response,
)
from switch2_agent.decision_request import DecisionRequest, LabeledImage
from switch2_agent.questions import Question

IMAGE_ONE = b"jpeg-image-one"
IMAGE_TWO = b"jpeg-image-two"
NAMES = ["press_A", "tap_B", "advisor"]
HISTORY_TEXT = (
    "## 直近の応答と送った操作 (古い順、最大 5 回)\n"
    "- t=3.80 秒 (0.10 秒前): 押下 none / 短押し A (前回の操作)"
)
PREVIOUS_OPERATION_TEXT = "── t=3.80 秒 (0.10 秒前) に、前回の応答の操作を送った ──"


def sample_request(images: list[LabeledImage] | None = None) -> DecisionRequest:
    if images is None:
        images = [
            LabeledImage("過去の場面 1/1 (3.00 秒前)", IMAGE_ONE, 1.0),
            LabeledImage("直近 1/1 (0.10 秒前)", IMAGE_TWO, 3.9),
        ]
    return DecisionRequest(
        instructions="あなたはコントローラーの判断をする。",
        state_text="## 戦略\n右へ進む",
        images=images,
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


def timeline_request() -> DecisionRequest:
    """前回の操作の後の画像と、操作の履歴・区切りの文を持つ要求を返す。"""
    return replace(
        sample_request(
            images=[
                LabeledImage(
                    "過去の場面 1/1 (t=1.00 秒、3.00 秒前)", IMAGE_ONE, 1.0, False
                ),
                LabeledImage(
                    "直近 1/1 (t=3.90 秒、0.10 秒前、前回の操作の後)",
                    IMAGE_TWO,
                    3.9,
                    True,
                ),
            ]
        ),
        history_text=HISTORY_TEXT,
        previous_operation_text=PREVIOUS_OPERATION_TEXT,
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def fixed_references(*references: str):
    """呼ぶたびに references を順に返す make_reference を返す。"""
    iterator = iter(references)
    return lambda: next(iterator)


def jev_body(answers: dict) -> dict:
    """公式ドキュメントの Noul の応答の例 (model・answers・usage) と同じ形の本文を返す。"""
    return {
        "model": "jev-1.14.0",
        "answers": answers,
        "usage": {"input_tokens": 360, "output_tokens": 39},
    }


ALL_ANSWERS = {
    "press_A": {"type": "noul", "noul": 0.99},
    "tap_B": {"type": "noul", "noul": 0.93},
    "advisor": {"type": "noul", "noul": 0.0},
}


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- 定数とヘッダー ---


def test_constants_follow_report():
    assert JEV_URL == "https://api.typesafe.ai/v1/systemone"
    assert DEFAULT_JEV_MODEL == "jev-1.14.0"
    assert API_KEY_ENV == "TYPESAFE_API_KEY"
    assert IMAGES_HEADER == "TypeSafe-Danger-Zone-Images"


def test_headers_with_images_include_danger_zone_images_header():
    assert build_jev_headers("ts-test", True) == {
        "Authorization": "Bearer ts-test",
        "Content-Type": "application/json",
        "TypeSafe-Danger-Zone-Images": "true",
    }


def test_headers_without_images_have_no_danger_zone_images_header():
    assert build_jev_headers("ts-test", False) == {
        "Authorization": "Bearer ts-test",
        "Content-Type": "application/json",
    }


def test_new_asset_reference_returns_distinct_uuids():
    first, second = new_asset_reference(), new_asset_reference()
    assert first != second
    uuid.UUID(first)
    uuid.UUID(second)


# --- payload ---


def test_payload_registers_images_in_assets_and_places_references_in_state():
    request = sample_request()
    payload = build_jev_payload(
        request, make_reference=fixed_references("ref-1", "ref-2")
    )
    assert payload["model"] == "jev-1.14.0"
    assert payload["state"] == {
        "instructions": request.instructions,
        "game_notes": request.state_text,
        "frames": [
            {"label": "過去の場面 1/1 (3.00 秒前)", "image": "ref-1"},
            {"label": "直近 1/1 (0.10 秒前)", "image": "ref-2"},
        ],
    }
    assert payload["assets"] == {
        "ref-1": {"type": "image", "format": "jpg", "data_base64": b64(IMAGE_ONE)},
        "ref-2": {"type": "image", "format": "jpg", "data_base64": b64(IMAGE_TWO)},
    }


def test_payload_questions_are_noul_map_keyed_by_name():
    request = sample_request()
    payload = build_jev_payload(
        request, make_reference=fixed_references("ref-1", "ref-2")
    )
    assert payload["questions"] == {
        q.name: {"type": "noul", "instructions": q.text} for q in request.questions
    }


def test_payload_references_every_asset_once_from_state_and_never_from_questions():
    payload = build_jev_payload(sample_request())
    references = list(payload["assets"])
    assert len(references) == 2
    assert len(set(references)) == 2
    state_text = json.dumps(payload["state"], ensure_ascii=False)
    questions_text = json.dumps(payload["questions"], ensure_ascii=False)
    for reference in references:
        assert state_text.count(reference) == 1
        assert reference not in questions_text


def test_payload_contains_each_image_once():
    text = json.dumps(build_jev_payload(sample_request()), ensure_ascii=False)
    assert text.count(b64(IMAGE_ONE)) == 1
    assert text.count(b64(IMAGE_TWO)) == 1


def test_payload_base64_has_no_data_url_prefix():
    payload = build_jev_payload(sample_request())
    for asset in payload["assets"].values():
        assert not asset["data_base64"].startswith("data:")
        assert base64.b64decode(asset["data_base64"], validate=True) in (
            IMAGE_ONE,
            IMAGE_TWO,
        )


def test_payload_without_images_has_no_assets():
    payload = build_jev_payload(sample_request(images=[]))
    assert "assets" not in payload
    assert payload["state"]["frames"] == []


def test_payload_uses_given_model():
    assert (
        build_jev_payload(sample_request(), model="jev-1.13.0")["model"] == "jev-1.13.0"
    )


def test_payload_state_has_operation_history_between_game_notes_and_frames():
    request = timeline_request()
    payload = build_jev_payload(
        request, make_reference=fixed_references("ref-1", "ref-2")
    )
    state = payload["state"]
    assert list(state) == ["instructions", "game_notes", "operation_history", "frames"]
    assert state["operation_history"] == HISTORY_TEXT
    assert state["game_notes"] == request.state_text


def test_payload_without_history_text_has_no_operation_history():
    payload = build_jev_payload(sample_request())
    assert list(payload["state"]) == ["instructions", "game_notes", "frames"]


def test_payload_uses_labels_as_is_without_previous_operation_text():
    # 前回の操作の前後はラベルに書いてあるため、区切りの文は Jev の要求に入れない
    request = timeline_request()
    payload = build_jev_payload(
        request, make_reference=fixed_references("ref-1", "ref-2")
    )
    assert payload["state"]["frames"] == [
        {"label": "過去の場面 1/1 (t=1.00 秒、3.00 秒前)", "image": "ref-1"},
        {"label": "直近 1/1 (t=3.90 秒、0.10 秒前、前回の操作の後)", "image": "ref-2"},
    ]
    assert PREVIOUS_OPERATION_TEXT not in json.dumps(payload, ensure_ascii=False)


# --- 応答の解釈 ---


def test_parse_reads_noul_values_by_id():
    assert parse_jev_response(jev_body(ALL_ANSWERS), NAMES) == {
        "press_A": 0.99,
        "tap_B": 0.93,
        "advisor": 0.0,
    }


def test_parse_ignores_answers_not_requested():
    answers = {**ALL_ANSWERS, "extra": {"type": "noul", "noul": 0.5}}
    assert set(parse_jev_response(jev_body(answers), NAMES)) == set(NAMES)


def test_parse_rejects_missing_answer():
    answers = {key: value for key, value in ALL_ANSWERS.items() if key != "tap_B"}
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), NAMES)


@pytest.mark.parametrize("value", [1.5, -0.2, "0.5", None, False])
def test_parse_rejects_invalid_noul(value):
    answers = {**ALL_ANSWERS, "press_A": {"type": "noul", "noul": value}}
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), NAMES)


@pytest.mark.parametrize(
    "body",
    [
        {"model": "jev-1.14.0"},
        {"answers": [{"type": "noul", "noul": 0.5}]},
        {"answers": {"press_A": 0.5}},
        {"answers": {"press_A": {"type": "choice", "choice": "yes"}}},
    ],
)
def test_parse_rejects_malformed_body(body):
    with pytest.raises(DecisionModelError):
        parse_jev_response(body, ["press_A"])


# --- HTTP ---


def test_decide_posts_payload_with_image_header_and_returns_decision():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_body(ALL_ANSWERS))

    model = JevModel("ts-test", client=mock_client(handler))
    decision = model.decide(sample_request())
    # 応答の本文の JSON の object を raw_response に入れて返す
    assert decision == Decision(
        probabilities={"press_A": 0.99, "tap_B": 0.93, "advisor": 0.0},
        refusals=0,
        raw_response=jev_body(ALL_ANSWERS),
    )
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == JEV_URL
    assert sent.headers["authorization"] == "Bearer ts-test"
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["typesafe-danger-zone-images"] == "true"
    body = json.loads(sent.content)
    assert body["model"] == "jev-1.14.0"
    assert [frame["image"] for frame in body["state"]["frames"]] == list(body["assets"])
    assert set(body["questions"]) == set(NAMES)


def test_decide_sends_operation_history():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_body(ALL_ANSWERS))

    JevModel("ts-test", client=mock_client(handler)).decide(timeline_request())
    body = json.loads(requests[0].content)
    assert body["state"].get("operation_history") == HISTORY_TEXT


def test_decide_without_images_has_no_image_header():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_body(ALL_ANSWERS))

    JevModel("ts-test", client=mock_client(handler)).decide(sample_request(images=[]))
    assert "typesafe-danger-zone-images" not in requests[0].headers


def test_decide_uses_given_model_and_timeout():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_body(ALL_ANSWERS))

    model = JevModel(
        "ts-test", client=mock_client(handler), timeout_s=4.5, model="jev-1.13.0"
    )
    model.decide(sample_request())
    assert json.loads(requests[0].content)["model"] == "jev-1.13.0"
    assert requests[0].extensions["timeout"]["read"] == 4.5


def test_decide_raises_decision_model_error_on_http_500_with_body_head():
    body = "E" * 500 + "TAIL"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=body)

    model = JevModel("ts-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError) as excinfo:
        model.decide(sample_request())
    message = str(excinfo.value)
    assert "500" in message
    assert "E" * 500 in message
    assert "TAIL" not in message


def test_decide_raises_decision_model_error_on_context_length_error():
    # 文脈長の超過は 400 (max_tokens_exceeded) で返る
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "max_tokens_exceeded"})

    model = JevModel("ts-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError, match="max_tokens_exceeded"):
        model.decide(sample_request())


def test_decide_raises_decision_model_error_on_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    model = JevModel("ts-test", client=mock_client(handler))
    with pytest.raises(DecisionModelError):
        model.decide(sample_request())
