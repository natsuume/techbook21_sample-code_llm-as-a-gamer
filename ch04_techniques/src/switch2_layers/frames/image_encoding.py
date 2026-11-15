"""フレームの画像の変換と JPEG 化 (surface に送る画像と、一定間隔の保存で共通)。I/O・スレッド・時刻を扱わない。"""

from dataclasses import dataclass

import numpy as np

DEFAULT_JPEG_QUALITY = 85


@dataclass(frozen=True)
class EncodedImage:
    """JPEG にした画像。

    jpeg     JPEG のバイト列
    width    画像の幅 (ピクセル)
    height   画像の高さ (ピクセル)
    """

    jpeg: bytes
    width: int
    height: int


def nv12_to_bgr(nv12: np.ndarray) -> np.ndarray:
    """NV12 のフレームを BGR の画像 (cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)) にする。"""
    raise NotImplementedError


def shrink_to_width(bgr: np.ndarray, width: int) -> np.ndarray:
    """bgr の幅が width より大きければ、縦横比を保って幅 width に縮小した画像を返す。

    高さは round(元の高さ * width / 元の幅)、補間は cv2.INTER_AREA。幅が width 以下なら bgr をそのまま返す (拡大しない)。
    """
    raise NotImplementedError


def encode_jpeg(bgr: np.ndarray, quality: int = DEFAULT_JPEG_QUALITY) -> EncodedImage:
    """bgr を cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality]) で JPEG にする。

    幅と高さは bgr の形から取る。符号化に失敗したら (cv2.imencode が False を返すか cv2.error を出したら。
    空の画像を含む) ValueError にする。
    """
    raise NotImplementedError
