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


def split_press_args(args: Sequence[str]) -> tuple[str, ...]:
    """各引数を str.split() (引数なし。空白の連続も 1 つの区切りにする) で分け、順に並べたタプルを返す。

    空の要素は含めない。例: ["RIGHT RIGHT", "A"] は ("RIGHT", "RIGHT", "A")、[" ", "B"] は ("B",)。
    """
    raise NotImplementedError


def unknown_action_message(action: str) -> str:
    """PRESS_ACTIONS に無い action を拒否する error 行を返す。

    例: "shot" なら `error press accepts tap / hold / down / up: shot`。
    """
    raise NotImplementedError


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
    3. どちらにも当たらなければ None を返す。トークンの数や書式の誤りはデーモンが判定するため、ここでは調べない
    """
    raise NotImplementedError
