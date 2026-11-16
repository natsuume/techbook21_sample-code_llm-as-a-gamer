"""コードの手順 (procedure): 引数の確かめと、PressAction と WaitAction の列への展開。I/O・スレッド・時刻を扱わない。

手順の一覧 (PROCEDURE_NAMES):
    名前         引数                                                     展開
    tap_repeat   button (ボタン名)、count (回数)、                         button の短押しと interval_seconds 秒の待ちを
                 interval_seconds (0 以上、60 以下)                         count 回
    walk         direction (UP・RIGHT・DOWN・LEFT)、count (回数)、         direction を seconds 秒押す操作と
                 seconds (0 より大きく 60 以下)、                           interval_seconds 秒の待ちを count 回
                 interval_seconds (0 以上、60 以下)
引数の値の規則:
    ボタン名   switch2_remote.controller_state.BUTTON_ORDER の名前 (大文字小文字を区別する)。押せないボタン (HOME など) も
               ここでは誤りにしない (実行のときに PadOutput.apply が外す)
    回数       1 以上 MAX_PROCEDURE_COUNT (100) 以下の整数。MAX_PROCEDURE_COUNT を超えたら誤り
    整数       JSON の整数 (int。bool と小数は誤り)
    秒数       JSON の数 (int か float。bool は誤り)。有限の値で、範囲は上の表のとおり
展開は、どの手順も (押す操作, 待ち) の組を count 回並べた 2 * count 個の列にする。interval_seconds が 0 でも、0 秒の
待ちを列に入れる (主ループに知らせる残りの操作の数を 2 * count にそろえるため)。
"""

from collections.abc import Mapping

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.decision_types import PressAction, WaitAction
from switch2_layers.orchestrator.json_values import (
    is_integer,
    is_number,
    seconds_problem,
)

TAP_REPEAT = "tap_repeat"
WALK = "walk"
# 手順の名前 (この順に、主ループへの指示で説明する)
PROCEDURE_NAMES: tuple[str, ...] = (TAP_REPEAT, WALK)
# 手順の名前 → 引数の名前 (この順に確かめる)
PROCEDURE_ARGUMENTS: dict[str, tuple[str, ...]] = {
    TAP_REPEAT: ("button", "count", "interval_seconds"),
    WALK: ("direction", "count", "seconds", "interval_seconds"),
}
# walk の direction に使える十字キーの方向
WALK_DIRECTIONS: tuple[str, ...] = ("UP", "RIGHT", "DOWN", "LEFT")
# interval_seconds と seconds の上限 (秒)。press の seconds と同じ hardware.pad_output.MAX_HOLD_SECONDS
MAX_PROCEDURE_SECONDS = MAX_HOLD_SECONDS
# count (手順を繰り返す回数) の上限
MAX_PROCEDURE_COUNT = 100


def procedure_argument_errors(name: str, args: Mapping[str, object]) -> list[str]:
    """args が手順 name の引数に合わない理由を、`<引数の名前>: <説明>` の文の並びで返す。合っていれば空のリスト。

    次をそれぞれ 1 つの誤りにする (見つけたものをすべて返す):
        - PROCEDURE_ARGUMENTS[name] の引数が args に無い
        - args に、PROCEDURE_ARGUMENTS[name] に無い名前がある
        - 引数の値が、モジュールの docstring の値の規則に合わない
    name が PROCEDURE_NAMES に無ければ ValueError (名前は呼び出し側が先に確かめる)。
    """
    if name not in PROCEDURE_ARGUMENTS:
        raise ValueError(f"unknown procedure: {name!r}")
    expected = PROCEDURE_ARGUMENTS[name]
    errors: list[str] = []
    for key in expected:
        if key not in args:
            errors.append(f"{key}: missing")
            continue
        problem = _argument_problem(key, args[key])
        if problem is not None:
            errors.append(f"{key}: {problem}")
    errors.extend(f"{key}: unknown argument" for key in args if key not in expected)
    return errors


def _argument_problem(key: str, value: object) -> str | None:
    """引数 key の値 value がモジュールの docstring の値の規則に合わなければ、その理由の文を返す。合えば None。"""
    if key == "button":
        if not isinstance(value, str) or value not in BUTTON_ORDER:
            return f"unknown button {value!r}"
        return None
    if key == "direction":
        if not isinstance(value, str) or value not in WALK_DIRECTIONS:
            return f"must be one of {', '.join(WALK_DIRECTIONS)}"
        return None
    if key == "count":
        if not is_integer(value) or not 1 <= value <= MAX_PROCEDURE_COUNT:
            return f"must be an integer from 1 to {MAX_PROCEDURE_COUNT}"
        return None
    if key == "seconds":
        return seconds_problem(value, MAX_PROCEDURE_SECONDS, allow_zero=False)
    # interval_seconds
    return seconds_problem(value, MAX_PROCEDURE_SECONDS, allow_zero=True)


def expand_procedure(
    name: str, args: Mapping[str, object]
) -> tuple[PressAction | WaitAction, ...]:
    """手順 name を args で展開した操作の列を返す (モジュールの docstring の表の「展開」)。

    tap_repeat   (PressAction(buttons=(button,)), WaitAction(interval_seconds)) を count 回
    walk         (PressAction(buttons=(direction,), seconds=seconds), WaitAction(interval_seconds)) を count 回
    秒数は float にする。procedure_argument_errors(name, args) が空でなければ (引数が合わなければ) ValueError。
    """
    errors = procedure_argument_errors(name, args)
    if errors:
        raise ValueError(f"invalid arguments for {name}: {'; '.join(errors)}")
    wait = WaitAction(seconds=_checked_number(args["interval_seconds"]))
    if name == TAP_REPEAT:
        press = PressAction(buttons=(str(args["button"]),))
    else:
        press = PressAction(
            buttons=(str(args["direction"]),),
            seconds=_checked_number(args["seconds"]),
        )
    count = args["count"]
    if not is_integer(count):
        raise ValueError(f"count of {name} is not an integer: {count!r}")
    return (press, wait) * count


def _checked_number(value: object) -> float:
    """確かめ済みの秒数の値 value を float にして返す。数でなければ ValueError。"""
    if not is_number(value):
        raise ValueError(f"not a number: {value!r}")
    return float(value)
