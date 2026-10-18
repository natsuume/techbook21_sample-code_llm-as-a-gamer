"""コントローラーの押下状態を表すデータと、その解釈・合成・レポート化を行う純粋関数。

I/O・スレッド・時刻を扱わない。押下状態の保持と送信は controller.Controller が行う。

用語:
    SPEC     ボタン名 A B X Y L R ZL ZR PLUS MINUS HOME UP RIGHT DOWN LEFT (大文字小文字を区別しない)、
             またはスティック LS:x,y / RS:x,y。x は右、y は上を正とし、範囲は -1.0〜1.0。
             スティックの値は小数表記だけを受け付ける (STICK_SPEC_PATTERN に fullmatch する。先頭の +、指数表記、nan、inf は不可)
    CHORD    SPEC を + でつないだもの。含まれる SPEC を同時に押す。
             空の要素 (A+、+A、A++B)、未知の SPEC、同じスティックを 2 回含むもの、
             十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) を含むものは ValueError にする。
             同じボタンの重複 (A+A) は押下状態が変わらないため許す
    TARGET   up で離す対象。ボタン名か LS / RS (大文字小文字を区別しない)。+ でつないでもよい

押下状態の 3 層:
    held     down で押したままにしている状態
    holds    hold の時限押下。複数同時に存在しうる
    tapping  tap で押している最中の 1 CHORD
    送るレポートは 3 層を合成したもの。各層は互いに同じボタン・スティックを含まない
    (merge と add_to_held が、重なる入力を ValueError にして防ぐ)

状態の正規化表記 (format_state):
    ボタンを BUTTON_ORDER の順、続いてスティックを STICK_ORDER の順に並べ、+ でつなぐ。
    スティックは LS:0.5,-1 のように各値を format(v, "g") で書く。何も押していなければ none

8 バイトのレポート (build_report):
    [b1, b2, hat, LX, LY, RX, RY, 0]
    b1   Y=0x01 B=0x02 A=0x04 X=0x08 L=0x10 R=0x20 ZL=0x40 ZR=0x80
    b2   MINUS=0x01 PLUS=0x02 HOME=0x10
    hat  押している十字キーの方向の組を DPAD_HAT で変換した値 (押していなければ 15)
    LX, LY, RX, RY
         スティック 1 軸の値 (stick_axis_to_byte)。Switch のレポートの Y は下向きに増える (0 が上) ため、
         LY / RY には -y を変換した値を入れる。例: LS:1,0 → LX=255, LY=128 / LS:0,1 → LY=0 / LS:-1,-1 → LX=0, LY=255
    中立は 00 00 0f 80 80 80 80 00 (NEUTRAL_REPORT)
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# ボタン名。並びは正規化表記の順でもある
BUTTON_ORDER = (
    "A",
    "B",
    "X",
    "Y",
    "L",
    "R",
    "ZL",
    "ZR",
    "PLUS",
    "MINUS",
    "HOME",
    "UP",
    "RIGHT",
    "DOWN",
    "LEFT",
)
BUTTONS = frozenset(BUTTON_ORDER)
# スティック名。並びは正規化表記の順でもある
STICK_ORDER = ("LS", "RS")
STICKS = frozenset(STICK_ORDER)
# BUTTONS のうち十字キーの方向。レポートではまとめて 1 つの hat の値にする
DPAD_DIRECTIONS = frozenset(["UP", "RIGHT", "DOWN", "LEFT"])
# 十字キーの押下方向の組 → hat の値 (上から時計回りに 0〜7、押していなければ 15)
DPAD_HAT = {
    frozenset(): 15,
    frozenset(["UP"]): 0,
    frozenset(["UP", "RIGHT"]): 1,
    frozenset(["RIGHT"]): 2,
    frozenset(["DOWN", "RIGHT"]): 3,
    frozenset(["DOWN"]): 4,
    frozenset(["DOWN", "LEFT"]): 5,
    frozenset(["LEFT"]): 6,
    frozenset(["UP", "LEFT"]): 7,
}
# 十字キー以外のボタン → (レポートのバイト位置, ビット)
BUTTON_BITS = {
    "Y": (0, 0x01),
    "B": (0, 0x02),
    "A": (0, 0x04),
    "X": (0, 0x08),
    "L": (0, 0x10),
    "R": (0, 0x20),
    "ZL": (0, 0x40),
    "ZR": (0, 0x80),
    "MINUS": (1, 0x01),
    "PLUS": (1, 0x02),
    "HOME": (1, 0x10),
}
# スティックの SPEC (大文字にそろえた後の形)。値は小数表記だけを許す
STICK_SPEC_PATTERN = re.compile(
    r"(LS|RS):(-?[0-9]+(?:\.[0-9]+)?),(-?[0-9]+(?:\.[0-9]+)?)"
)
STICK_NEUTRAL = 128
NEUTRAL_REPORT = bytes([0x00, 0x00, 0x0F, 0x80, 0x80, 0x80, 0x80, 0x00])


@dataclass(frozen=True)
class PadState:
    """ゲームパッドの押下状態。CHORD 1 つ分、または held などの 1 層分を表す。

    buttons は押しているボタン名 (BUTTONS の要素)、sticks はスティック名 (LS / RS) → (x, y)。
    不変の値として扱い、作成後に sticks を書き換えない。状態を変える関数は新しい PadState を返す。
    """

    buttons: frozenset[str] = frozenset()
    sticks: Mapping[str, tuple[float, float]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """ボタンもスティックも含まなければ True を返す。"""
        raise NotImplementedError

    def names(self) -> frozenset[str]:
        """含んでいるボタン名とスティック名 (LS / RS) の集合を返す。up の TARGET との照合に使う。"""
        raise NotImplementedError


def parse_stick_spec(spec: str) -> tuple[str, float, float]:
    """大文字にそろえた `LS:x,y` / `RS:x,y` を (スティック名, x, y) にする。

    STICK_SPEC_PATTERN に fullmatch しないもの、x か y が -1.0〜1.0 の範囲外のものは ValueError にする。
    """
    raise NotImplementedError


def parse_chord(text: str) -> PadState:
    """CHORD (SPEC を + でつないだもの) を大文字にそろえて PadState にする。

    空の要素、未知の SPEC、同じスティックの重複、十字キーの相反する方向は ValueError にする。
    同じボタンの重複は許す。
    """
    raise NotImplementedError


def join_chords(chords: Sequence[PadState]) -> PadState:
    """複数の CHORD を 1 つにつないだ CHORD にする (down CHORD [CHORD ...] で使う)。

    parse_chord に + でつないだ文字列を渡したときと同じ規則で検証する。
    同じボタンの重複は許し、同じスティックの重複と十字キーの相反する方向は ValueError にする。
    """
    raise NotImplementedError


def parse_up_targets(texts: Sequence[str]) -> frozenset[str]:
    """up の TARGET 群を大文字にそろえ、ボタン名とスティック名 (LS / RS) の集合にする。

    各 TARGET は + でつないでもよい。空の要素と、ボタン名でも LS / RS でもない要素は ValueError にする。
    up ALL は commands.handle_command が先に振り分けるため、ここでは ALL を受け付けない。
    """
    raise NotImplementedError


def merge(base: PadState, chord: PadState) -> PadState:
    """base (現在の押下状態) に chord を加えた状態を返す。tap と hold の衝突検証に使う。

    次の場合は ValueError にする:
        - chord のボタンが base で既に押されている
        - chord のスティックが base で既に倒されている
        - 合成後の十字キーが相反する方向 (UP と DOWN、LEFT と RIGHT) を含む
    """
    raise NotImplementedError


def merge_all(layers: Iterable[PadState]) -> PadState:
    """held・各 hold・tapping などの層を merge で順に合成した状態を返す。何も無ければ空の状態を返す。"""
    raise NotImplementedError


def add_to_held(held: PadState, holds: PadState, chord: PadState) -> PadState:
    """down の規則で held に chord を加えた新しい held を返す。holds は全 hold を合成した状態。

    - held に既にあるボタンは何もしない (冪等)
    - held に既にあるスティックは chord の値で置き換える
    - chord のボタン・スティックが holds にもあれば ValueError にする
    - 新しい held と holds を合わせた十字キーが相反する方向を含めば ValueError にする
    """
    raise NotImplementedError


def remove_targets(state: PadState, targets: frozenset[str]) -> PadState:
    """state から targets (ボタン名とスティック名 LS / RS) に含まれるものを除いた状態を返す。

    state に無い名前は無視する。押されていない TARGET のエラーは呼び出し側 (Controller.up) が判定する。
    """
    raise NotImplementedError


def stick_axis_to_byte(value: float) -> int:
    """スティックの 1 軸 (-1.0〜1.0) をレポートの値 (0〜255、中立 128) にする。

    round(128 + value * (127 if value >= 0 else 128)) で変換する。正側は 127 段、負側は 128 段とし、
    両端がちょうど 255 と 0 になるようにする。round は Python の偶数丸め。
    """
    raise NotImplementedError


def dpad_to_hat(directions: frozenset[str]) -> int:
    """十字キーの押下方向の組を DPAD_HAT で hat の値にする。相反する方向の組は ValueError にする。"""
    raise NotImplementedError


def build_report(state: PadState) -> bytes:
    """押下状態を 8 バイトのレポート [b1, b2, hat, LX, LY, RX, RY, 0] にする。

    倒していないスティックは中立 (128, 128) にする。LY / RY には -y を変換した値を入れる。
    """
    raise NotImplementedError


def format_state(state: PadState) -> str:
    """押下状態を正規化表記 (例: A+RIGHT+LS:0.5,-1) にする。空なら none を返す。"""
    raise NotImplementedError


def format_holds(holds: Sequence[tuple[PadState, int]]) -> str:
    """(hold の CHORD, 残り ms) の並びを status の holds の表記にする。

    各 hold を `B+RIGHT:1200ms` の形で書いて , でつなぐ。hold が無ければ none を返す。
    残り ms の算出 (切り捨て) は呼び出し側 (Controller.status_text) が行う。
    """
    raise NotImplementedError
