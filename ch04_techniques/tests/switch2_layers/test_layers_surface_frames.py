import cv2
import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import nv12_to_bgr, shrink_to_width
from switch2_layers.frames.surface_frames import (
    BOX_COLORS,
    SURFACE_MAX_FRAMES,
    SURFACE_WINDOW_S,
    BoxDrawingPlan,
    build_surface_image,
    draw_boxes,
    draw_frame_time,
    drawn_boxes_key,
    format_frame_label,
    select_boxes_to_draw,
    select_surface_frames,
)
from switch2_layers.surface.surface_types import Box, BoxSource

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


# --- box の書き込み ---

# 右下の 4 分の 1 にある box (時刻の文字と重ならない)。画像 640x360 では (384, 216)〜(512, 288)
BOX = Box("自機", 0.6, 0.6, 0.8, 0.8, 0.9, BoxSource.TRACKER)


def test_draw_boxes_draws_frame_and_name_and_keeps_original():
    original = np.full((360, 640, 3), 128, np.uint8)
    before = original.copy()
    drawn = draw_boxes(original, [BOX])
    assert np.array_equal(original, before)
    changed = np.any(drawn != original, axis=2)
    # 枠の 4 辺
    assert changed[216:289, 384].any()
    assert changed[216:289, 512].any()
    assert changed[216, 384:513].any()
    assert changed[288, 384:513].any()
    # 名前は枠の左上の上側
    assert changed[190:216, 384:450].any()
    # 枠の中央と、枠から離れた場所は変えない
    assert not changed[240:264, 430:470].any()
    assert not changed[:150, :].any()
    assert not changed[:, :350].any()


def test_draw_boxes_uses_box_colors_in_order():
    image = np.full((360, 640, 3), 128, np.uint8)
    first = Box("a", 0.1, 0.6, 0.2, 0.8, 0.9, BoxSource.TRACKER)
    second = Box("b", 0.6, 0.6, 0.8, 0.8, None, BoxSource.JEV)
    drawn = draw_boxes(image, [first, second])
    # 左の枠の左辺 (x = 64) は 1 つ目の色、右の枠の左辺 (x = 384) は 2 つ目の色
    assert tuple(int(v) for v in drawn[250, 64]) == BOX_COLORS[0]
    assert tuple(int(v) for v in drawn[250, 384]) == BOX_COLORS[1]


def test_draw_boxes_writes_name_inside_when_box_touches_top():
    image = np.full((360, 640, 3), 128, np.uint8)
    top_box = Box("敵", 0.6, 0.0, 0.8, 0.3, 0.9, BoxSource.TRACKER)
    changed = np.any(draw_boxes(image, [top_box]) != image, axis=2)
    # 枠 (x = 384〜512、y = 0〜108) の内側の左上に名前がある
    assert changed[4:40, 390:460].any()


def test_draw_boxes_without_boxes_returns_a_copy():
    image = np.full((360, 640, 3), 128, np.uint8)
    drawn = draw_boxes(image, [])
    assert np.array_equal(drawn, image)
    assert drawn is not image


def test_build_surface_image_draws_boxes_and_time():
    frame = CapturedFrame(t=3.25, nv12=gray_nv12(1280, 720))
    plain = build_surface_image(frame, width=640)
    with_box = build_surface_image(frame, width=640, boxes=[BOX])
    assert plain.jpeg != with_box.jpeg
    decoded = cv2.imdecode(np.frombuffer(with_box.jpeg, np.uint8), cv2.IMREAD_COLOR)
    shrunk = shrink_to_width(nv12_to_bgr(frame.nv12), 640)
    expected_bgr = draw_frame_time(draw_boxes(shrunk, [BOX]), 3.25)
    # JPEG の誤差の範囲で、box を描いてから時刻を書いた画像と同じ
    difference = np.abs(decoded.astype(int) - expected_bgr.astype(int))
    assert float(difference.mean()) < 2.0


def test_draw_boxes_colors_follow_numbers_in_target_list():
    image = np.full((360, 640, 3), 128, np.uint8)
    first = Box("a", 0.1, 0.6, 0.2, 0.8, 0.9, BoxSource.TRACKER)
    second = Box("b", 0.6, 0.6, 0.8, 0.8, None, BoxSource.JEV)
    # 一覧の番号で色を決める (並びの位置ではない)。5 番は先頭の色に戻る
    drawn = draw_boxes(image, [first, second], {"a": 3, "b": 5})
    assert tuple(int(v) for v in drawn[250, 64]) == BOX_COLORS[2]
    assert tuple(int(v) for v in drawn[250, 384]) == BOX_COLORS[0]


def test_same_target_keeps_its_color_when_another_target_is_missing():
    image = np.full((360, 640, 3), 128, np.uint8)
    second = Box("b", 0.6, 0.6, 0.8, 0.8, None, BoxSource.JEV)
    # 1 番の対象を見失っていても、2 番の対象は 2 番の色
    drawn = draw_boxes(image, [second], {"a": 1, "b": 2})
    assert tuple(int(v) for v in drawn[250, 384]) == BOX_COLORS[1]


def test_draw_boxes_skips_names_without_numbers():
    image = np.full((360, 640, 3), 128, np.uint8)
    drawn = draw_boxes(image, [BOX], {"敵": 1})
    assert np.array_equal(drawn, image)


def test_build_surface_image_passes_target_numbers():
    frame = CapturedFrame(t=3.25, nv12=gray_nv12(1280, 720))
    first = build_surface_image(
        frame, width=640, boxes=[BOX], target_numbers={"自機": 1}
    )
    second = build_surface_image(
        frame, width=640, boxes=[BOX], target_numbers={"自機": 2}
    )
    decoded = cv2.imdecode(np.frombuffer(second.jpeg, np.uint8), cv2.IMREAD_COLOR)
    shrunk = shrink_to_width(nv12_to_bgr(frame.nv12), 640)
    expected_bgr = draw_frame_time(draw_boxes(shrunk, [BOX], {"自機": 2}), 3.25)
    difference = np.abs(decoded.astype(int) - expected_bgr.astype(int))
    assert float(difference.mean()) < 2.0
    assert first.jpeg != second.jpeg


# --- 書き込む box の選び方と、保存の重複を除くキー ---

PLAYER = Box("自機", 0.1, 0.1, 0.2, 0.2, 0.9, BoxSource.TRACKER)
ENEMY = Box("敵", 0.5, 0.5, 0.6, 0.6, 0.8, BoxSource.TRACKER)
DOOR = Box("扉", 0.7, 0.1, 0.8, 0.3, 0.7, BoxSource.TRACKER)


def test_select_boxes_without_plan_keeps_all():
    assert select_boxes_to_draw([PLAYER, ENEMY], None, is_latest=True) == (
        PLAYER,
        ENEMY,
    )


def test_requeried_target_is_hidden_only_on_latest_frame():
    plan = BoxDrawingPlan(
        target_numbers={"自機": 1, "敵": 2}, hidden_on_latest=frozenset({"自機"})
    )
    assert select_boxes_to_draw([PLAYER, ENEMY], plan, is_latest=True) == (ENEMY,)
    assert select_boxes_to_draw([PLAYER, ENEMY], plan, is_latest=False) == (
        PLAYER,
        ENEMY,
    )


def test_boxes_of_targets_outside_the_list_are_not_drawn():
    plan = BoxDrawingPlan(target_numbers={"自機": 1, "敵": 2})
    assert select_boxes_to_draw([PLAYER, DOOR, ENEMY], plan, is_latest=False) == (
        PLAYER,
        ENEMY,
    )


def test_drawn_boxes_key_is_names_and_coordinates():
    assert drawn_boxes_key([]) == ()
    assert drawn_boxes_key([PLAYER, ENEMY]) == (
        ("自機", 0.1, 0.1, 0.2, 0.2),
        ("敵", 0.5, 0.5, 0.6, 0.6),
    )
    # 確信度と出どころは画像に書かないので、キーに入れない
    same_place = Box("自機", 0.1, 0.1, 0.2, 0.2, None, BoxSource.JEV)
    assert drawn_boxes_key([same_place]) == drawn_boxes_key([PLAYER])
