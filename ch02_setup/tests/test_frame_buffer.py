import pytest

from switch2_remote.frame_buffer import (
    LatestFrame,
    format_latest_age,
    initial_accumulator,
    next_keep,
)


def kept_indexes(ffmpeg_fps: int, process_fps: int, frame_count: int) -> list[int]:
    """初期値から next_keep を frame_count 回呼び、残したフレームの番号 (0 始まり) を返す。"""
    acc = initial_accumulator(ffmpeg_fps, process_fps)
    kept = []
    for index in range(frame_count):
        acc, keep = next_keep(acc, ffmpeg_fps, process_fps)
        if keep:
            kept.append(index)
    return kept


# --- 累積方式の間引き ---


@pytest.mark.parametrize(
    "ffmpeg_fps, process_fps, expected", [(30, 10, 20), (30, 12, 18), (60, 60, 0)]
)
def test_initial_accumulator_is_ffmpeg_fps_minus_process_fps(
    ffmpeg_fps, process_fps, expected
):
    assert initial_accumulator(ffmpeg_fps, process_fps) == expected


def test_next_keep_keeps_frame_when_accumulator_reaches_ffmpeg_fps():
    assert next_keep(20, 30, 10) == (0, True)


def test_next_keep_skips_frame_below_ffmpeg_fps():
    assert next_keep(0, 30, 10) == (10, False)


def test_next_keep_carries_over_remainder():
    # 24 + 12 = 36 は 30 以上なので残し、余りの 6 を次に持ち越す
    assert next_keep(24, 30, 12) == (6, True)


@pytest.mark.parametrize(
    "ffmpeg_fps, process_fps, expected",
    [(30, 10, 10), (30, 12, 12), (60, 60, 60), (30, 1, 1), (60, 7, 7)],
)
def test_next_keep_keeps_exactly_process_fps_per_ffmpeg_fps_frames(
    ffmpeg_fps, process_fps, expected
):
    assert len(kept_indexes(ffmpeg_fps, process_fps, ffmpeg_fps)) == expected


def test_next_keep_keeps_same_count_every_second():
    # 3 秒分 (90 枚) でも 1 秒あたり 12 枚ずつ残す
    kept = kept_indexes(30, 12, 90)
    assert [sum(1 for i in kept if i // 30 == sec) for sec in range(3)] == [12, 12, 12]


def test_next_keep_spreads_kept_frames_evenly():
    assert kept_indexes(30, 10, 30) == [0, 3, 6, 9, 12, 15, 18, 21, 24, 27]


@pytest.mark.parametrize(
    "ffmpeg_fps, process_fps", [(30, 10), (30, 12), (60, 60), (30, 1), (1, 1)]
)
def test_next_keep_keeps_first_frame(ffmpeg_fps, process_fps):
    assert kept_indexes(ffmpeg_fps, process_fps, 1) == [0]


def test_next_keep_keeps_first_frame_after_fps_change():
    # 30→10 で 2 枚進めると、次に残すのは 2 枚先 (累積値 10)
    acc = initial_accumulator(30, 10)
    acc, _ = next_keep(acc, 30, 10)
    acc, _ = next_keep(acc, 30, 10)
    assert acc == 10
    # 処理レートを 12 に変えたら累積値を初期値に戻し、次のフレームを残す
    acc = initial_accumulator(30, 12)
    assert next_keep(acc, 30, 12) == (0, True)


# --- LatestFrame ---


def test_latest_frame_is_empty_at_start():
    latest_frame = LatestFrame()
    assert latest_frame.latest() is None
    assert latest_frame.latest_time() is None


def test_latest_frame_returns_updated_frame_and_time():
    latest_frame = LatestFrame()
    latest_frame.update(1.0, "a")
    assert latest_frame.latest() == "a"
    assert latest_frame.latest_time() == 1.0


def test_latest_frame_replaces_frame_and_time_on_update():
    latest_frame = LatestFrame()
    latest_frame.update(1.0, "a")
    latest_frame.update(1.5, "b")
    assert latest_frame.latest() == "b"
    assert latest_frame.latest_time() == 1.5


def test_latest_frame_keeps_only_newest_frame_after_many_updates():
    # 0.25 秒おきに 20 枚 (0〜4.75 秒) 渡しても、持つのは最後の 1 枚とその時刻だけ
    latest_frame = LatestFrame()
    for i in range(20):
        latest_frame.update(i * 0.25, i)
    assert latest_frame.latest() == 19
    assert latest_frame.latest_time() == 4.75


# --- 最新フレームの経過時間 ---


def test_format_latest_age_is_none_without_frame():
    assert format_latest_age(None, 10.0) == "none"


@pytest.mark.parametrize(
    "latest_time, now, expected",
    [
        (10.0, 10.05, "0.05s"),
        (10.0, 10.0, "0.00s"),
        (3.0, 15.5, "12.50s"),
        (1.0, 1.006, "0.01s"),
    ],
)
def test_format_latest_age_shows_seconds_with_two_decimals(latest_time, now, expected):
    assert format_latest_age(latest_time, now) == expected
