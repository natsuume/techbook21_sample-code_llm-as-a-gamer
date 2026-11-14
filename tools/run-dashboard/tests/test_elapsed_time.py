import pytest

from run_dashboard.elapsed_time import (
    DAY_WRAP_TOLERANCE_S,
    SECONDS_PER_DAY,
    mono_elapsed,
    parse_wall_time,
    run_log_elapsed,
)


def test_seconds_per_day_and_day_wrap_tolerance():
    assert SECONDS_PER_DAY == 86400.0
    assert DAY_WRAP_TOLERANCE_S == 60.0


# --- parse_wall_time ---


@pytest.mark.parametrize(
    "value, expected",
    [
        ("00:00:00.000", 0.0),
        ("00:00:01.500", 1.5),
        ("23:59:50.250", 86390.25),
        ("12:34:56.789", 45296.789),
        ("23:59:59.999", 86399.999),
    ],
)
def test_parse_wall_time_reads_seconds_from_midnight(value, expected):
    assert parse_wall_time(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        # 文字列でない
        None,
        12.5,
        True,
        b"00:00:00.000",
        # 形が違う
        "",
        "0:00:00.000",
        "00:00:00",
        "00:00:00.00",
        "00:00:00.0000",
        "00-00-00.000",
        " 00:00:00.000",
        "00:00:00.000 ",
        "aa:bb:cc.ddd",
        # 範囲の外
        "24:00:00.000",
        "23:60:00.000",
        "23:59:60.000",
    ],
)
def test_parse_wall_time_rejects_other_values(value):
    assert parse_wall_time(value) is None


# --- run_log_elapsed ---


def test_run_log_elapsed_after_start_is_difference():
    assert run_log_elapsed(86395.25, 86390.0, after_start=True) == pytest.approx(5.25)


def test_run_log_elapsed_of_start_line_itself_is_zero():
    assert run_log_elapsed(86390.0, 86390.0, after_start=True) == 0.0


def test_run_log_elapsed_after_start_adds_a_day_when_below_minus_tolerance():
    # start が 23:59:50.000、その後の行が 00:00:02.750 (日付をまたいだ。差は -86387.25 秒)
    assert run_log_elapsed(2.75, 86390.0, after_start=True) == pytest.approx(12.75)
    # 差が -60 秒を 1 秒下回る
    assert run_log_elapsed(86329.0, 86390.0, after_start=True) == pytest.approx(86339.0)


def test_run_log_elapsed_after_start_adds_a_day_in_run_longer_than_twelve_hours():
    # start が 23:00:00.000、日付をまたいで 12.5 時間後の 11:30:00.000 に書いた行 (差は -41400 秒)
    assert run_log_elapsed(41400.0, 82800.0, after_start=True) == pytest.approx(45000.0)


def test_run_log_elapsed_after_start_keeps_small_negative_difference():
    # start と同時に別のスレッドが書いた行は、start の行より後でも時刻がわずかに前のことがある
    assert run_log_elapsed(86389.998, 86390.0, after_start=True) == pytest.approx(
        -0.002
    )


def test_run_log_elapsed_after_start_keeps_difference_of_exactly_minus_tolerance():
    assert run_log_elapsed(86330.0, 86390.0, after_start=True) == pytest.approx(-60.0)


def test_run_log_elapsed_before_start_keeps_negative_difference():
    # start の行より前の行 (起動直後の record_error など) は負の秒のまま
    assert run_log_elapsed(86389.5, 86390.0, after_start=False) == pytest.approx(-0.5)


def test_run_log_elapsed_before_start_adds_a_day_when_below_minus_day_plus_tolerance():
    # 差 -86387.25 秒は -(24 時間 - 60 秒) より小さいので、日付をまたいだとみなして 24 時間を足す
    assert run_log_elapsed(2.75, 86390.0, after_start=False) == pytest.approx(12.75)


def test_run_log_elapsed_after_start_subtracts_a_day_when_above_day_minus_tolerance():
    # 起点が 00:00:00.001、その後の行が前日の 23:59:59.999 (差は +86399.998 秒)
    assert run_log_elapsed(86399.999, 0.001, after_start=True) == pytest.approx(-0.002)


def test_run_log_elapsed_before_start_adds_a_day_when_line_is_just_after_midnight():
    # start が 23:59:59.999、その前の行が翌日の 00:00:00.001 (差は -86399.998 秒)
    assert run_log_elapsed(0.001, 86399.999, after_start=False) == pytest.approx(0.002)


def test_run_log_elapsed_before_start_keeps_small_positive_difference():
    assert run_log_elapsed(86391.0, 86390.0, after_start=False) == pytest.approx(1.0)
    assert run_log_elapsed(86450.0, 86390.0, after_start=False) == pytest.approx(60.0)


def test_run_log_elapsed_before_start_subtracts_a_day_when_above_tolerance():
    # start が 00:00:01.000、その前の行が前日の 23:59:59.900 (差は +86398.9 秒)
    assert run_log_elapsed(86399.9, 1.0, after_start=False) == pytest.approx(-1.1)


# --- mono_elapsed ---


@pytest.mark.parametrize(
    "mono, origin_mono, expected",
    [
        (1001.25, 1000.0, 1.25),
        (1001, 1000.0, 1.0),
        (999.5, 1000.0, -0.5),
    ],
)
def test_mono_elapsed_subtracts_origin(mono, origin_mono, expected):
    assert mono_elapsed(mono, origin_mono) == pytest.approx(expected)


@pytest.mark.parametrize(
    "mono, origin_mono",
    [
        (1001.25, None),
        (None, 1000.0),
        ("1001.25", 1000.0),
        (True, 1000.0),
    ],
)
def test_mono_elapsed_is_none_without_number_or_origin(mono, origin_mono):
    assert mono_elapsed(mono, origin_mono) is None
