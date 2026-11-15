import base64
import json

import httpx
import pytest

from switch2_layers.middle.luna_client import ERROR_BODY_HEAD_CHARS, LunaCallError
from switch2_layers.middle.luna_middle import (
    CALL_DETAIL_KEYS,
    LunaMiddleAnalyzer,
    build_call_details,
)
from switch2_layers.middle.middle_call_record import MiddleCallDetailsSource
from switch2_layers.middle.middle_types import MiddleFrame, MiddleInput, MiddleOutput

JPEG = b"\xff\xd8\xff\xe0frame"
MIDDLE_INPUT = MiddleInput(
    t=20.0,
    frames=(
        MiddleFrame(t=12.0, file="frames/interval/000012000.jpg", jpeg=JPEG),
        MiddleFrame(t=19.8, file="frames/interval/000019800.jpg", jpeg=JPEG),
    ),
    surface_rows=({"t_received": 18.0, "scene_changed": True},),
    operation_rows=(),
    scene_change_reported=True,
    scene_labels=(),
    previous_state=None,
    strategy="",
    current_tasks="",
    surface_rows_offset=0,
    operations_rows_offset=0,
)
OUTPUT = {
    "scene_name": "タイトル画面",
    # タイトル画面にはゲームの世界の中の場所が映っていない
    "place_name": "",
    "new_connections": [],
    "new_observations": [],
    "is_stuck": False,
    "stuck_description": "",
    "surface_wait_s": 1.5,
    "timeline": "## 操作のまとめ\n- まだ操作していない",
    "tasks": "- A でゲームを始める\n",
}
USAGE = {
    "input_tokens": 3000,
    "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    "output_tokens": 400,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 3400,
}
# (3000 - 1024) × 0.10 + 1024 × 0.01 + 400 × 0.50 = 407.84 (USD / 100 万トークン)
USAGE_COST = 0.00040784
USAGE_JSON = {
    "input_tokens": 3000,
    "cached_tokens": 1024,
    "cache_write_tokens": 0,
    "output_tokens": 400,
    "reasoning_tokens": 0,
}


def response_body(
    output: dict | None = None,
    status: str = "completed",
    content: list | None = None,
    usage: dict | None = USAGE,
    **extra,
) -> dict:
    """Responses API の応答の本文。content を渡さなければ output の JSON の output_text 1 つ。"""
    text = json.dumps(OUTPUT if output is None else output, ensure_ascii=False)
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "incomplete_details": None,
        "model": "gpt-6-luna",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}]
                if content is None
                else content,
            }
        ],
        "usage": usage,
        **extra,
    }


class RecordingHandler:
    """MockTransport の handler。受け取った要求を記録し、response を返す (例外なら出す)。"""

    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def make_analyzer(
    response: httpx.Response | Exception,
) -> tuple[LunaMiddleAnalyzer, RecordingHandler]:
    handler = RecordingHandler(response)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LunaMiddleAnalyzer("sk-test", client=client), handler


# --- 成功 ---


def test_analyze_returns_parsed_output():
    analyzer, handler = make_analyzer(httpx.Response(200, json=response_body()))
    assert analyzer.analyze(MIDDLE_INPUT) == MiddleOutput(
        scene_name="タイトル画面",
        is_stuck=False,
        stuck_description="",
        surface_wait_s=1.5,
        timeline="## 操作のまとめ\n- まだ操作していない",
        tasks="- A でゲームを始める\n",
        place_name=None,
        new_connections=(),
        new_observations=(),
    )
    assert len(handler.requests) == 1


def test_analyze_sends_the_expected_request():
    analyzer, handler = make_analyzer(httpx.Response(200, json=response_body()))
    analyzer.analyze(MIDDLE_INPUT)
    request = handler.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert request.extensions["timeout"]["read"] == 30.0
    payload = handler.payload()
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    text_format = payload["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert text_format["schema"]["required"] == [
        "scene_name",
        "place_name",
        "new_connections",
        "new_observations",
        "is_stuck",
        "stuck_description",
        "surface_wait_s",
        "timeline",
        "tasks",
    ]
    assert text_format["schema"]["additionalProperties"] is False
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    developer, user = payload["input"]
    assert developer["role"] == "developer"
    assert developer["content"][-1]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert user["role"] == "user"
    images = [part for part in user["content"] if part["type"] == "input_image"]
    expected_url = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
    assert [part["image_url"] for part in images] == [expected_url, expected_url]
    texts = [part["text"] for part in user["content"] if part["type"] == "input_text"]
    assert "フレーム t=12.000" in texts
    assert "フレーム t=19.800" in texts


def test_details_after_success_have_usage_cost_and_response():
    body = response_body()
    analyzer, _ = make_analyzer(httpx.Response(200, json=body))
    analyzer.analyze(MIDDLE_INPUT)
    details = analyzer.take_call_details()
    assert list(details) == ["usage", "cost_usd", "response", "error_body_head"]
    assert details["usage"] == USAGE_JSON
    assert details["cost_usd"] == pytest.approx(USAGE_COST)
    assert details["response"] == body
    assert details["error_body_head"] is None
    assert json.loads(json.dumps(details, ensure_ascii=False)) == details
    # 渡した記録は消える
    assert analyzer.take_call_details() == {}


def test_details_before_any_call_are_empty():
    analyzer, _ = make_analyzer(httpx.Response(200, json=response_body()))
    assert analyzer.take_call_details() == {}


def test_analyzer_is_a_call_details_source():
    analyzer, _ = make_analyzer(httpx.Response(200, json=response_body()))
    assert isinstance(analyzer, MiddleCallDetailsSource)


def test_response_without_usage_records_null_cost():
    body = response_body(usage=None)
    analyzer, _ = make_analyzer(httpx.Response(200, json=body))
    # usage が無くても、読めた出力は成功にする
    assert analyzer.analyze(MIDDLE_INPUT).scene_name == "タイトル画面"
    details = analyzer.take_call_details()
    assert details["usage"] is None
    assert details["cost_usd"] is None
    assert details["response"] == body


# --- 失敗 ---


def test_http_error_raises_and_records_body_head():
    error_text = json.dumps({"error": {"message": "Invalid schema", "type": "x"}})
    analyzer, handler = make_analyzer(httpx.Response(400, text=error_text))
    with pytest.raises(LunaCallError):
        analyzer.analyze(MIDDLE_INPUT)
    # 再試行しない
    assert len(handler.requests) == 1
    assert analyzer.take_call_details() == {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": error_text,
    }


def test_long_error_body_is_recorded_as_head():
    analyzer, _ = make_analyzer(
        httpx.Response(500, text="e" * (ERROR_BODY_HEAD_CHARS + 10))
    )
    with pytest.raises(LunaCallError):
        analyzer.analyze(MIDDLE_INPUT)
    assert analyzer.take_call_details()["error_body_head"] == "e" * (
        ERROR_BODY_HEAD_CHARS
    )


def test_timeout_raises_without_body():
    analyzer, handler = make_analyzer(httpx.ReadTimeout("timed out"))
    with pytest.raises(LunaCallError):
        analyzer.analyze(MIDDLE_INPUT)
    assert len(handler.requests) == 1
    assert analyzer.take_call_details() == {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": None,
    }


def test_incomplete_response_raises_and_records_usage():
    body = response_body(
        status="incomplete", incomplete_details={"reason": "max_output_tokens"}
    )
    analyzer, _ = make_analyzer(httpx.Response(200, json=body))
    with pytest.raises(LunaCallError, match="max_output_tokens"):
        analyzer.analyze(MIDDLE_INPUT)
    details = analyzer.take_call_details()
    assert details["usage"] == USAGE_JSON
    assert details["cost_usd"] == pytest.approx(USAGE_COST)
    assert details["response"] == body


def test_refusal_raises_and_records_response():
    body = response_body(content=[{"type": "refusal", "refusal": "Sorry, I can't."}])
    analyzer, _ = make_analyzer(httpx.Response(200, json=body))
    with pytest.raises(LunaCallError, match="Sorry, I can't."):
        analyzer.analyze(MIDDLE_INPUT)
    details = analyzer.take_call_details()
    assert details["response"] == body
    assert details["usage"] == USAGE_JSON


@pytest.mark.parametrize(
    "output",
    [
        {**OUTPUT, "surface_wait_s": 2.5},
        {**OUTPUT, "surface_wait_s": 0.1},
        {key: value for key, value in OUTPUT.items() if key != "tasks"},
        {**OUTPUT, "is_stuck": "no"},
        {**OUTPUT, "extra": 1},
    ],
    ids=["wait-too-long", "wait-too-short", "missing-key", "wrong-type", "extra-key"],
)
def test_output_that_does_not_match_schema_raises_and_records_response(output):
    # OUTPUT そのものは読める (誤りの出力が、別の理由で ValueError になって通らないように)
    valid, _ = make_analyzer(httpx.Response(200, json=response_body()))
    assert valid.analyze(MIDDLE_INPUT).scene_name == "タイトル画面"
    body = response_body(output=output)
    analyzer, _ = make_analyzer(httpx.Response(200, json=body))
    with pytest.raises(ValueError):
        analyzer.analyze(MIDDLE_INPUT)
    details = analyzer.take_call_details()
    assert details["response"] == body
    assert details["cost_usd"] == pytest.approx(USAGE_COST)


def test_each_analyze_replaces_previous_details():
    handler = RecordingHandler(httpx.Response(200, json=response_body()))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    analyzer = LunaMiddleAnalyzer("sk-test", client=client)
    analyzer.analyze(MIDDLE_INPUT)
    # 前の回の記録を渡さないまま、次の回が失敗する
    handler.response = httpx.ReadTimeout("timed out")
    with pytest.raises(LunaCallError):
        analyzer.analyze(MIDDLE_INPUT)
    details = analyzer.take_call_details()
    assert details["response"] is None
    assert details["usage"] is None


# --- 記録の組み立て ---


def test_build_call_details_keys():
    assert CALL_DETAIL_KEYS == ("usage", "cost_usd", "response", "error_body_head")
    assert build_call_details(None, None) == {
        "usage": None,
        "cost_usd": None,
        "response": None,
        "error_body_head": None,
    }
    body = response_body()
    details = build_call_details(body, None)
    assert list(details) == list(CALL_DETAIL_KEYS)
    assert details["usage"] == USAGE_JSON
    assert details["cost_usd"] == pytest.approx(USAGE_COST)
    assert details["response"] == body
    assert build_call_details(None, "oops")["error_body_head"] == "oops"


def test_api_key_is_kept_as_attribute():
    analyzer, _ = make_analyzer(httpx.Response(200, json=response_body()))
    assert analyzer.api_key == "sk-test"
    assert analyzer.timeout_s == 30.0
