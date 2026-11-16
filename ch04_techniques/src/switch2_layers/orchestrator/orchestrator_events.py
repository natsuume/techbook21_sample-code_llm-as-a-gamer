"""操作の実行と監視の部品が、主ループに渡す知らせの型。

主ループは次の入力を作るときに、前回の取り出し以降に起きたことと、今のマクロと監視の一覧を
OrchestratorEvents として受け取る。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from switch2_layers.orchestrator.decision_types import Action, MonitorCreate


@dataclass(frozen=True)
class CaseHit:
    """監視の条件 1 つが当たった回数と、実行した操作。

    monitor      監視の名前
    case_index   条件の番号 (0 始まり)
    condition    条件の文
    count        前回の取り出し以降に当たった回数
    actions      実行した操作の列
    """

    monitor: str
    case_index: int
    condition: str
    count: int
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class MonitorEnd:
    """監視の終わり。

    name     監視の名前
    reason   "completed" (once の条件を実行し終えた) か "failed" (Jev の失敗が続いた)
    detail   終わった理由の説明
    """

    name: str
    reason: Literal["completed", "failed"]
    detail: str


@dataclass(frozen=True)
class OrchestratorEvents:
    """主ループへの知らせ。

    case_hits                監視の条件ごとの当たり (前回の取り出し以降)
    monitor_ends             監視の終わり (前回の取り出し以降)
    discarded_action_count   新しい actions で捨てた操作の数 (前回の取り出し以降)
    blocked_buttons          押せずに外したボタン (前回の取り出し以降)
    running                  取り出した時点で、主ループの actions を実行中なら True
    remaining_action_count   取り出した時点の、主ループの actions の残りの操作の数
    macros                   今のマクロ (名前 → 操作の列)
    monitor                  今の監視。無ければ None
    """

    case_hits: tuple[CaseHit, ...]
    monitor_ends: tuple[MonitorEnd, ...]
    discarded_action_count: int
    blocked_buttons: tuple[str, ...]
    running: bool
    remaining_action_count: int
    macros: Mapping[str, tuple[Action, ...]]
    monitor: MonitorCreate | None
