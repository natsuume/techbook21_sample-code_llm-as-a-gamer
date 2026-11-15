import pytest

from run_dashboard.run_activity import (
    IN_PROGRESS_WINDOW_S,
    has_record_end_line,
    is_in_progress,
)

# run.jsonl の更新時刻 (整数の秒にして、差の計算に丸めの誤差が出ないようにする)
MTIME = 1_763_000_000.0


def test_in_progress_window_is_ten_seconds():
    assert IN_PROGRESS_WINDOW_S == 10.0


def test_run_without_run_log_is_not_in_progress():
    # run.jsonl が無い (更新時刻 None) 実行は、今の時刻によらず実行中でない
    assert is_in_progress(None, False, MTIME) is False


@pytest.mark.parametrize("now", [MTIME, MTIME + 5.0, MTIME - 5.0])
def test_run_with_record_end_is_not_in_progress(now):
    # record_end があれば、更新時刻との差が 0 でも実行中でない
    assert is_in_progress(MTIME, True, now) is False


@pytest.mark.parametrize(
    "now, expected",
    [
        # 差 0
        (MTIME, True),
        # 差ちょうど 10.0 秒は実行中
        (MTIME + 10.0, True),
        # 10 秒を少しでも超えたら実行中でない
        (MTIME + 10.000001, False),
        (MTIME + 60.0, False),
        # 更新時刻が今より後 (時計のずれ。差 -5) も実行中
        (MTIME - 5.0, True),
    ],
)
def test_in_progress_depends_on_seconds_since_update(now, expected):
    assert is_in_progress(MTIME, False, now) is expected


def test_has_record_end_line():
    assert has_record_end_line([]) is False
    assert (
        has_record_end_line(
            [{"event": "start"}, {"event": "record_end"}, {"event": "interrupted"}]
        )
        is True
    )
    assert has_record_end_line([{"event": "start"}, {"event": "decision"}]) is False
    # event が文字列でない行と、event の無い行は record_end の行ではない
    assert (
        has_record_end_line(
            [{"event": ["record_end"]}, {"event": None}, {"kind": "record_end"}]
        )
        is False
    )


def test_has_record_end_line_reads_iterable_once():
    # 行はジェネレーターでも渡せる
    lines = (line for line in [{"event": "start"}, {"event": "record_end"}])
    assert has_record_end_line(lines) is True
