"""主ループの Claude Code の応答の本文から、最後の ```json のコードブロックを読み、Decision にする。I/O を持たない。

コードブロック:
    行頭の ```json (後ろに空白だけを許す) の行から、次の行頭の ``` (後ろに空白だけを許す) の行までの間の文字列。
    本文に複数あれば、最後のものだけを読む。閉じていないブロックは数えない。改行は \\n か \\r\\n
    最後の閉じたブロックより後に (閉じたブロックが無ければ本文のどこかに)、閉じていない ```json の開きの行があれば、
    応答が途中で切れたとみなして誤りにする (前の閉じたブロックは読まない。古い案の指示を実行しないため)
JSON の形 (どのキーも省ける。どの object も、ここに無いキーがあれば誤り):
    {
      "actions": [<操作>, ...],
      "macros": {"set": {<名前>: [<操作>, ...], ...}, "delete": [<名前>, ...]},
      "monitor": {"action": "create", "name": <文字列>, "cases": [<条件>, ...]} | {"action": "stop"} | null
    }
    操作:
        {"type": "press", "buttons": [<ボタン名>, ...], "sticks": {"left": {"x": 数, "y": 数}, "right": {...}},
         "seconds": 数}                                  buttons・sticks・seconds はどれも省ける (下の規則)
        {"type": "wait", "seconds": 数}
        {"type": "procedure", "name": <手順の名前>, "args": {<引数の名前>: 値, ...}}
        {"type": "macro", "name": <マクロの名前>}
    条件:
        {"condition": <文字列>, "actions": [<操作>, ...], "repeat": "every" | "once"}
--mode delegate の JSON の形 (parse_decision の delegate に DelegateContext を渡したとき。monitor の代わりに delegate):
    {
      "actions": [<操作>, ...],
      "macros": {"set": {...}, "delete": [...]},
      "delegate": {"action": "start", "tasks": [<文>, ...], "cautions": [<文>, ...], "choices": [<行動>, ...]}
                | {"action": "update", "tasks": [...], "cautions": [...], "choices": [...]}
                | {"action": "stop"} | null
    }
    行動:
        {"name": <マクロの名前>, "description": <文>}
    start と update の tasks・cautions・choices は、どれも省ける。start で省いたキーは前の委任 (DelegateContext.previous)
    の値で埋め、update で省いたキーは変えない (DelegateUpdate の None)。delegate の null は None (委任を変えない)。
    委任中でも、委任を始める前でもない (DelegateContext.active が False) ときの update と stop は誤りにせず、そのまま
    DelegateUpdate・DelegateStop にする (JSON を書く間に委任が終わっていることがあるため。当てるかどうかは
    orchestrator_controller が決める)。update と stop の値の規則は、委任中かに依らず確かめる。
    delegate を渡さないとき (--mode orchestrator) は、最上位の delegate は知らないキーの誤り。渡したときは、最上位の
    monitor が知らないキーの誤り。
誤りにするもの:
    - コードブロックが無い、最後の閉じたブロックより後に閉じていない ```json の開きの行がある、JSON として読めない
      (json.loads の NaN・Infinity・-Infinity と、1e999 のように float で無限大になる数は読めるが、下の秒数・スティックの
      規則で誤りになる)、最上位が object でない
    - object に知らないキーがある。値の型が違う (actions・buttons・cases・delete が配列でない、sticks・args・set が
      object でない、名前や文が文字列でない、数が int か float でない (bool も誤り) など)。actions と macros は null も誤り
    - type が press・wait・procedure・macro のどれでもない (type が無いときも誤り)
    - buttons に BUTTON_ORDER (switch2_remote.controller_state) に無い名前がある (大文字小文字を区別する)。押せない
      ボタン (HOME など) は誤りにしない (実行のときに PadOutput.apply が外し、主ループに知らせる)
    - 1 つの press の buttons に、十字キー (UP・RIGHT・DOWN・LEFT) が 2 つ以上ある
    - press に buttons も sticks も無い (空の配列と空の object は、無いものとして扱う)
    - press と wait の seconds が 0 以下・nan・無限大、または MAX_ACTION_SECONDS を超える。wait には seconds が要る
    - sticks のキーが left・right でない。スティックの値に x か y が無い。x か y が -1〜1 の外 (nan・無限大を含む)、
      または math.hypot(x, y) が 1 を超える
    - 秒数とスティックの値に、float に収まらない大きな整数がある (有限の数でない、または範囲の外として扱う)
    - procedure の name が procedures.PROCEDURE_NAMES に無い、または args が手順の引数に合わない
      (procedures.procedure_argument_errors)
    - macro の name が、この JSON の macros を当てた後の登録に無い。当てた後の登録は、registered_macros に
      macros.set の名前を足し、macros.delete の名前を除いたもの (set と delete の両方にある名前は、除いた側になる。
      macro_registry.MacroRegistry.apply_changes と同じ順)。actions と monitor の条件の actions の両方で確かめる
    - macros.set のマクロの操作の列に macro がある (マクロの中からマクロは呼べない)
    - monitor の action が create・stop のどちらでもない。stop に action のほかのキーがある
    - monitor の create で、name が無い、cases が 0 個か MAX_MONITOR_CASES 個を超える、条件の文が空か空白だけ、
      repeat が every・once のどちらでもない (repeat が無いときも誤り)
委任の指示の誤り (delegate に DelegateContext を渡したとき):
    - delegate が object でも null でもない。action が無いか、start・update・stop のどれでもない。stop に action の
      ほかのキーがある。start・update に action・tasks・cautions・choices のほかのキーがある
    - 委任中 (DelegateContext.active) に start を書いた (委任中でないときの update と stop は誤りにしない)
    - start で tasks・cautions・choices のどれかを省いたのに、前の委任 (DelegateContext.previous) が無い
    - tasks が配列でないか、要素が 1〜MAX_DELEGATE_TASKS 個でない。cautions が配列でないか、要素が
      0〜MAX_DELEGATE_CAUTIONS 個でない。tasks・cautions の要素が文字列でないか、空か空白だけ
    - choices が配列でないか、要素が 1〜MAX_DELEGATE_CHOICES 個でない。行動が object でない、name か description が
      無いか文字列でない、知らないキーがある、description が空か空白だけ
    - 行動の name が、この JSON の macros を当てた後の登録に無い (actions の macro と同じ照らし方)、
      delegate_questions.RESERVED_OPTION_NAMES の名前、同じ choices の前の行動と同じ名前、または HOME を押すマクロ
      (マクロの操作の列を action_executor.expand_actions で展開した列に、buttons に HOME を含む press がある。マクロの
      操作の列は、この JSON の macros.set にあればそれ、無ければ DelegateContext.macros のもの)
    - start で省いて前の委任から使う choices の行動が、この JSON の macros を当てた後の登録に無いか、HOME を押す
      マクロ。委任中の update で省いて今の委任から使う choices の行動が、この JSON の macros を当てた後の登録に無い
      (HOME を押すかは、下の macros.set の規則で確かめる。委任中でないときの update で省いた choices は確かめない)
    - 委任中 (DelegateContext.active。委任を始める前を含む) に、macros.set で、委任の行動のマクロを HOME を押す
      操作の列にした (その操作の列を action_executor.expand_actions で展開した列に、buttons に HOME を含む press が
      ある)。委任の行動は、この JSON の update が choices を書いていればその choices、そうでなければ今の委任
      (DelegateContext.previous) の choices。delegate のキーの有無と値に依らず確かめる (delegate が無い・null・
      choices を省いた update・stop のどれでも誤り。stop で委任を終える JSON でも誤り)。委任の行動に使っていない
      マクロは、HOME を押す列でも登録できる
    - 委任中に、今の委任 (DelegateContext.previous) の choices の name を macros.delete に書いた (同じ JSON に update
      や stop があっても誤り)
値の直し方:
    PressAction.buttons は pad_action.sort_buttons の順 (重複を除く)、スティックの値と秒数は float にする。
    seconds を省いた press は seconds=None (短押し)。procedure の args は JSON の値のまま入れる。
    JSON に無いキーは、Decision の既定値 (空の actions・空の macros・監視と委任の指示なし) にする。monitor と
    delegate の null も None。tasks・cautions・choices の文と名前は書かれたまま (前後の空白も残す) 入れる。
誤りの説明:
    見つけた誤りをすべて、1 つずつ別の文にして DecisionError.messages に入れる。値の型が違うときは、その値の中は
    確かめない。キーや値についての誤りは `<パス>: <説明>` の形にする。パスは最上位のキーから `.<キー>` と
    `[<位置>]` (0 始まり) でたどる (例: `actions[1].buttons[0]`、`macros.set.next_message[0].type`、
    `monitor.cases[2].condition`)。誤りごとのパスは次のとおり:
        値の誤り・知らないキー               その値 (キー) のパス
        要るキーが無い                       そのキーのパス (例: type の無い操作は `actions[0].type`)
        press に buttons も sticks も無い     その操作のパス (`actions[0]`)
        十字キーが 2 つ以上                   buttons のパス (`actions[0].buttons`)
        スティックの長さが 1 を超える          そのスティックのパス (`actions[0].sticks.left`)
        条件の数が 0 個か上限を超える          cases のパス (`monitor.cases`)
        登録に無いマクロ                      その操作の name のパス (`actions[0].name`)
        マクロの中の macro                    その操作の type のパス (`macros.set.next_message[0].type`)
        手順の引数                           args の下のパス (`actions[0].args.count`。args が object でなければ
                                             `actions[0].args`)
        委任の指示の形の誤り                  その値のパス (`delegate`・`delegate.action`・`delegate.tasks[0]`・
                                             `delegate.choices[1].description` など)
        委任中の start                        `delegate.action`
        前の委任が無いのに省いたキー          そのキーのパス (例: `delegate.tasks`)
        tasks・cautions・choices の数         その配列のパス (例: `delegate.choices`)
        行動の name の誤り (登録に無い・予約の名前・重複・HOME を押すマクロ)
                                             その行動の name のパス (`delegate.choices[1].name`)
        省いて前の委任か今の委任から使う choices の誤り
                                             `delegate.choices`
        委任中に、委任の行動のマクロを HOME を押す操作の列にする
                                             そのマクロの macros.set のパス (`macros.set.<名前>`)
        委任中の行動のマクロを消す            その名前のパス (`macros.delete[0]`)
    コードブロックが無い・最後のブロックが閉じていない・JSON として読めない・最上位が object でないときは、パスを
    付けない文にする。読み取りの中で想定外の例外が出たときも、例外を外に出さず、UNEXPECTED_ERROR_MESSAGE に
    例外の説明を足した、パスを付けない文 1 つの誤りにする。
    説明の文は英語にする。
"""

import json
import logging
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from switch2_remote.controller_state import BUTTON_ORDER, DPAD_DIRECTIONS

from switch2_layers.error_text import describe_exception
from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.decision_types import (
    Action,
    Decision,
    DecisionError,
    DelegateContext,
    MacroAction,
    MonitorCase,
    MonitorCreate,
    MonitorStop,
    PressAction,
    ProcedureAction,
    RepeatMode,
    StickName,
    WaitAction,
)
from switch2_layers.orchestrator.json_values import (
    finite_float,
    is_number,
    seconds_problem,
)
from switch2_layers.orchestrator.procedures import (
    PROCEDURE_NAMES,
    procedure_argument_errors,
)
from switch2_layers.pad_action import sort_buttons

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# 最上位のキー
TOP_LEVEL_KEYS: tuple[str, ...] = ("actions", "macros", "monitor")
# --mode delegate の最上位のキー (parse_decision の delegate に DelegateContext を渡したとき)
DELEGATE_TOP_LEVEL_KEYS: tuple[str, ...] = ("actions", "macros", "delegate")
# delegate の action
DELEGATE_START = "start"
DELEGATE_UPDATE = "update"
DELEGATE_STOP = "stop"
# 委任の行動 (choices)・タスク (tasks)・注意事項 (cautions) の数の上限。tasks は 1 個以上、cautions は 0 個でもよい
MAX_DELEGATE_CHOICES = 12
MAX_DELEGATE_TASKS = 10
MAX_DELEGATE_CAUTIONS = 10
# 操作の type
ACTION_TYPES: tuple[str, ...] = ("press", "wait", "procedure", "macro")
# press と wait の seconds の上限 (秒)。hardware.pad_output.MAX_HOLD_SECONDS と同じ
MAX_ACTION_SECONDS = MAX_HOLD_SECONDS
# 監視の条件の数の上限
MAX_MONITOR_CASES = 8
# monitor の action
MONITOR_CREATE = "create"
MONITOR_STOP = "stop"
# 監視の条件の repeat
REPEAT_MODES: tuple[str, ...] = ("every", "once")

# 操作の type ごとの、使えるキー
_ACTION_KEYS: dict[str, tuple[str, ...]] = {
    "press": ("type", "buttons", "sticks", "seconds"),
    "wait": ("type", "seconds"),
    "procedure": ("type", "name", "args"),
    "macro": ("type", "name"),
}
_MACROS_KEYS = ("set", "delete")
_MONITOR_CREATE_KEYS = ("action", "name", "cases")
_MONITOR_STOP_KEYS = ("action",)
_CASE_KEYS = ("condition", "actions", "repeat")
_STICK_AXES = ("x", "y")
# コードブロックの開きの行と閉じの行 (行の末尾の \r は、行に分けるときに外す)
_OPEN_FENCE = re.compile(r"```json[ \t]*")
_CLOSE_FENCE = re.compile(r"```[ \t]*")

NO_BLOCK_MESSAGE = "no ```json block in the response"
UNCLOSED_BLOCK_MESSAGE = (
    "the last ```json block is not closed (the response may be cut off)"
)
NOT_OBJECT_MESSAGE = "the JSON in the ```json block must be an object"
UNEXPECTED_ERROR_MESSAGE = "unexpected error while reading the ```json block"


@dataclass(frozen=True)
class _ScannedBlocks:
    """本文から見つけた ```json のブロック。

    blocks          閉じたブロックの中身 (出てくる順)
    unclosed_tail   最後の閉じたブロックより後に、閉じていない開きの行があれば True
    """

    blocks: tuple[str, ...]
    unclosed_tail: bool


def _scan_json_blocks(text: str) -> _ScannedBlocks:
    """text を行に分けて、モジュールの docstring の「コードブロック」の規則で ```json のブロックを探す。"""
    blocks: list[str] = []
    content: list[str] | None = None
    for raw_line in text.split("\n"):
        line = raw_line.removesuffix("\r")
        if content is None:
            if _OPEN_FENCE.fullmatch(line):
                content = []
        elif _CLOSE_FENCE.fullmatch(line):
            blocks.append("\n".join(content))
            content = None
        else:
            content.append(line)
    return _ScannedBlocks(blocks=tuple(blocks), unclosed_tail=content is not None)


def find_last_json_block(text: str) -> str | None:
    """text の最後の ```json のコードブロックの中身 (開きと閉じの行を除いた行) を返す。無ければ None。

    ブロックの見つけ方はモジュールの docstring の「コードブロック」のとおり。最後の閉じたブロックより後の閉じていない
    開きの行は、この関数では見ない (has_unclosed_json_block_at_end で確かめる)。
    """
    blocks = _scan_json_blocks(text).blocks
    return blocks[-1] if blocks else None


def has_unclosed_json_block_at_end(text: str) -> bool:
    """text の最後の閉じた ```json のブロックより後に (閉じたブロックが無ければ text のどこかに)、閉じていない
    ```json の開きの行があれば True を返す。開きと閉じの行の見分け方は、モジュールの docstring の「コードブロック」の
    とおり。
    """
    return _scan_json_blocks(text).unclosed_tail


def parse_decision(
    text: str,
    registered_macros: Collection[str] = frozenset(),
    delegate: DelegateContext | None = None,
) -> Decision | DecisionError:
    """応答の本文 text の最後の ```json のコードブロックを読み、モジュールの docstring の規則で確かめた結果を返す。

    registered_macros は、今登録しているマクロの名前 (OrchestratorController.macro_names() の値)。macro の name を、
    これにこの JSON の macros を当てた後の登録と照らす。省くと、登録が空として照らす (この JSON の macros.set で
    登録する名前だけを呼べる)。
    delegate は --mode delegate のときの今の委任の状態 (OrchestratorController.delegate_context() の値)。渡すと、
    最上位のキーを DELEGATE_TOP_LEVEL_KEYS にして委任の指示を読み、渡さなければ (--mode orchestrator) TOP_LEVEL_KEYS
    にして監視の指示を読む。
    誤りが 1 つでもあれば DecisionError を返し、Decision は返さない (操作・マクロ・監視・委任のどれも変えないため)。
    例外は出さない (どんな text でも Decision か DecisionError を返す)。
    """
    if delegate is not None:
        raise NotImplementedError("parse_decision for --mode delegate")
    scanned = _scan_json_blocks(text)
    if scanned.unclosed_tail:
        return DecisionError((UNCLOSED_BLOCK_MESSAGE,))
    if not scanned.blocks:
        return DecisionError((NO_BLOCK_MESSAGE,))
    try:
        value = json.loads(scanned.blocks[-1])
    except (ValueError, RecursionError) as error:
        return DecisionError((f"the ```json block is not valid JSON: {error}",))
    if not isinstance(value, dict):
        return DecisionError((NOT_OBJECT_MESSAGE,))
    reader = _DecisionReader(frozenset(registered_macros))
    try:
        decision = reader.read(value)
    except Exception as error:
        # どんな本文でも例外を出さない契約のため、読み取りの想定外の失敗も誤りとして返す
        logger.warning("unexpected error while reading a decision", exc_info=True)
        return DecisionError(
            (f"{UNEXPECTED_ERROR_MESSAGE}: {describe_exception(error)}",)
        )
    if reader.errors:
        return DecisionError(tuple(reader.errors))
    return decision


class _DecisionReader:
    """JSON の object を、モジュールの docstring の規則で確かめながら Decision にする。誤りは errors に溜める。"""

    def __init__(self, registered_macros: frozenset[str]) -> None:
        self.registered_macros = registered_macros
        self.errors: list[str] = []
        # この JSON の macros を当てた後に呼べるマクロの名前 (read の中で決める)
        self.available_macros: frozenset[str] = registered_macros

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def reject_unknown_keys(
        self, value: Mapping[str, object], allowed: tuple[str, ...], prefix: str
    ) -> None:
        """value の allowed に無いキーを、`<prefix><キー>` のパスの誤りにする。"""
        for key in value:
            if key not in allowed:
                self.error(f"{prefix}{key}", "unknown key")

    def read(self, value: Mapping[str, object]) -> Decision:
        self.reject_unknown_keys(value, TOP_LEVEL_KEYS, "")
        set_value, delete_names = self.read_macro_names(value)
        self.available_macros = (
            self.registered_macros | frozenset(set_value or {})
        ) - frozenset(delete_names)
        macros_set: dict[str, tuple[Action, ...]] = {}
        if set_value is not None:
            for name, actions in set_value.items():
                macros_set[name] = self.read_actions(
                    actions, f"macros.set.{name}", in_macro=True
                )
        actions: tuple[Action, ...] = ()
        if "actions" in value:
            actions = self.read_actions(value["actions"], "actions", in_macro=False)
        monitor = self.read_monitor(value)
        return Decision(
            actions=actions,
            macros_set=macros_set,
            macros_delete=tuple(delete_names),
            monitor=monitor,
        )

    def read_macro_names(
        self, value: Mapping[str, object]
    ) -> tuple[dict[str, object] | None, list[str]]:
        """macros を確かめ、set の object (無いか誤りなら None) と delete の名前の並びを返す。"""
        if "macros" not in value:
            return None, []
        macros = value["macros"]
        if not isinstance(macros, dict):
            self.error("macros", "must be an object")
            return None, []
        self.reject_unknown_keys(macros, _MACROS_KEYS, "macros.")
        set_value: dict[str, object] | None = None
        if "set" in macros:
            if isinstance(macros["set"], dict):
                set_value = macros["set"]
            else:
                self.error("macros.set", "must be an object of macro names")
        delete_names: list[str] = []
        if "delete" in macros:
            raw_delete = macros["delete"]
            if not isinstance(raw_delete, list):
                self.error("macros.delete", "must be an array of macro names")
            else:
                for index, name in enumerate(raw_delete):
                    if isinstance(name, str):
                        delete_names.append(name)
                    else:
                        self.error(f"macros.delete[{index}]", "must be a string")
        return set_value, delete_names

    def read_actions(
        self, value: object, path: str, *, in_macro: bool
    ) -> tuple[Action, ...]:
        if not isinstance(value, list):
            self.error(path, "must be an array of actions")
            return ()
        actions: list[Action] = []
        for index, item in enumerate(value):
            action = self.read_action(item, f"{path}[{index}]", in_macro=in_macro)
            if action is not None:
                actions.append(action)
        return tuple(actions)

    def read_action(self, value: object, path: str, *, in_macro: bool) -> Action | None:
        if not isinstance(value, dict):
            self.error(path, "must be an object")
            return None
        if "type" not in value:
            self.error(f"{path}.type", "missing")
            return None
        action_type = value["type"]
        if not isinstance(action_type, str) or action_type not in ACTION_TYPES:
            self.error(f"{path}.type", f"must be one of {', '.join(ACTION_TYPES)}")
            return None
        self.reject_unknown_keys(value, _ACTION_KEYS[action_type], f"{path}.")
        if action_type == "press":
            return self.read_press(value, path)
        if action_type == "wait":
            return self.read_wait(value, path)
        if action_type == "procedure":
            return self.read_procedure(value, path)
        return self.read_macro_call(value, path, in_macro=in_macro)

    def read_seconds(self, value: object, path: str) -> float | None:
        problem = seconds_problem(value, MAX_ACTION_SECONDS, allow_zero=False)
        if problem is not None or not is_number(value):
            self.error(path, problem or "must be a number")
            return None
        return float(value)

    def read_press(self, value: Mapping[str, object], path: str) -> PressAction | None:
        buttons: tuple[str, ...] = ()
        has_buttons = False
        if "buttons" in value:
            raw_buttons = value["buttons"]
            has_buttons = not isinstance(raw_buttons, list) or bool(raw_buttons)
            buttons = self.read_buttons(raw_buttons, f"{path}.buttons")
        sticks: dict[StickName, tuple[float, float]] = {}
        has_sticks = False
        if "sticks" in value:
            raw_sticks = value["sticks"]
            has_sticks = not isinstance(raw_sticks, dict) or bool(raw_sticks)
            sticks = self.read_sticks(raw_sticks, f"{path}.sticks")
        if not has_buttons and not has_sticks:
            self.error(path, "press needs buttons or sticks")
        seconds: float | None = None
        if "seconds" in value:
            seconds = self.read_seconds(value["seconds"], f"{path}.seconds")
        return PressAction(buttons=buttons, sticks=sticks, seconds=seconds)

    def read_buttons(self, value: object, path: str) -> tuple[str, ...]:
        if not isinstance(value, list):
            self.error(path, "must be an array of button names")
            return ()
        names: list[str] = []
        for index, name in enumerate(value):
            if not isinstance(name, str):
                self.error(f"{path}[{index}]", "must be a string")
            elif name not in BUTTON_ORDER:
                self.error(f"{path}[{index}]", f"unknown button {name!r}")
            else:
                names.append(name)
        directions = {name for name in names if name in DPAD_DIRECTIONS}
        if len(directions) >= 2:
            self.error(path, "must not have two or more d-pad directions")
        return tuple(sort_buttons(names))

    def read_sticks(
        self, value: object, path: str
    ) -> dict[StickName, tuple[float, float]]:
        if not isinstance(value, dict):
            self.error(path, "must be an object of left and right")
            return {}
        sticks: dict[StickName, tuple[float, float]] = {}
        for key, stick_value in value.items():
            stick_path = f"{path}.{key}"
            if key == "left" or key == "right":
                position = self.read_stick(stick_value, stick_path)
                if position is not None:
                    sticks[key] = position
            else:
                self.error(stick_path, "unknown stick (must be left or right)")
        return sticks

    def read_stick(self, value: object, path: str) -> tuple[float, float] | None:
        if not isinstance(value, dict):
            self.error(path, "must be an object with x and y")
            return None
        self.reject_unknown_keys(value, _STICK_AXES, f"{path}.")
        coordinates: list[float] = []
        for axis in _STICK_AXES:
            axis_path = f"{path}.{axis}"
            if axis not in value:
                self.error(axis_path, "missing")
                continue
            coordinate = value[axis]
            if not is_number(coordinate):
                self.error(axis_path, "must be a number")
                continue
            # float に収まらない大きな整数は、範囲の外として扱う
            number = finite_float(coordinate)
            if number is None or not -1.0 <= number <= 1.0:
                self.error(axis_path, "must be between -1 and 1")
                continue
            coordinates.append(number)
        if len(coordinates) != len(_STICK_AXES):
            return None
        x, y = coordinates
        if math.hypot(x, y) > 1.0:
            self.error(path, "the length of (x, y) must be 1 or less")
            return None
        return (x, y)

    def read_wait(self, value: Mapping[str, object], path: str) -> WaitAction | None:
        if "seconds" not in value:
            self.error(f"{path}.seconds", "missing")
            return None
        seconds = self.read_seconds(value["seconds"], f"{path}.seconds")
        return None if seconds is None else WaitAction(seconds=seconds)

    def read_procedure(
        self, value: Mapping[str, object], path: str
    ) -> ProcedureAction | None:
        name = value.get("name")
        if not isinstance(name, str) or name not in PROCEDURE_NAMES:
            self.error(f"{path}.name", f"must be one of {', '.join(PROCEDURE_NAMES)}")
            return None
        if "args" not in value:
            self.error(f"{path}.args", "missing")
            return None
        args = value["args"]
        if not isinstance(args, dict):
            self.error(f"{path}.args", "must be an object")
            return None
        for message in procedure_argument_errors(name, args):
            self.errors.append(f"{path}.args.{message}")
        return ProcedureAction(name=name, args=dict(args))

    def read_macro_call(
        self, value: Mapping[str, object], path: str, *, in_macro: bool
    ) -> MacroAction | None:
        if in_macro:
            self.error(f"{path}.type", "a macro cannot call a macro")
            return None
        if "name" not in value:
            self.error(f"{path}.name", "missing")
            return None
        name = value["name"]
        if not isinstance(name, str):
            self.error(f"{path}.name", "must be a string")
            return None
        if name not in self.available_macros:
            self.error(f"{path}.name", f"macro {name!r} is not registered")
            return None
        return MacroAction(name=name)

    def read_monitor(
        self, value: Mapping[str, object]
    ) -> MonitorCreate | MonitorStop | None:
        monitor = value.get("monitor")
        if monitor is None:
            return None
        if not isinstance(monitor, dict):
            self.error("monitor", "must be an object or null")
            return None
        if "action" not in monitor:
            self.error("monitor.action", "missing")
            return None
        action = monitor["action"]
        if action == MONITOR_STOP:
            self.reject_unknown_keys(monitor, _MONITOR_STOP_KEYS, "monitor.")
            return MonitorStop()
        if action != MONITOR_CREATE:
            self.error("monitor.action", f"must be {MONITOR_CREATE} or {MONITOR_STOP}")
            return None
        self.reject_unknown_keys(monitor, _MONITOR_CREATE_KEYS, "monitor.")
        name = monitor.get("name")
        if "name" not in monitor:
            self.error("monitor.name", "missing")
        elif not isinstance(name, str):
            self.error("monitor.name", "must be a string")
        cases = self.read_cases(monitor)
        if not isinstance(name, str):
            return None
        return MonitorCreate(name=name, cases=cases)

    def read_cases(self, monitor: Mapping[str, object]) -> tuple[MonitorCase, ...]:
        if "cases" not in monitor:
            self.error("monitor.cases", "missing")
            return ()
        raw_cases = monitor["cases"]
        if not isinstance(raw_cases, list):
            self.error("monitor.cases", "must be an array of cases")
            return ()
        if not 1 <= len(raw_cases) <= MAX_MONITOR_CASES:
            self.error("monitor.cases", f"must have 1 to {MAX_MONITOR_CASES} cases")
        cases: list[MonitorCase] = []
        for index, raw_case in enumerate(raw_cases):
            case = self.read_case(raw_case, f"monitor.cases[{index}]")
            if case is not None:
                cases.append(case)
        return tuple(cases)

    def read_case(self, value: object, path: str) -> MonitorCase | None:
        if not isinstance(value, dict):
            self.error(path, "must be an object")
            return None
        self.reject_unknown_keys(value, _CASE_KEYS, f"{path}.")
        condition = value.get("condition")
        if "condition" not in value:
            self.error(f"{path}.condition", "missing")
        elif not isinstance(condition, str):
            self.error(f"{path}.condition", "must be a string")
        elif not condition.strip():
            self.error(f"{path}.condition", "must not be empty")
        actions: tuple[Action, ...] = ()
        if "actions" in value:
            actions = self.read_actions(
                value["actions"], f"{path}.actions", in_macro=False
            )
        else:
            self.error(f"{path}.actions", "missing")
        repeat = value.get("repeat")
        repeat_mode: RepeatMode | None = None
        if "repeat" not in value:
            self.error(f"{path}.repeat", "missing")
        elif repeat == "every" or repeat == "once":
            repeat_mode = repeat
        else:
            self.error(f"{path}.repeat", f"must be one of {', '.join(REPEAT_MODES)}")
        if not isinstance(condition, str) or repeat_mode is None:
            return None
        return MonitorCase(condition=condition, actions=actions, repeat=repeat_mode)
