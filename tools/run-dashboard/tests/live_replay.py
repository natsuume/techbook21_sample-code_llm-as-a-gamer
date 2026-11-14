"""テストのための、書き終えた実行の記録を、実行中に書き足されるのと同じ順に 1 行ずつ書き足す補助 (テストではない)。

元の実行のディレクトリー (synthetic_runs が書いたもの) から、書き足す 1 歩ずつ (ReplayStep) を経過秒の順に並べ、
空の実行のディレクトリーへ 1 歩ずつ書く。全部を書き終えた後のファイルは、元と同じバイト列になる。
    replay_steps          書き足す歩の列を作る
    copy_static_files     区間 API が読まない画像 (thumbs/・frames/) を先に写す
    apply_step            1 歩を書く
    shift_decision_times  元の実行の run.jsonl の decision の行の壁時計を、ずらして書き直す
    replayed_record_files replay_steps が書く記録のファイルの一覧 (書き終えた後に元と比べるため)
"""

import heapq
import json
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from switch2_agent.advisors.claude_code_headless import RESULT_FILE_NAME
from switch2_agent.advisors.stream_json_log import STREAM_FILE_NAME
from switch2_agent.run import RUN_LOG_FILE_NAME
from synthetic_runs import format_clock

from run_dashboard.advisor_runs import build_advisor_runs
from run_dashboard.elapsed_time import mono_elapsed, parse_wall_time
from run_dashboard.jsonl_lines import LineChunk, number_or_none, parse_lines
from run_dashboard.run_directory import ADVISOR_FILE_TOP_DIR_PATTERN
from run_dashboard.run_records import RECORD_FILE_NAMES, load_run, read_result_json

# copy_static_files が先に写すディレクトリー (区間 API と live_reader が読まない画像)
STATIC_DIRS = ("thumbs", "frames")

# 経過秒の鍵を持つ行の、鍵を返す関数 (行を JSON の object として読めなければ None を受け取る。鍵が無ければ None)
KeyOf = Callable[[dict | None], float | None]
# 経過秒の鍵と、その鍵の位置に書く歩
KeyedStep = tuple[float, "ReplayStep"]


@dataclass(frozen=True)
class ReplayStep:
    """実行のディレクトリーに書き足す 1 歩。

    relative_path  実行のディレクトリーからの相対パス (/ 区切り。例: run.jsonl、advisor-strategy-001/stream.jsonl)
    data           書くバイト列 (JSON Lines は元のファイルの 1 行と改行。改行で終わらない最後の行は改行なし。
                   それ以外はファイルの中身全体)
    whole_file     True ならファイルを書く (advisor の入力・result.json)、False なら追記する
    """

    relative_path: str
    data: bytes
    whole_file: bool


def replay_steps(source_run_dir: Path) -> list[ReplayStep]:
    """元の実行 source_run_dir の記録を、経過秒の順に 1 歩ずつ書き足す歩の列にして返す。

    4 つの JSON Lines (run.jsonl・requests.jsonl・frames.jsonl・pad.jsonl) と各 advisor のディレクトリーの
    stream.jsonl を、ファイルの中の順を保ったまま heapq.merge で経過秒の鍵の順に混ぜる。元のファイルを \\n で
    分けたバイト列のまま使い、改行で終わらない最後の行は改行なしの最後の 1 歩にする。
    経過秒の鍵:
        run.jsonl         run_records.load_run(元) の RunEvent の t (JSON の object として読めた行ごとに、順に対応させる)
        requests.jsonl    header の行は -inf、要求の行は t_received (数でなければ t_sent)
        frames・pad・stream  mono - origin_mono (load_run(元) の origin_mono)
        JSON として読めない行と、鍵の無い行は、同じファイルの直前の鍵 (ファイルの最初なら -inf)
    同じ鍵の順は result.json → stream → requests → run → frames → pad (merge に渡す順。要求の行を同じ時刻の
    decision の行より先に、result.json を同じ時刻の advisor の行より先に書く)。
    advisor のディレクトリー (名前が run_directory.ADVISOR_FILE_TOP_DIR_PATTERN に fullmatch するディレクトリー。
    名前の順) の入力 (stream.jsonl と result.json 以外のファイル。パスの順) は、その stream.jsonl の最初の行の
    直前に書く。result.json は、その実行の t_end (advisor_runs.build_advisor_runs の値。None か、役割を持たない
    形のディレクトリーなら +inf) の位置に書く。stream.jsonl の無いディレクトリーは、result.json と一緒に入力を書く
    (result.json も無ければ、入力を +inf の位置に書く)。
    元の実行が見つからなければ ValueError を出す。
    """
    records = load_run(source_run_dir.parent, source_run_dir.name)
    if records is None:
        raise ValueError(f"unknown run: {source_run_dir}")
    origin_mono = records.origin_mono
    t_end_by_name = {
        run.name: run.t_end
        for run in build_advisor_runs(
            records.advisor_dirs, records.requests, records.run_events, read_result_json
        )
    }
    run_times = iter([event.t for event in records.run_events])

    def run_key(record: dict | None) -> float | None:
        # JSON の object として読めた行だけが RunEvent になるため、その行ごとに次の t を使う
        return next(run_times) if record is not None else None

    def mono_key(record: dict | None) -> float | None:
        return (
            mono_elapsed(record.get("mono"), origin_mono)
            if record is not None
            else None
        )

    result_groups: list[list[KeyedStep]] = []
    stream_groups: list[list[KeyedStep]] = []
    for advisor_dir in _advisor_dirs(source_run_dir):
        inputs = _input_steps(source_run_dir, advisor_dir)
        stream_path = advisor_dir / STREAM_FILE_NAME
        result_path = advisor_dir / RESULT_FILE_NAME
        if stream_path.is_file():
            stream = _keyed_lines(source_run_dir, stream_path, mono_key)
            first_key = stream[0][0] if stream else math.inf
            stream_groups.append([(first_key, step) for step in inputs] + stream)
            inputs = []
        if result_path.is_file():
            t_end = t_end_by_name.get(advisor_dir.name)
            key = t_end if t_end is not None else math.inf
            result_step = ReplayStep(
                _relative(source_run_dir, result_path), result_path.read_bytes(), True
            )
            result_groups.append([(key, step) for step in [*inputs, result_step]])
        elif inputs:
            result_groups.append([(math.inf, step) for step in inputs])

    file_groups: list[list[KeyedStep]] = []
    key_of_kind: dict[str, KeyOf] = {
        "requests": _request_key,
        "run": run_key,
        "frames": mono_key,
        "pad": mono_key,
    }
    for kind in ("requests", "run", "frames", "pad"):
        path = source_run_dir / RECORD_FILE_NAMES[kind]
        if path.is_file():
            file_groups.append(_keyed_lines(source_run_dir, path, key_of_kind[kind]))

    merged = heapq.merge(
        *result_groups, *stream_groups, *file_groups, key=lambda pair: pair[0]
    )
    return [step for _, step in merged]


def _advisor_dirs(run_dir: Path) -> list[Path]:
    """run_dir の直下の advisor のディレクトリー (役割を持たない形を含む) を名前の順で返す。"""
    return sorted(
        (
            entry
            for entry in run_dir.iterdir()
            if entry.is_dir() and ADVISOR_FILE_TOP_DIR_PATTERN.fullmatch(entry.name)
        ),
        key=lambda entry: entry.name,
    )


def _relative(run_dir: Path, path: Path) -> str:
    """path の run_dir からの相対パス (/ 区切り) を返す。"""
    return path.relative_to(run_dir).as_posix()


def _input_steps(run_dir: Path, advisor_dir: Path) -> list[ReplayStep]:
    """advisor_dir の入力のファイル (stream.jsonl と result.json 以外。パスの順) を、ファイルを書く歩にして返す。"""
    excluded = {advisor_dir / STREAM_FILE_NAME, advisor_dir / RESULT_FILE_NAME}
    return [
        ReplayStep(_relative(run_dir, path), path.read_bytes(), True)
        for path in sorted(advisor_dir.rglob("*"))
        if path.is_file() and path not in excluded
    ]


def _parse_record(line: bytes) -> dict | None:
    """\\n を除いた 1 行 line を、サーバーと同じ規則 (jsonl_lines.parse_lines) で読んだ object を返す。

    読めない行・object でない行・空白だけの行は None。
    """
    records = parse_lines(LineChunk(lines=[line.removesuffix(b"\r")], next_offset=0))
    return records.records[0] if records.records else None


def _request_key(record: dict | None) -> float | None:
    """requests.jsonl の行の鍵 (header は -inf、要求の行は t_received、数でなければ t_sent) を返す。"""
    if record is None:
        return None
    if record.get("event") == "header":
        return -math.inf
    if record.get("event") != "request":
        return None
    t_received = number_or_none(record.get("t_received"))
    return (
        t_received if t_received is not None else number_or_none(record.get("t_sent"))
    )


def _keyed_lines(run_dir: Path, path: Path, key_of: KeyOf) -> list[KeyedStep]:
    """JSON Lines のファイル path の各行を、経過秒の鍵と、追記する歩の組にしてファイルの順に返す。

    鍵が None の行 (読めない行・鍵の無い行) は直前の行の鍵 (最初なら -inf) にする。改行で終わらない最後の行は、
    改行なしの歩にし、JSON として読まずに直前の行の鍵にする (サーバーも読まない行のため)。
    """
    relative_path = _relative(run_dir, path)
    pieces = path.read_bytes().split(b"\n")
    unterminated = pieces.pop()
    keyed: list[KeyedStep] = []
    previous_key = -math.inf
    for piece in pieces:
        key = key_of(_parse_record(piece))
        if key is not None:
            previous_key = key
        keyed.append((previous_key, ReplayStep(relative_path, piece + b"\n", False)))
    if unterminated:
        keyed.append((previous_key, ReplayStep(relative_path, unterminated, False)))
    return keyed


def copy_static_files(source_run_dir: Path, target_run_dir: Path) -> None:
    """source_run_dir の画像のディレクトリー (STATIC_DIRS) を target_run_dir に写す (無いものは写さない)。

    target_run_dir が無ければ作る。
    """
    target_run_dir.mkdir(parents=True, exist_ok=True)
    for name in STATIC_DIRS:
        source = source_run_dir / name
        if source.is_dir():
            shutil.copytree(source, target_run_dir / name, dirs_exist_ok=True)


def apply_step(target_run_dir: Path, step: ReplayStep) -> None:
    """step を target_run_dir に書く (親のディレクトリーを作ってから、whole_file ならファイルを書き、そうでなければ追記)。"""
    path = target_run_dir / step.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if step.whole_file:
        path.write_bytes(step.data)
        return
    with path.open("ab") as file:
        file.write(step.data)


def shift_decision_times(run_dir: Path, seconds: float) -> None:
    """run_dir の run.jsonl の decision の行の time を seconds 秒ずらして書き直す (ほかの行と行の順は変えない)。

    壁時計が monotonic の時計からずれた実行を作る。time が読めない decision の行は変えない。書き直した行は
    json.dumps(ensure_ascii=False) の 1 行にし、ほかの行は元のバイト列のままにする。
    """
    path = run_dir / RUN_LOG_FILE_NAME
    lines = []
    for piece in path.read_bytes().split(b"\n"):
        record = _parse_record(piece)
        wall = parse_wall_time(record.get("time")) if record is not None else None
        if (
            record is not None
            and record.get("event") == "decision"
            and wall is not None
        ):
            piece = json.dumps(
                {**record, "time": format_clock(wall + seconds)}, ensure_ascii=False
            ).encode("utf-8")
        lines.append(piece)
    path.write_bytes(b"\n".join(lines))


def replayed_record_files(run_dir: Path) -> list[str]:
    """replay_steps が書く記録のファイル (4 つの JSON Lines と advisor のディレクトリーの下のファイル) の、
    run_dir からの相対パスを並べて返す (書き終えた後に元と同じかを比べるため)。
    """
    names = [
        RECORD_FILE_NAMES[kind]
        for kind in ("run", "requests", "frames", "pad")
        if (run_dir / RECORD_FILE_NAMES[kind]).is_file()
    ]
    for advisor_dir in _advisor_dirs(run_dir):
        names.extend(
            _relative(run_dir, path)
            for path in sorted(advisor_dir.rglob("*"))
            if path.is_file()
        )
    return names
