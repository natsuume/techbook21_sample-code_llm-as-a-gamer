"""キャプチャーから受け取ったフレームを、直近の一定時間分だけメモリーに持つ。

hardware.capture_source.CaptureSource のスレッドが add で足し、surface のスレッドが frames と latest で読むため、
読み書きは 1 つのロックの中で行う。
"""

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

# 持つ時間の長さの既定値 (秒)。surface が使う直近 2 秒より少し長くとる
DEFAULT_BUFFER_SECONDS = 3.0


@dataclass(frozen=True)
class CapturedFrame:
    """時刻つきのフレーム 1 枚。

    t      実行の経過秒 (run_clock.RunClock.t_of(キャプチャーがフレームを残した time.monotonic の値))
    nv12   NV12 のフレーム ((高さ * 3 / 2) 行 x 幅 の uint8 の配列)。作った後は書き換えない
    """

    t: float
    nv12: np.ndarray


def frame_size(frame: CapturedFrame) -> tuple[int, int]:
    """frame の (幅, 高さ) を返す。高さは NV12 の行数 * 2 / 3。"""
    height = frame.nv12.shape[0] * 2 // 3
    return (int(frame.nv12.shape[1]), int(height))


class FrameBuffer:
    """直近 max_age_s 秒分のフレームを、t の古い順に持つ。"""

    def __init__(self, max_age_s: float = DEFAULT_BUFFER_SECONDS) -> None:
        """空のバッファーを作る。"""
        self.max_age_s = max_age_s
        self._lock = threading.Lock()
        self._frames: deque[CapturedFrame] = deque()

    def add(self, frame: CapturedFrame) -> bool:
        """frame を末尾に足し、t が frame.t - max_age_s より小さいフレームを先頭から捨てる。足したら True を返す。

        frame.t が持っている最新のフレームの t 以下なら、足さずに False を返す (同じフレームを 2 回持たない)。
        """
        with self._lock:
            if self._frames and frame.t <= self._frames[-1].t:
                return False
            self._frames.append(frame)
            oldest_kept = frame.t - self.max_age_s
            while self._frames[0].t < oldest_kept:
                self._frames.popleft()
            return True

    def frames(self) -> list[CapturedFrame]:
        """持っているフレームを古い順に並べたリスト (コピー) を返す。"""
        with self._lock:
            return list(self._frames)

    def latest(self) -> CapturedFrame | None:
        """最も新しいフレームを返す。まだ無ければ None。"""
        with self._lock:
            return self._frames[-1] if self._frames else None
