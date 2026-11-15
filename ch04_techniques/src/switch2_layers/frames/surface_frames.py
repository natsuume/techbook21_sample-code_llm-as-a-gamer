"""surface に送るフレームの選択と、時刻と box を書き込んだ JPEG 化。

surface の 1 回の要求では、直近 SURFACE_WINDOW_S 秒以内のフレームから最大 SURFACE_MAX_FRAMES 枚を選ぶ
(最新の 1 枚を必ず含め、残りを範囲から等間隔に選ぶ)。各画像の左上に、そのフレームの t を書き込む。
追えている box があれば、その枠と名前も書き込む (draw_boxes)。
"""

from collections.abc import Sequence

import cv2
import numpy as np

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import (
    DEFAULT_JPEG_QUALITY,
    EncodedImage,
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)
from switch2_layers.surface.surface_types import Box

SURFACE_WINDOW_S = 2.0  # surface に送るフレームの範囲 (直近の秒数)
SURFACE_MAX_FRAMES = 5  # surface に送るフレームの最大の枚数
# surface に送る画像の幅の上限 (これより広いフレームは縮小する)
SURFACE_IMAGE_WIDTH = 1280
# 時刻の文字の大きさ: 画像の幅 TEXT_SCALE_WIDTH ピクセルで倍率 1 にし、幅に比例させる (下限 TEXT_MIN_SCALE)
TEXT_SCALE_WIDTH = 1280
TEXT_MIN_SCALE = 0.4
# box の枠と名前の色 (BGR)。対象の並びの順に使い、足りなければ先頭から繰り返す
BOX_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 255, 255),  # 黄
    (255, 0, 255),  # マゼンタ
    (0, 255, 0),  # 緑
    (255, 128, 0),  # 青
)


def pick_evenly[Item](items: Sequence[Item], max_count: int) -> list[Item]:
    """古い順の items から最大 max_count 個を、添字で等間隔に選んで古い順に返す (最初と最後を必ず含む)。

    max_count 個以下ならすべて。max_count が 1 なら最後の 1 個。それ以外は添字 round(i * (n - 1) / (max_count - 1))
    (i = 0..max_count-1) を選ぶ。max_count が 1 未満なら ValueError にする。
    """
    if max_count < 1:
        raise ValueError(f"max_count must be 1 or more: {max_count}")
    n = len(items)
    if n <= max_count:
        return list(items)
    if max_count == 1:
        return [items[-1]]
    return [items[round(i * (n - 1) / (max_count - 1))] for i in range(max_count)]


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
    max_count が 1 未満なら ValueError にする (pick_evenly)。
    """
    in_range = [frame for frame in frames if now_t - window_s <= frame.t <= now_t]
    in_range.sort(key=lambda frame: frame.t)
    return pick_evenly(in_range, max_count)


def format_frame_label(t: float) -> str:
    """フレームの t を、画像に書き込む文字列とラベルの形 `t=<t を小数第 3 位まで>` にする (例: t=12.345)。"""
    return f"t={t:.3f}"


def draw_frame_time(bgr: np.ndarray, t: float) -> np.ndarray:
    """bgr の左上に format_frame_label(t) を書き込んだコピーを返す。bgr 自体は変えない。

    背景の明るさによらず読めるよう、黒の縁取りの上に白で書く。書き込む範囲は、幅 320 ピクセル以上の画像では
    左上の 4 分の 1 (左から幅の半分、上から高さの半分) の中に収める。
    """
    stamped = bgr.copy()
    width = stamped.shape[1]
    scale = max(TEXT_MIN_SCALE, width / TEXT_SCALE_WIDTH)
    thickness = max(1, round(scale * 2))
    label = format_frame_label(t)
    # 文字の高さ (幅とベースラインは使わない)
    (_, text_height), _ = cv2.getTextSize(
        label, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
    )
    margin = max(4, round(8 * scale))
    origin = (margin, margin + text_height)
    # 黒の縁取り (太い線) の上に白で書く
    cv2.putText(
        stamped,
        label,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (0, 0, 0),
        thickness + 2 * max(1, round(scale)),
        cv2.LINE_AA,
    )
    cv2.putText(
        stamped,
        label,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return stamped


def draw_boxes(bgr: np.ndarray, boxes: Sequence[Box]) -> np.ndarray:
    """bgr に、boxes の枠と名前 (Box.label) を書き込んだコピーを返す。bgr 自体は変えない。

    枠は、Box の座標 (0〜1) に画像の幅・高さを掛けて丸めた位置の長方形 (cv2.rectangle。線の種類は既定の
    cv2.LINE_8 で、枠の線の画素は色そのものになる)。色は boxes の i 番目に
    BOX_COLORS[i % len(BOX_COLORS)] を使い、太さは画像の幅に比例させる (draw_frame_time の文字と同じ倍率、最小 1)。
    名前は枠の左上の上側に、黒の縁取りの上に枠の色で書く (枠が画像の上端に接していて上側に書けなければ、枠の内側の
    左上に書く)。日本語の名前も cv2.putText で書く。boxes が空なら bgr のコピーをそのまま返す。
    """
    raise NotImplementedError


def build_surface_image(
    frame: CapturedFrame,
    width: int = SURFACE_IMAGE_WIDTH,
    quality: int = DEFAULT_JPEG_QUALITY,
    boxes: Sequence[Box] = (),
) -> EncodedImage:
    """frame を surface に送る JPEG にする。

    image_encoding.nv12_to_bgr → shrink_to_width(width) → draw_boxes(boxes) → draw_frame_time(frame.t) →
    encode_jpeg(quality) の順に行う (box と時刻は縮小した後の画像に書き、時刻を box の上に重ねる)。
    boxes が空なら draw_boxes を飛ばす。
    """
    bgr = shrink_to_width(nv12_to_bgr(frame.nv12), width)
    if boxes:
        bgr = draw_boxes(bgr, boxes)
    return encode_jpeg(draw_frame_time(bgr, frame.t), quality)
