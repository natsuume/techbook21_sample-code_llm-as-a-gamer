"""surface の操作の関門 (SurfaceOutputGate): 押せるボタンの制限と、段階が play のときだけ送ること。

surface のループ (surface_loop) は hardware.pad_output.PadOutput を直接触らず、この関門を通して操作を送る。
    - 押せるボタンは buttons.allowed_buttons() (HOME を除く 16 個)。PadOutput は HOME を含む 17 個を押せるように作り
      (本体の画面の操作と press_home の HOME のため)、surface の操作はここで 16 個に絞る
    - 段階が play でないときは送らない (play から離れるときの中立のレポートは、段階を変える側の app が送る)
「play か」の確かめと送信は、段階を変える処理と同じロックの中で行う (確かめた後、送る前に段階が変わらないようにする)。
"""

import threading
from collections.abc import Callable

from switch2_layers.buttons import FilteredAction, allowed_buttons, filter_action
from switch2_layers.hardware.pad_output import OutputStoppedError, PadOutput
from switch2_layers.pad_action import PadAction


class SurfaceOutputBlockedError(OutputStoppedError):
    """段階が play でないため、surface の操作を送らなかった。操作は送っていない。

    hardware.pad_output.OutputStoppedError の子クラスにし、surface のループは出力を止めたときと同じに扱う。
    """


class SurfaceOutputGate:
    """surface の操作を、押せるボタンに絞り、段階が play のときだけ送る。

    pad_output   操作の送信先 (HOME を含む 17 個を押せる PadOutput)
    is_play      今の段階が play なら True を返す関数 (lock を持っているときに呼ぶ)
    lock         段階を変える処理と同じロック。is_play の確かめと送信をこの中で行う
    """

    def __init__(
        self,
        pad_output: PadOutput,
        is_play: Callable[[], bool],
        lock: threading.Lock,
    ) -> None:
        """部品を持つ。ここでは何も送らない。"""
        self._pad_output = pad_output
        self._is_play = is_play
        self._lock = lock
        self._allowed_buttons = allowed_buttons()

    def apply(self, action: PadAction) -> FilteredAction:
        """action を buttons.filter_action(action, allowed_buttons) で絞り、段階が play なら送る。

        lock の中で次を行う:
            1. is_play() が False なら、何も送らずに SurfaceOutputBlockedError を出す
            2. 絞った操作を pad_output.apply(..., source="apply") で送る
        戻り値は FilteredAction(action=pad_output.apply が返した操作 (相反する方向などを外した後),
        blocked_buttons=1 の絞り込みで外したボタン)。pad_output.apply の例外 (OutputStoppedError・送信の失敗) は
        そのまま出す。
        """
        filtered = filter_action(action, self._allowed_buttons)
        with self._lock:
            if not self._is_play():
                raise SurfaceOutputBlockedError(
                    "the surface action was not sent outside the play phase"
                )
            sent = self._pad_output.apply(filtered.action, source="apply")
        return FilteredAction(
            action=sent.action, blocked_buttons=filtered.blocked_buttons
        )

    def neutral(self) -> None:
        """lock の中で、段階が play なら pad_output.neutral() を送る。play でなければ何もしない。"""
        with self._lock:
            if self._is_play():
                self._pad_output.neutral()

    @property
    def allowed_buttons(self) -> frozenset[str]:
        """surface の押せるボタンの集合 (buttons.allowed_buttons() の 16 個)。"""
        return self._allowed_buttons
