"""画面全体の知覚ハッシュ (dHash) と、2 つのハッシュの近さ。I/O・スレッド・時刻を扱わない。

dHash の作り方 (screen_hash):
    1. グレースケールにする (3 チャンネルの画像は BGR として cv2.COLOR_BGR2GRAY で変換する)
    2. 幅 hash_size + 1、高さ hash_size に cv2.INTER_AREA で縮小する
    3. 各行で、隣り合う 2 つの画素の左が右より明るければ 1、そうでなければ 0 のビットにする
       (hash_size 行 × hash_size 個 = hash_size ** 2 ビット)
    4. ビットを行の上から、各行の左から順に並べ、先頭を最上位のビットとする整数にする
明るさが一様に変わっても、隣り合う画素の明暗の向きは変わらないため、ハッシュは変わりにくい。
2 つのハッシュは、ハミング距離 (違うビットの数) が NEAR_DISTANCE_MAX 以下なら「近い」(同じ画面) とみなす。
"""

import cv2
import numpy as np

# dHash の縮小後の高さ (幅はこれより 1 大きい)。ハッシュは HASH_SIZE ** 2 ビット
HASH_SIZE = 8
# 2 つのハッシュを近いとみなすハミング距離の上限 (64 ビットの約 15%)。主人公が少し動く・会話の文字が進む程度の
# 変化は同じ画面とみなし、部屋や場所が変わった画面は新しい画面とみなす。
# 一定間隔のフレーム (幅 640) の記録 (約 1000 秒。家の出口の前で約 300 秒止まった区間を含む) に当てて選んだ
# (2026-11-16)。この記録では、上限が 10 以上なら止まった区間全体で、新しい画面が出ていない秒数が途切れずに 60 秒を
# 超えた (8 以下では途中で 60 秒を下回った)。上限 10 では、止まった区間より前の、長い会話を読み進めていた間と、
# 同じ部屋を行き来していた間も 60 秒を超え、12 以上では、さらに前の、部屋の中を探していた間も超えた
NEAR_DISTANCE_MAX = 10


def screen_hash(image: np.ndarray, hash_size: int = HASH_SIZE) -> int:
    """画像 image (BGR の uint8 の (高さ, 幅, 3) か、グレースケールの (高さ, 幅)) の dHash を返す。

    作り方はモジュールの docstring のとおり。hash_size は縮小後の高さ (既定 HASH_SIZE)。
    戻り値は 0 以上 2 ** (hash_size ** 2) 未満の整数。画像が空 (幅か高さが 0) なら ValueError。
    """
    if image.ndim < 2 or image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError(f"cannot hash an empty image: shape {image.shape}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(
        gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA
    ).astype(np.int16)
    bits = (small[:, :-1] > small[:, 1:]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming_distance(first: int, second: int) -> int:
    """2 つのハッシュ first と second のハミング距離 (2 進で違うビットの数) を返す。"""
    return (first ^ second).bit_count()


def is_near(first: int, second: int, max_distance: int = NEAR_DISTANCE_MAX) -> bool:
    """2 つのハッシュのハミング距離が max_distance 以下なら True (同じ画面とみなす) を返す。"""
    return hamming_distance(first, second) <= max_distance
