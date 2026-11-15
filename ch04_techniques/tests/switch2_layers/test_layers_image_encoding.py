import cv2
import numpy as np
import pytest

from switch2_layers.frames.image_encoding import (
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)


def test_nv12_to_bgr_shape():
    nv12 = np.full((36 * 3 // 2, 64), 128, np.uint8)
    bgr = nv12_to_bgr(nv12)
    assert bgr.shape == (36, 64, 3)


def test_shrink_to_width_keeps_aspect_ratio():
    bgr = np.zeros((720, 1280, 3), np.uint8)
    assert shrink_to_width(bgr, 640).shape == (360, 640, 3)
    assert shrink_to_width(bgr, 100).shape == (round(720 * 100 / 1280), 100, 3)


def test_shrink_to_width_does_not_enlarge():
    bgr = np.zeros((180, 320, 3), np.uint8)
    assert shrink_to_width(bgr, 320) is bgr
    assert shrink_to_width(bgr, 1280) is bgr


def test_encode_jpeg():
    bgr = np.full((18, 32, 3), 200, np.uint8)
    image = encode_jpeg(bgr, quality=90)
    assert (image.width, image.height) == (32, 18)
    assert image.jpeg.startswith(b"\xff\xd8")
    decoded = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (18, 32, 3)


def test_encode_jpeg_failure_is_value_error():
    with pytest.raises(ValueError):
        encode_jpeg(np.zeros((0, 0, 3), np.uint8))
