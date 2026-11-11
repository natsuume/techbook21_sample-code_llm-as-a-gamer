"""鮮明度の谷で場面を区切り、各場面で最も鮮明なフレームを「意味のあるフレーム」として残す。

場面の切り替わり (暗転、フェード、カメラの急な移動) では画像がぼけて鮮明度が下がる。
鮮明度の谷を場面の切り替わり、谷と谷の間の山 (最も鮮明なフレーム) をその場面の代表とみなす。
ストリームで扱うため、今の区間の山は次の谷が確定するまで返さない (「次の谷を待つ」方式)。
直前に残したフレームと似ている代表は、同じ場面の繰り返しとみなして返さない。
"""

import threading
from collections import deque
from typing import Literal

from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.base import FrameSimilarity

# 似ているとみなす差分 (image_metrics.mean_abs_diff、0〜255) の上限の既定値
DEFAULT_SIMILARITY_THRESHOLD = 0.15
# 谷とみなす鮮明度の比 (山の鮮明度に対する比) の既定値
DEFAULT_VALLEY_RATIO = 0.5
# MeaningfulFrameStore が持つフレームの数の既定値
DEFAULT_STORE_CAPACITY = 50

ValleyPhase = Literal["rising", "falling"]


class MeanAbsDiffSimilarity:
    """frame_selection.base.FrameSimilarity の実装。縮小したグレー画像の差分で判定する。

    threshold   似ているとみなす差分の上限 (この値未満なら似ている)
    """

    threshold: float

    def __init__(self, threshold: float = DEFAULT_SIMILARITY_THRESHOLD):
        """threshold を持つ判定を作る。"""
        raise NotImplementedError

    def is_similar(self, a: TimedFrame, b: TimedFrame) -> bool:
        """image_metrics.mean_abs_diff(a.gray_small, b.gray_small) < threshold なら True を返す。"""
        raise NotImplementedError


class SharpnessValleyDetector:
    """frame_selection.base.MeaningfulFrameDetector の実装。鮮明度の谷で場面を区切る。

    状態:
        phase       "rising" (山に向かって上がっている) か "falling" (谷に向かって下がっている)
        peak        今の区間で鮮明度が最大のフレーム。最初のフレームを受け取るまでは None
        valley      下降中に鮮明度が最小のフレーム。下降に入るまでは None
        last_kept   直前に返した (残した) フレーム。まだ無ければ None

    鮮明度の浅い揺れは谷にならない。谷になるのは、山の鮮明度 * valley_ratio 以下まで下がったときだけ。
    谷が確定するのは、谷の鮮明度の 1 / valley_ratio 倍以上まで回復したとき。
    """

    valley_ratio: float
    similarity: FrameSimilarity
    phase: ValleyPhase
    peak: TimedFrame | None
    valley: TimedFrame | None
    last_kept: TimedFrame | None

    def __init__(
        self,
        valley_ratio: float = DEFAULT_VALLEY_RATIO,
        similarity: FrameSimilarity | None = None,
    ):
        """判定を作る。similarity が None なら MeanAbsDiffSimilarity() を使う。

        0 < valley_ratio < 1 でなければ ValueError にする。
        初期状態は phase = "rising"、peak・valley・last_kept はすべて None。
        """
        raise NotImplementedError

    def feed(self, frame: TimedFrame) -> TimedFrame | None:
        """frame を受け取り、確定した意味のあるフレームを返す。確定しなければ None を返す。

        - 最初のフレーム (peak が None): peak = frame、phase = "rising" にして None を返す
        - rising:
            - frame.sharpness >= peak.sharpness なら peak = frame
            - そうでなく frame.sharpness <= peak.sharpness * valley_ratio なら、
              phase = "falling"、valley = frame にする
            - どちらでも None を返す
        - falling:
            - frame.sharpness <= valley.sharpness なら valley = frame にして None を返す
            - frame.sharpness * valley_ratio >= valley.sharpness かつ frame.sharpness > valley.sharpness
              (谷から回復した) なら谷を確定する: candidate = peak とし、新しい区間を peak = frame・
              phase = "rising" で始める (valley は None に戻す)。last_kept が None か、
              similarity.is_similar(candidate, last_kept) が偽なら、last_kept = candidate にして
              candidate を返す。似ていれば None を返す
            - それ以外は None を返す
        """
        raise NotImplementedError


class MeaningfulFrameStore:
    """確定した意味のあるフレームを、新しいものから capacity 枚まで持つ。

    判定は Capture のスレッドで行い、Pipeline のスレッドが読むため、読み書きは 1 つのロックの中で行う。
    フレームの time は add の順に増えていく前提とする。
    """

    capacity: int
    lock: threading.Lock
    frames: deque[TimedFrame]

    def __init__(self, capacity: int = DEFAULT_STORE_CAPACITY):
        """capacity 枚まで持つ、空の保管を作る。"""
        raise NotImplementedError

    def add(self, frame: TimedFrame) -> None:
        """frame を末尾に足す。capacity を超えたら最も古いフレームを捨てる。"""
        raise NotImplementedError

    def latest_before(self, time: float, count: int) -> list[TimedFrame]:
        """frame.time < time のフレームのうち、新しい count 枚を古い順に返す。

        該当が count 枚より少なければ、あるだけを返す。count <= 0 なら空のリストを返す。
        """
        raise NotImplementedError
