"""advisor の強制の呼び出しの型。Pipeline はこの型だけに依存する。

Pipeline は、要求の応答から操作を決めるたびに AdvisorTrigger.due を呼び、返った役割のうち、advisor の問いで
すでに求めた役割と、実行中の役割を除いて、advisor に求める。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ForcedCall:
    """強制の呼び出し 1 件。

    role_name   呼ぶ役割の name (advisor_roles.ADVISOR_ROLES の name)
    reason      呼ぶ理由の文。advisor への文脈 (context.md) のきっかけの行に書く
                (例: `どの役割の advisor も 60 秒呼ばれていなかったため、定期の呼び出しで呼んだ`)
    """

    role_name: str
    reason: str


@dataclass(frozen=True)
class AdvisorCallState:
    """強制の呼び出しを決めるときに渡す、advisor の呼び出しの状態。

    started_at              判断のループの開始時刻 (Pipeline.started_at)
    last_accepted           役割の name → その役割の advisor が最後に要求を受け付けた時刻。まだ受け付けていない役割は含まない
    advisor_probabilities   役割の name → 今の要求の、その役割の advisor の問いの確率 (確率を使う方式のため)
    """

    started_at: float
    last_accepted: Mapping[str, float]
    advisor_probabilities: Mapping[str, float]


class AdvisorTrigger(Protocol):
    """強制の呼び出しの方式。"""

    def due(self, state: AdvisorCallState, now: float) -> list[ForcedCall]:
        """時刻 now に強制で呼ぶ役割を返す (advisor_roles.ADVISOR_ROLES の順)。呼ぶ役割が無ければ空のリスト。

        すぐ戻る。役割が実行中かどうかは考えない (Pipeline が除く)。
        """
        ...
