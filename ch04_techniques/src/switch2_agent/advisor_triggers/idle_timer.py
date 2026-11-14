"""advisor が決まった秒数呼ばれていなければ、決めた役割の advisor を呼ぶ方式 (base.AdvisorTrigger の実装)。

数え始めるのは、advisor が要求を受け付けた時刻 (まだ受け付けていなければ、判断のループの開始時刻)。範囲は 2 つ:
    any    どの役割の advisor も interval_s 秒受け付けていなければ、roles のすべての役割を呼ぶ。
           どれかの役割が受け付けると (advisor の問いで呼んだときも)、数え直す
    each   roles の役割ごとに、その役割の advisor が interval_s 秒受け付けていなければ、その役割を呼ぶ
保存した実行の記録 (2026-11-14) で、any で 60 秒、呼ぶ役割を strategy だけにして見積もると、順調な実行では
一度も呼ばず、advisor が 3〜6 分呼ばれなかった行き詰まりの実行では 2〜6 回呼んだ (roles が 2 つなら、1 回に 2 件呼ぶ)。
each では、順調な実行でも役割ごとに定期的に呼ぶ。
I/O・スレッド・時刻を扱わない (時刻は引数で受け取る)。
"""

import math
from collections.abc import Sequence
from typing import Literal

from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.advisor_triggers.base import AdvisorCallState, ForcedCall

IdleScope = Literal["any", "each"]
IDLE_SCOPES: tuple[IdleScope, ...] = ("any", "each")
DEFAULT_IDLE_SECONDS = 60.0
DEFAULT_IDLE_SCOPE: IdleScope = "any"
DEFAULT_IDLE_ROLES = ("strategy", "advice")


class IdleTimerTrigger:
    """advisor_triggers.base.AdvisorTrigger の実装。

    属性:
        interval_s   呼ばれていない秒数がこれ以上になったら呼ぶ (0 より大きい有限の値)
        roles        呼ぶ役割の name (advisor_roles.ADVISOR_ROLES の順に並べ直す。1 つ以上、重複しない)
        scope        数える範囲 (IDLE_SCOPES の値)
    範囲外の値、ADVISOR_ROLES に無い役割、重複した役割、空の roles、IDLE_SCOPES に無い scope は ValueError にする。
    """

    def __init__(
        self,
        interval_s: float = DEFAULT_IDLE_SECONDS,
        roles: Sequence[str] = DEFAULT_IDLE_ROLES,
        scope: IdleScope = DEFAULT_IDLE_SCOPE,
    ):
        """値を確かめて属性に入れる。"""
        if not (math.isfinite(interval_s) and interval_s > 0):
            raise ValueError(
                f"interval_s must be a finite number more than 0: {interval_s}"
            )
        names = [role.name for role in ADVISOR_ROLES]
        unknown = [name for name in roles if name not in names]
        if unknown:
            raise ValueError(f"unknown advisor roles: {', '.join(unknown)}")
        if not roles or len(set(roles)) != len(roles):
            raise ValueError(f"roles must be one or more distinct names: {roles}")
        if scope not in IDLE_SCOPES:
            raise ValueError(f"scope must be one of {IDLE_SCOPES}: {scope}")
        self.interval_s = interval_s
        self.roles = tuple(name for name in names if name in roles)
        self.scope = scope

    def due(self, state: AdvisorCallState, now: float) -> list[ForcedCall]:
        """時刻 now に強制で呼ぶ役割を返す。

        any: 最後にどれかの役割が受け付けた時刻 (どれも受け付けていなければ state.started_at) から now までが
             interval_s 以上なら、roles のすべての役割を、理由
             `どの役割の advisor も <interval_s> 秒呼ばれていなかったため、定期の呼び出しで呼んだ` で返す
        each: roles の役割ごとに、その役割が最後に受け付けた時刻 (無ければ state.started_at) から now までが
             interval_s 以上なら、その役割を、理由
             `この役割の advisor が <interval_s> 秒呼ばれていなかったため、定期の呼び出しで呼んだ` で返す
        <interval_s> は :g の書式 (例: 60)。
        """
        if self.scope == "any":
            last = max([state.started_at, *state.last_accepted.values()])
            if now - last < self.interval_s:
                return []
            reason = (
                f"どの役割の advisor も {self.interval_s:g} 秒呼ばれていなかったため、"
                "定期の呼び出しで呼んだ"
            )
            return [ForcedCall(name, reason) for name in self.roles]
        reason = (
            f"この役割の advisor が {self.interval_s:g} 秒呼ばれていなかったため、"
            "定期の呼び出しで呼んだ"
        )
        return [
            ForcedCall(name, reason)
            for name in self.roles
            if now - state.last_accepted.get(name, state.started_at) >= self.interval_s
        ]
