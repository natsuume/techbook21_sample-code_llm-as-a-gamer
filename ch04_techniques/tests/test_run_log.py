import json
import re
import time
from pathlib import Path

from switch2_agent.run_log import RunLog, format_wall_time, summarize_event

TIME_PATTERN = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}")


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_format_wall_time_has_seconds_and_milliseconds():
    now = 1700000000.1234
    expected = time.strftime("%H:%M:%S", time.localtime(now)) + ".123"
    assert format_wall_time(now) == expected


def test_summarize_event_puts_event_name_first():
    assert (
        summarize_event({"images": 3, "event": "decision", "decide_s": 0.5})
        == "decision images=3 decide_s=0.5"
    )


def test_summarize_event_without_event_key():
    assert summarize_event({"error": "boom"}) == "event error=boom"


def test_write_appends_one_json_line_per_event(tmp_path):
    path = tmp_path / "run.jsonl"
    log = RunLog(path, echo=None)
    log.write({"event": "decision", "images": 3})
    log.write({"event": "advisor", "result": "戦略を更新した"})
    lines = read_lines(path)
    assert len(lines) == 2
    assert lines[0]["event"] == "decision"
    assert lines[0]["images"] == 3
    assert lines[1]["result"] == "戦略を更新した"
    for line in lines:
        assert TIME_PATTERN.fullmatch(line["time"])


def test_write_keeps_existing_lines(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text('{"event": "earlier"}\n', encoding="utf-8")
    RunLog(path, echo=None).write({"event": "decision"})
    assert [line["event"] for line in read_lines(path)] == ["earlier", "decision"]


def test_write_keeps_non_ascii_text_readable(tmp_path):
    path = tmp_path / "run.jsonl"
    RunLog(path, echo=None).write({"event": "advisor", "result": "戦略"})
    assert "戦略" in path.read_text(encoding="utf-8")


def test_write_converts_non_json_values_to_string(tmp_path):
    path = tmp_path / "run.jsonl"
    RunLog(path, echo=None).write(
        {"event": "advisor", "run_dir": Path("/tmp/advisor-001")}
    )
    assert read_lines(path)[0]["run_dir"] == "/tmp/advisor-001"


def test_write_echoes_summary_line(tmp_path):
    echoed: list[str] = []
    log = RunLog(tmp_path / "run.jsonl", echo=echoed.append)
    log.write({"event": "decision", "images": 3})
    assert len(echoed) == 1
    time_text, summary = echoed[0].split(" ", 1)
    assert TIME_PATTERN.fullmatch(time_text)
    assert summary == "decision images=3"
    assert "\n" not in echoed[0]
