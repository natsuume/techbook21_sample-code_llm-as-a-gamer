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
from switch2_agent.questions import Choice, Question

IMAGE_ONE = b"jpeg-image-one"
IMAGE_TWO = b"jpeg-image-two"
HISTORY_TEXT = (
    "## 直近の応答と送った操作 (古い順、最大 5 回)\n"
    "- t=3.80 秒 (0.10 秒前): 押下 none / 短押し A (前回の操作)"
)
PREVIOUS_OPERATION_TEXT = "── t=3.80 秒 (0.10 秒前) に、前回の応答の操作を送った ──"
# 確率の問いだけの要求の問い
PROBABILITY_QUESTIONS = [
    Question(
        "stick_LS_x_at_least_50",
        "左スティックを左右に倒す場合、傾きを 50% 以上にするべきか。",
        "stick_strength",
        "LS_x_50",
        "probability",
        (),
    ),
    Question(
        "stick_LS_x_at_least_75",
        "左スティックを左右に倒す場合、傾きを 75% 以上にするべきか。",
        "stick_strength",
        "LS_x_75",
        "probability",
        (),
    ),
    Question(
        "advisor",
        "今、advisor に戦略の更新を求めるべきか。",
        "advisor",
        "",
        "probability",
        (),
    ),
]
NAMES = [question.name for question in PROBABILITY_QUESTIONS]
BUTTON_QUESTION = Question(
    "button_A",
    "今、A ボタンをどうするか。",
    "button",
    "A",
    "choice",
    (
        Choice("none", "押さない"),
        Choice("hold", "押した状態にする。押した状態は次の判断まで続く"),
        Choice("tap", "1 回だけ短く押す"),
    ),
)
AXIS_QUESTION = Question(
    "stick_LS_y",
    "今、左スティックを上下に倒すか。",
    "stick_direction",
    "LS_y",
    "choice",
    (
        Choice("none", "上下には倒さない"),
        Choice("up", "上に倒す"),
        Choice("down", "下に倒す"),
    ),
)
# 選択肢の問いと確率の問いを混ぜた要求の問い
MIXED_QUESTIONS = [BUTTON_QUESTION, AXIS_QUESTION, *PROBABILITY_QUESTIONS]


def sample_request(
    images: list[LabeledImage] | None = None,
    questions: list[Question] | None = None,
) -> DecisionRequest:
    """画像 2 枚の要求を返す。questions が None なら、確率の問いだけ (PROBABILITY_QUESTIONS) にする。"""
    if images is None:
        images = [
            LabeledImage("過去の場面 1/1 (3.00 秒前)", IMAGE_ONE, 1.0),
            LabeledImage("直近 1/1 (0.10 秒前)", IMAGE_TWO, 3.9),
        ]
    return DecisionRequest(
        instructions="あなたはコントローラーの判断をする。",
        state_text="## 戦略\n右へ進む",
        images=images,
        questions=list(questions if questions is not None else PROBABILITY_QUESTIONS),
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
    "stick_LS_x_at_least_50": {"type": "noul", "noul": 0.99},
    "stick_LS_x_at_least_75": {"type": "noul", "noul": 0.93},
    "advisor": {"type": "noul", "noul": 0.0},
}
ALL_PROBABILITIES = {
    "stick_LS_x_at_least_50": 0.99,
    "stick_LS_x_at_least_75": 0.93,
    "advisor": 0.0,
}


def choice_answer(probabilities: object, choice: str = "none") -> dict:
    """公式ドキュメントの choice の answer の形 (type・choice・confidence・probabilities) を返す。"""
    return {
        "type": "choice",
        "choice": choice,
        "confidence": 0.5,
        "probabilities": probabilities,
    }


# MIXED_QUESTIONS の要求へのすべての答え
MIXED_ANSWERS = {
    "button_A": choice_answer({"none": 0.3, "hold": 0.6, "tap": 0.1}, "hold"),
    "stick_LS_y": choice_answer({"none": 0.2, "up": 0.1, "down": 0.7}, "down"),
    **ALL_ANSWERS,
}
MIXED_CHOICES = {
    "button_A": {"none": 0.3, "hold": 0.6, "tap": 0.1},
    "stick_LS_y": {"none": 0.2, "up": 0.1, "down": 0.7},
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


def test_payload_questions_put_choice_questions_with_criteria_in_choice_order():
    payload = build_jev_payload(
        sample_request(questions=MIXED_QUESTIONS),
        make_reference=fixed_references("ref-1", "ref-2"),
    )
    questions = payload["questions"]
    assert list(questions) == [q.name for q in MIXED_QUESTIONS]
    assert questions["button_A"] == {
        "type": "choice",
        "instructions": "今、A ボタンをどうするか。",
        "criteria": {
            "none": "押さない",
            "hold": "押した状態にする。押した状態は次の判断まで続く",
            "tap": "1 回だけ短く押す",
        },
    }
    # criteria は選択肢の順 (none が先頭)
    assert list(questions["button_A"]["criteria"]) == ["none", "hold", "tap"]
    assert list(questions["stick_LS_y"]["criteria"]) == ["none", "up", "down"]
    assert questions["advisor"] == {
        "type": "noul",
        "instructions": "今、advisor に戦略の更新を求めるべきか。",
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
    assert parse_jev_response(jev_body(ALL_ANSWERS), PROBABILITY_QUESTIONS) == (
        Decision(probabilities=ALL_PROBABILITIES, choices={}, refusals=0)
    )


def test_parse_ignores_answers_not_requested():
    answers = {**ALL_ANSWERS, "extra": {"type": "noul", "noul": 0.5}}
    decision = parse_jev_response(jev_body(answers), PROBABILITY_QUESTIONS)
    assert set(decision.probabilities) == set(NAMES)


def test_parse_rejects_missing_answer():
    answers = {
        key: value
        for key, value in ALL_ANSWERS.items()
        if key != "stick_LS_x_at_least_75"
    }
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), PROBABILITY_QUESTIONS)


@pytest.mark.parametrize("value", [1.5, -0.2, "0.5", None, False])
def test_parse_rejects_invalid_noul(value):
    answers = {**ALL_ANSWERS, "advisor": {"type": "noul", "noul": value}}
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), PROBABILITY_QUESTIONS)


@pytest.mark.parametrize(
    "body",
    [
        {"model": "jev-1.14.0"},
        {"answers": [{"type": "noul", "noul": 0.5}]},
        {"answers": {"advisor": 0.5}},
    ],
)
def test_parse_rejects_malformed_body(body):
    with pytest.raises(DecisionModelError):
        parse_jev_response(body, PROBABILITY_QUESTIONS[2:])


# --- 応答の解釈 (選択肢の問い) ---


def test_parse_reads_choice_probabilities_into_choices():
    decision = parse_jev_response(jev_body(MIXED_ANSWERS), MIXED_QUESTIONS)
    assert decision == Decision(
        probabilities=ALL_PROBABILITIES, choices=MIXED_CHOICES, refusals=0
    )


def test_parse_reads_probabilities_not_choice_or_confidence():
    # choice と confidence は probabilities と食い違っていても使わない
    answers = {
        **MIXED_ANSWERS,
        "button_A": {
            "type": "choice",
            "choice": "tap",
            "confidence": 0.99,
            "probabilities": {"none": 0.7, "hold": 0.2, "tap": 0.1},
        },
    }
    decision = parse_jev_response(jev_body(answers), MIXED_QUESTIONS)
    assert decision.choices["button_A"] == {"none": 0.7, "hold": 0.2, "tap": 0.1}


def test_parse_ignores_choice_names_not_in_question():
    answers = {
        **MIXED_ANSWERS,
        "button_A": choice_answer(
            {"none": 0.3, "hold": 0.6, "tap": 0.1, "double": 0.4}
        ),
    }
    decision = parse_jev_response(jev_body(answers), MIXED_QUESTIONS)
    assert decision.choices["button_A"] == {"none": 0.3, "hold": 0.6, "tap": 0.1}


def test_parse_accepts_choice_probabilities_not_summing_to_one():
    answers = {
        **MIXED_ANSWERS,
        "button_A": choice_answer({"none": 0.5, "hold": 0.5, "tap": 0.5}),
    }
    decision = parse_jev_response(jev_body(answers), MIXED_QUESTIONS)
    assert decision.choices["button_A"] == {"none": 0.5, "hold": 0.5, "tap": 0.5}


def test_parse_rejects_choice_answer_missing_a_choice():
    # すべての選択肢の確率がそろっていれば読める
    parse_jev_response(jev_body(MIXED_ANSWERS), MIXED_QUESTIONS)
    answers = {
        **MIXED_ANSWERS,
        "stick_LS_y": choice_answer({"none": 0.2, "up": 0.8}),
    }
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), MIXED_QUESTIONS)


@pytest.mark.parametrize("value", [1.5, -0.1, "0.5", None, True])
def test_parse_rejects_invalid_choice_probability(value):
    parse_jev_response(jev_body(MIXED_ANSWERS), MIXED_QUESTIONS)
    answers = {
        **MIXED_ANSWERS,
        "button_A": choice_answer({"none": 0.3, "hold": value, "tap": 0.1}),
    }
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), MIXED_QUESTIONS)


@pytest.mark.parametrize(
    "name, answer",
    [
        # 選択肢の問いに noul の答え
        ("button_A", {"type": "noul", "noul": 0.5}),
        # 確率の問いに choice の答え
        ("advisor", choice_answer({"none": 1.0})),
    ],
)
def test_parse_rejects_answer_of_other_answer_type(name, answer):
    parse_jev_response(jev_body(MIXED_ANSWERS), MIXED_QUESTIONS)
    answers = {**MIXED_ANSWERS, name: answer}
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), MIXED_QUESTIONS)


@pytest.mark.parametrize("probabilities", [[0.3, 0.6, 0.1], None, "hold"])
def test_parse_rejects_choice_probabilities_that_are_not_object(probabilities):
    parse_jev_response(jev_body(MIXED_ANSWERS), MIXED_QUESTIONS)
    answers = {**MIXED_ANSWERS, "button_A": choice_answer(probabilities)}
    with pytest.raises(DecisionModelError):
        parse_jev_response(jev_body(answers), MIXED_QUESTIONS)


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
        probabilities=ALL_PROBABILITIES,
        choices={},
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


def test_decide_sends_choice_questions_and_returns_choices():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_body(MIXED_ANSWERS))

    model = JevModel("ts-test", client=mock_client(handler))
    decision = model.decide(sample_request(questions=MIXED_QUESTIONS))
    assert decision == Decision(
        probabilities=ALL_PROBABILITIES,
        choices=MIXED_CHOICES,
        refusals=0,
        raw_response=jev_body(MIXED_ANSWERS),
    )
    body = json.loads(requests[0].content)
    assert body["questions"]["button_A"]["type"] == "choice"
    assert body["questions"]["advisor"]["type"] == "noul"


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
