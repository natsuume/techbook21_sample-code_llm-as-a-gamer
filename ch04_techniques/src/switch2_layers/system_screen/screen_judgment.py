"""照合の結果: 照合の choice の確率から本体の画面の名前を決める関数 (judge_system_screen) と、最新の結果 (LatestSystemScreen)。

照合の結果の決め方 (judge_system_screen):
    選択肢 (system_screen_references.SYSTEM_SCREEN_CHOICE_NAMES。none が先頭で、見本の名前が続く) のうち、確率が
    いちばん高いものを選ぶ。いちばん高い確率の選択肢が複数あれば、選択肢の並びで先のものを選ぶ。
    選んだ選択肢が none でなく、その確率が SYSTEM_SCREEN_THRESHOLD を超えたら (ちょうど同じ値は超えない)、その見本の
    名前を結果とする。それ以外 (none を選んだ、または選んだ確率が SYSTEM_SCREEN_THRESHOLD 以下) は None とする。
    確率の合計は確かめない (Jev の choice の確率の合計は、およそ 1 としか決まっていない)。

最新の結果 (LatestSystemScreen):
    安全の監視のスレッドが update で結果を渡し、ほかのスレッドが latest と wait_newer で読む。結果のうち、確かめた
    フレームの時刻 frame_t がいちばん新しいものを持つ。
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass

# 照合の結果を見本の名前にする、確率のしきい値 (この値を超えたら、その見本の画面とする)
SYSTEM_SCREEN_THRESHOLD = 0.5


@dataclass(frozen=True)
class SystemScreenJudgment:
    """照合の結果 1 回分。

    frame_t         照合したフレームの時刻 (実行の経過秒)
    t_received      照合の応答を受け取った時刻 (実行の経過秒)
    screen          照合の結果の見本の名前 (judge_system_screen の値)。どの見本の画面でもなければ None
    probabilities   照合の選択肢の名前 (none を含む) → 確率 (0〜1)。
                    system_screen_references.SYSTEM_SCREEN_CHOICE_NAMES のすべてを、その順に含む
    """

    frame_t: float
    t_received: float
    screen: str | None
    probabilities: Mapping[str, float]


def judge_system_screen(probabilities: Mapping[str, float]) -> str | None:
    """照合の選択肢の確率 probabilities から、照合の結果 (モジュールの docstring の決め方) を返す。

    probabilities は 選択肢の名前 → 確率 (0〜1)。SYSTEM_SCREEN_CHOICE_NAMES のどれかの名前が無ければ ValueError。
    SYSTEM_SCREEN_CHOICE_NAMES に無い名前は無視する。選択肢の並びは probabilities の並びではなく、
    SYSTEM_SCREEN_CHOICE_NAMES の順とする。
    """
    raise NotImplementedError


class LatestSystemScreen:
    """最新の照合の結果を持つ。update・latest・wait_newer は、どのスレッドから呼んでもよい。

    持つ結果は、受け取った結果のうち frame_t がいちばん新しいもの。まだ受け取っていなければ None。
    """

    def __init__(self) -> None:
        """結果をまだ持たない状態で作る。"""
        # 結果の読み書きと、新しい結果を待つスレッドへの知らせに使う
        self._condition = threading.Condition()
        self._latest: SystemScreenJudgment | None = None

    def update(self, judgment: SystemScreenJudgment) -> None:
        """judgment を受け取る。

        結果をまだ持っていないか、judgment.frame_t が今持っている結果の frame_t より大きければ、judgment に置き換え、
        wait_newer で待っているスレッドに知らせる。frame_t が今持っている結果の frame_t 以下なら、置き換えない。
        """
        raise NotImplementedError

    def latest(self) -> SystemScreenJudgment | None:
        """今持っている結果を返す。まだ受け取っていなければ None。"""
        raise NotImplementedError

    def wait_newer(
        self, after_frame_t: float, timeout_s: float
    ) -> SystemScreenJudgment | None:
        """frame_t が after_frame_t より大きい結果を返す。

        今持っている結果の frame_t が after_frame_t より大きければ、待たずにそれを返す。そうでなければ、そのような
        結果を受け取るまで最大 timeout_s 秒待ち、受け取ったらその時に持っている結果を返す。届かなければ None を返す。
        frame_t がちょうど after_frame_t の結果は、新しい結果としない。
        timeout_s が 0 以下なら待たない (持っている結果の frame_t が after_frame_t より大きければそれを、そうでなければ
        None を返す)。
        """
        raise NotImplementedError
