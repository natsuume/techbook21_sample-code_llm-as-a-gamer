import cv2
import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.surface_frames import (
    SURFACE_MAX_FRAMES,
    SURFACE_WINDOW_S,
    build_surface_image,
    draw_frame_time,
    format_frame_label,
    select_surface_frames,
)

NOW = 10.0


def gray_nv12(width: int, height: int, value: int = 128) -> np.ndarray:
    """一様な灰色の NV12 のフレーム (Y も UV も value)。"""
    return np.full((height * 3 // 2, width), value, np.uint8)


def frames_at(times: list[float]) -> list[CapturedFrame]:
    return [CapturedFrame(t=t, nv12=gray_nv12(64, 36)) for t in times]


def times_of(frames: list[CapturedFrame]) -> list[float]:
    return [item.t for item in frames]


def test_constants_follow_the_issue():
    assert SURFACE_WINDOW_S == 2.0
    assert SURFACE_MAX_FRAMES == 5


# --- フレームの選び方 ---


def test_no_frames_selects_nothing():
    assert select_surface_frames([], NOW) == []


def test_only_frames_older_than_two_seconds_select_nothing():
    frames = frames_at([5.0, 7.0, 7.999])
    assert select_surface_frames(frames, NOW) == []


def test_single_frame_is_selected():
    frames = frames_at([9.5])
    assert select_surface_frames(frames, NOW) == frames


def test_frame_exactly_two_seconds_old_is_included():
    frames = frames_at([7.9, 8.0, 9.0])
    assert times_of(select_surface_frames(frames, NOW)) == [8.0, 9.0]


def test_frames_newer_than_now_are_excluded():
    frames = frames_at([9.0, 10.0, 10.1])
    assert times_of(select_surface_frames(frames, NOW)) == [9.0, 10.0]


def test_up_to_five_frames_are_all_selected():
    frames = frames_at([8.2, 8.6, 9.0, 9.4, 9.8])
    assert select_surface_frames(frames, NOW) == frames


def test_more_than_five_frames_are_thinned_evenly_including_latest():
    # 0.1 秒ごとのフレーム (21 枚) を、2 秒の範囲から等間隔に 5 枚選ぶ
    times = [round(8.0 + i * 0.1, 3) for i in range(21)]
    selected = select_surface_frames(frames_at(times), NOW)
    assert times_of(selected) == [8.0, 8.5, 9.0, 9.5, 10.0]


def test_more_than_five_frames_with_old_frames_mixed_in():
    times = [6.0, 7.5] + [round(8.0 + i * 0.25, 3) for i in range(9)]
    selected = select_surface_frames(frames_at(times), NOW)
    assert len(selected) == 5
    assert times_of(selected) == [8.0, 8.5, 9.0, 9.5, 10.0]


def test_selection_always_contains_latest_frame_in_range():
    times = [9.0, 9.1, 9.2, 9.3, 9.4, 9.5, 9.97]
    selected = select_surface_frames(frames_at(times), NOW)
    assert len(selected) == 5
    assert selected[-1].t == 9.97
    assert selected[0].t == 9.0
    assert times_of(selected) == sorted(times_of(selected))


def test_max_count_one_selects_latest():
    frames = frames_at([9.0, 9.5, 9.9])
    assert times_of(select_surface_frames(frames, NOW, max_count=1)) == [9.9]


def test_max_count_below_one_is_an_error():
    with pytest.raises(ValueError):
        select_surface_frames(frames_at([9.0]), NOW, max_count=0)


# --- 時刻の書き込み ---


def test_format_frame_label():
    assert format_frame_label(12.3456) == "t=12.346"
    assert format_frame_label(0.0) == "t=0.000"


def test_draw_frame_time_writes_only_in_top_left_and_keeps_original():
    original = np.full((360, 640, 3), 128, np.uint8)
    before = original.copy()
    drawn = draw_frame_time(original, 12.345)
    assert np.array_equal(original, before)
    changed = np.any(drawn != original, axis=2)
    assert changed.any()
    assert changed[:180, :320].any()
    assert not changed[180:, :].any()
    assert not changed[:, 320:].any()


def test_draw_frame_time_differs_by_time():
    image = np.full((360, 640, 3), 128, np.uint8)
    assert not np.array_equal(draw_frame_time(image, 1.0), draw_frame_time(image, 2.0))


def test_build_surface_image_shrinks_and_stamps_time():
    frame = CapturedFrame(t=3.25, nv12=gray_nv12(1280, 720))
    image = build_surface_image(frame, width=640)
    assert (image.width, image.height) == (640, 360)
    decoded = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (360, 640, 3)
    # 時刻の文字 (白) は左上にだけあり、ほかは元の灰色のまま
    assert (decoded[:180, :320] > 200).any()
    assert not (decoded[180:, :] > 200).any()


def test_build_surface_image_does_not_enlarge_narrow_frames():
    frame = CapturedFrame(t=1.0, nv12=gray_nv12(320, 180))
    image = build_surface_image(frame, width=1280)
    assert (image.width, image.height) == (320, 180)


def test_build_surface_image_differs_by_time():
    nv12 = gray_nv12(640, 360)
    first = build_surface_image(CapturedFrame(t=1.0, nv12=nv12))
    second = build_surface_image(CapturedFrame(t=2.0, nv12=nv12))
    assert first.jpeg != second.jpeg
