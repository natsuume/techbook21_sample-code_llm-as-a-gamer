"""実行中に登録するマクロ (名前 → 操作の列) の一覧。I/O・スレッド・時刻を扱わない。

マクロは実行ディレクトリーの間 (OrchestratorController を作ってから捨てるまで) だけ持ち、主ループのセッションを
入れ替えても残す。マクロの操作の列には PressAction・WaitAction・ProcedureAction だけを入れ、MacroAction は入れない
(マクロの中からマクロは呼べない。decision_parser.parse_decision が確かめる)。
複数のスレッドから使うときは、呼び出し側 (OrchestratorController) がロックで順番を守る。
"""

from collections.abc import Iterable, Mapping

from switch2_layers.orchestrator.decision_types import Action


class MacroRegistry:
    """マクロの名前 → 登録した操作の列。空の一覧で作る。"""

    def __init__(self) -> None:
        """マクロの無い一覧を作る。"""
        self._macros: dict[str, tuple[Action, ...]] = {}

    def apply_changes(
        self, set_macros: Mapping[str, tuple[Action, ...]], delete_names: Iterable[str]
    ) -> None:
        """set_macros の名前を登録し (同じ名前があれば置き換える)、その後に delete_names の名前を消す (無い名前は何もしない)。

        同じ名前が両方にあれば、消えた状態になる。
        """
        for name, actions in set_macros.items():
            self._macros[name] = tuple(actions)
        for name in delete_names:
            self._macros.pop(name, None)

    def names_after(
        self, set_macros: Mapping[str, tuple[Action, ...]], delete_names: Iterable[str]
    ) -> frozenset[str]:
        """apply_changes(set_macros, delete_names) を当てた後の名前の集合を、一覧を変えずに返す。"""
        return (frozenset(self._macros) | frozenset(set_macros)) - frozenset(
            delete_names
        )

    def names(self) -> frozenset[str]:
        """登録している名前の集合を返す。"""
        return frozenset(self._macros)

    def get(self, name: str) -> tuple[Action, ...] | None:
        """name のマクロの操作の列を返す。無ければ None。"""
        return self._macros.get(name)

    def snapshot(self) -> dict[str, tuple[Action, ...]]:
        """登録しているマクロ (名前 → 操作の列) を、名前の順に並べた新しい辞書で返す。"""
        return {name: self._macros[name] for name in sorted(self._macros)}
