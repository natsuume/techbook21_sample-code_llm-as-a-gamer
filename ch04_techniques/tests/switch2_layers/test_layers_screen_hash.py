import numpy as np
import pytest

from switch2_layers.progress.screen_hash import (
    NEAR_DISTANCE_MAX,
    hamming_distance,
    is_near,
    screen_hash,
)

# 画面の模様の格子 (縦 GRID_ROWS × 横 GRID_COLUMNS のブロック) と、1 ブロックの大きさ (画素)
GRID_ROWS = 8
GRID_COLUMNS = 9
BLOCK_HEIGHT = 10
BLOCK_WIDTH = 16


def hadamard_signs(row: int) -> np.ndarray:
    """64 次の Sylvester 型のアダマール行列の row 行目を、8 × 8 の ±1 の配列にして返す。

    違う行どうしは、64 個のうちちょうど 32 個の符号が違う。
    """
    matrix = np.array([[1]])
    while matrix.shape[0] < 64:
        matrix = np.block([[matrix, matrix], [matrix, -matrix]])
    return matrix[row].reshape(GRID_ROWS, GRID_COLUMNS - 1)


def patterned_screen(row: int, brightness_offset: int = 0) -> np.ndarray:
    """横に隣り合うブロックの明暗の向きを hadamard_signs(row) にした、BGR の画面の画像を返す。

    各行は 128 から始め、右のブロックへ ±12 ずつ変える (平らな画像はどれも同じ dHash になるため、模様を付ける)。
    brightness_offset は、全体に足す明るさ。
    """
    signs = hadamard_signs(row)
    grid = np.full((GRID_ROWS, GRID_COLUMNS), 128, dtype=np.int32)
    grid[:, 1:] += np.cumsum(signs * 12, axis=1)
    grid += brightness_offset
    gray = np.repeat(np.repeat(grid, BLOCK_HEIGHT, axis=0), BLOCK_WIDTH, axis=1)
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    return np.stack([gray, gray, gray], axis=2)


def test_hamming_distance_counts_different_bits():
    assert hamming_distance(0, 0) == 0
    assert hamming_distance(0b1011, 0b0001) == 2
    assert hamming_distance(0b1111, 0b0000) == 4
    assert hamming_distance(1 << 63, 0) == 1


def test_is_near_uses_the_distance_limit():
    assert is_near(0b0000, 0b0011, max_distance=2) is True
    assert is_near(0b0000, 0b0111, max_distance=2) is False
    assert is_near(5, 5, max_distance=0) is True
    # 既定の上限
    near = (1 << NEAR_DISTANCE_MAX) - 1  # 下位の NEAR_DISTANCE_MAX ビットが 1
    assert is_near(0, near) is True
    assert is_near(0, (near << 1) | 1) is False


def test_same_screen_has_the_same_hash():
    image = patterned_screen(5)
    assert screen_hash(image) == screen_hash(image.copy())
    assert screen_hash(image) >= 0


def test_uniform_brightness_change_keeps_the_screen_near():
    original = screen_hash(patterned_screen(5))
    brighter = screen_hash(patterned_screen(5, brightness_offset=6))
    assert is_near(original, brighter)


def test_different_screens_are_not_near():
    hashes = [screen_hash(patterned_screen(row)) for row in (1, 2, 3, 7)]
    for index, first in enumerate(hashes):
        for second in hashes[index + 1 :]:
            assert not is_near(first, second)


def test_grayscale_image_has_the_same_hash_as_gray_bgr():
    bgr = patterned_screen(9)
    assert screen_hash(bgr[:, :, 0]) == screen_hash(bgr)


def test_hash_does_not_depend_on_image_size():
    # frames/interval/ の縮小の幅が変わっても、同じ画面は近い
    small = patterned_screen(4)
    large = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
    assert is_near(screen_hash(small), screen_hash(large))


def test_empty_image_is_an_error():
    with pytest.raises(ValueError):
        screen_hash(np.zeros((0, 0, 3), np.uint8))
