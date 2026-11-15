"""switch2_remote.capture.Capture から最新のフレームを受け取り、FrameBuffer と FrameStore に渡す。

Capture は、間引いたフレームのうち最新の 1 枚とその時刻 (time.monotonic) を latest_frame に持つ。
Capture の private なメソッドは上書きせず、公開の属性 (lock・latest_frame・ended) を短い間隔で読み (ポーリング)、
時刻が前回より新しければ新しいフレームとして扱う。読む部分は LatestFrameReader の Protocol にし、
テストでは偽物を渡す。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from switch2_remote.capture import Capture

from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.run_clock import RunClock

POLL_INTERVAL_S = 0.01  # 最新のフレームを確かめる間隔 (秒)。Capture の処理レート (10 fps) より十分短くとる


@dataclass(frozen=True)
class LatestCapture:
    """キャプチャーが持っている最新のフレーム。

    monotonic_time   フレームを残した時刻 (time.monotonic の値)
    nv12             NV12 のフレーム
    """

    monotonic_time: float
    nv12: np.ndarray


class LatestFrameReader(Protocol):
    """最新のフレームの読み取り元。run では SwitchCaptureReader を使う。"""

    def read_latest(self) -> LatestCapture | None:
        """最新のフレームを返す。まだ無ければ None。"""
        ...

    def is_ended(self) -> bool:
        """キャプチャーの出力が終わっていれば True。"""
        ...


class SwitchCaptureReader:
    """switch2_remote.capture.Capture の公開の属性から最新のフレームを読む LatestFrameReader。"""

    def __init__(self, capture: Capture) -> None:
        """capture を持つ。capture の起動と終了は呼び出し側が行う。"""
        raise NotImplementedError

    def read_latest(self) -> LatestCapture | None:
        """capture.lock の中で capture.latest_frame.latest_time() と latest() を読み、LatestCapture にして返す。

        どちらかが None (まだフレームが無い) なら None を返す。
        """
        raise NotImplementedError

    def is_ended(self) -> bool:
        """capture.lock の中で capture.ended を読んで返す。"""
        raise NotImplementedError


class CaptureSource:
    """reader をポーリングし、新しいフレームを frame_buffer と frame_store に渡す。

    reader          最新のフレームの読み取り元
    clock           フレームの時刻 (monotonic) を t に直す時計 (RunClock.t_of)
    frame_buffer    surface 用に直近のフレームを持つバッファー
    frame_store     一定間隔の保存 (FrameStore.offer_interval)
    on_frame        新しいフレームを渡し終えた後に、そのフレームを受け取る関数。None (既定) なら呼ばない
    poll_interval_s ポーリングの間隔 (秒)
    start_thread    True (既定) なら poll_interval_s ごとに poll_once を呼ぶスレッド (daemon) を起動する。
                    False ならテストから poll_once を直接呼ぶ
    """

    def __init__(
        self,
        reader: LatestFrameReader,
        clock: RunClock,
        frame_buffer: FrameBuffer,
        frame_store: FrameStore,
        on_frame: Callable[[CapturedFrame], None] | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        start_thread: bool = True,
    ) -> None:
        """属性を持ち、start_thread が True ならポーリングのスレッドを起動する。"""
        raise NotImplementedError

    def poll_once(self) -> CapturedFrame | None:
        """reader.read_latest() を 1 回読み、前回渡したフレームより monotonic_time が新しければ渡す。

        新しいフレームは CapturedFrame(t=clock.t_of(monotonic_time), nv12) にし、frame_buffer.add →
        frame_store.offer_interval → on_frame の順に渡して、その CapturedFrame を返す。
        新しいフレームが無ければ (まだ無い・同じ時刻のまま) 何もせず None を返す。
        """
        raise NotImplementedError

    def latest_t(self) -> float | None:
        """最後に渡したフレームの t を返す。まだ渡していなければ None。"""
        raise NotImplementedError

    def close(self) -> None:
        """ポーリングのスレッドを止め、終わるのを待つ。スレッドが無ければ何もしない。"""
        raise NotImplementedError
