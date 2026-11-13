import pytest

from run_dashboard.elapsed_time import (
    DAY_WRAP_THRESHOLD_S,
    SECONDS_PER_DAY,
    mono_elapsed,
    parse_wall_time,
    run_log_elapsed,
)


def test_seconds_per_day_and_day_wrap_threshold():
    assert SECONDS_PER_DAY == 86400.0
    assert DAY_WRAP_THRESHOLD_S == 43200.0


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


def test_run_log_elapsed_after_start_adds_a_day_when_below_minus_twelve_hours():
    # start が 23:59:50.000、その後の行が 00:00:02.750 (日付をまたいだ。差は -86387.25 秒)
    assert run_log_elapsed(2.75, 86390.0, after_start=True) == pytest.approx(12.75)
    # 差が -12 時間を 1 秒下回る
    assert run_log_elapsed(43189.0, 86390.0, after_start=True) == pytest.approx(43199.0)


def test_run_log_elapsed_after_start_keeps_small_negative_difference():
    # start と同時に別のスレッドが書いた行は、start の行より後でも時刻がわずかに前のことがある
    assert run_log_elapsed(86389.998, 86390.0, after_start=True) == pytest.approx(
        -0.002
    )


def test_run_log_elapsed_after_start_keeps_difference_of_exactly_minus_twelve_hours():
    assert run_log_elapsed(43190.0, 86390.0, after_start=True) == pytest.approx(
        -43200.0
    )


def test_run_log_elapsed_before_start_keeps_negative_difference():
    # start の行より前の行 (起動直後の record_error など) は負の秒のまま
    assert run_log_elapsed(86389.5, 86390.0, after_start=False) == pytest.approx(-0.5)


def test_run_log_elapsed_before_start_keeps_difference_below_minus_twelve_hours():
    assert run_log_elapsed(2.75, 86390.0, after_start=False) == pytest.approx(-86387.25)


def test_run_log_elapsed_before_start_keeps_positive_difference():
    assert run_log_elapsed(86391.0, 86390.0, after_start=False) == pytest.approx(1.0)


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
