"""surface が決めた 1 回分の操作の型 (押し続けるボタン・短押しのボタン・2 本のスティック) と、中立の操作。

ボタン名は switch2_remote.controller_state.BUTTON_ORDER の名前 (大文字) を使う。
この型自体は値を検証しない。押せないボタンと知らない名前は buttons.filter_action が外し、
スティックの範囲と十字キーの相反する方向は hardware.pad_output.PadOutput.apply が扱う。
"""

from collections.abc import Iterable
from dataclasses import dataclass

from switch2_remote.controller_state import BUTTON_ORDER

STICK_NEUTRAL: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class PadAction:
    """コントローラーの 1 回分の操作。

    held          押し続けるボタン名の集合 (次の操作まで押したままにする)
    taps          短押しするボタン名の集合 (hardware.pad_output.TAP_PRESS_S 秒だけ押して離す)
    left_stick    左スティックの (x, y)。x は右、y は上を正とし、各 -1.0〜1.0。
                  向きは (x, y) の向き、強さは (x, y) の長さで表す。(0, 0) は倒さない
    right_stick   右スティックの (x, y)。表し方は left_stick と同じ
    """

    held: frozenset[str] = frozenset()
    taps: frozenset[str] = frozenset()
    left_stick: tuple[float, float] = STICK_NEUTRAL
    right_stick: tuple[float, float] = STICK_NEUTRAL


# 何も押さず、スティックも倒さない操作
NEUTRAL_ACTION = PadAction()


def is_neutral(action: PadAction) -> bool:
    """action が held も taps も空で、両方のスティックが (0, 0) なら True を返す。"""
    return (
        not action.held
        and not action.taps
        and tuple(action.left_stick) == STICK_NEUTRAL
        and tuple(action.right_stick) == STICK_NEUTRAL
    )


def sort_buttons(names: Iterable[str]) -> list[str]:
    """ボタン名を重複なく並べたリストを返す。

    BUTTON_ORDER にある名前をその順に並べ、BUTTON_ORDER に無い名前 (知らない名前) は後ろに文字列の順で並べる。
    """
    unique = set(names)
    known = [name for name in BUTTON_ORDER if name in unique]
    unknown = sorted(unique - set(BUTTON_ORDER))
    return known + unknown


def action_to_json(action: PadAction) -> dict[str, object]:
    """action を記録 (surface.jsonl の action、operations.jsonl の held・taps・sticks) の形にする。

    {"held": sort_buttons(held), "taps": sort_buttons(taps), "sticks": {"LS": [x, y], "RS": [x, y]}}
    """
    return {
        "held": sort_buttons(action.held),
        "taps": sort_buttons(action.taps),
        "sticks": {"LS": list(action.left_stick), "RS": list(action.right_stick)},
    }
