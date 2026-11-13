"""1 つの実行のディレクトリーの記録を読み、経過秒を付けてまとめる。

ファイルを読むのは、このモジュールと run_directory だけにする。読むファイル (第4章の switch2_agent が書く。形は
ch04_techniques/README.md のログの節):
    run.jsonl                         実行のログ (switch2_agent.run_log.RunLog)
    requests.jsonl                    要求ごとの入出力 (switch2_agent.request_log.RequestLog)
    frames.jsonl・pad.jsonl            フレームの判定と送ったレポート (switch2_agent.dashboard_records)
    advisor-<役割>-<連番>/stream.jsonl  advisor の途中経過 (switch2_agent.advisors.stream_json_log)
    advisor-<役割>-<連番>/result.json   advisor の実行の結果 (switch2_agent.advisors.claude_code_headless)
JSON Lines は jsonl_lines.read_json_lines で読む (改行で終わっていない最後の行は読まず、JSON として読めない行は
読み飛ばして数える)。無いファイルと、読めない (OSError) ファイルは、無いものとして扱う。ファイルには何も書かない。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from switch2_agent.advisors.stream_json_log import STREAM_FILE_NAME
from switch2_agent.dashboard_records import (
    FRAMES_RECORD_FILE_NAME,
    PAD_RECORD_FILE_NAME,
)
from switch2_agent.request_log import REQUEST_LOG_FILE_NAME
from switch2_agent.run import RUN_LOG_FILE_NAME

from run_dashboard.elapsed_time import parse_wall_time, run_log_elapsed
from run_dashboard.jsonl_lines import ParsedLines, is_json_number, read_json_lines
from run_dashboard.run_directory import (
    AdvisorRunDir,
    find_run_dir,
    list_advisor_run_dirs,
)

# 記録の種類の名前。summary の skipped_lines のキーにも使う。advisor_steps は全部の stream.jsonl の合計
RECORD_KINDS = ("run", "requests", "frames", "pad", "advisor_steps")
# 実行のディレクトリーの直下にある JSON Lines のファイルの、種類 → ファイル名
RECORD_FILE_NAMES = {
    "run": RUN_LOG_FILE_NAME,
    "requests": REQUEST_LOG_FILE_NAME,
    "frames": FRAMES_RECORD_FILE_NAME,
    "pad": PAD_RECORD_FILE_NAME,
}


@dataclass(frozen=True)
class RunEvent:
    """run.jsonl の 1 行と、その経過秒。

    t        経過秒 (load_run の docstring の求め方)。求められなければ None
    record   その行の辞書 (time を含む、読んだままの辞書)
    """

    t: float | None
    record: dict


@dataclass(frozen=True)
class RunOverview:
    """実行の一覧 (GET /api/runs) の 1 件に使う、実行の記録の一部。

    name               実行のディレクトリーの名前
    start              run.jsonl の、event が "start" の最初の行。無ければ None
    origin_mono        start の origin_mono。数 (bool を除く int と float) でなければ None
    has_requests_file  requests.jsonl を読めるか
    """

    name: str
    start: dict | None
    origin_mono: float | None
    has_requests_file: bool


@dataclass(frozen=True)
class RunRecords:
    """1 つの実行の記録。

    name              実行のディレクトリーの名前
    run_dir           実行のディレクトリー
    start             run.jsonl の、event が "start" の最初の行。無ければ None
    origin_mono       start の origin_mono (--record-dashboard の記録の時計の起点)。数 (bool を除く int と float) でなければ None
    args              start の args (起動引数)。object (辞書) でなければ None
    run_events        run.jsonl の全行 (ファイルの順)。run.jsonl が無ければ空のリスト
    header            requests.jsonl の、event が "header" の最初の行。無ければ None
    requests          requests.jsonl の、event が "request" の行 (ファイルの順)。無ければ空のリスト
    frames            frames.jsonl の行 (ファイルの順)。ファイルが無ければ None
    pad               pad.jsonl の行 (ファイルの順)。ファイルが無ければ None
    advisor_dirs      役割ごとの advisor の実行のディレクトリー (run_directory.list_advisor_run_dirs)
    skipped_lines     RECORD_KINDS の種類 → 読み飛ばした行の数。5 つの種類すべてのキーを持つ (0 を含む)
    has_requests_file requests.jsonl を読めたか
    has_stream_file   advisor_dirs のどれかに stream.jsonl があって読めたか
    """

    name: str
    run_dir: Path
    start: dict | None
    origin_mono: float | None
    args: dict | None
    run_events: list[RunEvent]
    header: dict | None
    requests: list[dict]
    frames: list[dict] | None
    pad: list[dict] | None
    advisor_dirs: list[AdvisorRunDir]
    skipped_lines: dict[str, int]
    has_requests_file: bool
    has_stream_file: bool

    @property
    def overview(self) -> RunOverview:
        """この記録の、一覧の 1 件に使う部分 (load_run_overview と同じ値)。"""
        return RunOverview(
            name=self.name,
            start=self.start,
            origin_mono=self.origin_mono,
            has_requests_file=self.has_requests_file,
        )


def load_run_overview(runs_dir: Path, name: str) -> RunOverview | None:
    """runs_dir の実行 name の、一覧の 1 件に使う部分だけを読んで返す。

    run_directory.find_run_dir(runs_dir, name) で照合し、一致しなければ None を返す。読むのは run.jsonl だけで、
    requests.jsonl は開けるかだけを確かめる (一覧では実行ごとに大きなファイルを読まないため)。start と origin_mono の
    決め方は load_run と同じ。has_requests_file は requests.jsonl を開けるか (無いか、OSError なら False)。
    """
    run_dir = find_run_dir(runs_dir, name)
    if run_dir is None:
        return None
    run_lines = _records_of(_read_lines_if_readable(run_dir / RUN_LOG_FILE_NAME)) or []
    _, start = _find_start(run_lines)
    return RunOverview(
        name=name,
        start=start,
        origin_mono=_origin_mono_of(start),
        has_requests_file=_can_open(run_dir / REQUEST_LOG_FILE_NAME),
    )


def load_run(runs_dir: Path, name: str) -> RunRecords | None:
    """runs_dir の実行 name の記録を読んで返す。

    run_directory.find_run_dir(runs_dir, name) で照合し、一致しなければ None を返す。
    RECORD_FILE_NAMES のファイルと、advisor_dirs のそれぞれの stream.jsonl
    (switch2_agent.advisors.stream_json_log.STREAM_FILE_NAME) を jsonl_lines.read_json_lines で読み、
    読み飛ばした行の数を skipped_lines に入れる。無いファイルと読めないファイル (OSError) は無いものとして扱い、
    その種類の skipped_lines は 0 にする。stream.jsonl は skipped_lines と has_stream_file のためだけに読み、
    中身は持たない (中身は read_advisor_stream で読む)。

    run.jsonl の行の経過秒 t:
        start の行の time を elapsed_time.parse_wall_time で読んだ値を起点にし、各行に
        elapsed_time.run_log_elapsed(その行の time を parse_wall_time で読んだ値, 起点,
        after_start=その行がファイルの中で start の行より後にあるか) を付ける。start の行自身は 0.0。
        start の行が無い、start の行の time が読めない、その行の time が読めないときは t を None にする
        (t が None の行は skipped_lines に数えない)。
    """
    run_dir = find_run_dir(runs_dir, name)
    if run_dir is None:
        return None
    skipped_lines = dict.fromkeys(RECORD_KINDS, 0)
    parsed: dict[str, ParsedLines | None] = {}
    for kind, file_name in RECORD_FILE_NAMES.items():
        lines = _read_lines_if_readable(run_dir / file_name)
        parsed[kind] = lines
        if lines is not None:
            skipped_lines[kind] = lines.skipped

    run_lines = _records_of(parsed["run"]) or []
    start_index, start = _find_start(run_lines)
    args = start.get("args") if start is not None else None

    request_lines = _records_of(parsed["requests"]) or []
    header = next(
        (line for line in request_lines if line.get("event") == "header"), None
    )

    advisor_dirs = list_advisor_run_dirs(run_dir)
    has_stream_file = False
    for advisor_dir in advisor_dirs:
        stream = _read_lines_if_readable(advisor_dir.path / STREAM_FILE_NAME)
        if stream is not None:
            has_stream_file = True
            skipped_lines["advisor_steps"] += stream.skipped

    return RunRecords(
        name=name,
        run_dir=run_dir,
        start=start,
        origin_mono=_origin_mono_of(start),
        args=args if isinstance(args, dict) else None,
        run_events=_build_run_events(run_lines, start_index),
        header=header,
        requests=[line for line in request_lines if line.get("event") == "request"],
        frames=_records_of(parsed["frames"]),
        pad=_records_of(parsed["pad"]),
        advisor_dirs=advisor_dirs,
        skipped_lines=skipped_lines,
        has_requests_file=parsed["requests"] is not None,
        has_stream_file=has_stream_file,
    )


def _find_start(run_lines: list[dict]) -> tuple[int | None, dict | None]:
    """run.jsonl の行 run_lines の、event が "start" の最初の行の位置とその行を返す。無ければ (None, None)。"""
    for index, line in enumerate(run_lines):
        if line.get("event") == "start":
            return index, line
    return None, None


def _origin_mono_of(start: dict | None) -> float | None:
    """start の行の origin_mono が数 (bool を除く int と float) なら float にして返し、そうでなければ None を返す。"""
    origin_mono = start.get("origin_mono") if start is not None else None
    return float(origin_mono) if is_json_number(origin_mono) else None


def _can_open(path: Path) -> bool:
    """path のファイルを読み取りで開けるなら True を返す (中身は読まない)。無いか、開けない (OSError) なら False。"""
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def _read_lines_if_readable(path: Path) -> ParsedLines | None:
    """path の JSON Lines を jsonl_lines.read_json_lines で読んで返す。無いか、読めない (OSError) なら None を返す。"""
    try:
        return read_json_lines(path)
    except OSError:
        return None


def _records_of(lines: ParsedLines | None) -> list[dict] | None:
    """lines の行の辞書のリスト (lines.records) を返す。lines が None (ファイルが無い) なら None を返す。"""
    return lines.records if lines is not None else None


def _build_run_events(lines: list[dict], start_index: int | None) -> list[RunEvent]:
    """run.jsonl の行 lines (ファイルの順) に経過秒 t を付けた RunEvent のリストを返す (load_run の docstring の t)。

    start_index は start の行の lines の中の位置 (start の行が無ければ None)。
    """
    start_time = (
        parse_wall_time(lines[start_index].get("time"))
        if start_index is not None
        else None
    )
    events = []
    for index, line in enumerate(lines):
        t: float | None = None
        if start_index is not None and start_time is not None:
            if index == start_index:
                t = 0.0
            else:
                line_time = parse_wall_time(line.get("time"))
                if line_time is not None:
                    t = run_log_elapsed(
                        line_time, start_time, after_start=index > start_index
                    )
        events.append(RunEvent(t=t, record=line))
    return events


def read_advisor_stream(advisor_dir: AdvisorRunDir) -> list[dict] | None:
    """advisor_dir の stream.jsonl (switch2_agent.advisors.stream_json_log.STREAM_FILE_NAME) を読み、行の辞書のリストを返す。

    各要素は stream.jsonl の 1 行 ({"mono", "data"} か {"mono", "raw"}) のまま。jsonl_lines.read_json_lines で読み、
    読み飛ばした行は入れない。ファイルが無いか、読めない (OSError) なら None を返す。
    """
    return _records_of(_read_lines_if_readable(advisor_dir.path / STREAM_FILE_NAME))


def read_result_json(path: Path) -> dict | None:
    """advisor の result.json のファイル path を UTF-8 の JSON として読み、その object (辞書) を返す。

    ファイルが無い、読めない (OSError)、UTF-8 でない (UnicodeDecodeError)、JSON として読めない (ValueError)、
    読んだ値が object でないときは None を返す。advisor_runs.build_advisor_runs の read_result_json に渡す関数。
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    # UnicodeDecodeError は ValueError の派生の型なので、ValueError で一緒に捕まえる
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
