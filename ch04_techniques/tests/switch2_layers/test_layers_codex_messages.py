from pathlib import Path

import pytest

from switch2_layers.orchestrator.codex_messages import (
    REJECTION_TEXT,
    UNSUPPORTED_REQUEST_CODE,
    build_turn_input,
    build_turn_start_params,
    compaction_turn_id,
    is_notification,
    is_response,
    is_server_request,
    notification_events,
    notification_message,
    request_message,
    response_error_text,
    server_request_reply,
    thread_id_of_start_response,
)
from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    TurnResult,
)
from switch2_layers.orchestrator.turn_input import TurnImage, image_label

THREAD = "019a1fe0-thread"
OTHER_THREAD = "019a1fe0-child"
TURN = "019a1fe0-turn"


def notification(method: str, **params: object) -> dict:
    return {"method": method, "params": {"threadId": THREAD, "turnId": TURN, **params}}


def usage(input_tokens: object) -> dict:
    breakdown = {
        "totalTokens": 13882,
        "inputTokens": input_tokens,
        "cachedInputTokens": 13568,
        "cacheWriteInputTokens": 0,
        "outputTokens": 42,
        "reasoningOutputTokens": 0,
    }
    # 2026-11-21 の確かめの形 (last はその要求の値、total は積み上げ)
    return {
        "last": breakdown,
        "total": {**breakdown, "inputTokens": 82213},
        "modelContextWindow": 475000,
    }


# --- 行の種類 ---


def test_message_kinds():
    request = {"id": 3, "method": "item/commandExecution/requestApproval", "params": {}}
    response = {"id": 3, "result": {}}
    event = {"method": "turn/started", "params": {}}
    assert is_server_request(request) and not is_response(request)
    assert is_response(response) and not is_notification(response)
    assert is_notification(event) and not is_server_request(event)
    for value in (None, [], "text", 1):
        assert not is_server_request(value)
        assert not is_response(value)
        assert not is_notification(value)


def test_request_and_notification_forms():
    assert request_message(7, "turn/start", {"threadId": THREAD}) == {
        "id": 7,
        "method": "turn/start",
        "params": {"threadId": THREAD},
    }
    assert notification_message("initialized") == {"method": "initialized"}


def test_response_error_text():
    assert response_error_text({"id": 1, "result": {}}) is None
    assert (
        response_error_text({"id": 1, "error": {"code": -32600, "message": "bad"}})
        == "bad"
    )
    assert response_error_text({"id": 1, "error": "oops"}) == "oops"


def test_thread_id_of_start_response():
    assert (
        thread_id_of_start_response({"id": 2, "result": {"thread": {"id": THREAD}}})
        == THREAD
    )
    assert thread_id_of_start_response({"id": 2, "result": {}}) is None
    assert (
        thread_id_of_start_response({"id": 2, "result": {"thread": {"id": ""}}}) is None
    )
    assert thread_id_of_start_response({"id": 2, "error": {"message": "x"}}) is None


# --- 通知から出来事へ ---


@pytest.mark.parametrize("phase", ["commentary", "final_answer", None])
def test_agent_message_gives_the_text(phase):
    message = notification(
        "item/completed",
        item={"type": "agentMessage", "id": "m1", "text": "答え", "phase": phase},
    )
    assert notification_events(message, THREAD) == (AssistantText(text="答え"),)


def test_token_usage_gives_the_last_input_tokens():
    message = notification("thread/tokenUsage/updated", tokenUsage=usage(13840))
    assert notification_events(message, THREAD) == (ContextUsage(tokens=13840),)


@pytest.mark.parametrize("value", [True, "13840", None, 1.5])
def test_token_usage_that_is_not_an_integer_gives_nothing(value):
    message = notification("thread/tokenUsage/updated", tokenUsage=usage(value))
    assert notification_events(message, THREAD) == ()


def test_turn_completed_gives_the_turn_result():
    completed = notification(
        "turn/completed", turn={"id": TURN, "items": [], "status": "completed"}
    )
    assert notification_events(completed, THREAD) == (TurnResult(is_error=False),)


@pytest.mark.parametrize("status", ["failed", "interrupted", "inProgress", None])
def test_turn_that_did_not_complete_is_an_error(status):
    message = notification(
        "turn/completed",
        turn={"id": TURN, "items": [], "status": status, "error": {"message": "x"}},
    )
    assert notification_events(message, THREAD) == (TurnResult(is_error=True),)


def test_compaction_item_and_the_deprecated_notification_give_the_params():
    item = notification(
        "item/completed", item={"type": "contextCompaction", "id": "c1"}
    )
    deprecated = notification("thread/compacted")
    assert notification_events(item, THREAD) == (
        AutoCompaction(metadata=item["params"]),
    )
    assert notification_events(deprecated, THREAD) == (
        AutoCompaction(metadata=deprecated["params"]),
    )
    assert compaction_turn_id(notification_events(deprecated, THREAD)[0]) == TURN
    assert compaction_turn_id(AutoCompaction(metadata=None)) is None


@pytest.mark.parametrize(
    "message",
    [
        notification("turn/started", turn={"id": TURN}),
        notification("item/started", item={"type": "agentMessage", "text": "途中"}),
        notification("item/agentMessage/delta", delta="途"),
        notification("item/completed", item={"type": "commandExecution"}),
        notification("item/completed", item={"type": "agentMessage"}),
        notification("account/rateLimits/updated"),
        notification("error", error={"message": "x"}, willRetry=True),
        {"method": "turn/completed"},
        {"method": "item/completed", "params": "text"},
    ],
)
def test_other_notifications_give_no_event(message):
    assert notification_events(message, THREAD) == ()


def test_notifications_of_another_thread_give_no_event():
    for message in (
        notification("item/completed", item={"type": "agentMessage", "text": "子"}),
        notification(
            "turn/completed", turn={"id": TURN, "items": [], "status": "completed"}
        ),
        notification("thread/tokenUsage/updated", tokenUsage=usage(1)),
    ):
        message["params"]["threadId"] = OTHER_THREAD
        assert notification_events(message, THREAD) == ()


# --- サーバーからの要求への応答 ---


@pytest.mark.parametrize(
    "method",
    ["item/commandExecution/requestApproval", "item/fileChange/requestApproval"],
)
def test_approvals_are_declined(method):
    assert server_request_reply({"id": 5, "method": method, "params": {}}) == {
        "id": 5,
        "result": {"decision": "decline"},
    }


@pytest.mark.parametrize("method", ["applyPatchApproval", "execCommandApproval"])
def test_legacy_approvals_are_denied(method):
    assert server_request_reply({"id": "a", "method": method, "params": {}}) == {
        "id": "a",
        "result": {"decision": {"denied": {"rejection": REJECTION_TEXT}}},
    }


def test_permission_requests_get_no_extra_permissions():
    reply = server_request_reply(
        {"id": 6, "method": "item/permissions/requestApproval", "params": {}}
    )
    assert reply == {"id": 6, "result": {"permissions": {}}}


@pytest.mark.parametrize(
    "method",
    ["item/tool/requestUserInput", "mcpServer/elicitation/request", "item/tool/call"],
)
def test_other_requests_get_an_error(method):
    reply = server_request_reply({"id": 9, "method": method, "params": {}})
    assert reply["id"] == 9
    assert reply["error"]["code"] == UNSUPPORTED_REQUEST_CODE
    assert method in reply["error"]["message"]


# --- ターンの入力 ---


def test_turn_input_has_the_text_then_a_label_and_a_local_image_for_each_image():
    first = TurnImage(t=9.0, file="frames/interval/000009000.jpg", jpeg=b"a")
    second = TurnImage(t=10.0, file="frames/interval/000010000.jpg", jpeg=b"b")
    paths = [
        Path("/run/orchestrator/session-001/images/turn-0001-1.jpg"),
        Path("/x/2.jpg"),
    ]
    assert build_turn_input("本文", [(first, paths[0]), (second, paths[1])]) == [
        {"type": "text", "text": "本文"},
        {"type": "text", "text": image_label(first)},
        {"type": "localImage", "path": str(paths[0])},
        {"type": "text", "text": image_label(second)},
        {"type": "localImage", "path": str(paths[1])},
    ]


def test_turn_input_without_images_is_only_the_text():
    assert build_turn_input("引き継ぎ", []) == [{"type": "text", "text": "引き継ぎ"}]


def test_turn_start_params():
    turn_input = [{"type": "text", "text": "本文"}]
    assert build_turn_start_params(THREAD, turn_input, "low") == {
        "threadId": THREAD,
        "input": [{"type": "text", "text": "本文"}],
        "effort": "low",
    }
