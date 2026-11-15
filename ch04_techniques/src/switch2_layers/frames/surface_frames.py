"""surface に送るフレームの選択と、時刻と box を書き込んだ JPEG 化。

surface の 1 回の要求では、直近 SURFACE_WINDOW_S 秒以内のフレームから最大 SURFACE_MAX_FRAMES 枚を選ぶ
(最新の 1 枚を必ず含め、残りを範囲から等間隔に選ぶ)。各画像の左上に、そのフレームの t を書き込む。
追えている box があれば、その枠と名前も書き込む (draw_boxes)。どの box を書き込むかと枠の色は、その要求の
書き込み方 (BoxDrawingPlan) で決める (select_boxes_to_draw):
    - 書き込むのは、その要求の box の対象の一覧にある対象の box だけ
    - その要求で Box を問い直す対象の枠と名前は、最新のフレームの画像に書き込まない (古いフレームには書き込む)
    - 枠の色は、対象の一覧の中の番号で決める (見失った対象があっても、同じ対象は同じ色)
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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
# box の名前の文字の大きさ (時刻の文字の倍率に対する比)
BOX_LABEL_SCALE_RATIO = 0.75


@dataclass(frozen=True)
class BoxDrawingPlan:
    """1 回の要求で送る画像への box の書き込み方。

    target_numbers     対象の名前 → その要求の box の対象の一覧の中の番号 (1 始まり)。枠の色を決める。
                       ここに無い名前の box は書き込まない
    hidden_on_latest   最新のフレームの画像に書き込まない対象の名前 (その要求で Box を問い直す対象)
    """

    target_numbers: Mapping[str, int]
    hidden_on_latest: frozenset[str] = frozenset()


# 画像に書き込んだ box の内容 (対象の名前と座標の組の並び)。frames/surface/ の保存の重複を除くキーに使う
DrawnBoxesKey = tuple[tuple[str, float, float, float, float], ...]


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


def _text_scale(width: int) -> float:
    """幅 width の画像に書き込む時刻の文字の倍率 (TEXT_SCALE_WIDTH で 1。下限 TEXT_MIN_SCALE) を返す。"""
    return max(TEXT_MIN_SCALE, width / TEXT_SCALE_WIDTH)


def draw_frame_time(bgr: np.ndarray, t: float) -> np.ndarray:
    """bgr の左上に format_frame_label(t) を書き込んだコピーを返す。bgr 自体は変えない。

    背景の明るさによらず読めるよう、黒の縁取りの上に白で書く。書き込む範囲は、幅 320 ピクセル以上の画像では
    左上の 4 分の 1 (左から幅の半分、上から高さの半分) の中に収める。
    """
    stamped = bgr.copy()
    width = stamped.shape[1]
    scale = _text_scale(width)
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


def select_boxes_to_draw(
    boxes: Sequence[Box], plan: BoxDrawingPlan | None, is_latest: bool
) -> tuple[Box, ...]:
    """フレームの box (boxes) のうち、その画像に書き込むものを boxes の順で返す。

    plan が None なら boxes のすべて。plan があれば、名前が plan.target_numbers にある box だけを残し、
    is_latest (要求の最新のフレーム) なら、名前が plan.hidden_on_latest にある box も除く。
    """
    if plan is None:
        return tuple(boxes)
    return tuple(
        box
        for box in boxes
        if box.label in plan.target_numbers
        and not (is_latest and box.label in plan.hidden_on_latest)
    )


def drawn_boxes_key(boxes: Sequence[Box]) -> DrawnBoxesKey:
    """画像に書き込んだ box の内容 (名前と座標 x_min・y_min・x_max・y_max の組を boxes の順に並べたもの) を返す。

    確信度と出どころは画像に書かないため含めない。box を書き込まなければ空のタプル。
    """
    return tuple(
        (box.label, box.x_min, box.y_min, box.x_max, box.y_max) for box in boxes
    )


def draw_boxes(
    bgr: np.ndarray,
    boxes: Sequence[Box],
    target_numbers: Mapping[str, int] | None = None,
) -> np.ndarray:
    """bgr に、boxes の枠と名前 (Box.label) を書き込んだコピーを返す。bgr 自体は変えない。

    枠は、Box の座標 (0〜1) に画像の幅・高さを掛けて丸めた位置の長方形 (cv2.rectangle。線の種類は既定の
    cv2.LINE_8 で、枠の線の画素は色そのものになる)。色は、対象の一覧の中の番号 n (1 始まり) の box に
    BOX_COLORS[(n - 1) % len(BOX_COLORS)] を使う。番号は target_numbers[Box.label] で、target_numbers に無い名前の
    box は書き込まない。target_numbers が None なら、boxes の中の位置 (1 始まり) を番号にする。
    太さは画像の幅に比例させる (draw_frame_time の文字と同じ倍率、最小 1)。
    名前は枠の左上の上側に、黒の縁取りの上に枠の色で書く (枠が画像の上端に接していて上側に書けなければ、枠の内側の
    左上に書く)。日本語の名前も cv2.putText で書く。名前は、すべての枠を描いた後に書く (枠が名前に重ならない)。
    boxes が空なら bgr のコピーをそのまま返す。
    """
    drawn = bgr.copy()
    height, width = drawn.shape[:2]
    scale = _text_scale(width)
    thickness = max(1, round(scale * 2))
    labels: list[tuple[str, tuple[int, int], tuple[int, int, int]]] = []
    for box, number in _numbered_boxes(boxes, target_numbers):
        color = BOX_COLORS[(number - 1) % len(BOX_COLORS)]
        top_left = (round(box.x_min * width), round(box.y_min * height))
        bottom_right = (round(box.x_max * width), round(box.y_max * height))
        cv2.rectangle(drawn, top_left, bottom_right, color, thickness)
        labels.append((box.label, top_left, color))
    for label, top_left, color in labels:
        _draw_box_label(drawn, label, top_left, color, scale * BOX_LABEL_SCALE_RATIO)
    return drawn


def _numbered_boxes(
    boxes: Sequence[Box], target_numbers: Mapping[str, int] | None
) -> list[tuple[Box, int]]:
    """boxes の各 box と、その色を決める番号の組を返す (draw_boxes の docstring の規則)。"""
    if target_numbers is None:
        return [(box, index) for index, box in enumerate(boxes, start=1)]
    return [
        (box, target_numbers[box.label]) for box in boxes if box.label in target_numbers
    ]


def _draw_box_label(
    image: np.ndarray,
    label: str,
    top_left: tuple[int, int],
    color: tuple[int, int, int],
    scale: float,
) -> None:
    """image の、枠の左上 top_left の上側 (書けなければ枠の内側の左上) に、黒の縁取りの上に color で label を書く。"""
    text_thickness = max(1, round(scale * 2))
    outline_thickness = text_thickness + 2 * max(1, round(scale))
    (_, text_height), baseline = cv2.getTextSize(
        label, cv2.FONT_HERSHEY_SIMPLEX, scale, text_thickness
    )
    gap = max(2, round(4 * scale))
    x, y = top_left
    # 文字の下端 (ベースラインの下) を、枠の上端から gap だけ上にする
    origin = (x, y - gap - baseline)
    if origin[1] - text_height - outline_thickness < 0:
        # 上側に書けない: 枠の内側の左上に書く
        origin = (x + gap, y + gap + text_height + outline_thickness // 2)
    cv2.putText(
        image,
        label,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (0, 0, 0),
        outline_thickness,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        label,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        text_thickness,
        cv2.LINE_AA,
    )


def build_surface_image(
    frame: CapturedFrame,
    width: int = SURFACE_IMAGE_WIDTH,
    quality: int = DEFAULT_JPEG_QUALITY,
    boxes: Sequence[Box] = (),
    target_numbers: Mapping[str, int] | None = None,
) -> EncodedImage:
    """frame を surface に送る JPEG にする。

    image_encoding.nv12_to_bgr → shrink_to_width(width) → draw_boxes(boxes, target_numbers) →
    draw_frame_time(frame.t) → encode_jpeg(quality) の順に行う (box と時刻は縮小した後の画像に書き、時刻を box の
    上に重ねる)。boxes が空なら draw_boxes を飛ばす。
    """
    bgr = shrink_to_width(nv12_to_bgr(frame.nv12), width)
    if boxes:
        bgr = draw_boxes(bgr, boxes, target_numbers)
    return encode_jpeg(draw_frame_time(bgr, frame.t), quality)
