"""系列情報の「小目標の時刻」(subgoals の節)。strategy.md の小目標ごとに、立てた時刻と状態が最後に変わった時刻を追う。

小目標は、strategy.md の `小目標: <小目標の文> (状態: <状態>` の形の行から読む (Skill の record-strategy と
default_notes の既定の内容がこの形で書く)。全角の括弧とコロンも読む。状態は、括弧の中の最初の「。」か閉じ括弧の
前まで (例: `(状態: 達成。マットに乗った)` の状態は `達成`)。strategy.md は advisor が書く自由な文なので、
この形の行が無ければ小目標が無いものとして扱い、節を出さない。
小目標の文が書き換わったら、別の小目標として扱う。
Pipeline のスレッドだけから呼ぶため、ロックは持たない。
"""

import re
from dataclasses import dataclass

from switch2_agent.timeline.moments import format_moment

SUBGOAL_PATTERN = re.compile(r"小目標[:：]\s*(.+?)\s*[(（]状態[:：]\s*([^。)）]+)")


def parse_subgoals(strategy_text: str) -> dict[str, str]:
    """strategy_text の小目標の文 → 状態 (前後の空白を除いたもの) の辞書を、書かれている順で返す。無ければ空の辞書。"""
    return {
        match.group(1).strip(): match.group(2).strip()
        for match in SUBGOAL_PATTERN.finditer(strategy_text)
    }


@dataclass
class SubgoalRecord:
    """小目標 1 つの記録。

    first_seen      初めて読んだ時刻
    seen_at_start   最初の observe (実行の開始) のときに、すでに書かれていたら True
    status          最後に読んだ状態
    status_since    状態が status になった時刻
    """

    first_seen: float
    seen_at_start: bool
    status: str
    status_since: float


class SubgoalTracker:
    """小目標の文 → SubgoalRecord を持つ。

    属性:
        started_at   最初に observe した時刻。まだなら None
        records      小目標の文 → 記録。strategy.md から消えた小目標の記録も残す (同じ文で戻ったときのため)
        current      最後に observe した strategy の小目標の文の並び (書かれている順)
    """

    def __init__(self):
        """まだ何も observe していない状態で作る。"""
        self.started_at: float | None = None
        self.records: dict[str, SubgoalRecord] = {}
        self.current: list[str] = []

    def observe(self, strategy_text: str, now: float) -> None:
        """要求の時刻 now に読んだ strategy.md の中身を受け取る。

        parse_subgoals で読んだ小目標ごとに、初めて読んだ文なら記録を作り (最初の observe なら seen_at_start を
        True にする)、読んだことがある文で状態が変わっていれば、status と status_since を now にする。
        """
        at_start = self.started_at is None
        if at_start:
            self.started_at = now
        subgoals = parse_subgoals(strategy_text)
        for name, status in subgoals.items():
            record = self.records.get(name)
            if record is None:
                self.records[name] = SubgoalRecord(now, at_start, status, now)
            elif record.status != status:
                record.status = status
                record.status_since = now
        self.current = list(subgoals)


def format_subgoals(tracker: SubgoalTracker, now: float, origin: float) -> str:
    """小目標の時刻の節の文を返す。最後に読んだ strategy.md に小目標が無ければ空文字を返す。

    1 行目は見出し `## 小目標の時刻`。続けて小目標ごとに (書かれている順に) 1 行:
        `- 「<小目標の文>」: <立てた時刻>。状態が「<状態>」になったのは <format_moment(status_since)>`
    立てた時刻は、seen_at_start なら `実行の開始 (<format_moment(first_seen)>) にはすでにあった`、
    そうでなければ `<format_moment(first_seen)> に立てた`。
    """
    if not tracker.current:
        return ""
    lines = ["## 小目標の時刻"]
    for name in tracker.current:
        record = tracker.records[name]
        first = format_moment(record.first_seen, now, origin)
        added = (
            f"実行の開始 ({first}) にはすでにあった"
            if record.seen_at_start
            else f"{first} に立てた"
        )
        since = format_moment(record.status_since, now, origin)
        lines.append(
            f"- 「{name}」: {added}。状態が「{record.status}」になったのは {since}"
        )
    return "\n".join(lines)
