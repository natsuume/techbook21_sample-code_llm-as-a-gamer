"""系列情報の節をつないで、速い層の要求に足す 1 つの文 (DecisionRequest.timeline_text) にする。

Pipeline は要求のたびに、読んだゲームの情報を observe_notes に渡してから build を呼ぶ。advisor が要求を受け付けたら
record_advisor_call を呼ぶ。画面の変化は、キャプチャーのスレッドが ScreenChangeTracker.feed で直接渡す。
節は SECTION_NAMES の順に並べ、TimelineSettings.sections に入っている節だけを出す。空の節 (まだ材料が無いもの) は
出さず、節の間は空行 1 つでつなぐ。並びは、オフラインの実験 (2026-11-14) で速い層に渡して効果を測った文と同じ。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from switch2_agent.operation_log import OperationLog
from switch2_agent.timeline.action_runs import format_action_runs, summarize_action_runs
from switch2_agent.timeline.advisor_calls import (
    AdvisorCallTracker,
    format_advisor_calls,
)
from switch2_agent.timeline.note_changes import NoteChangeTracker, format_note_changes
from switch2_agent.timeline.screen_change import (
    ScreenChangeTracker,
    format_screen_change,
)
from switch2_agent.timeline.subgoals import SubgoalTracker, format_subgoals

# 節の名前 (起動引数 --timeline で選ぶ名前)。並びは文に並べる順でもある
SECTION_NAMES = ("actions", "screen", "notes", "advisor_calls", "subgoals")
DEFAULT_ACTION_WINDOW_S = 120.0  # 操作のまとめに入れる秒数の既定値
DEFAULT_MAX_ACTION_RUNS = 8  # 操作のまとめに書く区間の数の上限の既定値


@dataclass(frozen=True)
class TimelineSettings:
    """系列情報の設定。

    sections          出す節の名前 (SECTION_NAMES の要素。重複しない)。空なら何も出さない
    action_window_s   操作のまとめに入れる、直近の秒数 (0 より大きい)
    max_action_runs   操作のまとめに書く区間の数の上限 (1 以上)。超えたら古い区間を省く
    範囲外の値と、SECTION_NAMES に無い名前、重複した名前は ValueError にする。
    """

    sections: tuple[str, ...] = SECTION_NAMES
    action_window_s: float = DEFAULT_ACTION_WINDOW_S
    max_action_runs: int = DEFAULT_MAX_ACTION_RUNS

    def __post_init__(self) -> None:
        """値を確かめる。"""
        unknown = [name for name in self.sections if name not in SECTION_NAMES]
        if unknown:
            raise ValueError(f"unknown timeline sections: {', '.join(unknown)}")
        if len(set(self.sections)) != len(self.sections):
            raise ValueError(f"duplicate timeline sections: {self.sections}")
        if not self.action_window_s > 0:
            raise ValueError(
                f"action_window_s must be more than 0: {self.action_window_s}"
            )
        if self.max_action_runs < 1:
            raise ValueError(
                f"max_action_runs must be 1 or more: {self.max_action_runs}"
            )


class TimelineBuilder:
    """系列情報の文を作る。pipeline.TimelineLike の実装。

    属性:
        settings        設定
        operation_log   操作のまとめの材料 (Pipeline と同じ OperationLog)
        screen          画面の変化の材料。sections に "screen" が無ければ None でよい
        notes           ゲームの情報の更新時刻の追跡
        subgoals        小目標の時刻の追跡
        advisor_calls   advisor を呼んだ時刻の追跡
    """

    def __init__(
        self,
        settings: TimelineSettings,
        operation_log: OperationLog,
        screen: ScreenChangeTracker | None = None,
    ):
        """追跡の部品を空の状態で作る。sections に "screen" があるのに screen が None なら ValueError にする。"""
        if "screen" in settings.sections and screen is None:
            raise ValueError("screen tracker is required for the screen section")
        self.settings = settings
        self.operation_log = operation_log
        self.screen = screen
        self.notes = NoteChangeTracker()
        self.subgoals = SubgoalTracker()
        self.advisor_calls = AdvisorCallTracker()

    def observe_notes(self, notes: Mapping[str, str], now: float) -> None:
        """要求の時刻 now に読んだゲームの情報 notes (種別 → 中身) を、更新時刻と小目標の追跡に渡す。"""
        self.notes.observe(notes, now)
        self.subgoals.observe(notes.get("strategy", ""), now)

    def record_advisor_call(self, role_name: str, now: float) -> None:
        """役割 role_name の advisor が、時刻 now に要求を受け付けたことを記録する。"""
        self.advisor_calls.record(role_name, now)

    def build(self, now: float, origin: float) -> str:
        """sections の節の文を SECTION_NAMES の順に作り、空でないものを空行 1 つでつないで返す。

        now は要求の時刻、origin は経過秒の起点 (Pipeline.started_at)。sections が空なら空文字を返す。
        """
        texts = [
            self._section_text(name, now, origin)
            for name in SECTION_NAMES
            if name in self.settings.sections
        ]
        return "\n\n".join(text for text in texts if text)

    def _section_text(self, name: str, now: float, origin: float) -> str:
        """節 name の文を返す。"""
        if name == "actions":
            return self._actions_text(now, origin)
        if name == "screen":
            assert self.screen is not None  # __init__ で確かめている
            return format_screen_change(self.screen.static_since(), now, origin)
        if name == "notes":
            return format_note_changes(self.notes, now, origin)
        if name == "advisor_calls":
            return format_advisor_calls(self.advisor_calls, now, origin)
        return format_subgoals(self.subgoals, now, origin)

    def _actions_text(self, now: float, origin: float) -> str:
        """操作のまとめの節の文を返す。

        直近 action_window_s 秒の記録 (operation_log.since(now - action_window_s)) をまとめる。記録が上限に達していて、
        最も古い記録が窓の始まりより後なら、窓の始まりまでの記録が捨てられているため truncated にする。
        """
        window_start = now - self.settings.action_window_s
        records = self.operation_log.since(window_start)
        oldest = self.operation_log.oldest_time()
        truncated = (
            self.operation_log.is_full()
            and oldest is not None
            and oldest > window_start
        )
        return format_action_runs(
            summarize_action_runs(records),
            origin,
            self.settings.action_window_s,
            self.settings.max_action_runs,
            truncated,
        )
