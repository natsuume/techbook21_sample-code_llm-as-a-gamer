"""advisor に渡すフレームを、Decision Model への要求とは別に選ぶ。

advisor が場面の流れを読めるよう、時間の幅を持たせて選ぶ。フレームの履歴の全体 (直近 history.max_age_s 秒) から
等間隔に最大 recent_max 枚と、それより前の意味のあるフレームのうち新しい meaningful_max 枚を選ぶ。
"""

from dataclasses import dataclass

from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.base import RecentFrameSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore

DEFAULT_RECENT_MAX = 5  # フレームの履歴から選ぶフレームの最大数
DEFAULT_MEANINGFUL_MAX = 5  # 意味のあるフレームから選ぶ最大数


@dataclass(frozen=True)
class AdvisorFrames:
    """advisor に渡すフレーム。

    recent       フレームの履歴から選んだフレーム (古い順)
    meaningful   recent の最初のフレームより前の意味のあるフレーム (古い順)
    """

    recent: list[TimedFrame]
    meaningful: list[TimedFrame]


def select_advisor_frames(
    history: FrameHistory,
    store: MeaningfulFrameStore,
    selector: RecentFrameSelector,
    now: float,
    recent_max: int = DEFAULT_RECENT_MAX,
    meaningful_max: int = DEFAULT_MEANINGFUL_MAX,
) -> AdvisorFrames:
    """advisor に渡すフレームを選ぶ。

        recent = selector.select(history.between(now - history.max_age_s, now), recent_max)
        meaningful = store.latest_before(<recent の最初のフレームの time。recent が空なら now>, meaningful_max)

    meaningful は recent の最初のフレームより前 (time が小さい) のフレームだけになる
    (store.latest_before は time ちょうどのフレームを含めない)。
    """
    raise NotImplementedError
