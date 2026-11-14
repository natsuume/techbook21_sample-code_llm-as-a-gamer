"""系列情報の「advisor を呼んだ時刻」(advisor_calls の節)。役割ごとに、advisor が最後に要求を受け付けた時刻を追う。

Pipeline が、advisor が要求を受け付けた (request_update が True を返した) ときに AdvisorCallTracker.record を呼ぶ。
Pipeline のスレッドだけから呼ぶため、ロックは持たない。
"""

from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.timeline.moments import format_moment


class AdvisorCallTracker:
    """役割の name → その役割の advisor が最後に要求を受け付けた時刻 (last_called) を持つ。"""

    def __init__(self):
        """どの役割もまだ呼んでいない状態で作る。"""
        self.last_called: dict[str, float] = {}

    def record(self, role_name: str, time: float) -> None:
        """役割 role_name の advisor が、時刻 time に要求を受け付けたことを記録する。"""
        self.last_called[role_name] = time


def format_advisor_calls(tracker: AdvisorCallTracker, now: float, origin: float) -> str:
    """advisor を呼んだ時刻の節の文を返す。

    1 行目は見出し `## advisor を最後に呼んだ時刻`。続けて advisor_roles.ADVISOR_ROLES の順に、役割ごとに
    `- <role.title>: <format_moment(最後に受け付けた時刻)>` か、まだ呼んでいなければ
    `- <role.title>: この実行ではまだ呼んでいない` を書く。
    """
    lines = ["## advisor を最後に呼んだ時刻"]
    for role in ADVISOR_ROLES:
        called = tracker.last_called.get(role.name)
        if called is None:
            lines.append(f"- {role.title}: この実行ではまだ呼んでいない")
        else:
            lines.append(f"- {role.title}: {format_moment(called, now, origin)}")
    return "\n".join(lines)
