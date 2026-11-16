import json
from pathlib import Path

from switch2_layers.orchestrator.session_record import (
    SessionEndReason,
    SessionRecordWriter,
    TurnKind,
    TurnOutcome,
    TurnRecord,
    compact_row,
    last_json_block,
    session_end_row,
    session_start_row,
    turn_row,
)
from switch2_layers.run_directory import RunPaths


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]


def turn_record(**changes: object) -> TurnRecord:
    values: dict[str, object] = {
        "session": 2,
        "turn": 3,
        "kind": TurnKind.TURN,
        "t_sent": 10.5,
        "t_result": 13.2501,
        "input_text": "## 時刻\nt=10.500",
        "images": ("frames/interval/000009000.jpg", "frames/interval/000010000.jpg"),
        "response_json": '{"actions": []}',
        "errors": None,
        "outcome": TurnOutcome.APPLIED,
        "usage_tokens": 14148,
    }
    values.update(changes)
    return TurnRecord(**values)  # type: ignore[arg-type]


# --- 応答の JSON の文 ---


def test_last_json_block_is_taken_from_the_response():
    text = (
        '前の案\n```json\n{"actions": [1]}\n```\n'
        'やり直す\n```json\n{"actions": [{"type": "wait", "seconds": 1}]}\n```\n'
    )
    assert last_json_block(text) == '{"actions": [{"type": "wait", "seconds": 1}]}'


def test_multiline_json_block_is_kept_as_written():
    text = '答え\n```json\n{\n  "actions": []\n}\n```'
    assert last_json_block(text) == '{\n  "actions": []\n}'


def test_no_json_block_gives_none():
    assert last_json_block("JSON を書き忘れた") is None
    assert last_json_block("```\n{}\n```") is None
    assert last_json_block(None) is None


# --- sessions.jsonl の行 ---


def test_session_start_row():
    row = session_start_row(12.0, 1, "orchestrator/session-001/stream.jsonl")
    assert row == {
        "t": 12.0,
        "event": "start",
        "session": 1,
        "stream": "orchestrator/session-001/stream.jsonl",
    }
    assert list(row) == ["t", "event", "session", "stream"]


def test_session_end_row():
    row = session_end_row(
        40.0, 1, SessionEndReason.ROTATE, 121_000, 0, completed_turns=7, error=None
    )
    assert row == {
        "t": 40.0,
        "event": "end",
        "session": 1,
        "reason": "rotate",
        "usage_tokens": 121_000,
        "returncode": 0,
        "completed_turns": 7,
        "error": None,
    }
    assert list(row) == [
        "t",
        "event",
        "session",
        "reason",
        "usage_tokens",
        "returncode",
        "completed_turns",
        "error",
    ]


def test_session_end_reasons_are_the_four_of_the_issue():
    assert {reason.value for reason in SessionEndReason} == {
        "rotate",
        "timeout",
        "exit",
        "close",
    }


def test_compact_row():
    row = compact_row(30.0, 2, {"trigger": "auto", "pre_tokens": 967_000})
    assert row == {
        "t": 30.0,
        "event": "compact",
        "session": 2,
        "metadata": {"trigger": "auto", "pre_tokens": 967_000},
    }


# --- turns.jsonl の行 ---


def test_turn_row_has_the_keys_in_order():
    row = turn_row(turn_record())
    assert list(row) == [
        "session",
        "turn",
        "kind",
        "t_sent",
        "t_result",
        "duration_s",
        "input_text",
        "images",
        "response_json",
        "errors",
        "outcome",
        "usage_tokens",
    ]
    assert row["kind"] == "turn"
    assert row["outcome"] == "applied"
    assert row["duration_s"] == 2.75
    assert row["images"] == [
        "frames/interval/000009000.jpg",
        "frames/interval/000010000.jpg",
    ]
    assert row["errors"] is None


def test_turn_row_of_an_invalid_turn_lists_the_errors():
    row = turn_row(
        turn_record(
            outcome=TurnOutcome.INVALID, errors=("unknown key 'action'", "no actions")
        )
    )
    assert row["outcome"] == "invalid"
    assert row["errors"] == ["unknown key 'action'", "no actions"]


def test_turn_row_without_result_has_no_duration():
    row = turn_row(
        turn_record(
            kind=TurnKind.HANDOFF,
            t_result=None,
            response_json=None,
            outcome=TurnOutcome.TIMEOUT,
            images=(),
        )
    )
    assert row["kind"] == "handoff"
    assert row["t_result"] is None
    assert row["duration_s"] is None
    assert row["outcome"] == "timeout"
    assert row["images"] == []


# --- 書き込み ---


def test_writer_creates_the_directory_and_appends_rows(tmp_path):
    paths = RunPaths(tmp_path / "runs" / "20261116-120000")
    paths.root.mkdir(parents=True)
    writer = SessionRecordWriter(paths)
    writer.write_session(
        session_start_row(1.0, 1, "orchestrator/session-001/stream.jsonl")
    )
    writer.write_turn(turn_record(session=1, turn=1))
    writer.write_turn(turn_record(session=1, turn=2))
    sessions = read_jsonl(paths.root / "orchestrator" / "sessions.jsonl")
    turns = read_jsonl(paths.root / "orchestrator" / "turns.jsonl")
    assert sessions == [
        session_start_row(1.0, 1, "orchestrator/session-001/stream.jsonl")
    ]
    assert [row["turn"] for row in turns] == [1, 2]
    assert turns[0] == turn_row(turn_record(session=1, turn=1))


def test_writer_failure_does_not_raise(tmp_path):
    paths = RunPaths(tmp_path / "runs" / "20261116-120000")
    paths.root.mkdir(parents=True)
    # orchestrator をファイルにして、ディレクトリーを作れなくする
    (paths.root / "orchestrator").write_text("not a directory\n", encoding="utf-8")
    writer = SessionRecordWriter(paths)
    writer.write_session(session_start_row(1.0, 1, "x"))
    writer.write_turn(turn_record())
