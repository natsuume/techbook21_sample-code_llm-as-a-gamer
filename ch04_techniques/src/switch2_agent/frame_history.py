"""処理レートに間引いたフレームを、時刻・縮小したグレー画像・鮮明度とともに一定時間分だけ持つ。

Capture のフレーム読み取りスレッドが add_now で足し、Pipeline のスレッドが between と latest で読むため、
読み書きは 1 つのロックの中で行う。
"""

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TimedFrame:
    """時刻つきのフレーム 1 枚。

    time         フレームの時刻 (time.monotonic の値、秒)。Capture では、受け取って縮小と鮮明度を計算した後、
                 履歴に足すときの時刻 (FrameHistory.add_now)
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

    フレームの time は足した順に増えていく前提とする (Capture は受け取った順に add_now で足す)。

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
        with self.lock:
            self._append_and_drop_old(frame)

    def add_now(
        self,
        clock: Callable[[], float],
        nv12: np.ndarray,
        gray_small: np.ndarray,
        sharpness: float,
    ) -> TimedFrame:
        """ロックの中で clock() の時刻を取り、その時刻の TimedFrame を作って末尾に足し、作った TimedFrame を返す。

        nv12・gray_small・sharpness は TimedFrame の同名の属性にする。古いフレームは add と同じ規則で捨てる。
        時刻を付けることと足すことを同じロックの中で行うのは、between と latest を呼ぶ側が、時刻を付けたのに
        まだ履歴に無いフレームを見ないようにするため (呼ぶ側が clock() で now を取ってから between(start, now) を
        呼んだとき、time が now より前のフレームは、もう履歴に入っている。time がちょうど now のフレームは、
        まだ履歴に無いことがあり、now を始まりに含む次の区間で読める)。
        """
        with self.lock:
            frame = TimedFrame(
                time=clock(), nv12=nv12, gray_small=gray_small, sharpness=sharpness
            )
            self._append_and_drop_old(frame)
        return frame

    def _append_and_drop_old(self, frame: TimedFrame) -> None:
        """add と add_now の共通の処理。lock の中で呼ぶ。

        frame を末尾に足し、time が frame.time - max_age_s より古い (小さい) フレームを先頭から捨てる。
        """
        oldest_kept = frame.time - self.max_age_s
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
