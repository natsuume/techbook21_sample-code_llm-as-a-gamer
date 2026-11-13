import json

import pytest
from synthetic_runs import CONTROLS_RUN_NAME, STRATEGY_RUN_NAME

from run_dashboard.run_directory import AdvisorRunDir
from run_dashboard.run_records import (
    RECORD_KINDS,
    load_run,
    read_advisor_stream,
    read_result_json,
)

NO_SKIPPED_LINES = {"run": 0, "requests": 0, "frames": 0, "pad": 0, "advisor_steps": 0}


def test_record_kinds():
    assert RECORD_KINDS == ("run", "requests", "frames", "pad", "advisor_steps")


@pytest.mark.parametrize("name", ["20991231-000000", "..", "", "a/b"])
def test_load_run_returns_none_for_unknown_run(runs, name):
    assert load_run(runs.runs_dir, name) is None


def test_load_dashboard_run(runs):
    run = runs.dashboard
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.name == run.name
    assert records.run_dir == run.run_dir
    assert records.start is not None
    assert records.start["event"] == "start"
    assert records.start["time"] == "23:59:50.000"
    assert records.origin_mono == 1000.0
    assert records.args == run.args
    assert records.args["threshold_advisor"] == 0.6
    assert [event.record["event"] for event in records.run_events] == [
        "record_error",
        "start",
        "decision",
        "decision_error",
        "decision",
        "advisor",
        "decision",
        "record_error",
        "decision",
        "capture_stopped",
        "record_end",
    ]
    # start の行より前の record_error は負の秒、00:00 台の行 (12.75 秒以降) は日付をまたいで 24 時間を足す
    assert [event.t for event in records.run_events] == pytest.approx(
        [-0.5, 0.0, 1.25, 2.125, 3.5, 5.25, 6.0, 7.0, 12.75, 14.5, 15.0]
    )
    # 行の辞書は time を含めて読んだまま
    assert records.run_events[0].record == {
        "time": "23:59:49.500",
        "event": "record_error",
        "kind": "pad",
        "error": "No space left on device",
    }
    assert records.header is not None
    assert records.header["event"] == "header"
    assert len(records.header["questions"]) == 37
    assert [request["number"] for request in records.requests] == [1, 2, 3, 4, 5, 6]
    assert records.frames is not None
    assert [frame["mono"] for frame in records.frames] == [
        1000.5,
        1000.75,
        1001.0,
        1001.25,
        1005.5,
        1012.25,
        1013.75,
    ]
    assert records.pad is not None
    assert [report["source"] for report in records.pad] == [
        "apply",
        "tap_release",
        "neutral",
        "apply",
        "apply",
    ]
    assert records.advisor_dirs == [
        AdvisorRunDir(
            CONTROLS_RUN_NAME, "controls", 1, run.run_dir / CONTROLS_RUN_NAME
        ),
        AdvisorRunDir(
            STRATEGY_RUN_NAME, "strategy", 1, run.run_dir / STRATEGY_RUN_NAME
        ),
    ]
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is True


def test_load_run_without_dashboard_records(runs):
    run = runs.no_dashboard
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.start is not None
    assert records.start["model"] == "decisions"
    assert records.origin_mono is None
    assert records.args is None
    assert [event.record["event"] for event in records.run_events] == [
        "start",
        "decision",
        "decision",
        "decision",
        "advisor",
        "interrupted",
    ]
    assert [event.t for event in records.run_events] == pytest.approx(
        [0.0, 1.0, 2.0, 13.5, 20.0, 21.0]
    )
    assert records.header is None
    assert records.requests == []
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == [
        AdvisorRunDir(run.tips_run, "tips", 1, run.run_dir / run.tips_run)
    ]
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is False
    assert records.has_stream_file is False


def test_load_run_with_save_requests_only(runs):
    run = runs.save_requests
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.origin_mono is None
    assert records.args is None
    assert [event.t for event in records.run_events] == pytest.approx([0.0, 1.5, 2.5])
    assert records.header is not None
    assert [request["number"] for request in records.requests] == [1, 2]
    assert records.requests[0]["images"][0]["file"] == run.request_files[1]
    # frames/ (要求の画像) はあっても frames.jsonl は無い
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == []
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is False


def test_load_run_with_broken_lines_counts_skipped_lines(runs):
    run = runs.broken
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.skipped_lines == {
        "run": 1,
        "requests": 1,
        "frames": 1,
        "pad": 1,
        "advisor_steps": 1,
    }
    # 読めた行は記録がそろった実行と同じ。改行で終わっていない frames.jsonl の最後の行は読まない
    assert len(records.run_events) == 11
    assert [request["number"] for request in records.requests] == [1, 2, 3, 4, 5, 6]
    assert records.frames is not None
    assert len(records.frames) == 7
    assert records.pad is not None
    assert len(records.pad) == 5
    assert records.run_events[-1].t == pytest.approx(15.0)


@pytest.mark.parametrize(
    "attribute, question_count",
    [("no_answer_type", 4), ("single_advisor_question", 34)],
)
def test_load_old_format_runs(runs, attribute, question_count):
    run = getattr(runs, attribute)
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.header is not None
    assert len(records.header["questions"]) == question_count
    assert [request["number"] for request in records.requests] == [1, 2]
    assert [event.record["event"] for event in records.run_events] == [
        "start",
        "decision",
        "decision",
        "advisor",
    ]
    assert [event.t for event in records.run_events] == pytest.approx(
        [0.0, 1.5, 3.0, 5.0]
    )
    # 役割を持たない形の advisor-001/ は、役割ごとの advisor の実行の一覧に入れない
    assert records.advisor_dirs == []
    assert records.frames is None
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is False


def write_run_lines(run_dir, lines) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )


def test_lines_with_unreadable_time_have_no_t_and_are_not_skipped(tmp_path):
    runs_dir = tmp_path / "runs"
    write_run_lines(
        runs_dir / "20261101-000000",
        [
            {"time": "10:00:00.000", "event": "start"},
            {"time": "25:00:00.000", "event": "bridge_wait"},
            {"event": "interrupted"},
            {"time": "10:00:01.250", "event": "bridge_wait_end", "waited_s": 1.25},
        ],
    )
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert [event.t for event in records.run_events] == [0.0, None, None, 1.25]
    assert records.skipped_lines["run"] == 0


def test_run_without_start_line_has_no_t(tmp_path):
    runs_dir = tmp_path / "runs"
    write_run_lines(
        runs_dir / "20261101-000000",
        [{"time": "10:00:00.000", "event": "bridge_wait"}],
    )
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert records.start is None
    assert records.origin_mono is None
    assert [event.t for event in records.run_events] == [None]


def test_empty_run_dir_has_no_records(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "20261101-000000").mkdir(parents=True)
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert records.start is None
    assert records.run_events == []
    assert records.header is None
    assert records.requests == []
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == []
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is False
    assert records.has_stream_file is False


# --- read_advisor_stream ---


def test_read_advisor_stream_returns_lines_including_raw_lines(runs):
    run_dir = runs.dashboard.run_dir
    advisor_dir = AdvisorRunDir(
        STRATEGY_RUN_NAME, "strategy", 1, run_dir / STRATEGY_RUN_NAME
    )
    lines = read_advisor_stream(advisor_dir)
    assert lines is not None
    assert len(lines) == 10
    assert lines[1]["mono"] == 1002.0
    assert lines[1]["data"]["message"]["content"][0]["name"] == "Read"
    assert lines[6] == {"mono": 1003.5, "raw": "not a json line"}
    assert lines[-1]["data"]["type"] == "result"


def test_read_advisor_stream_skips_unreadable_lines(runs):
    run_dir = runs.broken.run_dir
    advisor_dir = AdvisorRunDir(
        STRATEGY_RUN_NAME, "strategy", 1, run_dir / STRATEGY_RUN_NAME
    )
    lines = read_advisor_stream(advisor_dir)
    assert lines is not None
    assert len(lines) == 10


def test_read_advisor_stream_is_none_without_stream_file(runs):
    run = runs.no_dashboard
    advisor_dir = AdvisorRunDir(run.tips_run, "tips", 1, run.run_dir / run.tips_run)
    assert read_advisor_stream(advisor_dir) is None


# --- read_result_json ---


def test_read_result_json_returns_object(tmp_path):
    path = tmp_path / "result.json"
    path.write_text('{"returncode": 0, "duration_s": 4.0}', encoding="utf-8")
    assert read_result_json(path) == {"returncode": 0, "duration_s": 4.0}


@pytest.mark.parametrize(
    "content",
    [b"[1, 2]", b"not json", b'{"returncode": 0', b'{"note": "\xff\xfe"}'],
)
def test_read_result_json_is_none_for_unreadable_content(tmp_path, content):
    path = tmp_path / "result.json"
    path.write_bytes(content)
    assert read_result_json(path) is None


def test_read_result_json_is_none_for_missing_file(tmp_path):
    assert read_result_json(tmp_path / "result.json") is None
