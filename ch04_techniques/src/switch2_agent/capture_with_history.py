"""第2章の Capture に、処理レートに間引いたフレームの履歴と、鮮明度の計算を足す。

残したフレームごとに、縮小したグレー画像と鮮明度を計算して TimedFrame にし、FrameHistory に足す。
on_frame があれば、同じ TimedFrame を渡して呼ぶ (run では意味のあるフレームの判定に使う)。
1 フレーム分の変換は純粋な関数 make_timed_frame に分け、テストはこの関数で行う
(CaptureWithHistory 自体は ffmpeg.exe を起動するため、テストしない)。
"""

import time
from collections.abc import Callable

from switch2_remote.capture import DEVICE_HEIGHT, DEVICE_WIDTH, Capture

from switch2_agent.frame_history import FrameHistory, TimedFrame


def make_timed_frame(
    raw: bytes, now: float, width: int = DEVICE_WIDTH, height: int = DEVICE_HEIGHT
) -> TimedFrame:
    """NV12 の生データ 1 フレーム分 (width * height * 3 // 2 バイト) を、時刻 now の TimedFrame にする。

    nv12 は raw を (height * 3 // 2, width) の uint8 の配列にしたもの、gray_small は
    image_metrics.to_small_gray(nv12, height)、sharpness は image_metrics.sharpness(gray_small)。
    raw の長さが 1 フレーム分でなければ ValueError にする。
    """
    raise NotImplementedError


class CaptureWithHistory(Capture):
    """Capture の読み取りスレッドで、残したフレームを TimedFrame にして history に足す。

    属性 (Capture の属性に加えて):
        history    残したフレームを足す FrameHistory
        on_frame   TimedFrame を作るたびに呼ぶ関数。None なら呼ばない
        clock      フレームの時刻に使う時計 (既定 time.monotonic)
    """

    history: FrameHistory
    on_frame: Callable[[TimedFrame], None] | None
    clock: Callable[[], float]

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
        raise NotImplementedError

    def _read_frames(self) -> None:
        """Capture._read_frames を置き換える。フレームの読み取りスレッドの本体。

        親と同じく、受信するたびにロックの中で frame_no を数え、next_keep で残すかを判定する。
        残すフレームは、ロックの中で clock() の時刻と NV12 の配列で latest_frame を更新し、
        ロックの外で make_timed_frame で TimedFrame にして history.add に渡し、on_frame があれば呼ぶ。
        on_frame の例外 (Exception) はスレッドを止めないよう捕まえ、標準エラー出力に 1 行書いて続ける。
        出力が終わったら、親と同じくロックの中で ended を True にする。
        """
        raise NotImplementedError
