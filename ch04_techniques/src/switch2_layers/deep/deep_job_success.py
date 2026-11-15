"""deep のジョブが正常に終わったかを、実行ディレクトリーに残る記録から読み取る。

次の 2 つの記録のどちらかが正常な終わりを示していれば、そのジョブは正常に終わったものとして扱う:
    run.jsonl の deep_end
        job がそのジョブのディレクトリーの実行ディレクトリーからの相対パス (deep/<種類>-<連番>) で、
        status が DEEP_END_STATUS_OK ("ok") の行。run で動かしたジョブに残る (DeepRunner の種類を問わない)
    ジョブのディレクトリーの result.json (deep.claude_stream_log)
        returncode が 0、is_error が False、stopped が False。Claude Code で動かしたジョブに残る
        (deep-once は run.jsonl に書かないため、deep-once で動かしたジョブにはこちらだけが残る)
どちらの記録も正常な終わりを示さないジョブ (まだ実行中、起動を拒んだ、失敗した、stop・kill で止めたジョブと、
deep-once で動かしたフェイクと map のジョブ) は、正常に終わったものとして扱わない。
"""

import json
from collections.abc import Collection
from pathlib import Path

from switch2_layers.deep.claude_stream_log import RESULT_FILE
from switch2_layers.file_io import read_json_lines
from switch2_layers.records.run_log import EVENT_DEEP_END

# run.jsonl の deep_end の status のうち、正常に終わったことを表す値 (deep_scheduler.DeepJobStatus.OK の値)
DEEP_END_STATUS_OK = "ok"


def read_succeeded_jobs(run_log: Path) -> frozenset[str]:
    """run.jsonl (run_log) の deep_end のうち、status が DEEP_END_STATUS_OK の行の job の集合を返す。

    job は実行ディレクトリーからのジョブのディレクトリーの相対パス (deep/<種類>-<連番>)。run.jsonl が無ければ空の集合。
    読み飛ばす行の規則は file_io.read_json_lines と同じ。job が文字列でない行は数えない。
    """
    jobs: set[str] = set()
    for row in read_json_lines(run_log):
        if row.get("event") != EVENT_DEEP_END:
            continue
        if row.get("status") != DEEP_END_STATUS_OK:
            continue
        job = row.get("job")
        if isinstance(job, str):
            jobs.add(job)
    return frozenset(jobs)


def is_success_result_record(record: object) -> bool:
    """result.json を読んだ値 record が、正常に終わったジョブのものなら True を返す。

    record が object で、returncode が 0 (真偽値を除く整数)、is_error が False、stopped が False のとき True。
    """
    if not isinstance(record, dict):
        return False
    returncode = record.get("returncode")
    if isinstance(returncode, bool) or returncode != 0:
        return False
    return record.get("is_error") is False and record.get("stopped") is False


def read_result_shows_success(job_dir: Path) -> bool:
    """job_dir/result.json を読み、is_success_result_record の結果を返す。無い・読めないときは False。"""
    try:
        record = json.loads((job_dir / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return is_success_result_record(record)


def is_succeeded_job(
    job: str, job_dir: Path, succeeded_in_run_log: Collection[str]
) -> bool:
    """ジョブが正常に終わったなら True を返す。

    job                    ジョブのディレクトリーの実行ディレクトリーからの相対パス (run.jsonl の deep_end の job)
    job_dir                ジョブのディレクトリー
    succeeded_in_run_log   read_succeeded_jobs の結果
    job が succeeded_in_run_log にあるか、read_result_shows_success(job_dir) が True なら True。
    """
    return job in succeeded_in_run_log or read_result_shows_success(job_dir)
