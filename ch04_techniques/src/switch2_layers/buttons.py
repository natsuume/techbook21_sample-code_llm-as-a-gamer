"""押せるボタンの集合と、コントローラーへ送る直前のボタンの制限。

押せるボタンは switch2_remote.controller_state.BUTTON_ORDER の 17 個から HOME を除いた 16 個。
--allow-home を付けたときだけ HOME を足す (ゲームの起動・終了まで含めて検証する場合)。
制限は hardware.pad_output.PadOutput.apply が送る直前に filter_action で行い、外したボタンは
surface.jsonl の blocked_buttons に記録する。
"""

from collections.abc import Collection
from dataclasses import dataclass

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.pad_action import PadAction

HOME_BUTTON = "HOME"
# コントローラーのボタンのすべて (17 個)
ALL_BUTTONS: tuple[str, ...] = BUTTON_ORDER


def allowed_buttons(allow_home: bool) -> frozenset[str]:
    """押せるボタンの集合を返す。allow_home が False なら ALL_BUTTONS から HOME を除いた 16 個、True なら 17 個。"""
    raise NotImplementedError


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
    raise NotImplementedError
