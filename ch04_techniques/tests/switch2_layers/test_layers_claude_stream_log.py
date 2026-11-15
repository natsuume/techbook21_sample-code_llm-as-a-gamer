import json
from pathlib import Path

from switch2_layers.deep.claude_stream_log import (
    RESULT_FILE,
    STREAM_FILE,
    ClaudeStreamLog,
    assistant_text,
    build_result_record,
    is_result_message,
    permission_denials_of,
    stream_line_record,
)

INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5-5"}
ASSISTANT = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "text", "text": "文書を読む"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
            {"type": "text", "text": "次に編集する"},
        ]
    },
}
TOOL_ONLY = {
    "type": "assistant",
    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
}
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "攻略情報を更新した",
    "total_cost_usd": 0.42,
    "duration_ms": 61234,
    "permission_denials": [{"tool_name": "WebFetch"}],
}


def line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


def read_lines(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


def test_file_names_follow_the_issue():
    assert (STREAM_FILE, RESULT_FILE) == ("stream.jsonl", "result.json")


def test_stream_line_record_of_json_and_other_lines():
    assert stream_line_record(line(INIT), 1.23456) == {
        "elapsed_s": 1.235,
        "data": INIT,
    }
    assert stream_line_record("not json\r\n", 2.0) == {
        "elapsed_s": 2.0,
        "raw": "not json",
    }
    # object でない JSON も data に入れる
    assert stream_line_record("[1, 2]\n", 0.0) == {"elapsed_s": 0.0, "data": [1, 2]}


def test_is_result_message():
    assert is_result_message(RESULT)
    assert not is_result_message(ASSISTANT)
    assert not is_result_message("result")


def test_assistant_text_joins_text_blocks():
    assert assistant_text(ASSISTANT) == "文書を読む\n次に編集する"
    assert assistant_text(TOOL_ONLY) is None
    assert assistant_text(RESULT) is None
    assert assistant_text({"type": "assistant", "message": "broken"}) is None


def test_stream_log_writes_each_line_and_remembers_result(tmp_path):
    path = tmp_path / STREAM_FILE
    log = ClaudeStreamLog(path)
    log.add_line(line(INIT), 0.5)
    log.add_line(line(ASSISTANT), 1.0)
    log.add_line("garbage\n", 1.5)
    log.add_line(line(TOOL_ONLY), 2.0)
    log.add_line(line(RESULT), 3.0)
    log.close()
    assert read_lines(path) == [
        {"elapsed_s": 0.5, "data": INIT},
        {"elapsed_s": 1.0, "data": ASSISTANT},
        {"elapsed_s": 1.5, "raw": "garbage"},
        {"elapsed_s": 2.0, "data": TOOL_ONLY},
        {"elapsed_s": 3.0, "data": RESULT},
    ]
    assert log.result_message == RESULT
    # ツールの呼び出しだけの行は、最後の文を変えない
    assert log.last_assistant_text == "文書を読む\n次に編集する"
    assert log.write_error is None


def test_stream_log_starts_with_an_empty_file(tmp_path):
    path = tmp_path / STREAM_FILE
    path.write_text("old\n", encoding="utf-8")
    log = ClaudeStreamLog(path)
    log.close()
    assert path.read_text(encoding="utf-8") == ""
    assert log.result_message is None
    assert log.last_assistant_text is None


def test_build_result_record_with_result_message():
    record = build_result_record(
        returncode=0,
        duration_s=61.23456,
        result_message=RESULT,
        last_assistant_text="途中の文",
        stopped=False,
        killed=False,
        error=None,
        stderr="",
    )
    assert record == {
        "returncode": 0,
        "duration_s": 61.235,
        "cost_usd": 0.42,
        "last_message": "攻略情報を更新した",
        "is_error": False,
        "permission_denials": [{"tool_name": "WebFetch"}],
        "stopped": False,
        "killed": False,
        "error": None,
        "stderr": "",
    }
    assert list(record) == [
        "returncode",
        "duration_s",
        "cost_usd",
        "last_message",
        "is_error",
        "permission_denials",
        "stopped",
        "killed",
        "error",
        "stderr",
    ]


def test_build_result_record_without_result_message_uses_last_assistant_text():
    # SIGTERM で止めると result の行は出ない
    record = build_result_record(
        returncode=143,
        duration_s=5.0,
        result_message=None,
        last_assistant_text="途中の文",
        stopped=True,
        killed=False,
        error=None,
        stderr="terminated\n",
    )
    assert record["cost_usd"] is None
    assert record["is_error"] is None
    assert record["last_message"] == "途中の文"
    assert record["permission_denials"] == []
    assert record["stopped"] is True
    assert record["stderr"] == "terminated\n"


def test_build_result_record_ignores_values_of_wrong_types():
    broken = {"type": "result", "result": 3, "total_cost_usd": "x", "is_error": "no"}
    record = build_result_record(
        returncode=1,
        duration_s=0.0,
        result_message=broken,
        last_assistant_text=None,
        stopped=False,
        killed=False,
        error=None,
        stderr="",
    )
    assert record["cost_usd"] is None
    assert record["is_error"] is None
    assert record["last_message"] is None
    assert record["permission_denials"] == []


def test_permission_denials_of():
    assert permission_denials_of(RESULT) == [{"tool_name": "WebFetch"}]
    assert permission_denials_of(None) == []
    assert permission_denials_of({"permission_denials": "x"}) == []
