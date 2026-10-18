import pytest

from switch2_remote.frame_buffer import (
    FrameBuffer,
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


# --- FrameBuffer ---


def test_frame_buffer_is_empty_at_start():
    buffer = FrameBuffer(5)
    assert len(buffer) == 0
    assert buffer.latest() is None


def test_frame_buffer_latest_returns_last_appended_frame():
    buffer = FrameBuffer(5)
    buffer.append(0.0, "a")
    buffer.append(0.1, "b")
    assert buffer.latest() == "b"
    assert len(buffer) == 2


def test_frame_buffer_drops_frames_older_than_seconds():
    buffer = FrameBuffer(5)
    buffer.append(0.0, "a")
    buffer.append(3.0, "b")
    buffer.append(6.0, "c")
    # 6.0 - 5 = 1.0 より古い "a" だけを捨てる
    assert len(buffer) == 2
    assert buffer.latest() == "c"


def test_frame_buffer_keeps_frame_exactly_seconds_old():
    buffer = FrameBuffer(5)
    buffer.append(0.0, "a")
    buffer.append(5.0, "b")
    assert len(buffer) == 2
    buffer.append(5.5, "c")
    assert len(buffer) == 2


def test_frame_buffer_keeps_newest_frame_after_long_gap():
    buffer = FrameBuffer(5)
    buffer.append(0.0, "a")
    buffer.append(0.1, "b")
    buffer.append(100.0, "c")
    assert len(buffer) == 1
    assert buffer.latest() == "c"


def test_frame_buffer_holds_frames_for_seconds_regardless_of_rate():
    # 1 秒保持で 0.25 秒おきに 20 枚 (0〜4.75 秒) 追加すると、3.75〜4.75 秒の 5 枚が残る
    buffer = FrameBuffer(1)
    for i in range(20):
        buffer.append(i * 0.25, i)
    assert len(buffer) == 5
    assert buffer.latest() == 19


def test_frame_buffer_latest_time_is_none_when_empty():
    assert FrameBuffer(5).latest_time() is None


def test_frame_buffer_latest_time_returns_time_of_last_appended_frame():
    buffer = FrameBuffer(5)
    buffer.append(1.0, "a")
    buffer.append(1.5, "b")
    assert buffer.latest_time() == 1.5


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
