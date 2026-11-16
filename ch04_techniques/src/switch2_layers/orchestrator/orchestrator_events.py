"""主ループが次の入力を作るときに取り出す出来事の型 (OrchestratorController.take_events の戻り値)。

前回の取り出し以降に起きたこと (case_hits・monitor_ends・discarded_action_count・blocked_buttons) と、取り出した時点の
値 (running・remaining_action_count・macros・monitor) を持つ。作った後は、Mapping の欄 (macros) の中身を書き換えない。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from switch2_layers.orchestrator.decision_types import Action, MonitorCreate

# 監視が終わった理由: completed は once の条件の操作を実行し終えた、failed は Jev の失敗が続いた
MonitorEndReason = Literal["completed", "failed"]


@dataclass(frozen=True)
class CaseHit:
    """前回の取り出し以降に、監視の条件 1 つに当たった回数。

    monitor      監視の名前
    case_index   条件の位置 (0 始まり。Jev の選択肢 C<case_index>)
    condition    条件の文
    count        当たって操作を実行した回数 (1 以上)
    actions      実行した操作の列 (条件に書かれたまま。procedure と macro は展開しない)
    """

    monitor: str
    case_index: int
    condition: str
    count: int
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class MonitorEnd:
    """監視が終わったこと。

    name     監視の名前
    reason   終わった理由
    detail   理由の説明 (どの条件で終えたか、どんな失敗が何回続いたか)
    """

    name: str
    reason: MonitorEndReason
    detail: str


@dataclass(frozen=True)
class OrchestratorEvents:
    """主ループへの知らせ。

    case_hits                前回の取り出し以降に当たった条件 (最初に当たった順。同じ監視の同じ条件は 1 つにまとめる)
    monitor_ends             前回の取り出し以降に終わった監視 (終わった順)
    discarded_action_count   前回の取り出し以降に、新しい actions で捨てた主ループの操作の数 (procedure と macro を
                             展開した後の数。捨てたときに実行中だった操作を含む)
    blocked_buttons          前回の取り出し以降に、押せずに外したボタン (pad_action.sort_buttons の順、重複なし)
    running                  取り出した時点で、主ループの actions の残りがある (実行中の操作を含む) なら True
    remaining_action_count   取り出した時点の、主ループの actions の残りの操作の数 (procedure と macro を展開した後の数。
                             実行中の操作を含む)
    macros                   取り出した時点のマクロ (名前 → 登録した操作の列。名前の順)
    monitor                  取り出した時点の監視 (作ったときの指示のまま)。無ければ None
    """

    case_hits: tuple[CaseHit, ...] = ()
    monitor_ends: tuple[MonitorEnd, ...] = ()
    discarded_action_count: int = 0
    blocked_buttons: tuple[str, ...] = ()
    running: bool = False
    remaining_action_count: int = 0
    macros: Mapping[str, tuple[Action, ...]] = field(default_factory=dict)
    monitor: MonitorCreate | None = None
