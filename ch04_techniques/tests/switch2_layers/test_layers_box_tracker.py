"""NCC の追跡を、合成の画像 (乱数の種を固定した背景と、模様のある四角い対象) で確かめる。

対象の模様は 8 ピクセル四方の白黒のブロックで、背景はなだらかな明るさの変化に弱いノイズを足したもの
(一様な面は分散が 0 で、正規化相互相関が決まらないため、どちらにも模様を持たせる)。
一様な対象の扱いだけは、一様な背景に一様な四角を置いた画像 (plain_captured) で確かめる。
"""

import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.surface.box_tracker import (
    DEFAULT_LOST_THRESHOLD,
    DEFAULT_RECENT_THRESHOLD,
    DEFAULT_SEARCH_MARGIN,
    TRACKING_SCALE,
    NccBoxTracker,
    NormalizedBox,
    TrackingFrame,
    tracking_frame_from_captured,
)

FULL_WIDTH = 1280
FULL_HEIGHT = 720
SPRITE_SIZE = 48  # 対象の一辺 (フル解像度のピクセル)
SPRITE_Y = 300
STEP = 8  # 1 フレームあたりの移動 (フル解像度のピクセル)


def background() -> np.ndarray:
    rng = np.random.default_rng(1)
    base = np.linspace(60, 140, FULL_WIDTH, dtype=np.float32)[None, :]
    base = base.repeat(FULL_HEIGHT, axis=0)
    noise = rng.normal(0, 6, (FULL_HEIGHT, FULL_WIDTH)).astype(np.float32)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def sprite() -> np.ndarray:
    rng = np.random.default_rng(2)
    blocks = rng.integers(0, 2, (SPRITE_SIZE // 8, SPRITE_SIZE // 8)) * 255
    return np.kron(blocks, np.ones((8, 8))).astype(np.uint8)


def captured(t: float, x: int | None) -> CapturedFrame:
    """左上 (x, SPRITE_Y) に対象を置いたフレーム。x が None なら対象を置かない。UV 面は 128。"""
    y_plane = background()
    if x is not None:
        y_plane[SPRITE_Y : SPRITE_Y + SPRITE_SIZE, x : x + SPRITE_SIZE] = sprite()
    uv_plane = np.full((FULL_HEIGHT // 2, FULL_WIDTH), 128, np.uint8)
    return CapturedFrame(t=t, nv12=np.vstack([y_plane, uv_plane]))


def tracking(t: float, x: int | None) -> TrackingFrame:
    return tracking_frame_from_captured(captured(t, x))


def sprite_box(x: int) -> NormalizedBox:
    return NormalizedBox(
        left=x / FULL_WIDTH,
        top=SPRITE_Y / FULL_HEIGHT,
        right=(x + SPRITE_SIZE) / FULL_WIDTH,
        bottom=(SPRITE_Y + SPRITE_SIZE) / FULL_HEIGHT,
    )


def center_error_px(box: NormalizedBox, x: int) -> float:
    """box の中心と、左上 (x, SPRITE_Y) の対象の中心のずれ (フル解像度のピクセル)。"""
    expected = sprite_box(x)
    dx = ((box.left + box.right) - (expected.left + expected.right)) / 2 * FULL_WIDTH
    dy = ((box.top + box.bottom) - (expected.top + expected.bottom)) / 2 * FULL_HEIGHT
    return float(np.hypot(dx, dy))


def test_constants_follow_the_issue():
    assert TRACKING_SCALE == 0.5
    assert DEFAULT_LOST_THRESHOLD == 0.5
    assert DEFAULT_SEARCH_MARGIN == 1.5
    assert DEFAULT_RECENT_THRESHOLD == 0.7


# --- 追跡に使う画像 ---


def test_tracking_frame_is_half_size_luma():
    frame = captured(1.25, 300)
    prepared = tracking_frame_from_captured(frame)
    assert prepared.t == 1.25
    assert prepared.gray.shape == (FULL_HEIGHT // 2, FULL_WIDTH // 2)
    assert prepared.gray.dtype == np.uint8
    # Y 面 (輝度) を縮小したもの: 対象の位置の明るさの平均は、元の Y 面とほぼ同じ
    y_plane = frame.nv12[:FULL_HEIGHT]
    region = y_plane[SPRITE_Y : SPRITE_Y + SPRITE_SIZE, 300 : 300 + SPRITE_SIZE]
    half = prepared.gray[SPRITE_Y // 2 : (SPRITE_Y + SPRITE_SIZE) // 2, 150:174]
    assert abs(float(region.mean()) - float(half.mean())) < 2.0


def test_tracking_frame_with_scale_one_copies_luma():
    frame = captured(0.5, None)
    prepared = tracking_frame_from_captured(frame, scale=1.0)
    assert np.array_equal(prepared.gray, frame.nv12[:FULL_HEIGHT])
    assert prepared.gray.base is not frame.nv12


# --- 移動を追う ---


def test_tracks_a_moving_target():
    tracker = NccBoxTracker()
    tracker.start(tracking(0.0, 300), sprite_box(300))
    for i in range(1, 11):
        x = 300 + STEP * i
        result = tracker.update(tracking(i * 0.1, x))
        assert result.found, f"lost at frame {i}"
        assert result.score >= DEFAULT_LOST_THRESHOLD
        assert center_error_px(result.box, x) <= 2.0


def test_result_box_keeps_the_size_and_stays_inside_the_image():
    tracker = NccBoxTracker()
    tracker.start(tracking(0.0, 300), sprite_box(300))
    result = tracker.update(tracking(0.1, 308))
    width = (result.box.right - result.box.left) * FULL_WIDTH
    height = (result.box.bottom - result.box.top) * FULL_HEIGHT
    assert width == pytest.approx(SPRITE_SIZE, abs=2.0)
    assert height == pytest.approx(SPRITE_SIZE, abs=2.0)
    assert 0.0 <= result.box.left < result.box.right <= 1.0
    assert 0.0 <= result.box.top < result.box.bottom <= 1.0


def test_target_that_stays_still_is_found_with_high_score():
    tracker = NccBoxTracker()
    tracker.start(tracking(0.0, 600), sprite_box(600))
    result = tracker.update(tracking(0.1, 600))
    assert result.found
    assert result.score > 0.9
    assert center_error_px(result.box, 600) <= 1.0


# --- 見失いを検出する ---


def blended(t: float, x: int, ratio: float) -> TrackingFrame:
    """左上 (x, SPRITE_Y) の対象の模様を、別の独立な模様へ ratio (0〜1) の割合で混ぜたフレーム。"""
    rng = np.random.default_rng(3)
    other_blocks = rng.integers(0, 2, (SPRITE_SIZE // 8, SPRITE_SIZE // 8)) * 255
    other = np.kron(other_blocks, np.ones((8, 8))).astype(np.float32)
    mix = (1 - ratio) * sprite().astype(np.float32) + ratio * other
    y_plane = background()
    y_plane[SPRITE_Y : SPRITE_Y + SPRITE_SIZE, x : x + SPRITE_SIZE] = np.clip(
        mix, 0, 255
    ).astype(np.uint8)
    uv_plane = np.full((FULL_HEIGHT // 2, FULL_WIDTH), 128, np.uint8)
    return tracking_frame_from_captured(
        CapturedFrame(t=t, nv12=np.vstack([y_plane, uv_plane]))
    )


def test_recent_template_follows_a_gradually_changing_look():
    # 同じ位置のまま、模様が 10 フレームかけて別の模様に変わる。最初の見本だけでは途中で見失うが、
    # 直近の見本 (相関が高かったときの見た目) も使えば最後まで追える
    with_recent = NccBoxTracker()
    without_recent = NccBoxTracker(recent_threshold=2.0)
    for tracker in (with_recent, without_recent):
        tracker.start(blended(0.0, 600, 0.0), sprite_box(600))
    for i in range(1, 11):
        frame = blended(i * 0.1, 600, i / 10)
        kept = with_recent.update(frame)
        dropped = without_recent.update(frame)
    assert kept.found
    assert center_error_px(kept.box, 600) <= 1.0
    assert not dropped.found


def test_detects_loss_when_target_disappears():
    tracker = NccBoxTracker()
    tracker.start(tracking(0.0, 300), sprite_box(300))
    assert tracker.update(tracking(0.1, 308)).found
    lost = tracker.update(tracking(0.2, None))
    assert not lost.found
    assert lost.score < DEFAULT_LOST_THRESHOLD


def test_detects_loss_when_target_jumps_out_of_the_search_window():
    tracker = NccBoxTracker()
    tracker.start(tracking(0.0, 200), sprite_box(200))
    # 探す窓 (box の長い辺の 1.5 倍の余白) より遠くへ移った
    result = tracker.update(tracking(0.1, 900))
    assert not result.found


def test_threshold_decides_loss():
    tracker = NccBoxTracker(threshold=1.01)
    tracker.start(tracking(0.0, 300), sprite_box(300))
    # 相関は 1 を超えないため、しきい値が 1 より大きければ必ず見失いになる
    assert not tracker.update(tracking(0.1, 300)).found


def plain_captured(t: float, x: int) -> CapturedFrame:
    """一様な背景 (明るさ 100) の左上 (x, SPRITE_Y) に、一様な四角 (明るさ 200) を置いたフレーム。UV 面は 128。"""
    y_plane = np.full((FULL_HEIGHT, FULL_WIDTH), 100, np.uint8)
    y_plane[SPRITE_Y : SPRITE_Y + SPRITE_SIZE, x : x + SPRITE_SIZE] = 200
    uv_plane = np.full((FULL_HEIGHT // 2, FULL_WIDTH), 128, np.uint8)
    return CapturedFrame(t=t, nv12=np.vstack([y_plane, uv_plane]))


def test_uniform_target_is_lost_without_moving():
    # 画素の値がすべて同じ見本では正規化相互相関が決まらず、cv2.TM_CCOEFF_NORMED は窓のどの位置でも 1 を返す。
    # そうした見本は照合に使わず、同じ画像でも位置を変えずに見失いとする
    tracker = NccBoxTracker()
    tracker.start(
        tracking_frame_from_captured(plain_captured(0.0, 600)), sprite_box(600)
    )
    for i in range(1, 3):
        frame = tracking_frame_from_captured(plain_captured(i * 0.1, 600))
        result = tracker.update(frame)
        assert not result.found, f"found at frame {i}"
        assert result.score == 0.0
        assert center_error_px(result.box, 600) <= 1.0


# --- 使い方の誤り ---


def test_update_before_start_is_an_error():
    with pytest.raises(RuntimeError):
        NccBoxTracker().update(tracking(0.0, 300))


@pytest.mark.parametrize(
    "box",
    [
        NormalizedBox(0.5, 0.5, 0.4, 0.6),
        NormalizedBox(0.1, 0.6, 0.2, 0.6),
        NormalizedBox(-0.1, 0.1, 0.2, 0.2),
        NormalizedBox(0.1, 0.1, 1.2, 0.2),
    ],
)
def test_invalid_start_box_is_an_error(box):
    with pytest.raises(ValueError):
        NccBoxTracker().start(tracking(0.0, 300), box)


def test_tiny_box_is_widened_to_the_minimum_template():
    tracker = NccBoxTracker()
    # フル解像度で 2 ピクセル四方 (追跡の画像では 1 ピクセル) の box でも始められる
    tiny = NormalizedBox(
        left=320 / FULL_WIDTH,
        top=320 / FULL_HEIGHT,
        right=322 / FULL_WIDTH,
        bottom=322 / FULL_HEIGHT,
    )
    tracker.start(tracking(0.0, 300), tiny)
    result = tracker.update(tracking(0.1, 300))
    assert result.box.right > result.box.left
