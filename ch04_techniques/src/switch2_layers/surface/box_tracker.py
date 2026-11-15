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
            位置の見た目。最後に更新したもの) の 2 つ。相関の最大値が threshold 未満なら見失いとする
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from switch2_layers.frames.frame_buffer import CapturedFrame

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
    raise NotImplementedError


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
        raise NotImplementedError

    def start(self, frame: TrackingFrame, box: NormalizedBox) -> None:
        """frame.gray の中の box の範囲を最初の見本にし、直近の見本と移動量を消す。

        範囲のピクセルは、左端・上端を round(left × 幅)・round(top × 高さ)、右端・下端を round(right × 幅)・
        round(bottom × 高さ) とする。幅か高さが MIN_TEMPLATE_SIZE 未満なら MIN_TEMPLATE_SIZE に広げ、画像の外に
        出る分は内側にずらす。box の座標が 0〜1 の外か、left >= right か top >= bottom か、画像が MIN_TEMPLATE_SIZE
        より小さければ ValueError。
        """
        raise NotImplementedError

    def update(self, frame: TrackingFrame) -> TrackResult:
        """frame.gray で見本を探し、結果を返す (モジュールの docstring の update)。

        - 窓は画像の中に切り詰める。窓が見本より小さければ、位置を変えずに score 0.0・found False を返す
        - 相関が数でない (nan など) 位置は 0.0 とみなす
        - 返す box は、最大の相関の位置にある見本と同じ大きさの範囲 (画像の中に収まる)
        - found なら移動量を (今回の中心 − 前回の中心) にし、見失ったら 0 にする
        - 相関が recent_threshold 以上なら、その範囲の見た目を直近の見本にする
        start の前に呼んだら RuntimeError。
        """
        raise NotImplementedError
