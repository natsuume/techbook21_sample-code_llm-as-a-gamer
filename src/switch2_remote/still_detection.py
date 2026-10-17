"""画面が静止したかを判定する純粋関数。

連続するフレームの縮小グレースケール画像どうしの差 (動き量) を測り、
動き量が閾値未満のフレームが何枚続いたかを数える。
"""

import cv2
import numpy as np

STILL_TH = 0.5  # 静止とみなす動き量の上限。ゲーム画面の微細なアニメーションを静止として扱える緩さにしている


def motion_between(prev_small: np.ndarray | None, small: np.ndarray) -> float:
    """縮小グレースケール画像どうしの平均絶対差 (0〜255)。前フレームが無ければ最大値を返す。"""
    if prev_small is None:
        return 255.0
    return float(cv2.absdiff(small, prev_small).mean())


def next_still_count(
    still_count: int, motion: float, still_th: float = STILL_TH
) -> int:
    """動き量が still_th 未満なら連続静止フレーム数を 1 増やし、そうでなければ 0 に戻す。"""
    return still_count + 1 if motion < still_th else 0
