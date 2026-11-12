import numpy as np
import pytest
from switch2_remote.capture import Capture

from switch2_agent.capture_with_history import CaptureWithHistory, make_timed_frame
from switch2_agent.image_metrics import sharpness, to_small_gray

# 第2章の Capture のフレームの大きさ (DEVICE_WIDTH x DEVICE_HEIGHT)
WIDTH, HEIGHT = 1280, 720


def nv12_checkerboard() -> np.ndarray:
    """Y 面が市松模様、色差が 128 の 1280x720 の NV12 の配列を返す。"""
    rows, cols = np.indices((HEIGHT, WIDTH))
    y_plane = (((rows // 16 + cols // 16) % 2) * 255).astype(np.uint8)
    uv_plane = np.full((HEIGHT // 2, WIDTH), 128, np.uint8)
    return np.vstack([y_plane, uv_plane])


def test_capture_with_history_extends_chapter2_capture():
    assert issubclass(CaptureWithHistory, Capture)


def test_make_timed_frame_builds_frame_from_raw_nv12():
    nv12 = nv12_checkerboard()
    timed = make_timed_frame(nv12.tobytes(), 12.5)
    assert timed.time == 12.5
    assert timed.nv12.shape == (HEIGHT * 3 // 2, WIDTH)
    assert timed.nv12.dtype == np.uint8
    assert np.array_equal(timed.nv12, nv12)


def test_make_timed_frame_computes_small_gray_and_sharpness():
    nv12 = nv12_checkerboard()
    timed = make_timed_frame(nv12.tobytes(), 3.0)
    expected_gray = to_small_gray(nv12, HEIGHT)
    assert timed.gray_small.shape == (180, 320)
    assert np.array_equal(timed.gray_small, expected_gray)
    assert timed.sharpness == pytest.approx(sharpness(expected_gray))


def test_make_timed_frame_rejects_raw_of_wrong_length():
    with pytest.raises(ValueError):
        make_timed_frame(b"\x00" * 100, 1.0)
