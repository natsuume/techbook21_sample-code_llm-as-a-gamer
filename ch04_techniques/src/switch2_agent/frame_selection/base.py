"""フレームの選び方の型。実装を差し替えられるよう、Pipeline と判定の処理はこの型だけに依存する。"""

from collections.abc import Sequence
from typing import Protocol

from switch2_agent.frame_history import TimedFrame


class RecentFrameSelector(Protocol):
    """直近の区間のフレームから、要求に載せるフレームを選ぶ。"""

    def select(self, frames: Sequence[TimedFrame], max_count: int) -> list[TimedFrame]:
        """古い順の frames から最大 max_count 枚を選び、古い順に返す。"""
        ...


class FrameSimilarity(Protocol):
    """2 枚のフレームが似ているか (同じ場面の代表として重複するか) を判定する。"""

    def is_similar(self, a: TimedFrame, b: TimedFrame) -> bool:
        """a と b が似ていれば True を返す。"""
        ...


class MeaningfulFrameDetector(Protocol):
    """フレームを 1 枚ずつ受け取り、意味のあるフレーム (場面の代表) が確定したらそれを返す。"""

    def feed(self, frame: TimedFrame) -> TimedFrame | None:
        """frame を受け取る。意味のあるフレームが確定したらそれを返し、確定しなければ None を返す。"""
        ...
