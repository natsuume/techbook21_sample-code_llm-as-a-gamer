"""1 回の要求に載せるフレームを選ぶ。

直近の区間 (前回の要求の送信から今まで) のフレームから等間隔に最大 recent_max 枚と、
直近の区間より前の意味のあるフレームのうち新しい meaningful_max 枚を載せる。
"""

from dataclasses import dataclass

from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.base import RecentFrameSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore

DEFAULT_RECENT_MAX = 5  # 直近の区間から載せるフレームの最大数
DEFAULT_MEANINGFUL_MAX = 5  # 意味のあるフレームから載せる最大数
# 最初の要求 (前回の送信が無い) で、直近の区間とみなす長さ (秒)
DEFAULT_FIRST_WINDOW_S = 0.5


@dataclass(frozen=True)
class RequestFrames:
    """1 回の要求に載せるフレーム。

    recent       直近の区間から選んだフレーム (古い順)
    meaningful   直近の区間より前の意味のあるフレーム (古い順)
    """

    recent: list[TimedFrame]
    meaningful: list[TimedFrame]


def select_request_frames(
    history: FrameHistory,
    store: MeaningfulFrameStore,
    selector: RecentFrameSelector,
    previous_sent_at: float | None,
    now: float,
    recent_max: int = DEFAULT_RECENT_MAX,
    meaningful_max: int = DEFAULT_MEANINGFUL_MAX,
    first_window_s: float = DEFAULT_FIRST_WINDOW_S,
) -> RequestFrames:
    """要求に載せるフレームを選ぶ。

    直近の区間の始まり start は previous_sent_at (前回の要求の送信時刻)。
    previous_sent_at が None (最初の要求) なら now - first_window_s。
        recent = selector.select(history.between(start, now), recent_max)
        meaningful = store.latest_before(start, meaningful_max)
    """
    raise NotImplementedError
