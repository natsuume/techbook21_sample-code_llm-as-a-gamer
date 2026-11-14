"""実行が実行中か (第4章のエージェントが記録を書き足している途中か) を決める純粋な関数。

実行中は、run.jsonl に終了の記録 (event が "record_end" の行) が無く、かつ run.jsonl が最近更新された実行。
record_end は強制終了などで書かれないことがあるため、更新が止まった実行も実行中としない。
時刻は外から受け取り (time.time() と同じ時計の秒)、I/O を持たない。
"""

from collections.abc import Iterable, Mapping

# run.jsonl の更新からこの秒数以内なら実行中とする (秒)
IN_PROGRESS_WINDOW_S = 10.0


def is_in_progress(
    run_log_mtime: float | None, has_record_end: bool, now: float
) -> bool:
    """実行が実行中なら True を返す。

    run_log_mtime は run.jsonl の更新時刻 (os.stat の st_mtime。run.jsonl が無ければ None)、has_record_end は
    run.jsonl に record_end の行があるか、now は今の時刻 (run_log_mtime と同じ時計の秒)。
        - run_log_mtime が None なら False
        - has_record_end が真なら False
        - それ以外は now - run_log_mtime <= IN_PROGRESS_WINDOW_S (ちょうど IN_PROGRESS_WINDOW_S 秒は実行中。
          更新時刻が now より後 (差が負) も実行中)
    """
    raise NotImplementedError


def has_record_end_line(run_lines: Iterable[Mapping]) -> bool:
    """run.jsonl の行 run_lines に、event が "record_end" の行が 1 つでもあれば True を返す。

    event が文字列でない行と、event の無い行は、record_end の行ではない。
    """
    raise NotImplementedError
