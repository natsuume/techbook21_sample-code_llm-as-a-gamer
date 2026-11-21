"""主ループの codex app-server と話す JSON-RPC の行の形と読み方 (どれも I/O を扱わない)。

codex app-server は標準入出力で、1 行に 1 つの JSON の JSON-RPC を話す (2026-11-21、codex-cli 0.171.0)。
    要求       {"id": <整数>, "method": <名前>, "params": {...}}     (こちらから送る。サーバーからも届く)
    応答       {"id": <要求の id>, "result": {...}} か {"id": <要求の id>, "error": {"code", "message"}}
    通知       {"method": <名前>, "params": {...}}                   (id が無い)
送るもの: initialize (build_initialize_params) の応答の後に通知 initialized、thread/start
(codex_session_command.build_thread_start_params)、ターンごとに turn/start (build_turn_start_params)。
サーバーからの要求 (承認の要求など) には、server_request_reply の応答を返す。

届く通知のうち、主ループの出来事 (main_loop_session) にするもの (notification_events。主ループのスレッドのものだけ):
    item/completed (params.item.type が agentMessage)        AssistantText(item.text)。1 つのターンに、途中の文
                                                            (phase commentary) と最後の文 (phase final_answer) の
                                                            両方が届くことがある。主ループは最後に届いた文を読む
    thread/tokenUsage/updated                                ContextUsage(params.tokenUsage.last.inputTokens)。
                                                            モデルへの要求ごとに届き、last はその要求の値 (cached を
                                                            含む入力の大きさ。ターンの合計ではない)。turn/completed
                                                            より前に届く
    item/completed (params.item.type が contextCompaction)   AutoCompaction(params)
    thread/compacted                                         AutoCompaction(params) (スキーマで非推奨とされ、代わりが
                                                            contextCompaction の item。同じターンで両方が届いたときの
                                                            重複は、セッションが turnId で除く。起こして確かめては
                                                            いない)
    turn/completed                                           TurnResult(is_error = params.turn.status が completed で
                                                            ない (failed・interrupted))
ほかの通知 (thread/status/changed・turn/started・item/started・item/agentMessage/delta・account/rateLimits/updated・
mcpServer/startupStatus/updated・error など) は、stream.jsonl に書くだけで、出来事にしない。
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from switch2_layers.orchestrator.main_loop_session import (
    AssistantText,
    AutoCompaction,
    ContextUsage,
    MainLoopSessionEvent,
    TurnResult,
)
from switch2_layers.orchestrator.turn_input import TurnImage, image_label

INITIALIZE_METHOD = "initialize"
INITIALIZED_METHOD = "initialized"
THREAD_START_METHOD = "thread/start"
TURN_START_METHOD = "turn/start"
ITEM_COMPLETED_METHOD = "item/completed"
TOKEN_USAGE_METHOD = "thread/tokenUsage/updated"
TURN_COMPLETED_METHOD = "turn/completed"
THREAD_COMPACTED_METHOD = "thread/compacted"
# item/completed の item.type
AGENT_MESSAGE_ITEM = "agentMessage"
CONTEXT_COMPACTION_ITEM = "contextCompaction"
# turn/completed の turn.status のうち、誤りでないもの
TURN_COMPLETED_STATUS = "completed"
# 断る承認の要求 (v2 の形。応答の decision は "decline")
DECLINE_APPROVAL_METHODS: tuple[str, ...] = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
)
# 断る承認の要求 (古い形。応答の decision は {"denied": {"rejection": ...}})
DENY_REVIEW_METHODS: tuple[str, ...] = ("applyPatchApproval", "execCommandApproval")
# 追加の権限の要求 (応答で何も足さない)
PERMISSIONS_APPROVAL_METHOD = "item/permissions/requestApproval"
# 古い形の承認を断るときの理由の文 (モデルに届く)
REJECTION_TEXT = "主ループでは承認を求められない。権限の範囲の中で別の方法を使う"
# ほかの要求に返す誤りの code と message (JSON-RPC の Method not found)
UNSUPPORTED_REQUEST_CODE = -32601
UNSUPPORTED_REQUEST_MESSAGE = "this client does not handle {method}"


def request_message(
    request_id: int, method: str, params: Mapping[str, object]
) -> dict[str, object]:
    """要求 {"id": request_id, "method": method, "params": params} を返す。"""
    return {"id": request_id, "method": method, "params": dict(params)}


def notification_message(method: str) -> dict[str, object]:
    """引数の無い通知 {"method": method} を返す (initialized で使う)。"""
    return {"method": method}


def is_server_request(message: object) -> bool:
    """message が object で、id と method の両方を持てば True (サーバーからの要求)。"""
    return isinstance(message, dict) and "id" in message and "method" in message


def is_response(message: object) -> bool:
    """message が object で、id を持ち method を持たなければ True (こちらの要求への応答)。"""
    return isinstance(message, dict) and "id" in message and "method" not in message


def is_notification(message: object) -> bool:
    """message が object で、method を持ち id を持たなければ True (通知)。"""
    return isinstance(message, dict) and "method" in message and "id" not in message


def response_error_text(response: Mapping[str, object]) -> str | None:
    """応答 response が誤り (error を持つ) なら、その説明の文 (error.message。無ければ error の JSON の文字列) を返す。
    誤りでなければ None。"""
    if "error" not in response:
        return None
    error = response.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return str(error)


def thread_id_of_start_response(response: Mapping[str, object]) -> str | None:
    """thread/start の応答 response の result.thread.id (文字列) を返す。形が合わなければ None。"""
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return None
    thread_id = thread.get("id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def server_request_reply(message: Mapping[str, object]) -> dict[str, object]:
    """サーバーからの要求 message への応答を返す。

    DECLINE_APPROVAL_METHODS        {"id", "result": {"decision": "decline"}} (断る。ターンは続く)
    DENY_REVIEW_METHODS             {"id", "result": {"decision": {"denied": {"rejection": REJECTION_TEXT}}}}
    PERMISSIONS_APPROVAL_METHOD     {"id", "result": {"permissions": {}}} (権限を何も足さない)
    ほか (利用者への質問・MCP・認証など)
                                    {"id", "error": {"code": UNSUPPORTED_REQUEST_CODE,
                                                     "message": UNSUPPORTED_REQUEST_MESSAGE}}
    """
    request_id = message.get("id")
    method = message.get("method")
    if method in DECLINE_APPROVAL_METHODS:
        return {"id": request_id, "result": {"decision": "decline"}}
    if method in DENY_REVIEW_METHODS:
        return {
            "id": request_id,
            "result": {"decision": {"denied": {"rejection": REJECTION_TEXT}}},
        }
    if method == PERMISSIONS_APPROVAL_METHOD:
        return {"id": request_id, "result": {"permissions": {}}}
    return {
        "id": request_id,
        "error": {
            "code": UNSUPPORTED_REQUEST_CODE,
            "message": UNSUPPORTED_REQUEST_MESSAGE.format(method=method),
        },
    }


def notification_events(
    message: Mapping[str, object], thread_id: str
) -> tuple[MainLoopSessionEvent, ...]:
    """通知 message を、モジュールの docstring の対応で出来事にして返す (0 か 1 個)。

    params.threadId が thread_id でない通知 (サブエージェントのスレッドなど) と、形の合わない通知は出来事にしない
    (空のタプル)。使用量は、last.inputTokens が整数 (bool を除く) のときだけ出来事にする。
    """
    params = message.get("params")
    if not isinstance(params, dict) or params.get("threadId") != thread_id:
        return ()
    method = message.get("method")
    if method == ITEM_COMPLETED_METHOD:
        return _item_events(params)
    if method == TOKEN_USAGE_METHOD:
        tokens = _last_input_tokens(params)
        return () if tokens is None else (ContextUsage(tokens=tokens),)
    if method == THREAD_COMPACTED_METHOD:
        return (AutoCompaction(metadata=dict(params)),)
    if method == TURN_COMPLETED_METHOD:
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return ()
        return (TurnResult(is_error=turn.get("status") != TURN_COMPLETED_STATUS),)
    return ()


def compaction_turn_id(event: AutoCompaction) -> str | None:
    """要約の出来事 event の metadata (通知の params) の turnId を返す。無ければ None (重複を除くのに使う)。"""
    metadata = event.metadata
    if isinstance(metadata, dict) and isinstance(metadata.get("turnId"), str):
        return metadata["turnId"]
    return None


def build_turn_input(
    text: str, images: Sequence[tuple[TurnImage, Path]]
) -> list[dict[str, object]]:
    """turn/start の input の列を返す。

    [{"type": "text", "text": text}] の後に、images (画像と、その JPEG を書いたファイルの絶対パス) の順に、画像ごとに
    {"type": "text", "text": turn_input.image_label(画像)} と {"type": "localImage", "path": str(パス)} を足す
    (Claude 版の turn_input.build_turn_content と同じ並びで、画像を base64 の代わりにファイルのパスで渡す)。
    app-server のプロセスがサンドボックスの外でファイルを読むため、モデルから読めない場所 (orchestrator/) に置いてよい。
    """
    items: list[dict[str, object]] = [{"type": "text", "text": text}]
    for image, path in images:
        items.append({"type": "text", "text": image_label(image)})
        items.append({"type": "localImage", "path": str(path)})
    return items


def build_turn_start_params(
    thread_id: str, turn_input: Sequence[Mapping[str, object]], effort: str
) -> dict[str, object]:
    """turn/start の引数 {"threadId": thread_id, "input": [...], "effort": effort} を返す。"""
    return {
        "threadId": thread_id,
        "input": [dict(item) for item in turn_input],
        "effort": effort,
    }


def _item_events(params: Mapping[str, object]) -> tuple[MainLoopSessionEvent, ...]:
    """item/completed の params を出来事にする (agentMessage の文か、contextCompaction の要約)。"""
    item = params.get("item")
    if not isinstance(item, dict):
        return ()
    kind = item.get("type")
    if kind == AGENT_MESSAGE_ITEM and isinstance(item.get("text"), str):
        return (AssistantText(text=item["text"]),)
    if kind == CONTEXT_COMPACTION_ITEM:
        return (AutoCompaction(metadata=dict(params)),)
    return ()


def _last_input_tokens(params: Mapping[str, object]) -> int | None:
    """thread/tokenUsage/updated の params の tokenUsage.last.inputTokens を返す。整数 (bool を除く) でなければ None。"""
    usage = params.get("tokenUsage")
    if not isinstance(usage, dict):
        return None
    last = usage.get("last")
    if not isinstance(last, dict):
        return None
    tokens = last.get("inputTokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool):
        return tokens
    return None
