"""押せるボタンの集合と、コントローラーへ送る直前のボタンの制限。

コントローラーのボタンは switch2_remote.controller_state.BUTTON_ORDER の 17 個 (ALL_BUTTONS)。
    surface の押せるボタン       ALL_BUTTONS から HOME を除いた 16 個 (allowed_buttons)。surface は HOME を押さない
    本体の画面の操作と press_home の HOME
                                 ALL_BUTTONS の 17 個 (hardware.pad_output.PadOutput はこれで作る)
surface の操作の制限は surface.surface_output_gate.SurfaceOutputGate.apply が送る直前に filter_action で行い、外した
ボタンは surface.jsonl の blocked_buttons に記録する。
"""

from collections.abc import Collection
from dataclasses import dataclass, replace

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.pad_action import PadAction, sort_buttons

HOME_BUTTON = "HOME"
# コントローラーのボタンのすべて (17 個)
ALL_BUTTONS: tuple[str, ...] = BUTTON_ORDER


def allowed_buttons() -> frozenset[str]:
    """surface の押せるボタンの集合 (ALL_BUTTONS から HOME を除いた 16 個) を返す。"""
    return frozenset(ALL_BUTTONS) - {HOME_BUTTON}


@dataclass(frozen=True)
class FilteredAction:
    """ボタンの制限を通した操作。

    action            押せないボタンを held と taps から外した操作
    blocked_buttons   外したボタン名 (pad_action.sort_buttons の順、重複なし)。外さなければ空
    """

    action: PadAction
    blocked_buttons: tuple[str, ...]


def filter_action(action: PadAction, allowed: Collection[str]) -> FilteredAction:
    """action の held と taps から、allowed に無い名前 (押せないボタンと、ボタン名でない知らない名前) を外す。

    blocked_buttons は held と taps のどちらかから外した名前。スティックは変えない。
    何も外さなければ、action と同じ値の操作と空の blocked_buttons を返す。
    """
    allowed_set = frozenset(allowed)
    held = frozenset(action.held) & allowed_set
    taps = frozenset(action.taps) & allowed_set
    blocked = (set(action.held) - allowed_set) | (set(action.taps) - allowed_set)
    if not blocked:
        return FilteredAction(action=action, blocked_buttons=())
    return FilteredAction(
        action=replace(action, held=held, taps=taps),
        blocked_buttons=tuple(sort_buttons(blocked)),
    )
