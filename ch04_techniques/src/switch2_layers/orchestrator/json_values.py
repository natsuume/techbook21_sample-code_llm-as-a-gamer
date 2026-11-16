"""主ループが書く JSON の値 (数・整数・秒数) の確かめ。decision_parser と procedures が使う。I/O を持たない。

JSON の true・false は Python の bool (int の派生型) になるため、どの関数も数として扱わない。
"""

import math
from typing import TypeGuard


def is_number(value: object) -> TypeGuard[int | float]:
    """value が数 (int か float。bool は除く) なら True を返す。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_integer(value: object) -> TypeGuard[int]:
    """value が整数 (int。bool と小数は除く) なら True を返す。"""
    return isinstance(value, int) and not isinstance(value, bool)


def finite_float(value: float) -> float | None:
    """数 value を float にして返す。float に収まらない (桁あふれする大きな整数) か、有限でなければ None。"""
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def seconds_problem(value: object, maximum: float, allow_zero: bool) -> str | None:
    """value が秒数として使えなければ、その理由の英語の文を返す。使えれば None。

    秒数として使えるのは、有限の数で、allow_zero なら 0 以上、そうでなければ 0 より大きく、maximum 以下の値。
    float に収まらない大きな整数は、有限の数でないものとして扱う。
    """
    if not is_number(value):
        return "must be a number"
    seconds = finite_float(value)
    if seconds is None:
        return "must be a finite number"
    if allow_zero and seconds < 0.0:
        return "must be 0 or more"
    if not allow_zero and seconds <= 0.0:
        return "must be more than 0"
    if seconds > maximum:
        return f"must be {maximum:g} or less"
    return None
