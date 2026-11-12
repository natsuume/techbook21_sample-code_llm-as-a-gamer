"""直近の区間のフレームから、最初と最後を含めて等間隔に選ぶ。"""

from collections.abc import Sequence

from switch2_agent.frame_history import TimedFrame


class EvenIntervalSelector:
    """frame_selection.base.RecentFrameSelector の実装。"""

    def select(self, frames: Sequence[TimedFrame], max_count: int) -> list[TimedFrame]:
        """古い順の frames から最大 max_count 枚を等間隔に選び、古い順に返す。

        - max_count <= 0 なら ValueError にする
        - len(frames) <= max_count なら、すべてを返す (frames が空なら空のリスト)
        - max_count == 1 なら、最後の 1 枚を返す
        - それ以外は、n = len(frames) として、添字 round(i * (n - 1) / (max_count - 1))
          (i = 0..max_count-1) のフレームを返す。最初と最後のフレームを必ず含む
        """
        if max_count <= 0:
            raise ValueError(f"max_count must be 1 or more: {max_count}")
        n = len(frames)
        if n <= max_count:
            return list(frames)
        if max_count == 1:
            return [frames[-1]]
        return [frames[round(i * (n - 1) / (max_count - 1))] for i in range(max_count)]
