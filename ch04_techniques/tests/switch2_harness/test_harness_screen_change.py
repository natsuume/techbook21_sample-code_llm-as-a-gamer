import cv2
import numpy as np
import pytest

from switch2_harness.screen_change import (
    PIXEL_DIFF_THRESHOLD,
    SAMPLE_SIZE,
    STATIC_RATIO,
    change_ratio,
    is_static,
    load_gray,
)


def gray(value: int, height: int = 720, width: int = 1280) -> np.ndarray:
    return np.full((height, width), value, dtype=np.uint8)


def test_constants():
    assert SAMPLE_SIZE == (160, 90)
    assert PIXEL_DIFF_THRESHOLD == 24
    assert STATIC_RATIO == 0.01


def test_same_images_have_no_change():
    assert change_ratio(gray(100), gray(100)) == 0.0


def test_black_and_white_change_everywhere():
    assert change_ratio(gray(0), gray(255)) == 1.0


def test_left_half_changed_is_half():
    before = gray(0)
    after = gray(0)
    after[:, :640] = 200
    assert change_ratio(before, after) == pytest.approx(0.5)


def test_difference_must_exceed_the_threshold():
    assert change_ratio(gray(100), gray(100 + PIXEL_DIFF_THRESHOLD)) == 0.0
    assert change_ratio(gray(100), gray(100 + PIXEL_DIFF_THRESHOLD + 1)) == 1.0


def test_difference_is_absolute():
    assert change_ratio(gray(200), gray(100)) == 1.0


def test_images_of_different_sizes_are_compared_after_resizing():
    small = gray(0, height=180, width=320)
    large = gray(0)
    large[:360, :] = 255  # 上半分
    assert change_ratio(small, large) == pytest.approx(0.5)


def test_small_change_ratio_value():
    before = gray(0)
    after = gray(0)
    # 1280x720 の 8x8 の 1 ブロックは、160x90 に縮小すると 1 画素になる
    after[:8, :8] = 255
    assert change_ratio(before, after) == pytest.approx(1 / (160 * 90))


@pytest.mark.parametrize(
    ("ratio", "static"),
    [(0.0, True), (0.0099, True), (0.01, False), (0.5, False), (1.0, False)],
)
def test_is_static(ratio, static):
    assert is_static(ratio) is static


def test_load_gray_reads_png_as_grayscale(tmp_path):
    path = tmp_path / "frame.png"
    color = np.zeros((90, 160, 3), dtype=np.uint8)
    color[:, :, :] = (50, 50, 50)
    assert cv2.imwrite(str(path), color)
    image = load_gray(path)
    assert image.shape == (90, 160)
    assert image.dtype == np.uint8
    assert int(image[0, 0]) == 50


def test_load_gray_rejects_missing_file(tmp_path):
    path = tmp_path / "missing.png"
    with pytest.raises(ValueError) as error:
        load_gray(path)
    assert str(error.value) == f"cannot read image: {path}"


def test_load_gray_rejects_non_image_file(tmp_path):
    path = tmp_path / "broken.png"
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError):
        load_gray(path)
