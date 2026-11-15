import json
from pathlib import Path

import pytest

from switch2_layers.deep.deep_job_success import (
    DEEP_END_STATUS_OK,
    is_succeeded_job,
    is_success_result_record,
    read_result_shows_success,
    read_succeeded_jobs,
)
from switch2_layers.deep.deep_scheduler import DeepJobStatus

SUCCESS_RESULT = {
    "returncode": 0,
    "duration_s": 60.0,
    "cost_usd": 0.5,
    "last_message": "更新した",
    "is_error": False,
    "permission_denials": [],
    "stopped": False,
    "killed": False,
    "error": None,
    "stderr": "",
}


def write_lines(path: Path, rows: list[object], tail: str = "") -> None:
    text = "".join(json.dumps(row) + "\n" for row in rows) + tail
    path.write_text(text, encoding="utf-8")


def test_ok_status_is_the_value_of_the_scheduler():
    assert DEEP_END_STATUS_OK == DeepJobStatus.OK.value


def test_read_succeeded_jobs_reads_ok_deep_end_rows(tmp_path):
    run_log = tmp_path / "run.jsonl"
    write_lines(
        run_log,
        [
            {"t": 0.0, "event": "start", "args": {}},
            {"t": 1.0, "event": "deep_start", "job": "deep/strategy-001"},
            {"t": 2.0, "event": "deep_end", "job": "deep/strategy-001", "status": "ok"},
            {
                "t": 3.0,
                "event": "deep_end",
                "job": "deep/advice-001",
                "status": "failed",
            },
            {"t": 4.0, "event": "deep_end", "job": "deep/tips-001", "status": "killed"},
            {"t": 5.0, "event": "deep_end", "job": 3, "status": "ok"},
            {"t": 6.0, "event": "deep_skipped", "job": "deep/map-001", "status": "ok"},
        ],
        # 書いている途中の行は読まない
        tail='{"t": 7.0, "event": "deep_end", "job": "deep/map-002", "status": "ok"',
    )
    assert read_succeeded_jobs(run_log) == frozenset({"deep/strategy-001"})


def test_read_succeeded_jobs_without_run_log_is_empty(tmp_path):
    assert read_succeeded_jobs(tmp_path / "run.jsonl") == frozenset()


def test_success_result_record():
    assert is_success_result_record(SUCCESS_RESULT)


@pytest.mark.parametrize(
    "record",
    [
        {**SUCCESS_RESULT, "returncode": 1},
        {**SUCCESS_RESULT, "returncode": None},
        {**SUCCESS_RESULT, "returncode": False},
        {**SUCCESS_RESULT, "is_error": True},
        {**SUCCESS_RESULT, "is_error": None},
        {**SUCCESS_RESULT, "stopped": True},
        {"returncode": 0},
        [SUCCESS_RESULT],
        None,
    ],
)
def test_other_result_records_are_not_success(record):
    assert not is_success_result_record(record)


def test_read_result_shows_success(tmp_path):
    assert not read_result_shows_success(tmp_path)
    (tmp_path / "result.json").write_text("{", encoding="utf-8")
    assert not read_result_shows_success(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps(SUCCESS_RESULT), encoding="utf-8")
    assert read_result_shows_success(tmp_path)


def test_is_succeeded_job_reads_either_record(tmp_path):
    job_dir = tmp_path / "deep" / "strategy-001"
    job_dir.mkdir(parents=True)
    assert not is_succeeded_job("deep/strategy-001", job_dir, frozenset())
    assert is_succeeded_job("deep/strategy-001", job_dir, {"deep/strategy-001"})
    (job_dir / "result.json").write_text(json.dumps(SUCCESS_RESULT), encoding="utf-8")
    assert is_succeeded_job("deep/strategy-001", job_dir, frozenset())
