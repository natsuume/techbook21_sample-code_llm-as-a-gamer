"""主ループの Claude Code の応答の本文から、最後の ```json のコードブロックを読み、Decision にする。I/O を持たない。

コードブロック:
    行頭の ```json (後ろに空白だけを許す) の行から、次の行頭の ``` (後ろに空白だけを許す) の行までの間の文字列。
    本文に複数あれば、最後のものだけを読む。閉じていないブロックは数えない。改行は \\n か \\r\\n
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
誤りにするもの:
    - コードブロックが無い、JSON として読めない (json.loads の NaN・Infinity・-Infinity と、1e999 のように float で
      無限大になる数は読めるが、下の秒数・スティックの規則で誤りになる)、最上位が object でない
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
    - procedure の name が procedures.PROCEDURE_NAMES に無い、または args が手順の引数に合わない
      (procedures.procedure_argument_errors)
    - macro の name が、この JSON の macros を当てた後の登録に無い。当てた後の登録は、registered_macros に
      macros.set の名前を足し、macros.delete の名前を除いたもの (set と delete の両方にある名前は、除いた側になる。
      macro_registry.MacroRegistry.apply_changes と同じ順)。actions と monitor の条件の actions の両方で確かめる
    - macros.set のマクロの操作の列に macro がある (マクロの中からマクロは呼べない)
    - monitor の action が create・stop のどちらでもない。stop に action のほかのキーがある
    - monitor の create で、name が無い、cases が 0 個か MAX_MONITOR_CASES 個を超える、条件の文が空か空白だけ、
      repeat が every・once のどちらでもない (repeat が無いときも誤り)
値の直し方:
    PressAction.buttons は pad_action.sort_buttons の順 (重複を除く)、スティックの値と秒数は float にする。
    seconds を省いた press は seconds=None (短押し)。procedure の args は JSON の値のまま入れる。
    JSON に無いキーは、Decision の既定値 (空の actions・空の macros・監視の指示なし) にする。monitor の null も None。
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
    コードブロックが無い・JSON として読めない・最上位が object でないときは、パスを付けない文にする。
    説明の文は英語にする。
"""

from collections.abc import Collection

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.decision_types import Decision, DecisionError

# 最上位のキー
TOP_LEVEL_KEYS: tuple[str, ...] = ("actions", "macros", "monitor")
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


def find_last_json_block(text: str) -> str | None:
    """text の最後の ```json のコードブロックの中身 (開きと閉じの行を除いた行) を返す。無ければ None。

    ブロックの見つけ方はモジュールの docstring の「コードブロック」のとおり。
    """
    raise NotImplementedError


def parse_decision(
    text: str, registered_macros: Collection[str] = frozenset()
) -> Decision | DecisionError:
    """応答の本文 text の最後の ```json のコードブロックを読み、モジュールの docstring の規則で確かめた結果を返す。

    registered_macros は、今登録しているマクロの名前 (OrchestratorController.macro_names() の値)。macro の name を、
    これにこの JSON の macros を当てた後の登録と照らす。省くと、登録が空として照らす (この JSON の macros.set で
    登録する名前だけを呼べる)。
    誤りが 1 つでもあれば DecisionError を返し、Decision は返さない (操作・マクロ・監視のどれも変えないため)。
    例外は出さない (どんな text でも Decision か DecisionError を返す)。
    """
    raise NotImplementedError
