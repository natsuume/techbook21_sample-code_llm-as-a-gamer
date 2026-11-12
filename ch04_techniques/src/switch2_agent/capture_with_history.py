"""第2章の Capture に、処理レートに間引いたフレームの履歴と、鮮明度の計算を足す。

残したフレームごとに、縮小したグレー画像と鮮明度を計算し、FrameHistory.add_now で時刻を付けて TimedFrame にし、
履歴に足す。on_frame があれば、同じ TimedFrame を渡して呼ぶ (run では意味のあるフレームの判定に使う)。
1 フレーム分の変換 (生データ → NV12 の配列 → 縮小したグレー画像と鮮明度) は純粋な関数 (raw_to_nv12、
image_metrics の to_small_gray・sharpness) で行い、テストは同じ変換をまとめた make_timed_frame で行う
(CaptureWithHistory 自体は ffmpeg.exe を起動するため、テストしない)。
"""

import logging
import time
from collections.abc import Callable

import numpy as np
from switch2_remote.capture import DEVICE_HEIGHT, DEVICE_WIDTH, Capture
from switch2_remote.capture_command import iter_raw_frames
from switch2_remote.frame_buffer import next_keep

from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.image_metrics import sharpness, to_small_gray

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)


def raw_to_nv12(raw: bytes, width: int, height: int) -> np.ndarray:
    """NV12 の生データ 1 フレーム分を (height * 3 // 2, width) の uint8 の配列にする (コピーしない)。

    raw の長さが width * height * 3 // 2 バイトでなければ ValueError にする。
    """
    expected = width * height * 3 // 2
    if len(raw) != expected:
        raise ValueError(f"raw frame must be {expected} bytes: {len(raw)}")
    return np.frombuffer(raw, np.uint8).reshape(height * 3 // 2, width)


def timed_frame_from_nv12(nv12: np.ndarray, now: float, height: int) -> TimedFrame:
    """NV12 の配列から、縮小したグレー画像と鮮明度を計算して時刻 now の TimedFrame にする。"""
    gray_small = to_small_gray(nv12, height)
    return TimedFrame(
        time=now,
        nv12=nv12,
        gray_small=gray_small,
        sharpness=sharpness(gray_small),
    )


def make_timed_frame(
    raw: bytes, now: float, width: int = DEVICE_WIDTH, height: int = DEVICE_HEIGHT
) -> TimedFrame:
    """NV12 の生データ 1 フレーム分 (width * height * 3 // 2 バイト) を、時刻 now の TimedFrame にする。

    nv12 は raw を (height * 3 // 2, width) の uint8 の配列にしたもの、gray_small は
    image_metrics.to_small_gray(nv12, height)、sharpness は image_metrics.sharpness(gray_small)。
    raw の長さが 1 フレーム分でなければ ValueError にする。
    """
    return timed_frame_from_nv12(raw_to_nv12(raw, width, height), now, height)


class CaptureWithHistory(Capture):
    """Capture の読み取りスレッドで、残したフレームを TimedFrame にして history に足す。

    属性 (Capture の属性に加えて):
        history    残したフレームを足す FrameHistory
        on_frame   TimedFrame を作るたびに呼ぶ関数。None なら呼ばない
        clock      フレームの時刻に使う時計 (既定 time.monotonic)
    """

    def __init__(
        self,
        ffmpeg_fps: int,
        process_fps: int,
        history: FrameHistory,
        on_frame: Callable[[TimedFrame], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        """history・on_frame・clock を属性に入れてから、Capture.__init__(ffmpeg_fps, process_fps) を呼ぶ。

        親の __init__ がフレームの読み取りスレッド (_read_frames) を起動するため、
        読み取りスレッドが使う属性は super().__init__ より前に入れる。
        """
        self.history = history
        self.on_frame = on_frame
        self.clock = clock
        super().__init__(ffmpeg_fps, process_fps)

    def _read_frames(self) -> None:
        """Capture._read_frames を置き換える。フレームの読み取りスレッドの本体。

        親と同じく、受信するたびにロックの中で frame_no を数え、next_keep で残すかを判定する。
        残すフレームは、ロックの中で clock() の時刻と NV12 の配列で latest_frame を更新し、
        ロックの外で縮小したグレー画像と鮮明度を計算して、history.add_now(clock, ...) で時刻を付けて履歴に足し、
        on_frame があれば、add_now が返した TimedFrame を渡して呼ぶ。
        時刻は add_now が履歴のロックの中で付けるため、Pipeline が clock() で now を取ってから history.between を
        呼んだとき、time が now より前のフレームは、もう履歴に入っている (FrameHistory.add_now の docstring)。
        on_frame の例外 (Exception) はスレッドを止めないよう捕まえ、logging で標準エラー出力に書いて続ける
        (`on_frame failed` の 1 行の後に、原因を追えるよう例外のトレースバックが続く)。
        出力が終わったら、親と同じくロックの中で ended を True にする。
        """
        for raw in iter_raw_frames(self.proc.stdout, self.frame_bytes):
            with self.lock:
                self.frame_no += 1
                self.accumulator, keep = next_keep(
                    self.accumulator, self.ffmpeg_fps, self.process_fps
                )
                if not keep:
                    continue
                now = self.clock()
                nv12 = raw_to_nv12(raw, DEVICE_WIDTH, DEVICE_HEIGHT)
                self.latest_frame.update(now, nv12)
            # 縮小と鮮明度の計算はロックの外で行い、その間も status や shot を待たせない
            gray_small = to_small_gray(nv12, DEVICE_HEIGHT)
            sharpness_value = sharpness(gray_small)
            frame = self.history.add_now(self.clock, nv12, gray_small, sharpness_value)
            if self.on_frame is not None:
                # 判定の失敗でフレームの読み取りを止めないため、失敗の型を問わずに捕まえる
                try:
                    self.on_frame(frame)
                except Exception:
                    logger.exception("on_frame failed")
        with self.lock:
            self.ended = True
