"""2 枚の画面の画像がどれだけ変わったか (変化の割合) と、PNG のグレースケールでの読み込み。

change_ratio と is_static は純粋関数で、ファイルの読み込みは load_gray だけが行う。
"""

from pathlib import Path

import numpy as np

# 比べる前に縮小する大きさ (幅, 高さ)。16:9 の画面を 160x90 にする
SAMPLE_SIZE: tuple[int, int] = (160, 90)

# 画素の差の絶対値がこれを超えたら、その画素は変わったとみなす (0〜255)
PIXEL_DIFF_THRESHOLD = 24

# 変化の割合がこれ未満なら「変化なし」とする
STATIC_RATIO = 0.01


def change_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """グレースケール (uint8 の 2 次元配列) の画像 a と b の変化の割合 (0.0〜1.0) を返す。

    a と b をそれぞれ cv2.resize(..., SAMPLE_SIZE, interpolation=cv2.INTER_AREA) で縮小し、
    画素ごとの差の絶対値が PIXEL_DIFF_THRESHOLD を超える画素の数を、画素の総数 (160 * 90) で割る。
    a と b の元の大きさは違ってよい。
    """
    raise NotImplementedError


def is_static(ratio: float) -> bool:
    """変化の割合 ratio が STATIC_RATIO 未満 (変化なし) なら True を返す。"""
    raise NotImplementedError


def load_gray(path: Path) -> np.ndarray:
    """path の画像を cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) でグレースケールとして読む。

    読めなければ (ファイルが無い、画像でない) ValueError にする。メッセージは `cannot read image: <path>`。
    """
    raise NotImplementedError
