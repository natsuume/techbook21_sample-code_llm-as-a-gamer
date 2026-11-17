"""主ループが操作の実行と監視の部品 (OrchestratorController) から受け取る型。

OrchestratorEvents (take_events の戻り値): 主ループが次の入力を作るときに取り出す出来事。前回の取り出し以降に起きたこと
(case_hits・monitor_ends・discarded_action_count・blocked_buttons・delegate_ends・delegate_discarded_action_count・
delegate_skipped_actions) と、取り出した時点の値 (running・remaining_action_count・macros・monitor・delegation) を
持つ。作った後は、Mapping の欄 (macros) の中身を書き換えない。delegation は --mode delegate のときだけ None でない
(turn_input が委任の節を置くかをこれで決める)。
MainActionsRun (main_actions_run の戻り値): 主ループの空でない actions の操作の列 1 つの、始めた・最後に離した・終えた
時刻。主ループは、これと frames/latest/ の最新のフレームの時刻を見て、前のターンの操作の列が終わり、その後に撮った
フレームが届くのを待つ (orchestrator.action_settle)。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from switch2_layers.orchestrator.decision_types import (
    Action,
    DelegateStart,
    MonitorCreate,
)

# 監視が終わった理由: completed は once の条件の操作を実行し終えた、failed は Jev の失敗が続いた
MonitorEndReason = Literal["completed", "failed"]
# 委任が終わって操作権が主ループに戻った理由: jev_return は Jev が「操作を返す」を続けて選んだ、main_stop は主ループが
# delegate の stop を書いた、errors は Jev の失敗が続いた
DelegateEndReason = Literal["jev_return", "main_stop", "errors"]
# 委任中でも委任を始める前でもなかったため当てなかった、主ループの delegate の action の名前
DelegateSkippedAction = Literal["update", "stop"]


@dataclass(frozen=True)
class CaseHit:
    """前回の取り出し以降に、監視の条件 1 つに当たった回数 (監視の世代と条件の位置の組ごとに 1 つ)。

    monitor      監視の名前
    case_index   条件の位置 (0 始まり。Jev の選択肢 C<case_index>)
    condition    条件の文 (当たった世代の監視の条件のもの)
    count        当たって操作を実行した回数 (1 以上)
    actions      実行した操作の列 (当たった世代の監視の条件に書かれたまま。procedure と macro は展開しない)
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
class DelegateEnd:
    """委任が終わり、操作権が主ループに戻ったこと (主ループへの知らせ。実行全体を止めて終えた委任は入れない)。

    reason          戻った理由
    detail          理由の説明 (orchestrator.delegate_loop の delegate_return_detail・MAIN_STOP_DETAIL・
                    delegate_failure_detail の文)
    chosen_counts   その委任 (始めてから終わるまで) で Jev が選んだ回数を、(選択肢の名前, 回数) で並べたもの
                    (orchestrator.delegate_loop.order_chosen_counts の並び。0 回の選択肢も含む)。数えるのは、Jev の
                    答えで処理を決めた要求 (delegate.jsonl の handling が ignored でなく、error が null の行) だけ
    failure_count   その委任で失敗した要求 (Jev の HTTP のエラーなど。delegate.jsonl の handling が ignored でなく、
                    error がある行) の数 (続いた回数ではなく合計)。chosen_counts には入れない
    """

    reason: DelegateEndReason
    detail: str
    chosen_counts: tuple[tuple[str, int], ...]
    failure_count: int


@dataclass(frozen=True)
class DelegatePick:
    """委任のループで Jev が選んで始めた行動 1 つ。

    name   行動の名前 (DelegateChoice.name。マクロの名前)
    t      その行動 (マクロの操作の列) を始めた時刻 (実行の経過秒)
    """

    name: str
    t: float


@dataclass(frozen=True)
class DelegationStatus:
    """取り出した時点の委任の状況 (--mode delegate のときの OrchestratorEvents.delegation)。

    active          委任中なら True (委任を始める指示を受け付け、主ループの actions の終わりを待っている間も含む)
    started_t       委任を始めた時刻。委任中でないか、主ループの actions の終わりを待っていてまだ始めていなければ None
    values          委任中なら今のタスク・注意事項・行動。委任中でなければ前の委任の値 (start で省いたキーに使う値)。
                    まだ委任が無ければ None
    last_pick       今の委任で Jev が直近に選んで始めた行動。委任中でないか、まだ選んでいなければ None
    return_streak   今の委任で Jev が「操作を返す」を続けて選んだ回数。委任中でなければ 0
    """

    active: bool
    started_t: float | None
    values: DelegateStart | None
    last_pick: DelegatePick | None
    return_streak: int


@dataclass(frozen=True)
class OrchestratorEvents:
    """主ループへの知らせ。

    case_hits                前回の取り出し以降に当たった条件 (最初に当たった順)。監視の世代と条件の位置の組ごとに
                             1 つにまとめる (同じ名前の監視でも、作り直した監視の条件は別の CaseHit にする)
    monitor_ends             前回の取り出し以降に終わった監視 (終わった順)
    discarded_action_count   前回の取り出し以降に、新しい actions で捨てた主ループの操作の数 (procedure と macro を
                             展開した後の数。捨てたときに実行中だった操作を含む)
    blocked_buttons          前回の取り出し以降に、押せずに外したボタン (pad_action.sort_buttons の順、重複なし。
                             委任の行動の操作で外したボタンを含む)
    running                  取り出した時点で、主ループの actions の残りがある (実行中の操作を含む) なら True
    remaining_action_count   取り出した時点の、主ループの actions の残りの操作の数 (procedure と macro を展開した後の数。
                             実行中の操作を含む)
    macros                   取り出した時点のマクロ (名前 → 登録した操作の列。名前の順)
    monitor                  取り出した時点の監視 (作ったときの指示のまま)。無ければ None
    delegate_ends            前回の取り出し以降に、操作権が主ループに戻った委任 (戻った順)
    delegate_discarded_action_count
                             前回の取り出し以降に、委任中のため捨てた主ループの actions の操作の数 (JSON の actions の
                             要素の数。procedure と macro は展開しない)
    delegate_skipped_actions 前回の取り出し以降に、委任中でも委任を始める前でもなかったため当てなかった、主ループの
                             delegate の action の名前 (update か stop。当てようとした順)。同じ JSON の actions・macros は
                             当ててある
    delegation               取り出した時点の委任の状況 (--mode delegate のとき)。--mode orchestrator では None
    """

    case_hits: tuple[CaseHit, ...] = ()
    monitor_ends: tuple[MonitorEnd, ...] = ()
    discarded_action_count: int = 0
    blocked_buttons: tuple[str, ...] = ()
    running: bool = False
    remaining_action_count: int = 0
    macros: Mapping[str, tuple[Action, ...]] = field(default_factory=dict)
    monitor: MonitorCreate | None = None
    delegate_ends: tuple[DelegateEnd, ...] = ()
    delegate_discarded_action_count: int = 0
    delegate_skipped_actions: tuple[DelegateSkippedAction, ...] = ()
    delegation: DelegationStatus | None = None


@dataclass(frozen=True)
class MainActionsRun:
    """主ループの空でない actions の操作の列 1 つの時刻 (procedure と macro を展開した後の列。時刻は実行の経過秒)。

    委任の行動の操作の列はこの時刻に入れない (主ループは委任の操作の終わりを待たない)。

    started_t    操作の実行と監視の部品が、列を始めた時刻 (まだ始めていなければ None)
    released_t   列の操作が、最後にボタンかスティックを離した時刻 (列を途中で捨てたときに中立に戻した時刻を含む)。
                 まだ離していないか、押す操作の無い列なら None
    ended_t      列が終わった時刻 (最後の操作を終えた時刻か、途中で捨てた時刻)。まだ終わっていなければ None
    ended_t が None でなければ、started_t も None でない。
    """

    started_t: float | None
    released_t: float | None
    ended_t: float | None
