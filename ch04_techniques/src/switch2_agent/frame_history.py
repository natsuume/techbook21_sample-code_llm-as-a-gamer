"""処理レートに間引いたフレームを、時刻・縮小したグレー画像・鮮明度とともに一定時間分だけ持つ。

Capture のフレーム読み取りスレッドが add し、Pipeline のスレッドが between と latest で読むため、
読み書きは 1 つのロックの中で行う。
"""

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TimedFrame:
    """時刻つきのフレーム 1 枚。

    time         フレームを受け取った時刻 (time.monotonic の値、秒)
    nv12         NV12 のフレーム (高さ * 3 / 2 行 x 幅)
    gray_small   nv12 の Y 面を縮小したグレー画像 (image_metrics.to_small_gray)
    sharpness    gray_small の鮮明度 (image_metrics.sharpness)
    """

    time: float
    nv12: np.ndarray
    gray_small: np.ndarray
    sharpness: float


class FrameHistory:
    """直近 max_age_s 秒分のフレームを古い順に持つ。

    フレームの time は add の順に増えていく前提とする (Capture が受け取った順に足す)。

    属性:
        max_age_s   持つ時間の長さ (秒)
        lock        frames の読み書きを守るロック
        frames      持っているフレーム (古い順)
    """

    def __init__(self, max_age_s: float):
        """max_age_s 秒分のフレームを持つ、空の履歴を作る。"""
        self.max_age_s = max_age_s
        self.lock = threading.Lock()
        self.frames: deque[TimedFrame] = deque()

    def add(self, frame: TimedFrame) -> None:
        """frame を末尾に足し、time が frame.time - max_age_s より古い (小さい) フレームを先頭から捨てる。

        time がちょうど frame.time - max_age_s のフレームは残す。
        """
        oldest_kept = frame.time - self.max_age_s
        with self.lock:
            self.frames.append(frame)
            while self.frames[0].time < oldest_kept:
                self.frames.popleft()

    def between(self, start: float, end: float) -> list[TimedFrame]:
        """start <= time <= end のフレームを古い順に返す (両端を含む)。無ければ空のリストを返す。"""
        with self.lock:
            return [frame for frame in self.frames if start <= frame.time <= end]

    def latest(self) -> TimedFrame | None:
        """最も新しいフレームを返す。まだ無ければ None を返す。"""
        with self.lock:
            return self.frames[-1] if self.frames else None
