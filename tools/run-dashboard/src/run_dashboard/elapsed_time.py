"""記録の時刻を、経過秒 (第4章の Pipeline の起点 started_at からの秒) にする純粋な関数。

記録の時刻には 2 つの形がある:
    - run.jsonl の行の time: 壁時計の時刻 `HH:MM:SS.mmm` (switch2_agent.run_log.format_wall_time の形)。日付を持たない。
      起点の行 (start の行か、時刻の分かる decision の行。run_records.load_run) の time との差を、起点の経過秒に
      足して経過秒にする (start の time と Pipeline の起点には数ミリ秒のずれがあるが、そのまま使う)
    - frames.jsonl・pad.jsonl・stream.jsonl の mono: time.monotonic() の秒。run.jsonl の start の origin_mono
      (Pipeline.started_at) を引くと経過秒になる
I/O を持たない。
"""

import re

from run_dashboard.jsonl_lines import is_json_number

SECONDS_PER_DAY = 86400.0
# 壁時計の時刻 HH:MM:SS.mmm (\d は全角などの数字にも一致するため、[0-9] で書く)
WALL_TIME_PATTERN = re.compile(r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})")
# 起点の行 (start の行など) の時刻との前後のずれとして、日付をまたいだとみなさずにそのまま返す差の上限 (秒)。
# 第4章の RunLog.write は時刻をロックの外で取るため、start と同時に別のスレッドが書いた行は、ファイルの順と
# 時刻の順が数ミリ秒食い違うことがある。その食い違いを十分に含む幅にする
DAY_WRAP_TOLERANCE_S = 60.0


def parse_wall_time(value: object) -> float | None:
    """壁時計の時刻 `HH:MM:SS.mmm` (時・分・秒は 2 桁、ミリ秒は 3 桁) を、0 時からの秒にして返す。

    例: "23:59:50.250" は 86390.25、"00:00:01.500" は 1.5。
    value が文字列でない、この形に fullmatch しない、範囲の外 (時は 0〜23、分と秒は 0〜59) なら None を返す。
    """
    if not isinstance(value, str):
        return None
    match = WALL_TIME_PATTERN.fullmatch(value)
    if match is None:
        return None
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def run_log_elapsed(line_time: float, start_time: float, after_start: bool) -> float:
    """run.jsonl の 1 行の経過秒を返す。

    line_time はその行の time、start_time は起点の行の time (どちらも parse_wall_time の秒)。起点の行は、start の
    行か、run_records.load_run が時刻の起点にする decision の行 (同じ number の要求の行が requests.jsonl にある行)。
    差 line_time - start_time を、日付をまたいだかどうかで直して返す:
        - after_start が真 (その行が起点の行より後にある): 差を [-DAY_WRAP_TOLERANCE_S,
          SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S] に収める。差が -DAY_WRAP_TOLERANCE_S より小さければ、
          日付をまたいだとみなして SECONDS_PER_DAY を足す。差が SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S より
          大きければ (起点の行が 0 時の直前で、その行の時刻がわずかに前の 23 時台)、SECONDS_PER_DAY を引く。
          -DAY_WRAP_TOLERANCE_S 以上の負の差 (start と同時に別のスレッドが書いた行の数ミリ秒の負など) は、
          負のまま返す
        - after_start が偽 (start の行より前の行。起動直後の record_error など): 差を
          [-(SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S), +DAY_WRAP_TOLERANCE_S] に収める。差が +DAY_WRAP_TOLERANCE_S
          より大きければ、日付をまたいだ (start が 0 時の直後で、その行が前日の 23 時台) とみなして
          SECONDS_PER_DAY を引く。差が -(SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S) より小さければ (start が 0 時の
          直前で、その行の時刻がわずかに後の 0 時台)、SECONDS_PER_DAY を足す
    起点の行から 24 時間 - DAY_WRAP_TOLERANCE_S より離れた行は、正しく直せない。
    """
    difference = line_time - start_time
    if after_start:
        if difference < -DAY_WRAP_TOLERANCE_S:
            return difference + SECONDS_PER_DAY
        if difference > SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S:
            return difference - SECONDS_PER_DAY
        return difference
    if difference > DAY_WRAP_TOLERANCE_S:
        return difference - SECONDS_PER_DAY
    if difference < -(SECONDS_PER_DAY - DAY_WRAP_TOLERANCE_S):
        return difference + SECONDS_PER_DAY
    return difference


def mono_elapsed(mono: object, origin_mono: float | None) -> float | None:
    """mono の時刻 (time.monotonic の秒) の経過秒 mono - origin_mono を返す。

    mono が数 (int か float。bool は除く) で、origin_mono が None でなければ mono - origin_mono。
    それ以外は None を返す。
    """
    if not is_json_number(mono) or origin_mono is None:
        return None
    return float(mono - origin_mono)
