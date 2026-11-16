"""一定間隔のフレームの画面のハッシュの並びから、画面の停滞の値を計算する (ScreenProgressTracker)。I/O を扱わない。

フレームを 1 枚ずつ時刻の順に渡し (add_frame は画像、add_hash はハッシュ)、そのたびに最新のフレームの値を返す。
    static_s     画面が変わっていない秒数。最新のフレームのハッシュに近いハッシュが、途切れずに続いている時間
                 (最新のフレームの時刻 − 続いている最初のフレームの時刻)。最新のフレームが直前のフレームに
                 近くなければ 0。近さは、隣どうしではなく、どれも最新のフレームのハッシュと比べる
    no_novel_s   新しい画面が出ていない秒数。最新のフレームの時刻 − 最後に新しい画面が出た時刻。
                 新しい画面は、それより前の直近 window_s 秒 (時刻の差が window_s 以下) のどのフレームのハッシュにも
                 近くないフレーム。最初のフレームは新しい画面とする
窓より古いフレーム (最新のフレームとの時刻の差が window_s を超えるもの) のハッシュは捨て、持つハッシュを窓の分だけに
する。static_s も持っているハッシュだけで数えるため、画面が window_s 秒より長く変わらなくても、static_s は
window_s を超えない。no_novel_s は時刻だけで数えるため、window_s を超えることがある。
左右に動きながら同じ範囲にとどまる停滞では、static_s は 0 に戻り続けるが、過去に見た画面に戻っているため
no_novel_s は増え続ける。
"""

import math
from collections import deque

import numpy as np

from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.screen_hash import NEAR_DISTANCE_MAX, is_near, screen_hash
from switch2_layers.run_clock import round_to_milliseconds

# --progress-window の既定値 (秒)
DEFAULT_PROGRESS_WINDOW_S = 300.0


class ScreenProgressTracker:
    """フレームのハッシュを窓の分だけ持ち、画面の停滞の値を計算する。1 つのスレッドからだけ呼ぶ。

    window_s       新しい画面かを決めるときに見る範囲 (直近の秒数。0 より大きい有限の値。そうでなければ ValueError)
    max_distance   2 つのハッシュを近いとみなすハミング距離の上限 (既定 screen_hash.NEAR_DISTANCE_MAX)
    """

    def __init__(self, window_s: float, max_distance: int = NEAR_DISTANCE_MAX) -> None:
        """フレームを持たない状態で作る。window_s が 0 より大きい有限の値でなければ ValueError。"""
        if not (math.isfinite(window_s) and window_s > 0):
            raise ValueError(
                f"window_s must be a finite number more than 0: {window_s}"
            )
        self.window_s = window_s
        self.max_distance = max_distance
        # 窓の中のフレームの (時刻, ハッシュ)。古い順
        self._kept: deque[tuple[float, int]] = deque()
        self._last_novel_t: float | None = None

    @property
    def kept_count(self) -> int:
        """持っているフレームのハッシュの数 (最新のフレームを含む)。"""
        return len(self._kept)

    def add_frame(self, t: float, image: np.ndarray) -> ProgressState:
        """時刻 t のフレームの画像 image を足し、最新のフレームの値を返す。

        add_hash(t, screen_hash.screen_hash(image)) と同じ。
        """
        return self.add_hash(t, screen_hash(image))

    def add_hash(self, t: float, frame_hash: int) -> ProgressState:
        """時刻 t のフレームのハッシュ frame_hash を足し、最新のフレームの値を返す。

        1. t が前に足したフレームの時刻以下なら ValueError (何も変えない)
        2. t との時刻の差 (run_clock.round_to_milliseconds で丸めた値) が window_s を超えるフレームのハッシュを捨てる
        3. 残ったどのハッシュにも近くなければ (残りが無いときを含む)、新しい画面とし、最後に新しい画面が出た時刻を t にする
        4. このフレームのハッシュを足す
        5. static_s: 直前のフレームから古い方へ、このフレームのハッシュに近いハッシュが続く間たどり、続いた最初の
           フレームの時刻を t から引く。直前のフレームが近くない (または無い) なら 0
        戻り値は ProgressState(t, static_s, no_novel_s = t − 最後に新しい画面が出た時刻, window_s) で、
        t・static_s・no_novel_s は run_clock.round_to_milliseconds で小数第 3 位に丸める。
        """
        kept = self._kept
        if kept and t <= kept[-1][0]:
            raise ValueError(
                f"frame time {t} is not after the previous frame {kept[-1][0]}"
            )
        while kept and round_to_milliseconds(t - kept[0][0]) > self.window_s:
            kept.popleft()
        if not any(self._near(frame_hash, kept_hash) for _, kept_hash in kept):
            self._last_novel_t = t
        static_start_t = t
        for kept_t, kept_hash in reversed(kept):
            if not self._near(frame_hash, kept_hash):
                break
            static_start_t = kept_t
        kept.append((t, frame_hash))
        last_novel_t = t if self._last_novel_t is None else self._last_novel_t
        return ProgressState(
            t=round_to_milliseconds(t),
            static_s=round_to_milliseconds(t - static_start_t),
            no_novel_s=round_to_milliseconds(t - last_novel_t),
            window_s=self.window_s,
        )

    def _near(self, first: int, second: int) -> bool:
        """2 つのハッシュが近い (ハミング距離が max_distance 以下) なら True。"""
        return is_near(first, second, self.max_distance)
