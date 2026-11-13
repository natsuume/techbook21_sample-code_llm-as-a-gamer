"""Decision Model に毎回送る問いの一覧。

問いは 4 種類 (QuestionKind):
    button            そのボタンを押さないか、押した状態にするか、1 回だけ短く押すか (選択肢の問い)
    stick_direction   そのスティックを、その軸 (左右か上下) のどちらに倒すか、倒さないか (選択肢の問い)
    stick_strength    そのスティックをその軸に倒す場合、傾きをその段階以上にするべきか (確率の問い)
    advisor           その役割の advisor (advisor_roles.ADVISOR_ROLES) に、ゲームの情報の更新や助言を求めるべきか
                      (確率の問い。役割ごとに 1 問)
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
# スティックの軸 → 問いの文で使う倒す向きの呼び名。並びは問いの順でもある
STICK_AXIS_LABELS = {"x": "左右", "y": "上下"}
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
                  スティック名・軸・段階を _ でつないだもの、advisor は役割の name (advisor_roles.AdvisorRole.name)
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


# ボタンの問いの選択肢 (この順)
BUTTON_CHOICES = (
    Choice(NONE_CHOICE, "押さない"),
    Choice(HOLD_CHOICE, "押した状態にする。押した状態は次の判断まで続く"),
    Choice(TAP_CHOICE, "1 回だけ短く押す"),
)
# スティックの軸 → その軸の方向の問いの選択肢 (この順)
STICK_DIRECTION_CHOICES = {
    "x": (
        Choice(NONE_CHOICE, "左右には倒さない"),
        Choice("left", "左に倒す"),
        Choice("right", "右に倒す"),
    ),
    "y": (
        Choice(NONE_CHOICE, "上下には倒さない"),
        Choice("up", "上に倒す"),
        Choice("down", "下に倒す"),
    ),
}


def build_questions() -> list[Question]:
    """問いの一覧 (37 問) を次の順で返す。

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
    4. advisor の問い 4 問 (advisor_roles.ADVISOR_ROLES の順。最後に置く)。役割ごとに、name は role.question_name
       (例: advisor_strategy)、文は role.question_text、kind は advisor、target は role.name、確率の問い
    """
    return [
        *build_button_questions(),
        *build_stick_direction_questions(),
        *build_stick_strength_questions(),
        Question(
            name=ADVISOR_QUESTION_NAME,
            text=ADVISOR_QUESTION_TEXT,
            kind="advisor",
            target="",
            answer_type="probability",
            choices=(),
        ),
    ]


def build_advisor_questions() -> list[Question]:
    """advisor の問い 4 問 (build_questions の 4) を、advisor_roles.ADVISOR_ROLES の順で返す。"""
    raise NotImplementedError


def build_button_questions() -> list[Question]:
    """ボタンの問い 17 問 (build_questions の 1) を返す。"""
    return [
        Question(
            name=f"button_{button}",
            text=f"今、{BUTTON_LABELS[button]}をどうするか。",
            kind="button",
            target=button,
            answer_type="choice",
            choices=BUTTON_CHOICES,
        )
        for button in BUTTON_ORDER
    ]


def build_stick_direction_questions() -> list[Question]:
    """スティックの方向の問い 4 問 (build_questions の 2) を返す。"""
    return [
        Question(
            name=f"stick_{stick}_{axis}",
            text=f"今、{stick_label}を{axis_label}に倒すか。",
            kind="stick_direction",
            target=f"{stick}_{axis}",
            answer_type="choice",
            choices=STICK_DIRECTION_CHOICES[axis],
        )
        for stick, stick_label in STICK_LABELS.items()
        for axis, axis_label in STICK_AXIS_LABELS.items()
    ]


def build_stick_strength_questions() -> list[Question]:
    """スティックの強さの問い 12 問 (build_questions の 3) を返す。"""
    return [
        Question(
            name=f"stick_{stick}_{axis}_at_least_{level}",
            text=stick_strength_text(stick_label, axis_label, level),
            kind="stick_strength",
            target=f"{stick}_{axis}_{level}",
            answer_type="probability",
            choices=(),
        )
        for stick, stick_label in STICK_LABELS.items()
        for axis, axis_label in STICK_AXIS_LABELS.items()
        for level in STICK_STRENGTH_LEVELS
    ]


def stick_strength_text(stick_label: str, axis_label: str, level: int) -> str:
    """スティックの強さの問いの文を返す (build_questions の 3)。

    level が 100 なら「傾きをいっぱい (100%) にするべきか」、それ以外は「傾きを <level>% 以上にするべきか」とする。
    """
    if level == 100:
        strength = "傾きをいっぱい (100%) にするべきか。"
    else:
        strength = f"傾きを {level}% 以上にするべきか。"
    return f"{stick_label}を{axis_label}に倒す場合、{strength}"
