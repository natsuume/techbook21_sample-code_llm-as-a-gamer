import json

from switch2_harness.event_log import (
    EVENT_COMMANDS,
    CommandEvent,
    append_event,
    event_from_json_line,
    event_to_json_line,
    read_events,
)

LOOK = CommandEvent(
    time=1000.25,
    command="look",
    args=(),
    reply="ok /w/frames/000001.png",
    ok=True,
    elapsed_s=0.5,
    frame="frames/000001.png",
)
PRESS = CommandEvent(
    time=1002.0,
    command="press",
    args=("tap", "HOME"),
    reply="error HOME は押せない",
    ok=False,
    elapsed_s=0.0,
)


def test_event_commands():
    assert EVENT_COMMANDS == ("look", "press", "status", "finish", "run")


def test_frame_defaults_to_none():
    assert PRESS.frame is None


def test_event_to_json_line_has_every_key():
    line = event_to_json_line(LOOK)
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert json.loads(line) == {
        "time": 1000.25,
        "command": "look",
        "args": [],
        "reply": "ok /w/frames/000001.png",
        "ok": True,
        "elapsed_s": 0.5,
        "frame": "frames/000001.png",
    }


def test_event_to_json_line_writes_null_frame_and_japanese_as_is():
    line = event_to_json_line(PRESS)
    data = json.loads(line)
    assert data["frame"] is None
    assert data["args"] == ["tap", "HOME"]
    assert "押せない" in line


def test_event_from_json_line_round_trip():
    assert event_from_json_line(event_to_json_line(LOOK)) == LOOK
    assert event_from_json_line(event_to_json_line(PRESS)) == PRESS


def test_event_from_json_line_makes_args_a_tuple():
    line = json.dumps(
        {
            "time": 1.0,
            "command": "run",
            "args": ["stop", "deadline"],
            "reply": "stopped",
            "ok": True,
            "elapsed_s": 0.0,
            "frame": None,
        }
    )
    assert event_from_json_line(line).args == ("stop", "deadline")


def test_append_event_then_read_events(tmp_path):
    path = tmp_path / "events.jsonl"
    append_event(path, LOOK)
    append_event(path, PRESS)
    assert read_events(path) == [LOOK, PRESS]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_append_event_keeps_existing_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(event_to_json_line(LOOK), encoding="utf-8")
    append_event(path, PRESS)
    assert read_events(path) == [LOOK, PRESS]


def test_append_event_writes_utf8(tmp_path):
    path = tmp_path / "events.jsonl"
    append_event(path, PRESS)
    assert "押せない" in path.read_bytes().decode("utf-8")


def test_read_events_of_missing_file_is_empty(tmp_path):
    assert read_events(tmp_path / "events.jsonl") == []


def test_read_events_skips_blank_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        event_to_json_line(LOOK) + "\n   \n" + event_to_json_line(PRESS),
        encoding="utf-8",
    )
    assert read_events(path) == [LOOK, PRESS]
