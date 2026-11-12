"""Decision Model に毎回送る問いの一覧。

問いは 4 種類 (QuestionKind):
    press     そのボタンを押した状態にするべきか (押した状態は次の判断まで続く)
    tap       そのボタンを 1 回だけ短く押すべきか
    stick     そのスティックをその方向に倒すべきか
    advisor   advisor に戦略の更新を求めるべきか
どの問いも真である確率で答えさせ、action_decision がしきい値と比べて操作を決める。
"""

from dataclasses import dataclass
from typing import Literal

from switch2_remote.controller_state import BUTTON_ORDER

QuestionKind = Literal["press", "tap", "stick", "advisor"]

# ボタン名 (controller_state.BUTTON_ORDER の要素) → 問いの文で使う呼び名
BUTTON_LABELS = {
    "A": "A ボタン",
    "B": "B ボタン",
    "X": "X ボタン",
    "Y": "Y ボタン",
    "L": "L ボタン",
    "R": "R ボタン",
    "ZL": "ZL ボタン",
    "ZR": "ZR ボタン",
    "L3": "L3 (左スティックの押し込み)",
    "R3": "R3 (右スティックの押し込み)",
    "PLUS": "+ ボタン",
    "MINUS": "- ボタン",
    "HOME": "HOME ボタン",
    "UP": "十字キーの上",
    "RIGHT": "十字キーの右",
    "DOWN": "十字キーの下",
    "LEFT": "十字キーの左",
}
# スティック名 → 問いの文で使う呼び名。並びは問いの順でもある
STICK_LABELS = {"LS": "左スティック", "RS": "右スティック"}
# スティックを倒す方向 → 問いの文で使う呼び名。並びは問いの順でもある
STICK_DIRECTION_LABELS = {"UP": "上", "DOWN": "下", "LEFT": "左", "RIGHT": "右"}
# advisor の問いの name
ADVISOR_QUESTION_NAME = "advisor"
ADVISOR_QUESTION_TEXT = (
    "今、advisor (戦略を考える上位のモデル) に戦略の更新を求めるべきか。"
    "今の戦略が画面の状況に合わないとき、新しい場面に入ったとき、"
    "同じ失敗を繰り返しているときに真にする。"
)


@dataclass(frozen=True)
class Question:
    """Decision Model に送る問い 1 つ。

    name     API に送る問いの ID。英数字と _ だけからなり、問いの一覧の中で重複しない
    text     日本語の問いの文
    kind     問いの種類 (press / tap / stick / advisor)
    target   press と tap はボタン名 (BUTTON_ORDER の要素)、stick は `LS_UP` のような
             スティック名と方向を _ でつないだもの、advisor は空文字
    """

    name: str
    text: str
    kind: QuestionKind
    target: str


def build_questions() -> list[Question]:
    """問いの一覧 (43 問) を次の順で返す。

    1. controller_state.BUTTON_ORDER の順の 17 ボタンそれぞれについて、press_<ボタン> と tap_<ボタン> の 2 問
       (34 問。press_A、tap_A、press_B、tap_B、… の順)。
       press_<ボタン> の文は「今、<呼び名>を押した状態にするべきか。押した状態は次の判断まで続く。」、
       tap_<ボタン> の文は「今、<呼び名>を 1 回だけ短く押すべきか。」。呼び名は BUTTON_LABELS の値で、
       呼び名と「を」の間に空白を入れない (例: 「今、A ボタンを押した状態にするべきか。…」)
    2. STICK_LABELS の順の LS / RS それぞれについて、STICK_DIRECTION_LABELS の順の UP / DOWN / LEFT / RIGHT の
       stick_<スティック>_<方向> の 8 問。target は <スティック>_<方向>、文は「今、<スティックの呼び名>を
       <方向の呼び名>に倒すべきか。」(例: 「今、左スティックを上に倒すべきか。」)
    3. name が ADVISOR_QUESTION_NAME、文が ADVISOR_QUESTION_TEXT、target が空文字の advisor の 1 問
    """
    questions: list[Question] = []
    for button in BUTTON_ORDER:
        label = BUTTON_LABELS[button]
        questions.append(
            Question(
                name=f"press_{button}",
                text=f"今、{label}を押した状態にするべきか。押した状態は次の判断まで続く。",
                kind="press",
                target=button,
            )
        )
        questions.append(
            Question(
                name=f"tap_{button}",
                text=f"今、{label}を 1 回だけ短く押すべきか。",
                kind="tap",
                target=button,
            )
        )
    for stick, stick_label in STICK_LABELS.items():
        for direction, direction_label in STICK_DIRECTION_LABELS.items():
            questions.append(
                Question(
                    name=f"stick_{stick}_{direction}",
                    text=f"今、{stick_label}を{direction_label}に倒すべきか。",
                    kind="stick",
                    target=f"{stick}_{direction}",
                )
            )
    questions.append(
        Question(
            name=ADVISOR_QUESTION_NAME,
            text=ADVISOR_QUESTION_TEXT,
            kind="advisor",
            target="",
        )
    )
    return questions
