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

from dataclasses import dataclass
from pathlib import Path

from switch2_agent.dashboard_records import (
    FRAMES_RECORD_FILE_NAME,
    PAD_RECORD_FILE_NAME,
)
from switch2_agent.request_log import REQUEST_LOG_FILE_NAME
from switch2_agent.run import RUN_LOG_FILE_NAME

from run_dashboard.run_directory import AdvisorRunDir

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
    raise NotImplementedError


def read_advisor_stream(advisor_dir: AdvisorRunDir) -> list[dict] | None:
    """advisor_dir の stream.jsonl (switch2_agent.advisors.stream_json_log.STREAM_FILE_NAME) を読み、行の辞書のリストを返す。

    各要素は stream.jsonl の 1 行 ({"mono", "data"} か {"mono", "raw"}) のまま。jsonl_lines.read_json_lines で読み、
    読み飛ばした行は入れない。ファイルが無いか、読めない (OSError) なら None を返す。
    """
    raise NotImplementedError


def read_result_json(path: Path) -> dict | None:
    """advisor の result.json のファイル path を UTF-8 の JSON として読み、その object (辞書) を返す。

    ファイルが無い、読めない (OSError)、UTF-8 でない (UnicodeDecodeError)、JSON として読めない (ValueError)、
    読んだ値が object でないときは None を返す。advisor_runs.build_advisor_runs の read_result_json に渡す関数。
    """
    raise NotImplementedError
