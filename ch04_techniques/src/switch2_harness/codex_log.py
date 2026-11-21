"""codex exec --json の出力 (agent.jsonl と批評役の critic-<NNN>.jsonl) を読む純粋関数。

出力は 1 行 1 つの JSON のオブジェクトで、次の形のものを読む (2026-11-21、codex-cli 0.171.0 で確かめた):
    {"type": "thread.started", "thread_id": "<ID>"}       起動のたび (resume でも同じ ID) に最初に出る
    {"type": "turn.started"}
    {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}   エージェントの発言
    {"type": "item.completed", "item": {"type": "command_execution", "command": "...", ...}}
    {"type": "item.completed", "item": {"type": "file_change", ...}}  apply_patch の書き込み
    {"type": "item.completed", "item": {"type": "error", "message": "..."}}  Codex の警告 (失敗ではない)
    {"type": "turn.completed", "usage": {"input_tokens": .., "cached_input_tokens": .., "output_tokens": ..,
                                         "reasoning_output_tokens": .., ...}}
    {"type": "error", "message": "..."} と {"type": "turn.failed", "error": {"message": "..."}}  ターンの失敗
turn.completed の usage は、そのスレッドの累計 (resume で続けた起動では、前の起動の分を含む)。turn.completed には
スレッドの ID が無いため、前に出た thread.started の ID と組にする。費用 (USD) は出ない。
JSON として読めない行 (警告の行・途中で切れた行) と、JSON のオブジェクトでない行は読み飛ばす。
"""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields

THREAD_STARTED = "thread.started"
TURN_STARTED = "turn.started"
TURN_COMPLETED = "turn.completed"
TURN_FAILED = "turn.failed"
ITEM_COMPLETED = "item.completed"
AGENT_MESSAGE = "agent_message"

# item.completed のうち、応答に数えない item の種類 (推論の要約と、Codex 自身の警告)
NON_RESPONSE_ITEM_TYPES: tuple[str, ...] = ("reasoning", "error")


@dataclass(frozen=True)
class TokenUsage:
    """turn.completed の usage のうち、集計するトークン数。無い項目・数でない項目は 0。"""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0


def codex_messages(lines: Iterable[str]) -> Iterator[dict[str, object]]:
    """行のうち、JSON のオブジェクトとして読める行を順に返す。読めない行と空行は読み飛ばす。"""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            value: object = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _completed_item(message: dict[str, object]) -> dict[str, object] | None:
    """message が item.completed なら、その item (オブジェクト) を返す。そうでなければ None。"""
    item = message.get("item")
    if message.get("type") == ITEM_COMPLETED and isinstance(item, dict):
        return item
    return None


def _agent_message_text(message: dict[str, object]) -> str | None:
    """message が agent_message の item.completed で、text が空白だけでない文字列なら、前後の空白を除いて返す。"""
    item = _completed_item(message)
    if item is None or item.get("type") != AGENT_MESSAGE:
        return None
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def last_thread_id(lines: Iterable[str]) -> str | None:
    """thread.started の thread_id (文字列) の最後のものを返す。無ければ None。起動し直し (resume) に使う。"""
    found: str | None = None
    for message in codex_messages(lines):
        thread_id = message.get("thread_id")
        if message.get("type") == THREAD_STARTED and isinstance(thread_id, str):
            found = thread_id
    return found


def agent_message_texts(lines: Iterable[str]) -> list[str]:
    """エージェントの発言 (agent_message の item.completed の text。前後の空白を除き、空は除く) を順に返す。

    振り返りの批評役に渡す、プレイヤーの発言に使う。
    """
    texts: list[str] = []
    for message in codex_messages(lines):
        text = _agent_message_text(message)
        if text is not None:
            texts.append(text)
    return texts


def final_agent_message(lines: Iterable[str]) -> str | None:
    """最後のターンが成功して終わったとき、そのターンの最後の発言を返す。そうでなければ None。

    最後の turn.completed・turn.failed・{"type": "error"} のうち最後に出たものが turn.completed で、
    その前の (最後の turn.started より後の) agent_message があれば、その text (前後の空白を除いたもの) を返す。
    批評役の出力から批評を取り出すのに使う。
    """
    last_text: str | None = None
    result: str | None = None
    for message in codex_messages(lines):
        kind = message.get("type")
        if kind == TURN_STARTED:
            last_text = None
            result = None
        elif kind == TURN_COMPLETED:
            result = last_text
        elif kind in (TURN_FAILED, "error"):
            result = None
            last_text = None
        else:
            text = _agent_message_text(message)
            if text is not None:
                last_text = text
    return result


def _count(value: object) -> int:
    """value が int (bool を除く) ならその値、そうでなければ 0 を返す。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def usage_from(message: dict[str, object]) -> TokenUsage:
    """turn.completed の message の usage を TokenUsage にする。usage が無い・オブジェクトでなければすべて 0。"""
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    return TokenUsage(
        **{field.name: _count(usage.get(field.name)) for field in fields(TokenUsage)}
    )


def max_usage_by_thread(lines: Iterable[str]) -> dict[str | None, TokenUsage]:
    """スレッドごとに、turn.completed の usage の項目ごとの最大の値を返す。

    turn.completed は、その前に出た最後の thread.started の thread_id のスレッドのものとする
    (thread.started より前の turn.completed は None のスレッド)。usage は累計なので、最大の値がそのスレッドで使った量になる。
    """
    current: str | None = None
    found: dict[str | None, TokenUsage] = {}
    for message in codex_messages(lines):
        kind = message.get("type")
        thread_id = message.get("thread_id")
        if kind == THREAD_STARTED and isinstance(thread_id, str):
            current = thread_id
        elif kind == TURN_COMPLETED:
            usage = usage_from(message)
            before = found.get(current, TokenUsage())
            found[current] = TokenUsage(
                **{
                    field.name: max(
                        getattr(before, field.name), getattr(usage, field.name)
                    )
                    for field in fields(TokenUsage)
                }
            )
    return found


def count_turns_completed(lines: Iterable[str]) -> int:
    """turn.completed の行の数を返す。"""
    return sum(
        1 for message in codex_messages(lines) if message.get("type") == TURN_COMPLETED
    )


def count_responses(lines: Iterable[str]) -> int:
    """応答の数を返す。item.completed のうち、item の種類が NON_RESPONSE_ITEM_TYPES でないもの
    (発言・コマンドの実行・ファイルの変更など) の数。"""
    count = 0
    for message in codex_messages(lines):
        item = _completed_item(message)
        if item is not None and item.get("type") not in NON_RESPONSE_ITEM_TYPES:
            count += 1
    return count


def has_codex_output(lines: Iterable[str]) -> bool:
    """item.completed か turn.completed の行が 1 つでもあれば True を返す (report がエージェントの記録の有無に使う)。"""
    return any(
        message.get("type") in (ITEM_COMPLETED, TURN_COMPLETED)
        for message in codex_messages(lines)
    )
