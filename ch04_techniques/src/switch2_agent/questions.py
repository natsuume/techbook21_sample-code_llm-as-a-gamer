"""Decision Model に毎回送る問いの一覧。

問いは 4 種類 (QuestionKind):
    button            そのボタンを押さないか、押した状態にするか、1 回だけ短く押すか (選択肢の問い)
    stick_direction   そのスティックを、その軸 (左右か上下) のどちらに倒すか、倒さないか (選択肢の問い)
    stick_strength    そのスティックをその軸に倒す場合、傾きをその段階以上にするべきか (確率の問い)
    advisor           advisor に戦略の更新を求めるべきか (確率の問い)
答えの型は 2 つ (AnswerType):
    choice        選択肢の問い。「なし」(NONE_CHOICE) を含む選択肢の中から 1 つを選ぶ問いで、選択肢ごとの確率で答えさせる
    probability   確率の問い。真である確率で答えさせる
排他的な操作 (同じボタンの押した状態と短押し、スティックの左と右) は選択肢の問いにして、矛盾する答えが出ないようにする。
action_decision が答えから操作を決める。
"""

from dataclasses import dataclass
from typing import Literal

from switch2_remote.controller_state import BUTTON_ORDER

QuestionKind = Literal["button", "stick_direction", "stick_strength", "advisor"]
AnswerType = Literal["choice", "probability"]

# 選択肢の名前。NONE_CHOICE は、どの選択肢の問いにもある「なし」(何もしない) の選択肢
NONE_CHOICE = "none"
HOLD_CHOICE = "hold"
TAP_CHOICE = "tap"
# スティックの軸 → (負の向きの選択肢の名前, 正の向きの選択肢の名前)。並びは問いの軸の順でもある
STICK_AXIS_DIRECTIONS = {"x": ("left", "right"), "y": ("down", "up")}
# スティックの強さの問いの段階 (傾きの %)。並びは問いの順でもある
STICK_STRENGTH_LEVELS = (50, 75, 100)

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
class Choice:
    """選択肢の問いの選択肢 1 つ。

    name          API に送る選択肢の名前 (英小文字。NONE_CHOICE・HOLD_CHOICE・TAP_CHOICE、STICK_AXIS_DIRECTIONS の値)
    description   日本語の選択肢の説明
    """

    name: str
    description: str


@dataclass(frozen=True)
class Question:
    """Decision Model に送る問い 1 つ。

    name          API に送る問いの ID。英数字と _ だけからなり、問いの一覧の中で重複しない
    text          日本語の問いの文
    kind          問いの種類 (button / stick_direction / stick_strength / advisor)
    target        button はボタン名 (BUTTON_ORDER の要素)、stick_direction は `LS_x` のような
                  スティック名と軸を _ でつないだもの、stick_strength は `LS_x_50` のような
                  スティック名・軸・段階を _ でつないだもの、advisor は空文字
    answer_type   答えの型。button と stick_direction は "choice"、stick_strength と advisor は "probability"
    choices       選択肢の問いの選択肢 (build_questions の docstring の順。先頭は NONE_CHOICE)。
                  確率の問いでは空のタプル
    """

    name: str
    text: str
    kind: QuestionKind
    target: str
    answer_type: AnswerType
    choices: tuple[Choice, ...]


def build_questions() -> list[Question]:
    """問いの一覧 (34 問) を次の順で返す。

    呼び名は BUTTON_LABELS と STICK_LABELS の値で、呼び名と「を」の間に空白を入れない。
    選択肢は、Jev が先頭の選択肢に寄りやすいと公式ドキュメントに書かれているため、「なし」(NONE_CHOICE) を先頭に置く。
    1. ボタンの問い 17 問 (controller_state.BUTTON_ORDER の順)。name は button_<ボタン>、kind は button、
       target はボタン名、選択肢の問い。文は「今、<呼び名>をどうするか。」(例: 「今、A ボタンをどうするか。」)。
       選択肢 (この順):
           none   「押さない」
           hold   「押した状態にする。押した状態は次の判断まで続く」
           tap    「1 回だけ短く押す」
    2. スティックの方向の問い 4 問 (STICK_LABELS の順の LS / RS それぞれに、横 x → 縦 y の順)。
       name は stick_<スティック>_<軸> (例: stick_LS_x)、kind は stick_direction、target は <スティック>_<軸>、
       選択肢の問い。
       - x の文は「今、<スティックの呼び名>を左右に倒すか。」(例: 「今、左スティックを左右に倒すか。」)。
         選択肢 (この順): none「左右には倒さない」、left「左に倒す」、right「右に倒す」
       - y の文は「今、<スティックの呼び名>を上下に倒すか。」。
         選択肢 (この順): none「上下には倒さない」、up「上に倒す」、down「下に倒す」
    3. スティックの強さの問い 12 問 (2 の 4 軸それぞれに、STICK_STRENGTH_LEVELS の順の 50 → 75 → 100)。
       name は stick_<スティック>_<軸>_at_least_<段階> (例: stick_LS_x_at_least_50)、kind は stick_strength、
       target は <スティック>_<軸>_<段階> (例: LS_x_50)、確率の問い。<向き> は x なら「左右」、y なら「上下」。
       - 50 と 75 の文は「<スティックの呼び名>を<向き>に倒す場合、傾きを <段階>% 以上にするべきか。」
         (例: 「左スティックを左右に倒す場合、傾きを 50% 以上にするべきか。」)
       - 100 の文は「<スティックの呼び名>を<向き>に倒す場合、傾きをいっぱい (100%) にするべきか。」
    4. advisor の問い 1 問。name が ADVISOR_QUESTION_NAME、文が ADVISOR_QUESTION_TEXT、kind が advisor、
       target が空文字の、確率の問い
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
                answer_type="probability",
                choices=(),
            )
        )
        questions.append(
            Question(
                name=f"tap_{button}",
                text=f"今、{label}を 1 回だけ短く押すべきか。",
                kind="tap",
                target=button,
                answer_type="probability",
                choices=(),
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
                    answer_type="probability",
                    choices=(),
                )
            )
    questions.append(
        Question(
            name=ADVISOR_QUESTION_NAME,
            text=ADVISOR_QUESTION_TEXT,
            kind="advisor",
            target="",
            answer_type="probability",
            choices=(),
        )
    )
    return questions
