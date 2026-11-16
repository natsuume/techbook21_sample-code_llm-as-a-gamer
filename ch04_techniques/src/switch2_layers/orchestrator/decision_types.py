"""主ループの Claude Code が各ターンの最後に書く JSON を読んだ結果の型。

JSON の形 (最上位のキーは actions・macros・monitor で、どれも省ける):
    {"actions": [操作, ...],
     "macros": {"set": {名前: [操作, ...]}, "delete": [名前, ...]},
     "monitor": {"action": "create", "name": ..., "cases": [{"condition": 条件の文, "actions": [操作, ...],
                 "repeat": "every" | "once"}, ...]} | {"action": "stop"} | null}
操作は type で 4 種類に分かれる:
    press       {"type": "press", "buttons": [ボタン名, ...], "sticks": {"left" | "right": {"x", "y"}},
                 "seconds": 秒}。seconds を省くと短押し
    wait        {"type": "wait", "seconds": 秒}
    procedure   {"type": "procedure", "name": 手順の名前, "args": {引数}}
    macro       {"type": "macro", "name": マクロの名前}
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PressAction:
    """ボタンを押し、スティックを倒す操作。

    buttons   押すボタンの名前
    sticks    倒すスティック ("left" か "right") → (x, y)
    seconds   押し続ける秒数。None なら短押し
    """

    buttons: tuple[str, ...]
    sticks: Mapping[Literal["left", "right"], tuple[float, float]]
    seconds: float | None


@dataclass(frozen=True)
class WaitAction:
    """何も押さずに seconds 秒待つ操作。"""

    seconds: float


@dataclass(frozen=True)
class ProcedureAction:
    """コードの手順 name を引数 args で実行する操作。"""

    name: str
    args: Mapping[str, object]


@dataclass(frozen=True)
class MacroAction:
    """登録したマクロ name を実行する操作。"""

    name: str


# 1 つの操作
Action = PressAction | WaitAction | ProcedureAction | MacroAction


@dataclass(frozen=True)
class MonitorCase:
    """監視の条件 1 つ。

    condition   画面の条件の文
    actions     当たったときに実行する操作の列
    repeat      "every" (当たるたびに実行する) か "once" (1 回実行したら監視を終える)
    """

    condition: str
    actions: tuple[Action, ...]
    repeat: Literal["every", "once"]


@dataclass(frozen=True)
class MonitorCreate:
    """監視を作る指示 (今の監視を止めてから作る)。"""

    name: str
    cases: tuple[MonitorCase, ...]


@dataclass(frozen=True)
class MonitorStop:
    """今の監視を止める指示。"""


@dataclass(frozen=True)
class Decision:
    """誤りの無い JSON を読んだ結果。

    actions         実行する操作の列 (キーが無ければ空)
    macros_set      登録するマクロ (名前 → 操作の列)
    macros_delete   消すマクロの名前
    monitor         監視の指示。変えないなら None
    """

    actions: tuple[Action, ...]
    macros_set: Mapping[str, tuple[Action, ...]]
    macros_delete: tuple[str, ...]
    monitor: MonitorCreate | MonitorStop | None


@dataclass(frozen=True)
class DecisionError:
    """JSON が無い・読めない・形に合わないときの誤り。messages は誤りの説明 (1 つ以上)。"""

    messages: tuple[str, ...]
