"""実行を始めてからの経過秒 t を返す時計。

記録の時刻は、すべてこの時計の t (秒。小数第 3 位 = ミリ秒の精度) で書く。
t は time.monotonic() の値と起点 (実行を始めたときの time.monotonic() の値) の差で、壁時計の変更の影響を受けない。
テストでは monotonic に偽の時計を渡し、時間を進める。
"""

import time
from collections.abc import Callable

T_DECIMALS = 3  # t の小数の桁数 (ミリ秒の精度)


def round_to_milliseconds(seconds: float) -> float:
    """seconds を小数第 3 位 (ミリ秒) に丸めた値 round(seconds, T_DECIMALS) を返す。"""
    raise NotImplementedError


def t_to_milliseconds(t: float) -> int:
    """t (秒) をミリ秒の整数 round(t * 1000) にする。フレームの画像のファイル名に使う。"""
    raise NotImplementedError


class RunClock:
    """実行の経過秒 t を返す時計。状態を変えないため、複数のスレッドから呼んでよい。

    monotonic   単調に増える秒を返す関数 (既定 time.monotonic)。キャプチャーのフレームの時刻と同じ時計を使う
    origin      t = 0 とする monotonic の値。None なら、作るときに monotonic() を 1 回呼んだ値にする
    """

    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        origin: float | None = None,
    ) -> None:
        """monotonic と起点を持つ。origin が None なら、ここで monotonic() を 1 回呼んで起点にする。"""
        raise NotImplementedError

    @property
    def origin(self) -> float:
        """t = 0 とする monotonic の値。"""
        raise NotImplementedError

    def now(self) -> float:
        """今の t = round_to_milliseconds(monotonic() - origin) を返す。"""
        raise NotImplementedError

    def t_of(self, monotonic_value: float) -> float:
        """monotonic の値 (キャプチャーのフレームの時刻など) を t = round_to_milliseconds(monotonic_value - origin) に直す。

        起点より前の値なら負の t を返す。
        """
        raise NotImplementedError


def frozen_clock(t: float) -> RunClock:
    """いつも t を返す時計 (RunClock(monotonic=常に t を返す関数, origin=0.0)) を返す。

    middle-once と deep-once で、保存済みの実行の最後の時刻に時計を固定するために使う。
    """
    raise NotImplementedError
