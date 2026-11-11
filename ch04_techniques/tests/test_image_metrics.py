import cv2
import numpy as np

from switch2_agent.image_metrics import (
    ANALYSIS_HEIGHT,
    ANALYSIS_WIDTH,
    mean_abs_diff,
    nv12_to_jpeg,
    sharpness,
    to_small_gray,
)

WIDTH, HEIGHT = 1280, 720  # 合成する NV12 のフレームの大きさ


def checkerboard(height: int, width: int, cell: int = 8) -> np.ndarray:
    """cell 画素ごとに 0 と 255 が入れ替わる市松模様の uint8 の画像を返す。"""
    rows, cols = np.indices((height, width))
    return (((rows // cell + cols // cell) % 2) * 255).astype(np.uint8)


def nv12_frame(y_plane: np.ndarray, uv_value: int = 128) -> np.ndarray:
    """Y 面 y_plane (高さ x 幅) の下に、色差を uv_value でそろえた面を付けた NV12 の配列を返す。"""
    height, width = y_plane.shape
    uv_plane = np.full((height // 2, width), uv_value, np.uint8)
    return np.vstack([y_plane, uv_plane])


def test_analysis_size_is_320x180():
    assert (ANALYSIS_WIDTH, ANALYSIS_HEIGHT) == (320, 180)


def test_checkerboard_is_sharper_than_blurred_image():
    board = checkerboard(ANALYSIS_HEIGHT, ANALYSIS_WIDTH)
    blurred = cv2.GaussianBlur(board, (15, 15), 5)
    assert sharpness(board) > sharpness(blurred)


def test_sharpness_of_uniform_image_is_zero():
    assert sharpness(np.full((ANALYSIS_HEIGHT, ANALYSIS_WIDTH), 100, np.uint8)) == 0.0


def test_sharpness_returns_float():
    assert isinstance(sharpness(checkerboard(ANALYSIS_HEIGHT, ANALYSIS_WIDTH)), float)


def test_mean_abs_diff_of_same_image_is_zero():
    board = checkerboard(ANALYSIS_HEIGHT, ANALYSIS_WIDTH)
    assert mean_abs_diff(board, board.copy()) == 0.0


def test_mean_abs_diff_is_mean_of_absolute_difference():
    dark = np.full((ANALYSIS_HEIGHT, ANALYSIS_WIDTH), 10, np.uint8)
    bright = np.full((ANALYSIS_HEIGHT, ANALYSIS_WIDTH), 30, np.uint8)
    # 引き算の順によらず、差の絶対値の平均になる
    assert mean_abs_diff(dark, bright) == 20.0
    assert mean_abs_diff(bright, dark) == 20.0


def test_to_small_gray_shrinks_y_plane_to_320x180():
    gray = to_small_gray(nv12_frame(checkerboard(HEIGHT, WIDTH)), HEIGHT)
    assert gray.shape == (180, 320)
    assert gray.dtype == np.uint8


def test_to_small_gray_uses_only_y_plane():
    # 色差の面 (200) は使わず、Y 面 (100) だけを縮小する
    nv12 = nv12_frame(np.full((HEIGHT, WIDTH), 100, np.uint8), uv_value=200)
    gray = to_small_gray(nv12, HEIGHT)
    assert np.all(gray == 100)


def test_nv12_to_jpeg_can_be_decoded_with_original_size():
    jpeg = nv12_to_jpeg(nv12_frame(checkerboard(HEIGHT, WIDTH)), HEIGHT, None, 85)
    assert isinstance(jpeg, bytes)
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape == (720, 1280, 3)


def test_nv12_to_jpeg_keeps_size_when_width_is_original_width():
    jpeg = nv12_to_jpeg(nv12_frame(checkerboard(HEIGHT, WIDTH)), HEIGHT, 1280, 85)
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (720, 1280, 3)


def test_nv12_to_jpeg_shrinks_keeping_aspect_ratio_when_width_is_given():
    jpeg = nv12_to_jpeg(nv12_frame(checkerboard(HEIGHT, WIDTH)), HEIGHT, 640, 85)
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (360, 640, 3)


def test_nv12_to_jpeg_lower_quality_makes_smaller_file():
    nv12 = nv12_frame(checkerboard(HEIGHT, WIDTH))
    assert len(nv12_to_jpeg(nv12, HEIGHT, None, 10)) < len(
        nv12_to_jpeg(nv12, HEIGHT, None, 95)
    )
