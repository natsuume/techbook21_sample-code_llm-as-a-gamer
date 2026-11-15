"""surface に送るフレームの選択と、時刻を書き込んだ JPEG 化。

surface の 1 回の要求では、直近 SURFACE_WINDOW_S 秒以内のフレームから最大 SURFACE_MAX_FRAMES 枚を選ぶ
(最新の 1 枚を必ず含め、残りを範囲から等間隔に選ぶ)。各画像の左上に、そのフレームの t を書き込む。
"""

from collections.abc import Sequence

import numpy as np

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY, EncodedImage

SURFACE_WINDOW_S = 2.0  # surface に送るフレームの範囲 (直近の秒数)
SURFACE_MAX_FRAMES = 5  # surface に送るフレームの最大の枚数
# surface に送る画像の幅の上限 (これより広いフレームは縮小する)
SURFACE_IMAGE_WIDTH = 1280


def select_surface_frames(
    frames: Sequence[CapturedFrame],
    now_t: float,
    window_s: float = SURFACE_WINDOW_S,
    max_count: int = SURFACE_MAX_FRAMES,
) -> list[CapturedFrame]:
    """t の古い順の frames から、surface に送るフレームを選んで古い順に返す。

    範囲は now_t - window_s <= t <= now_t (両端を含む)。範囲の外のフレームは選ばない。
    - 範囲のフレームが 0 枚 (window_s 秒より古いフレームしか無い場合を含む) なら空のリスト
    - 範囲のフレームが max_count 枚以下なら、そのすべて
    - 多ければ、範囲の n 枚から添字 round(i * (n - 1) / (max_count - 1)) (i = 0..max_count-1) のフレームを選ぶ。
      範囲の最新の 1 枚と最も古い 1 枚を必ず含み、残りは添字で等間隔になる
      (キャプチャーのフレームはほぼ一定の間隔で届くため、時刻でもほぼ等間隔になる)
    - max_count が 1 なら範囲の最新の 1 枚
    max_count が 1 未満なら ValueError にする。
    """
    raise NotImplementedError


def format_frame_label(t: float) -> str:
    """フレームの t を、画像に書き込む文字列とラベルの形 `t=<t を小数第 3 位まで>` にする (例: t=12.345)。"""
    raise NotImplementedError


def draw_frame_time(bgr: np.ndarray, t: float) -> np.ndarray:
    """bgr の左上に format_frame_label(t) を書き込んだコピーを返す。bgr 自体は変えない。

    背景の明るさによらず読めるよう、黒の縁取りの上に白で書く。書き込む範囲は、幅 320 ピクセル以上の画像では
    左上の 4 分の 1 (左から幅の半分、上から高さの半分) の中に収める。
    """
    raise NotImplementedError


def build_surface_image(
    frame: CapturedFrame,
    width: int = SURFACE_IMAGE_WIDTH,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> EncodedImage:
    """frame を surface に送る JPEG にする。

    image_encoding.nv12_to_bgr → shrink_to_width(width) → draw_frame_time(frame.t) → encode_jpeg(quality) の順に行う
    (時刻は縮小した後の画像に書く)。
    """
    raise NotImplementedError
