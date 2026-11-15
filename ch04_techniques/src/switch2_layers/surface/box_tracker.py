"""一度取った box を、以降のフレームで追う部品 (BoxTracker の Protocol と、NCC のテンプレートマッチングの実装)。

追跡は、キャプチャーが渡す生のフレーム (frames.frame_buffer.CapturedFrame) から作った TrackingFrame で行う
(surface に送る JPEG は、時刻や枠を書き込んであるため使わない)。TrackingFrame の画像は、NV12 の Y 面
(輝度。そのままグレースケール) を TRACKING_SCALE 倍に縮小したもの。
box の座標は画像の幅・高さを 1 とした割合 (NormalizedBox) で受け渡し、縮小率に依らない。

NccBoxTracker (NCC = 正規化相互相関):
    start   最初の box の範囲を、見本 (最初の見本) として切り出す
    update  前回の位置に、前回の移動量を足した位置を中心に、box の大きさの周りに margin × (box の長い辺) の余白を
            足した窓を探す。窓の中で、見本ごとに cv2.matchTemplate (cv2.TM_CCOEFF_NORMED) を計算し、
            最大の相関の位置に box を動かす。見本は、最初の見本と、直近の見本 (相関が recent_threshold 以上だった
            位置の見た目。最後に更新したもの) の 2 つ。相関の最大値が threshold 未満なら見失いとする。
            画素の値がすべて同じ見本は、相関が決まらない (TM_CCOEFF_NORMED は窓のどの位置でも 1 を返す) ため
            照合に使わず、使える見本が無ければ見失いとする。
            見失ったときは、次の窓の中心を最後に見えていた位置のままにする (移動量は 0)
"""

import math
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from switch2_layers.frames.frame_buffer import CapturedFrame, frame_size

TRACKING_SCALE = 0.5  # 追跡に使う画像の縮小率
DEFAULT_SEARCH_MARGIN = 1.5  # 探す窓の余白 (box の長い辺の何倍か)
DEFAULT_LOST_THRESHOLD = 0.5  # 相関の最大値がこれ未満なら見失いとする
DEFAULT_RECENT_THRESHOLD = 0.7  # 相関がこれ以上の位置の見た目を、直近の見本にする
MIN_TEMPLATE_SIZE = 4  # 見本の幅と高さの最小値 (追跡に使う画像のピクセル)


@dataclass(frozen=True)
class TrackingFrame:
    """追跡に渡すフレーム 1 枚。

    t      フレームの時刻 (CapturedFrame.t)
    gray   NV12 の Y 面を TRACKING_SCALE 倍に縮小したグレースケールの画像 (高さ x 幅 の uint8)。作った後は書き換えない
    """

    t: float
    gray: np.ndarray


@dataclass(frozen=True)
class NormalizedBox:
    """画像の幅・高さを 1 とした割合の box (0〜1)。left < right、top < bottom。

    left     左端
    top      上端
    right    右端
    bottom   下端
    """

    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class TrackResult:
    """1 フレームの追跡の結果。

    box     そのフレームでの位置 (見失ったときも、最も確からしい位置)
    score   確信度 (NCC では相関の最大値。-1〜1)
    found   見えていると判定したら True (NCC では score >= threshold)
    """

    box: NormalizedBox
    score: float
    found: bool


class BoxTracker(Protocol):
    """1 つの対象を追う部品。対象ごとに 1 つ作り、start の後に、時刻の古い順のフレームで update を呼ぶ。

    1 つのスレッドから使う (surface.box_tracking_state.BoxTrackingState がロックの中で呼ぶ)。
    """

    def start(self, frame: TrackingFrame, box: NormalizedBox) -> None:
        """frame の中の box の位置で追跡を始める (前の追跡の状態は捨てる)。box が画像に収まらなければ ValueError。"""
        ...

    def update(self, frame: TrackingFrame) -> TrackResult:
        """次のフレーム frame での位置と確信度を返す。start の前に呼んだら RuntimeError。"""
        ...


def tracking_frame_from_captured(
    frame: CapturedFrame, scale: float = TRACKING_SCALE
) -> TrackingFrame:
    """frame を TrackingFrame にする。

    Y 面は NV12 の先頭の (高さ) 行 (frames.frame_buffer.frame_size の高さ)。これを幅 round(幅 × scale)、
    高さ round(高さ × scale) に cv2.INTER_AREA で縮小する。scale が 1 なら縮小せず Y 面のコピーを使う。
    """
    width, height = frame_size(frame)
    y_plane = frame.nv12[:height]
    if scale == 1:
        gray = y_plane.copy()
    else:
        gray = cv2.resize(
            y_plane,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return TrackingFrame(t=frame.t, gray=gray)


@dataclass(frozen=True)
class _PixelRect:
    """追跡に使う画像の中の範囲 (ピクセル)。x と y は左上、width と height は幅と高さ。"""

    x: int
    y: int
    width: int
    height: int


def _normalized(rect: _PixelRect, image_width: int, image_height: int) -> NormalizedBox:
    """rect を、画像の幅・高さを 1 とした割合の NormalizedBox にする。"""
    return NormalizedBox(
        left=rect.x / image_width,
        top=rect.y / image_height,
        right=(rect.x + rect.width) / image_width,
        bottom=(rect.y + rect.height) / image_height,
    )


def _crop(gray: np.ndarray, rect: _PixelRect) -> np.ndarray:
    """gray の rect の範囲のコピーを返す。"""
    return gray[rect.y : rect.y + rect.height, rect.x : rect.x + rect.width].copy()


def _is_uniform(template: np.ndarray) -> bool:
    """template の画素の値がすべて同じ (分散が 0) なら True。"""
    return int(template.min()) == int(template.max())


class NccBoxTracker:
    """BoxTracker の実装。NCC のテンプレートマッチング (モジュールの docstring)。

    margin             探す窓の余白 (box の長い辺の何倍か)
    threshold          相関の最大値がこれ未満なら見失い (found = False)
    recent_threshold   相関がこれ以上なら、その位置の見た目を直近の見本にする
    """

    def __init__(
        self,
        margin: float = DEFAULT_SEARCH_MARGIN,
        threshold: float = DEFAULT_LOST_THRESHOLD,
        recent_threshold: float = DEFAULT_RECENT_THRESHOLD,
    ) -> None:
        """設定を持つ。追跡の状態は start で作る。"""
        self.margin = margin
        self.threshold = threshold
        self.recent_threshold = recent_threshold
        self._first_template: np.ndarray | None = None
        self._recent_template: np.ndarray | None = None
        # 最後に見えていた位置と、前回の移動量 (ピクセル)
        self._rect: _PixelRect | None = None
        self._velocity = (0, 0)

    def start(self, frame: TrackingFrame, box: NormalizedBox) -> None:
        """frame.gray の中の box の範囲を最初の見本にし、直近の見本と移動量を消す。

        範囲のピクセルは、左端・上端を round(left × 幅)・round(top × 高さ)、右端・下端を round(right × 幅)・
        round(bottom × 高さ) とする。幅か高さが MIN_TEMPLATE_SIZE 未満なら MIN_TEMPLATE_SIZE に広げ、画像の外に
        出る分は内側にずらす。box の座標が 0〜1 の外か、left >= right か top >= bottom か、画像が MIN_TEMPLATE_SIZE
        より小さければ ValueError。
        """
        values = (box.left, box.top, box.right, box.bottom)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            raise ValueError(f"box must be inside 0..1: {box}")
        if box.left >= box.right or box.top >= box.bottom:
            raise ValueError(f"box must have an area: {box}")
        image_height, image_width = frame.gray.shape[:2]
        if image_width < MIN_TEMPLATE_SIZE or image_height < MIN_TEMPLATE_SIZE:
            raise ValueError(
                f"image is smaller than {MIN_TEMPLATE_SIZE} pixels: "
                f"{image_width}x{image_height}"
            )
        left = round(box.left * image_width)
        top = round(box.top * image_height)
        width = max(round(box.right * image_width) - left, MIN_TEMPLATE_SIZE)
        height = max(round(box.bottom * image_height) - top, MIN_TEMPLATE_SIZE)
        width = min(width, image_width)
        height = min(height, image_height)
        # 画像の外に出る分は内側にずらす
        rect = _PixelRect(
            x=min(max(left, 0), image_width - width),
            y=min(max(top, 0), image_height - height),
            width=width,
            height=height,
        )
        self._first_template = _crop(frame.gray, rect)
        self._recent_template = None
        self._rect = rect
        self._velocity = (0, 0)

    def update(self, frame: TrackingFrame) -> TrackResult:
        """frame.gray で見本を探し、結果を返す (モジュールの docstring の update)。

        - 窓は画像の中に切り詰める。窓が見本より小さければ、位置を変えずに score 0.0・found False を返す
        - 画素の値がすべて同じ見本は照合に使わない。使える見本が無ければ、位置を変えずに score 0.0・found False を返す
        - 相関が数でない (nan など) 位置は 0.0 とみなす
        - 返す box は、最大の相関の位置にある見本と同じ大きさの範囲 (画像の中に収まる)
        - found なら移動量を (今回の中心 − 前回の中心) にして、今回の位置を次の前回の位置にする。見失ったら移動量を
          0 にし、前回の位置は最後に見えていた位置のままにする
        - 相関が recent_threshold 以上なら、その範囲の見た目を直近の見本にする
        start の前に呼んだら RuntimeError。
        """
        if self._first_template is None or self._rect is None:
            raise RuntimeError("NccBoxTracker.update must be called after start")
        gray = frame.gray
        image_height, image_width = gray.shape[:2]
        rect = self._rect
        window = self._search_window(rect, image_width, image_height)
        if window.width < rect.width or window.height < rect.height:
            self._velocity = (0, 0)
            return TrackResult(_normalized(rect, image_width, image_height), 0.0, False)
        window_image = gray[
            window.y : window.y + window.height, window.x : window.x + window.width
        ]
        match = self._best_match(window_image)
        if match is None:
            self._velocity = (0, 0)
            return TrackResult(_normalized(rect, image_width, image_height), 0.0, False)
        score, (offset_x, offset_y) = match
        found_rect = _PixelRect(
            x=window.x + offset_x,
            y=window.y + offset_y,
            width=rect.width,
            height=rect.height,
        )
        found = score >= self.threshold
        if found:
            # 大きさは変えないので、中心の移動量は左上の移動量と同じ
            self._velocity = (found_rect.x - rect.x, found_rect.y - rect.y)
            self._rect = found_rect
        else:
            self._velocity = (0, 0)
        if score >= self.recent_threshold:
            self._recent_template = _crop(gray, found_rect)
        return TrackResult(
            _normalized(found_rect, image_width, image_height), score, found
        )

    def _search_window(
        self, rect: _PixelRect, image_width: int, image_height: int
    ) -> _PixelRect:
        """前回の位置 rect に前回の移動量を足した位置の周りに、margin × (長い辺) の余白を足した窓を、画像の中に
        切り詰めて返す。"""
        margin_px = math.ceil(self.margin * max(rect.width, rect.height))
        center_x = rect.x + self._velocity[0]
        center_y = rect.y + self._velocity[1]
        left = max(center_x - margin_px, 0)
        top = max(center_y - margin_px, 0)
        right = min(center_x + rect.width + margin_px, image_width)
        bottom = min(center_y + rect.height + margin_px, image_height)
        return _PixelRect(
            x=left, y=top, width=max(right - left, 0), height=max(bottom - top, 0)
        )

    def _best_match(
        self, window_image: np.ndarray
    ) -> tuple[float, tuple[int, int]] | None:
        """見本ごとに窓の中の相関を計算し、(最大の相関, 窓の中のその位置の左上) を返す。数でない相関は 0.0 とみなす。

        画素の値がすべて同じ見本は飛ばす。使える見本が無ければ None。
        """
        best: tuple[float, tuple[int, int]] | None = None
        for template in (self._first_template, self._recent_template):
            if template is None or _is_uniform(template):
                continue
            correlation = cv2.matchTemplate(
                window_image, template, cv2.TM_CCOEFF_NORMED
            )
            correlation = np.nan_to_num(correlation, nan=0.0, posinf=0.0, neginf=0.0)
            _, max_value, _, max_location = cv2.minMaxLoc(correlation)
            if best is None or max_value > best[0]:
                best = (
                    float(max_value),
                    (int(max_location[0]), int(max_location[1])),
                )
        return best
