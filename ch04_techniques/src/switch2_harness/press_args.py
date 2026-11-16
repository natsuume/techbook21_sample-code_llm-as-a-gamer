"""press サブコマンドの引数の検証 (純粋関数)。

press はデーモンの入力のコマンドのうち tap / hold / down / up だけを通し、HOME を押す入力を拒否する。
HOME 画面に戻るときは finish を使う (押したままの入力を離してから HOME を押すため)。
"""

from collections.abc import Sequence

# press で送れるデーモンのコマンド
PRESS_ACTIONS: tuple[str, ...] = ("tap", "hold", "down", "up")

# press では押せないボタン
FORBIDDEN_BUTTON = "HOME"

HOME_REJECTED_MESSAGE = (
    "error HOME cannot be pressed with press. Use finish to return to the HOME screen"
)


def unknown_action_message(action: str) -> str:
    """PRESS_ACTIONS に無い action を拒否する error 行を返す。

    例: "shot" なら `error press accepts tap / hold / down / up: shot`。
    """
    raise NotImplementedError


def press_args_error(action: str, args: Sequence[str]) -> str | None:
    """press の引数を検証し、拒否するなら error 行を、通すなら None を返す。

    1. action が PRESS_ACTIONS に無ければ (大文字小文字を区別する) unknown_action_message(action) を返す
    2. HOME を含めば HOME_REJECTED_MESSAGE を返す。調べる引数は次のとおり:
       - tap / down: 各引数 (CHORD)
       - hold: 1 つ目の引数 (CHORD)。2 つ目 (ミリ秒) は調べない。引数が無ければ何も調べない
       - up: 各引数 (TARGET)
       調べる引数はそれぞれ + で分け、各部分の前後の空白を除いて大文字にし、FORBIDDEN_BUTTON と比べる
       (例: "home"、"B+HOME"、"b + Home" はいずれも HOME を含む)
    3. どちらにも当たらなければ None を返す。引数の数や書式の誤りはデーモンが判定するため、ここでは調べない
    """
    raise NotImplementedError
