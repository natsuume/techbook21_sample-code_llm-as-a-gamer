"""Jev の答えから、surface の出力 (操作・deep を呼ぶ種類・情報ラベル・場面転換・box を使う対象) を決める純粋な関数。

答えは 2 つの map で受け取る (surface.jev_client.JevAnswers の choices と nouls):
    choices   選択肢の問いの ID → (選択肢の名前 → 確率)
    nouls     noul の問いの ID → 真である確率
問いの ID と選択肢の名前は surface.surface_questions の規則に従う。I/O・スレッド・時刻を扱わない。

操作の決め方 (decide_pad_action):
    1. ボタン: none・hold・tap の確率を p_none・p_hold・p_tap とする。押す側の合計 p_hold + p_tap が p_none より
       大きい (>) ときだけ押す (同じ値なら押さない。同じ値かは probabilities_equal で判定する)。
       押すなら、p_hold >= p_tap のとき押し続け (held)、そうでなければ短押し (taps) にする (同じ値なら押し続け)
    2. 十字キー: 相反する方向 (UP と DOWN、LEFT と RIGHT) が、押し続けと短押しを合わせて両方とも押す側になったら、
       押す側の合計が高い方を残す (同じ値なら両方とも外す)
    3. スティックの向き: 軸ごとに、確率が最も高い選択肢を採る。最大の確率の選択肢が 2 つ以上あれば none
    4. スティックの強さ: 向きが none でない軸だけ計算する。50・75・100 の確率を p50・p75・p100 とし、
       q50 = p50、q75 = min(p75, q50)、q100 = min(p100, q75) と単調に下がるよう切り下げて、
       強さ s = STICK_MIN_STRENGTH + STICK_STRENGTH_STEP × (q50 + q75 + q100) (0.25〜1.0) にする。
       軸の値は、正の向き (right / up) なら +s、負の向き (left / down) なら −s、none なら 0
ほかの出力:
    deep を呼ぶ種類   advisor の問いの確率が threshold_advisor を超えた (>) 種類
    情報ラベル        INFO_LABEL_NAMES の順に、名前と確率 (しきい値で選ばず、すべて記録する)
    場面転換          scene_changed の確率が SCENE_CHANGE_THRESHOLD を超えた (>) とき True
    box を使う対象    box の存在の確率が BOX_EXISTS_THRESHOLD 以上の対象 (未満なら、その対象の box を使わない)
"""

from collections.abc import Mapping, Sequence

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.pad_action import PadAction
from switch2_layers.surface.box_targets import BoxTarget
from switch2_layers.surface.surface_questions import SurfaceQuestion
from switch2_layers.surface.surface_types import InfoLabel

# 十字キーの相反する方向の組
OPPOSITE_DPAD_PAIRS = (("UP", "DOWN"), ("LEFT", "RIGHT"))
# スティックを倒すときの傾きの最小値と、強さの段階 1 つあたりの増分。傾きは 0.25〜1.0 になる
STICK_MIN_STRENGTH = 0.25
STICK_STRENGTH_STEP = 0.25
# 確率を足した値どうしを比べるときに、同じ値とみなす差の上限 (浮動小数点数の丸めの差を同じ値として扱う)
PROBABILITY_TOLERANCE = 1e-9
SCENE_CHANGE_THRESHOLD = 0.5  # 場面転換の確率がこれを超えたら (>) 検知したとする
BOX_EXISTS_THRESHOLD = 0.5  # box の存在の確率がこれ未満なら、その対象の box を使わない


def probabilities_equal(a: float, b: float) -> bool:
    """a と b の差が PROBABILITY_TOLERANCE 以下なら True を返す。"""
    raise NotImplementedError


def check_answers(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
) -> None:
    """questions のうち choice と noul の問いの答えがすべてあるかを確かめ、足りなければ ValueError にする。

    choice の問いは choices に ID があり、その問いのすべての選択肢の確率があること。noul の問いは nouls に ID が
    あること。box の問いは確かめない。メッセージには足りない ID (選択肢なら `<ID>.<選択肢>`) を並べる。
    """
    raise NotImplementedError


def adopt_buttons(
    choices: Mapping[str, Mapping[str, float]],
    questions: Sequence[SurfaceQuestion],
) -> dict[str, tuple[str, float]]:
    """ボタンの問いの答えから、押すボタン → (HOLD_CHOICE か TAP_CHOICE, 押す側の合計) を返す (操作の決め方の 1)。"""
    raise NotImplementedError


def remove_opposite_dpad(
    adopted: Mapping[str, tuple[str, float]],
) -> dict[str, tuple[str, float]]:
    """adopt_buttons の結果から、十字キーの相反する方向を操作の決め方の 2 で外した新しい辞書を返す。adopted は変えない。"""
    raise NotImplementedError


def choose_direction(question: SurfaceQuestion, values: Mapping[str, float]) -> str:
    """スティックの向きの問いの答え values から、確率が最も高い選択肢の名前を返す (操作の決め方の 3)。

    最大の確率の選択肢が 2 つ以上あれば NONE_CHOICE。question.choices に無い名前は見ない。
    """
    raise NotImplementedError


def stick_strength(p50: float, p75: float, p100: float) -> float:
    """強さの問いの 50・75・100 の確率から、傾きの大きさ (0.25〜1.0) を返す (操作の決め方の 4)。"""
    raise NotImplementedError


def decide_sticks(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
) -> dict[str, tuple[float, float]]:
    """スティックの向きと強さの問いから、スティック名 (LS / RS) → (x, y) を返す (操作の決め方の 3 と 4)。

    両方の軸が 0 のスティックも (0.0, 0.0) として含める。
    """
    raise NotImplementedError


def decide_pad_action(
    choices: Mapping[str, Mapping[str, float]],
    nouls: Mapping[str, float],
    questions: Sequence[SurfaceQuestion],
) -> PadAction:
    """答えと questions から、操作 (モジュールの docstring の操作の決め方) を決めて返す。

    はじめに check_answers で答えがそろっているかを確かめる (スティックの強さの答えは、その軸の向きが none でも要る)。
    held は押し続けるボタン、taps は短押しのボタン、left_stick と right_stick は decide_sticks の LS と RS。
    questions に無い ID と選択肢は無視する。
    """
    raise NotImplementedError


def decide_deep_kinds(
    nouls: Mapping[str, float], threshold_advisor: float
) -> frozenset[DocumentKind]:
    """advisor の問い (advisor_<種類>。surface_questions.ADVISOR_KINDS) の確率が threshold_advisor を超えた (>)
    種類の集合を返す。しきい値ちょうどは入れない。答えが足りなければ ValueError。"""
    raise NotImplementedError


def decide_info_labels(nouls: Mapping[str, float]) -> tuple[InfoLabel, ...]:
    """情報ラベルの問い (info_<名前>) の答えを、INFO_LABEL_NAMES の順の InfoLabel(名前, 確率) にして返す。

    5 つすべてを返す (しきい値で選ばない)。答えが足りなければ ValueError。
    """
    raise NotImplementedError


def decide_scene_changed(nouls: Mapping[str, float]) -> bool:
    """scene_changed の確率が SCENE_CHANGE_THRESHOLD を超えたら (>) True を返す。ちょうどは False。
    答えが無ければ ValueError。"""
    raise NotImplementedError


def visible_target_names(
    nouls: Mapping[str, float], targets: Sequence[BoxTarget]
) -> frozenset[str]:
    """box の存在の問い (box_exists_<番号>) の確率が BOX_EXISTS_THRESHOLD 以上の対象の名前の集合を返す。

    <番号> は targets の中の 1 始まりの位置。ちょうど BOX_EXISTS_THRESHOLD は含める。答えが足りなければ ValueError。
    """
    raise NotImplementedError
