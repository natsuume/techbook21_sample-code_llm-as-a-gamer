import json

import httpx
import pytest

from switch2_layers.middle.luna_client import (
    ERROR_BODY_HEAD_CHARS,
    LUNA_TIMEOUT_S,
    RESPONSES_URL,
    LunaCallError,
    build_headers,
    post_responses,
    read_output_text,
)

PAYLOAD = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "hi"}]}


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def message(*content: dict) -> dict:
    return {
        "type": "message",
        "id": "msg_test",
        "status": "completed",
        "role": "assistant",
        "content": list(content),
    }


def output_text_part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": []}


def response_body(
    status: str = "completed", output: list | None = None, **extra
) -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "incomplete_details": None,
        "model": "gpt-6-luna",
        "output": [message(output_text_part('{"a": 1}'))] if output is None else output,
        "usage": None,
        **extra,
    }


# --- 定数とヘッダー ---


def test_constants_follow_the_issue():
    assert RESPONSES_URL == "https://api.openai.com/v1/responses"
    assert LUNA_TIMEOUT_S == 30.0
    assert ERROR_BODY_HEAD_CHARS > 0


def test_headers_use_bearer_token_and_json():
    assert build_headers("sk-test") == {
        "Authorization": "Bearer sk-test",
        "Content-Type": "application/json",
    }


# --- 送信 ---


def test_post_sends_payload_once_with_headers_and_timeout():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response_body())

    body = post_responses(mock_client(handler), "sk-test", PAYLOAD)
    assert body == response_body()
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == PAYLOAD
    # タイムアウトは接続・送信・受信・接続の空きの待ちのそれぞれ 30 秒
    assert request.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_error_is_raised_once_with_body_head(status):
    calls = []
    error_text = json.dumps({"error": {"message": "bad request", "type": "invalid"}})

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text=error_text)

    with pytest.raises(LunaCallError) as error_info:
        post_responses(mock_client(handler), "sk-test", PAYLOAD)
    error = error_info.value
    # 再試行しない
    assert len(calls) == 1
    assert str(status) in str(error)
    assert error.error_body_head == error_text
    assert error.response_body is None
    assert "sk-test" not in str(error)


def test_long_error_body_is_cut_to_head():
    long_text = "x" * (ERROR_BODY_HEAD_CHARS + 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=long_text)

    with pytest.raises(LunaCallError) as error_info:
        post_responses(mock_client(handler), "sk-test", PAYLOAD)
    assert error_info.value.error_body_head == "x" * ERROR_BODY_HEAD_CHARS


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_transport_error_is_raised_without_body(exception):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise exception

    with pytest.raises(LunaCallError) as error_info:
        post_responses(mock_client(handler), "sk-test", PAYLOAD)
    assert len(calls) == 1
    assert error_info.value.error_body_head is None
    assert error_info.value.response_body is None


def test_transport_error_text_is_not_in_the_description():
    # ヘッダーに使えない文字を含む API キーでは、httpx の例外の文がヘッダーの値 (API キー) を含む
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError("Illegal header value b'Bearer sk-secret\\n'")

    with pytest.raises(LunaCallError) as error_info:
        post_responses(mock_client(handler), "sk-secret\n", PAYLOAD)
    assert "sk-secret" not in str(error_info.value)
    assert "LocalProtocolError" in str(error_info.value)


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '"text"'])
def test_body_that_is_not_a_json_object_is_an_error(text):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=text)

    with pytest.raises(LunaCallError) as error_info:
        post_responses(mock_client(handler), "sk-test", PAYLOAD)
    assert error_info.value.error_body_head == text


# --- 応答の読み取り ---


def test_read_output_text_of_completed_message():
    assert read_output_text(response_body()) == '{"a": 1}'


def test_read_output_text_finds_message_after_other_items():
    body = response_body(
        output=[
            {"type": "reasoning", "id": "rs_test", "summary": []},
            message(output_text_part('{"b": 2}')),
        ]
    )
    assert read_output_text(body) == '{"b": 2}'


def test_read_output_text_joins_parts_in_order():
    body = response_body(
        output=[message(output_text_part('{"a": '), output_text_part("1}"))]
    )
    assert read_output_text(body) == '{"a": 1}'


def phased_message(phase: str | None, text: str) -> dict:
    return {**message(output_text_part(text)), "phase": phase}


def test_read_output_text_reads_only_the_final_answer_message():
    # commentary の message には、JSON の後に意味の無い文字が続くことがある
    body = response_body(
        output=[
            phased_message("commentary", '{"a": 1}壊れた文字\n{"a": '),
            phased_message("final_answer", '{"a": 2}'),
        ]
    )
    assert read_output_text(body) == '{"a": 2}'


def test_read_output_text_reads_all_messages_without_a_final_answer():
    body = response_body(
        output=[phased_message(None, '{"a": '), phased_message(None, "1}")]
    )
    assert read_output_text(body) == '{"a": 1}'


def test_refusal_in_the_final_answer_message_is_an_error():
    body = response_body(
        output=[
            phased_message("commentary", '{"a": 1}'),
            {
                **message({"type": "refusal", "refusal": "I can't help with that."}),
                "phase": "final_answer",
            },
        ]
    )
    with pytest.raises(LunaCallError) as error_info:
        read_output_text(body)
    assert "I can't help with that." in str(error_info.value)


def test_incomplete_response_is_an_error_with_reason():
    body = response_body(
        status="incomplete",
        incomplete_details={"reason": "max_output_tokens"},
        output=[message(output_text_part('{"a": '))],
    )
    with pytest.raises(LunaCallError) as error_info:
        read_output_text(body)
    assert "incomplete" in str(error_info.value)
    assert "max_output_tokens" in str(error_info.value)
    assert error_info.value.response_body == body


@pytest.mark.parametrize("status", ["failed", "cancelled", "queued", "in_progress"])
def test_other_status_is_an_error(status):
    with pytest.raises(LunaCallError) as error_info:
        read_output_text(response_body(status=status))
    assert status in str(error_info.value)


def test_missing_status_is_an_error():
    body = response_body()
    del body["status"]
    with pytest.raises(LunaCallError):
        read_output_text(body)


def test_refusal_in_completed_response_is_an_error():
    body = response_body(
        output=[message({"type": "refusal", "refusal": "I can't help with that."})]
    )
    with pytest.raises(LunaCallError) as error_info:
        read_output_text(body)
    assert "I can't help with that." in str(error_info.value)
    assert error_info.value.response_body == body


@pytest.mark.parametrize(
    "output",
    [
        [],
        [{"type": "reasoning", "id": "rs_test", "summary": []}],
        [message()],
        "not a list",
        [message({"type": "output_text", "text": None})],
        [{"type": "message", "content": "not a list"}],
    ],
)
def test_missing_output_text_is_an_error(output):
    body = response_body(output=output)
    with pytest.raises(LunaCallError) as error_info:
        read_output_text(body)
    assert error_info.value.response_body == body
