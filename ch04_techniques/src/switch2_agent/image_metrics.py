"""フレームの画像を縮小・比較・符号化する純粋な関数。

鮮明度 (sharpness) はラプラシアンの分散で測る。ぼけた画像や暗転・切り替わりの途中の画像ほど小さくなる。
差分 (mean_abs_diff) は縮小したグレー画像どうしの画素の差の絶対値の平均で、0〜255 の値になる。
I/O・スレッド・時刻を扱わない。
"""

import cv2
import numpy as np

# 鮮明度と差分の計算に使う縮小後の大きさ (幅, 高さ)
ANALYSIS_WIDTH, ANALYSIS_HEIGHT = 320, 180


def to_small_gray(nv12: np.ndarray, height: int) -> np.ndarray:
    """NV12 の Y 面 (nv12[:height]) を ANALYSIS_WIDTH x ANALYSIS_HEIGHT に縮小した uint8 の配列を返す。

    縮小は cv2.resize(..., (ANALYSIS_WIDTH, ANALYSIS_HEIGHT), interpolation=cv2.INTER_AREA) で行う。
    戻り値の形は (ANALYSIS_HEIGHT, ANALYSIS_WIDTH)。height は元のフレームの高さ (Y 面の行数)。
    """
    return cv2.resize(
        nv12[:height],
        (ANALYSIS_WIDTH, ANALYSIS_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


def sharpness(gray: np.ndarray) -> float:
    """グレー画像の鮮明度 cv2.Laplacian(gray, cv2.CV_64F).var() を float で返す。

    輪郭が多くはっきりした画像ほど大きく、一様な画像は 0 になる。
    """
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """同じ形の 2 枚のグレー画像の差 cv2.absdiff(a, b).mean() を float (0〜255) で返す。

    同じ画像なら 0 になる。
    """
    return float(cv2.absdiff(a, b).mean())


def nv12_to_jpeg(
    nv12: np.ndarray, height: int, width: int | None, quality: int
) -> bytes:
    """NV12 のフレームを JPEG のバイト列にする。

    cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12) で BGR にする。width が None でなく元の幅と違えば、
    縦横比を保って幅 width に縮小する (高さは round(元の高さ * width / 元の幅)、補間は cv2.INTER_AREA)。
    cv2.imencode(".jpg", ..., [cv2.IMWRITE_JPEG_QUALITY, quality]) の結果を返す。
    height は元のフレームの高さ (Y 面の行数)。符号化に失敗したら ValueError にする。
    """
    bgr = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
    original_width = bgr.shape[1]
    if width is not None and width != original_width:
        resized_height = round(height * width / original_width)
        bgr = cv2.resize(bgr, (width, resized_height), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("failed to encode frame as JPEG")
    return encoded.tobytes()
