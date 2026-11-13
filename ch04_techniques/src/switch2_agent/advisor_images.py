"""advisor に渡す画像 (AdvisorContext.images) を、選んだフレームから作る。

並びは意味のあるフレーム (古い順) → フレームの履歴から選んだフレーム (古い順) で、全体も時刻の古い順になる。
各画像のラベルには、実行を始めてからの経過秒 (t) と何秒前の画像かを書く。前回の操作の前か後かは書かない。
"""

from collections.abc import Callable

from switch2_agent.decision_request import LabeledImage
from switch2_agent.frame_history import TimedFrame
from switch2_agent.frame_selection.advisor_frames import AdvisorFrames


def label_advisor_frames(
    frames: AdvisorFrames, now: float, origin: float
) -> list[tuple[str, TimedFrame]]:
    """advisor に渡すフレームを並べ、(ラベル, フレーム) のリストにする。

    並びは frames.meaningful (古い順) → frames.recent (古い順)。origin は経過秒の起点 (Pipeline.started_at)。
    k はその種類の中での 1 始まりの番号、n はその種類のフレームの数。t は frame.time - origin、
    秒前は now - frame.time で、どちらも小数第 2 位まで書く。ラベルの先頭の語は decision_request の
    MEANINGFUL_LABEL と RECENT_LABEL。
    - 意味のあるフレーム: `過去の場面 <k>/<n> (t=<t> 秒、<秒前> 秒前)` (例: `過去の場面 2/5 (t=115.80 秒、12.30 秒前)`)
    - フレームの履歴から選んだフレーム: `直近 <k>/<n> (t=<t> 秒、<秒前> 秒前)` (例: `直近 4/5 (t=127.89 秒、0.21 秒前)`)
    どちらも空なら、空のリストを返す。
    """
    raise NotImplementedError


def build_advisor_images(
    frames: AdvisorFrames,
    now: float,
    origin: float,
    encode: Callable[[TimedFrame], bytes],
) -> list[LabeledImage]:
    """label_advisor_frames(frames, now, origin) の順に、各フレームを encode で JPEG にした LabeledImage のリストを返す。

    LabeledImage の label はそのラベル、frame_time はフレームの time、after_previous_operation は False。
    encode は、この並びの順に 1 枚につき 1 回呼ぶ。
    """
    raise NotImplementedError
