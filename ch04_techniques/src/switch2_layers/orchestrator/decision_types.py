"""主ループの Claude Code が各ターンの最後に書く JSON を読んだ結果の型 (操作・マクロ・監視の指示)。

JSON の形と、読み取りの規則 (どの値を誤りにするか) は orchestrator.decision_parser に書く。
この型自体は値を検証しない。decision_parser.parse_decision が、検証を通った値だけをこの型にする。
作った後は、Mapping の欄 (sticks・args・macros_set) の中身を書き換えない。

操作の種類 (Action):
    PressAction       ボタンを押し、スティックを倒す。秒数が無ければ短押し (hardware.pad_output.TAP_PRESS_S 秒)
    WaitAction        何も押さずに待つ
    ProcedureAction   コードの手順 (orchestrator.procedures) の呼び出し。実行のときに PressAction と WaitAction の列にする
    MacroAction       登録したマクロ (orchestrator.macro_registry) の呼び出し。実行のときに登録した操作の列にする
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

# press の sticks のキー (左右のスティック)
StickName = Literal["left", "right"]
# 監視の条件の繰り返し方: every は当たるたびに操作を実行し、once は 1 回実行し終えたら監視を終える
RepeatMode = Literal["every", "once"]


@dataclass(frozen=True)
class PressAction:
    """ボタンを押し、スティックを倒す操作。

    buttons   押すボタン名 (switch2_remote.controller_state.BUTTON_ORDER の名前)。十字キーは 1 つまで
    sticks    スティック (left・right) → (x, y)。x は右、y は上を正とし、各 -1.0〜1.0、(x, y) の長さは 1 以下。
              無いスティックは倒さない
    seconds   押し続ける秒数 (0 より大きく 60 以下)。None なら短押し (TAP_PRESS_S 秒。スティックも同じ秒数だけ倒す)
    """

    buttons: tuple[str, ...] = ()
    sticks: Mapping[StickName, tuple[float, float]] = field(default_factory=dict)
    seconds: float | None = None


@dataclass(frozen=True)
class WaitAction:
    """何も押さずに seconds 秒待つ操作。"""

    seconds: float


@dataclass(frozen=True)
class ProcedureAction:
    """コードの手順の呼び出し。

    name   手順の名前 (orchestrator.procedures.PROCEDURE_NAMES の要素)
    args   引数の名前 → 値 (JSON から読んだ値。手順ごとの引数は orchestrator.procedures に書く)
    """

    name: str
    args: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MacroAction:
    """登録したマクロ name の呼び出し。"""

    name: str


# 主ループが書く操作 1 つ
Action = PressAction | WaitAction | ProcedureAction | MacroAction


@dataclass(frozen=True)
class MonitorCase:
    """監視の条件 1 つ。

    condition   画面の条件の文 (主ループが書いたまま。Jev の選択肢の説明になる)
    actions     条件に当たったときに実行する操作の列
    repeat      every なら当たるたびに実行し、once なら 1 回実行し終えたら監視を終える
    """

    condition: str
    actions: tuple[Action, ...]
    repeat: RepeatMode


@dataclass(frozen=True)
class MonitorCreate:
    """監視を作る指示 (今の監視は止める)。

    name    監視の名前 (主ループへの知らせと monitor.jsonl に書く)
    cases   条件の列 (1〜8 個)。Jev の選択肢は、この順に C0・C1… とする
    """

    name: str
    cases: tuple[MonitorCase, ...]


@dataclass(frozen=True)
class MonitorStop:
    """今の監視を止める指示。"""


@dataclass(frozen=True)
class Decision:
    """1 ターン分の指示。

    actions         実行する操作の列。空なら、実行中の前の操作の列をそのまま続ける (JSON に actions が無いときも空)
    macros_set      登録するマクロ (名前 → 操作の列)。同じ名前があれば置き換える。マクロの中に MacroAction は無い
    macros_delete   消すマクロの名前 (無い名前は何もしない)
    monitor         監視の指示。None なら今の監視を変えない
    """

    actions: tuple[Action, ...] = ()
    macros_set: Mapping[str, tuple[Action, ...]] = field(default_factory=dict)
    macros_delete: tuple[str, ...] = ()
    monitor: MonitorCreate | MonitorStop | None = None


@dataclass(frozen=True)
class DecisionError:
    """JSON を読めなかった、または I/O 契約の形に合わなかったときの誤り。

    messages   誤りの説明 (1 つ以上)。主ループが次の入力に入れる
    """

    messages: tuple[str, ...]

    def __post_init__(self) -> None:
        """messages が空なら ValueError にする (誤りの説明の無い誤りを作らない)。"""
        if not self.messages:
            raise ValueError("DecisionError needs at least one message")
