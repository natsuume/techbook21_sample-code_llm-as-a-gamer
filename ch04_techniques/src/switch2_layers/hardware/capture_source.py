"""switch2_remote.capture.Capture から最新のフレームを受け取り、FrameBuffer と FrameStore に渡す。

Capture は、間引いたフレームのうち最新の 1 枚とその時刻 (time.monotonic) を latest_frame に持つ。
Capture の private なメソッドは上書きせず、公開の属性 (lock・latest_frame・ended) を短い間隔で読み (ポーリング)、
時刻が前回より新しければ新しいフレームとして扱う。読む部分は LatestFrameReader の Protocol にし、
テストでは偽物を渡す。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from switch2_remote.capture import Capture

from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.run_clock import RunClock

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

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
        self.capture = capture

    def read_latest(self) -> LatestCapture | None:
        """capture.lock の中で capture.latest_frame.latest_time() と latest() を読み、LatestCapture にして返す。

        どちらかが None (まだフレームが無い) なら None を返す。
        """
        with self.capture.lock:
            frame_time = self.capture.latest_frame.latest_time()
            nv12 = self.capture.latest_frame.latest()
        if frame_time is None or nv12 is None:
            return None
        return LatestCapture(monotonic_time=frame_time, nv12=nv12)

    def is_ended(self) -> bool:
        """capture.lock の中で capture.ended を読んで返す。"""
        with self.capture.lock:
            return bool(self.capture.ended)


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
        self.reader = reader
        self.clock = clock
        self.frame_buffer = frame_buffer
        self.frame_store = frame_store
        self.on_frame = on_frame
        self.poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        self._last_monotonic_time: float | None = None
        self._latest_t: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._run, name="capture-source", daemon=True
            )
            self._thread.start()

    def poll_once(self) -> CapturedFrame | None:
        """reader.read_latest() を 1 回読み、前回渡したフレームより monotonic_time が新しければ渡す。

        新しいフレームは CapturedFrame(t=clock.t_of(monotonic_time), nv12) にし、frame_buffer.add →
        frame_store.offer_interval → on_frame の順に渡して、その CapturedFrame を返す。
        新しいフレームが無ければ (まだ無い・同じ時刻のまま) 何もせず None を返す。
        t が負のフレーム (実行の起点より前にキャプチャーが残したフレーム) は捨てる: frame_buffer・frame_store・
        on_frame に渡さず、latest_t も変えずに None を返す (同じフレームを次の呼び出しで読み直しても捨てる)。
        負の t は画像のファイル名にできず、surface の要求や保存を止めてしまうため。
        """
        latest = self.reader.read_latest()
        if latest is None:
            return None
        with self._lock:
            if (
                self._last_monotonic_time is not None
                and latest.monotonic_time <= self._last_monotonic_time
            ):
                return None
            self._last_monotonic_time = latest.monotonic_time
        frame = CapturedFrame(
            t=self.clock.t_of(latest.monotonic_time), nv12=latest.nv12
        )
        if frame.t < 0:
            return None
        self.frame_buffer.add(frame)
        self.frame_store.offer_interval(frame)
        with self._lock:
            self._latest_t = frame.t
        if self.on_frame is not None:
            self.on_frame(frame)
        return frame

    def latest_t(self) -> float | None:
        """最後に渡したフレームの t を返す。まだ渡していなければ None。"""
        with self._lock:
            return self._latest_t

    def close(self) -> None:
        """ポーリングのスレッドを止め、終わるのを待つ。スレッドが無ければ何もしない。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        """close() まで、poll_interval_s ごとに poll_once を呼ぶ (ポーリングのスレッドの本体)。

        poll_once の例外 (Exception) はスレッドを止めずに logging で警告を書く。
        """
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.poll_once()
            except Exception:
                logger.warning("polling the capture failed", exc_info=True)
