"""画面全体の知覚ハッシュ (dHash) と、2 つのハッシュの近さ。I/O・スレッド・時刻を扱わない。

dHash の作り方 (screen_hash):
    1. グレースケールにする (3 チャンネルの画像は BGR として cv2.COLOR_BGR2GRAY で変換する)
    2. 幅 HASH_SIZE + 1、高さ HASH_SIZE に cv2.INTER_AREA で縮小する
    3. 各行で、隣り合う 2 つの画素の左が右より明るければ 1、そうでなければ 0 のビットにする
       (HASH_SIZE 行 × HASH_SIZE 個 = HASH_SIZE ** 2 ビット)
    4. ビットを行の上から、各行の左から順に並べ、先頭を最上位のビットとする整数にする
明るさが一様に変わっても、隣り合う画素の明暗の向きは変わらないため、ハッシュは変わりにくい。
2 つのハッシュは、ハミング距離 (違うビットの数) が NEAR_DISTANCE_MAX 以下なら「近い」(同じ画面) とみなす。
"""

import numpy as np

# dHash の縮小後の高さ (幅はこれより 1 大きい)。ハッシュは HASH_SIZE ** 2 ビット
HASH_SIZE = 8
# 2 つのハッシュを近いとみなすハミング距離の上限
NEAR_DISTANCE_MAX = 10


def screen_hash(image: np.ndarray) -> int:
    """画像 image (BGR の uint8 の (高さ, 幅, 3) か、グレースケールの (高さ, 幅)) の dHash を返す。

    作り方はモジュールの docstring のとおり。戻り値は 0 以上 2 ** (HASH_SIZE ** 2) 未満の整数。
    画像が空 (幅か高さが 0) なら ValueError。
    """
    raise NotImplementedError


def hamming_distance(first: int, second: int) -> int:
    """2 つのハッシュ first と second のハミング距離 (2 進で違うビットの数) を返す。"""
    raise NotImplementedError


def is_near(first: int, second: int, max_distance: int = NEAR_DISTANCE_MAX) -> bool:
    """2 つのハッシュのハミング距離が max_distance 以下なら True (同じ画面とみなす) を返す。"""
    raise NotImplementedError
