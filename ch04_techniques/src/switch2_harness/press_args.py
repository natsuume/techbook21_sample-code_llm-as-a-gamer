"""press サブコマンドの引数の検証 (純粋関数)。

press はデーモンの入力のコマンドのうち tap / hold / down / up だけを通し、HOME を押す入力を拒否する。
HOME 画面に戻るときは finish を使う (押したままの入力を離してから HOME を押すため)。

デーモンは受け取った 1 行を str.split() で分け直してから解釈する (switch2_remote.commands.handle_command)。
そのため press の引数も、送る前に同じ分け方 (split_press_args) で分け直し、分け直したトークンを調べて送る。
こうしないと、`"A HOME"` のように空白を含む 1 つの引数が検査を通り、デーモンでは HOME として押される。
"""

from collections.abc import Sequence

# press で送れるデーモンのコマンド
PRESS_ACTIONS: tuple[str, ...] = ("tap", "hold", "down", "up")

# press では押せないボタン
FORBIDDEN_BUTTON = "HOME"

HOME_REJECTED_MESSAGE = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)

# press tap で 1 回に送れる CHORD の数の上限。
# デーモンは tap を送り終えるまで次のコマンドを受け付けず (1 つの CHORD に約 370 ミリ秒)、
# run の後片付けの up ALL は実行中の tap の後に処理される。10 個 (約 3.7 秒) に限ると、
# run を止めたときに実行中の tap が終わり、up ALL が後片付けの応答の上限 (10 秒) の中で実行される。
MAX_TAP_CHORDS = 10


def split_press_args(args: Sequence[str]) -> tuple[str, ...]:
    """各引数を str.split() (引数なし。空白の連続も 1 つの区切りにする) で分け、順に並べたタプルを返す。

    空の要素は含めない。例: ["RIGHT RIGHT", "A"] は ("RIGHT", "RIGHT", "A")、[" ", "B"] は ("B",)。
    """
    return tuple(token for arg in args for token in arg.split())


def unknown_action_message(action: str) -> str:
    """PRESS_ACTIONS に無い action を拒否する error 行を返す。

    例: "shot" なら `error press accepts tap / hold / down / up: shot`。
    """
    return f"error press accepts tap / hold / down / up: {action}"


def too_many_tap_chords_message(count: int) -> str:
    """CHORD が MAX_TAP_CHORDS より多い tap を拒否する error 行を返す。

    例: 12 なら `error press tap accepts up to 10 chords at a time: 12`。
    """
    return f"error press tap accepts up to {MAX_TAP_CHORDS} chords at a time: {count}"


def _contains_home(token: str) -> bool:
    """CHORD か TARGET の 1 つのトークンが HOME を含めば True を返す。"""
    return any(part.strip().upper() == FORBIDDEN_BUTTON for part in token.split("+"))


def press_args_error(action: str, tokens: Sequence[str]) -> str | None:
    """press の ACTION と、split_press_args で分け直したトークン列 tokens を検証する。

    拒否するなら error 行を、通すなら None を返す。
    1. action が PRESS_ACTIONS のどれとも完全に一致しなければ (大文字小文字と空白も区別する。
       例: "TAP" や "tap A" は一致しない) unknown_action_message(action) を返す
    2. HOME を含めば HOME_REJECTED_MESSAGE を返す。調べるトークンは次のとおり:
       - tap / down: 各トークン (CHORD)
       - hold: 1 つ目のトークン (CHORD)。2 つ目 (ミリ秒) は調べない。トークンが無ければ何も調べない
       - up: 各トークン (TARGET)
       調べるトークンはそれぞれ + で分け、各部分の前後の空白を除いて大文字にし、FORBIDDEN_BUTTON と比べる
       (例: "home"、"B+HOME" はいずれも HOME を含む)
    3. action が tap で、トークン (CHORD) が MAX_TAP_CHORDS より多ければ too_many_tap_chords_message を返す
    4. どれにも当たらなければ None を返す。それ以外のトークンの数や書式の誤りはデーモンが判定するため、ここでは調べない
    """
    if action not in PRESS_ACTIONS:
        return unknown_action_message(action)
    checked = tokens[:1] if action == "hold" else tokens
    if any(_contains_home(token) for token in checked):
        return HOME_REJECTED_MESSAGE
    if action == "tap" and len(tokens) > MAX_TAP_CHORDS:
        return too_many_tap_chords_message(len(tokens))
    return None
