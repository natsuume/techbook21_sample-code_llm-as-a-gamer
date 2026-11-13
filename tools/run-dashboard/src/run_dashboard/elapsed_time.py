"""記録の時刻を、経過秒 (第4章の Pipeline の起点 started_at からの秒) にする純粋な関数。

記録の時刻には 2 つの形がある:
    - run.jsonl の行の time: 壁時計の時刻 `HH:MM:SS.mmm` (switch2_agent.run_log.format_wall_time の形)。日付を持たない。
      start の行の time との差を経過秒にする (start の time と Pipeline の起点には数ミリ秒のずれがあるが、そのまま使う)
    - frames.jsonl・pad.jsonl・stream.jsonl の mono: time.monotonic() の秒。run.jsonl の start の origin_mono
      (Pipeline.started_at) を引くと経過秒になる
I/O を持たない。
"""

SECONDS_PER_DAY = 86400.0


def parse_wall_time(value: object) -> float | None:
    """壁時計の時刻 `HH:MM:SS.mmm` (時・分・秒は 2 桁、ミリ秒は 3 桁) を、0 時からの秒にして返す。

    例: "23:59:50.250" は 86390.25、"00:00:01.500" は 1.5。
    value が文字列でない、この形に fullmatch しない、範囲の外 (時は 0〜23、分と秒は 0〜59) なら None を返す。
    """
    raise NotImplementedError


def run_log_elapsed(line_time: float, start_time: float, after_start: bool) -> float:
    """run.jsonl の 1 行の経過秒を返す。

    line_time はその行の time、start_time は start の行の time (どちらも parse_wall_time の秒)。
    差 line_time - start_time を返す。after_start が真 (その行が start の行より後にある) で差が負なら、
    日付をまたいだとみなして SECONDS_PER_DAY を足す。after_start が偽 (start の行より前の行。起動直後の
    record_error など) なら、差が負でもそのまま返す。
    """
    raise NotImplementedError


def mono_elapsed(mono: object, origin_mono: float | None) -> float | None:
    """mono の時刻 (time.monotonic の秒) の経過秒 mono - origin_mono を返す。

    mono が数 (int か float。bool は除く) で、origin_mono が None でなければ mono - origin_mono。
    それ以外は None を返す。
    """
    raise NotImplementedError
