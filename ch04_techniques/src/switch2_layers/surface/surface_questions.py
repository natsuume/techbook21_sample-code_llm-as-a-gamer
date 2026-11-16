"""surface が Jev に送る問いの一覧と、要求の state に入れる役割と規則の説明 (SURFACE_INSTRUCTIONS)。

問いの型は Jev の 3 つ (QuestionType): choice (選択肢の確率)、noul (真である確率)、box (画像の中の位置)。
問いの種類 (QuestionKind) と ID は次のとおり。並びもこの順にする (build_surface_questions):
    button            button_<ボタン>                         choice  押せるボタンごと (BUTTON_ORDER の順)
    stick_direction   stick_<LS|RS>_<x|y>                     choice  2 本 × 横・縦 = 4
    stick_strength    stick_<LS|RS>_<x|y>_at_least_<段階>      noul    4 軸 × 50・75・100 = 12
    advisor           advisor_<種類>                           noul    ADVISOR_KINDS の 4 つ
    info_label        info_<名前>                              noul    INFO_LABEL_NAMES の 5 つ
    scene_change      scene_changed                           noul    1
    box_exists        box_exists_<番号>                        noul    box の対象ごと
    box               box_<番号>                               box     問い直す対象ごと
<番号> は対象の一覧 (surface.box_targets) の中の 1 始まりの位置。
選択肢の問いは「なし」(NONE_CHOICE) を先頭に置く。公式ドキュメント
(https://docs.typesafe.ai/model-jaggedness.md の "Choice option order") に、Jev は先頭の選択肢に寄ることがあると
書かれているため、寄ったときに何もしない側に倒す。ボタンの選択肢は none・tap・hold_short・hold_long の順
(押す時間の短い順)、スティックの向きの選択肢は none・負の向き・正の向きの順。
最新のフレームを指す問いの文には、画像の参照文字列ではなく state の中のパス (latest_frame_path) を書く
(参照文字列は書くたびに同じ画像が足されるため)。
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.safety.safety_rules import SAFETY_RULES
from switch2_layers.surface.box_targets import BoxTarget

# state の中でフレームの一覧を置くキー (問いの文がパスで参照する)
FRAMES_STATE_KEY = "frames"

NONE_CHOICE = "none"
TAP_CHOICE = "tap"
HOLD_SHORT_CHOICE = "hold_short"
HOLD_LONG_CHOICE = "hold_long"
# ボタンの問いの押す側の選択肢 (押す時間の短い順。確率の最大が同率なら、先にあるものを選ぶ)
PRESS_CHOICES = (TAP_CHOICE, HOLD_SHORT_CHOICE, HOLD_LONG_CHOICE)
# hold_short と hold_long で押し続ける秒数
HOLD_SHORT_SECONDS = 0.3
HOLD_LONG_SECONDS = 1.0
# 押し続けの選択肢の名前 → 押し続ける秒数
HOLD_CHOICE_SECONDS = {
    HOLD_SHORT_CHOICE: HOLD_SHORT_SECONDS,
    HOLD_LONG_CHOICE: HOLD_LONG_SECONDS,
}
# スティック名と軸。並びは問いの順でもある
STICKS = ("LS", "RS")
STICK_AXES = ("x", "y")
# スティックの軸 → (負の向きの選択肢の名前, 正の向きの選択肢の名前)
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
STICK_LABELS = {"LS": "左スティック", "RS": "右スティック"}
STICK_AXIS_LABELS = {"x": "左右", "y": "上下"}

# advisor 判定 (deep を呼ぶか) を問う文書の種類。マップは問わない。並びは問いの順でもある
ADVISOR_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind.CONTROLS,
    DocumentKind.TIPS,
    DocumentKind.STRATEGY,
    DocumentKind.ADVICE,
)
# 情報ラベルの名前 (操作説明・攻略情報・大方針・詰まり・マップ)。並びは問いの順と、SurfaceOutput.info_labels の順
INFO_LABEL_NAMES = ("controls", "tips", "strategy", "stuck", "map")
SCENE_CHANGE_QUESTION_NAME = "scene_changed"


class QuestionType(StrEnum):
    """Jev の問いの型。値は要求の questions の type に書く。"""

    CHOICE = "choice"
    NOUL = "noul"
    BOX = "box"


class QuestionKind(StrEnum):
    """Jev に送る問いの種類。SAFETY のほかは surface の問い (モジュールの docstring の表)。

    SAFETY は安全の監視の問い (safety.safety_questions) で、surface の要求には入れない。
    """

    BUTTON = "button"
    STICK_DIRECTION = "stick_direction"
    STICK_STRENGTH = "stick_strength"
    ADVISOR = "advisor"
    INFO_LABEL = "info_label"
    SCENE_CHANGE = "scene_change"
    BOX_EXISTS = "box_exists"
    BOX = "box"
    SAFETY = "safety"


@dataclass(frozen=True)
class Choice:
    """選択肢の問いの選択肢 1 つ。

    name          API に送る選択肢の名前 (要求の criteria のキー、応答の probabilities のキー)
    description   日本語の選択肢の説明
    """

    name: str
    description: str


@dataclass(frozen=True)
class SurfaceQuestion:
    """Jev に送る問い 1 つ。

    name            問いの ID (モジュールの docstring の表。英数字と _ だけで、一覧の中で重複しない)
    instructions    日本語の問いの文
    kind            問いの種類
    target          button はボタン名、stick_direction は `LS_x` の形、stick_strength は `LS_x_50` の形、
                    advisor は文書の種類の値 (DocumentKind.value)、info_label は情報ラベルの名前、
                    scene_change は空文字列、box_exists と box は対象の名前 (BoxTarget.name)、
                    safety は問いの名前 (safety.safety_questions.SAFETY_QUESTION_NAMES の要素)
    question_type   Jev の問いの型。button と stick_direction は choice、box は box、ほかは noul
    choices         選択肢の問いの選択肢 (先頭は NONE_CHOICE)。ほかの型では空のタプル
    """

    name: str
    instructions: str
    kind: QuestionKind
    target: str
    question_type: QuestionType
    choices: tuple[Choice, ...] = ()


# 押し続けの選択肢の説明の、秒数の後に続く文
_HOLD_CONTINUES_TEXT = (
    "まだ離していないうちに、次の判断でもこのボタンの押し続け (hold_short か hold_long) を選ぶと、"
    "離さずに続けて押し、その選択肢の秒数を数え直す。離した後に選ぶと、押し直す"
)
# ボタンの問いの選択肢 (この順)
BUTTON_CHOICES = (
    Choice(NONE_CHOICE, "押さない"),
    Choice(TAP_CHOICE, "1 回だけ短く押す"),
    Choice(
        HOLD_SHORT_CHOICE,
        f"{HOLD_SHORT_SECONDS} 秒押し続けてから離す。{_HOLD_CONTINUES_TEXT}",
    ),
    Choice(
        HOLD_LONG_CHOICE,
        f"{HOLD_LONG_SECONDS} 秒押し続けてから離す。{_HOLD_CONTINUES_TEXT}",
    ),
)
# スティックの軸 → その軸の向きの問いの選択肢 (none・負の向き・正の向きの順)
STICK_DIRECTION_CHOICES = {
    "x": (
        Choice(NONE_CHOICE, "左右には倒さない"),
        Choice("left", "左に倒す"),
        Choice("right", "右に倒す"),
    ),
    "y": (
        Choice(NONE_CHOICE, "上下には倒さない"),
        Choice("down", "下に倒す"),
        Choice("up", "上に倒す"),
    ),
}

# 要求の state の instructions に入れる、役割と規則の説明。最後に安全のための規則 (safety.safety_rules) を置く
SURFACE_INSTRUCTIONS = (
    "あなたは、Nintendo Switch 2 のゲームを遊ぶエージェントの surface (コントローラーを操作する層) です。\n"
    "`frames` は直近 2 秒以内のゲームの画面で、古い順に並び、最後が最新です。"
    "各画像の左上には時刻を、追えている対象には枠と名前を書き込んであります。"
    "書き込んだ時刻・枠・名前は、ゲームの画面の一部ではありません。\n"
    "`documents` は、今の場面の短期タスクリスト (tasks)・攻略情報 (tips)・操作説明 (controls)・"
    "詰まったときの助言 (advice) です。`timeline` は上位の層がまとめた系列情報とその作成時刻、"
    "`recent_actions` は直近に送った操作 (古い順) です。\n"
    "`advisor` は、deep (文書をまとめる上位のモデル) の文書の種類ごとの呼び出しの状況 (実行中のジョブ、前回の終了、"
    "今求めると捨てられるか、surface が最後に求めた結果) です。\n"
    "規則:\n"
    "- 短期タスクリストの実行中のタスクを進める操作を選ぶ。\n"
    "- 操作の問い (ボタンとスティック) と、ほかの問い (deep を呼ぶか・情報ラベル・場面転換・box) は、"
    "それぞれ独立に答える。ほかの問いへの答えで操作を変えない。\n" + SAFETY_RULES
)

# advisor 判定の問いの文 (文書の種類 → 文)。build_advisor_questions が、末尾に ADVISOR_STATUS_RULE_TEXT の文を足す
ADVISOR_QUESTION_TEXTS: dict[DocumentKind, str] = {
    DocumentKind.CONTROLS: (
        "今、deep (文書をまとめる上位のモデル) に、操作説明 (`documents.controls`) の更新を求めるべきか。"
        "unknown と書かれたボタンを試して画面の反応が分かったとき、書かれている操作と画面の反応が合わないとき、"
        "新しい種類の操作 (メニュー、会話、乗り物など) が画面に出たときに真にする。"
    ),
    DocumentKind.TIPS: (
        "今、deep (文書をまとめる上位のモデル) に、攻略情報 (`documents.tips`) の更新を求めるべきか。"
        "敵、アイテム、ゲームの仕組み、注意点など、まだ書かれていないゲームの情報が画面に出たときに真にする。"
    ),
    DocumentKind.STRATEGY: (
        "今、deep (文書をまとめる上位のモデル) に、大方針 (ゲームの目標と進め方) の更新を求めるべきか。"
        "今の目標を達成したとき、今の目標が達成できないと分かったとき、"
        "短期タスクリスト (`documents.tasks`) が画面の状況に合わないときに真にする。"
    ),
    DocumentKind.ADVICE: (
        "今、deep (文書をまとめる上位のモデル) に、詰まったときの助言 (`documents.advice`) を求めるべきか。"
        "系列情報 (`timeline`) に止まっていると書かれているとき、同じ操作や同じ失敗を繰り返して先に進めないとき、"
        "何をすればよいか分からないときに真にする。"
    ),
}
# advisor 判定のすべての問いの文の末尾に足す共通の文。{kind} は文書の種類の値 (DocumentKind.value) に置き換える
ADVISOR_STATUS_RULE_TEXT = (
    "ただし、`advisor.{kind}` に実行中のジョブがあるとき、"
    "または今求めると捨てられると書かれているときは偽にする。"
)

# 情報ラベルの問いの文 (名前 → 文)。{frame} は latest_frame_path の値に置き換える
INFO_LABEL_QUESTION_TEXTS: dict[str, str] = {
    "controls": (
        "`{frame}` は、操作説明 (ボタンやスティックへのゲームの反応) を書き足したり直したりするのに役立つ場面か。"
    ),
    "tips": "`{frame}` は、攻略情報 (敵・アイテム・ゲームの仕組み・注意点) を書き足すのに役立つ場面か。",
    "strategy": "`{frame}` は、大方針 (ゲームの目標と進め方) を見直すのに役立つ場面か。",
    "stuck": "`{frame}` は、先に進めずに詰まっている様子が分かる場面か。",
    "map": "`{frame}` は、マップ (場所と、場所どうしのつながり) を記録するのに役立つ場面か。",
}
SCENE_CHANGE_QUESTION_TEXT = "`frames` の画像の間で、画面の種類 (フィールド、メニュー、会話、戦闘、読み込み中の画面など) が変わったか。"
# box の存在と box の問いの文。{frame} は latest_frame_path の値、{name} と {description} は対象の名前と説明
BOX_EXISTS_QUESTION_TEXT = "`{frame}` に「{name}」({description}) が写っているか。"
BOX_QUESTION_TEXT = "`{frame}` の中の「{name}」({description}) を囲む box。"


def box_exists_question_name(number: int) -> str:
    """対象の一覧の <number> 番目 (1 始まり) の対象の、box の存在の問いの ID box_exists_<number> を返す。

    number が 1 未満なら ValueError。
    """
    _check_target_number(number)
    return f"box_exists_{number}"


def box_question_name(number: int) -> str:
    """対象の一覧の <number> 番目 (1 始まり) の対象の、box の問いの ID box_<number> を返す。number が 1 未満なら ValueError。"""
    _check_target_number(number)
    return f"box_{number}"


def _check_target_number(number: int) -> None:
    """対象の番号 number が 1 未満なら ValueError にする。"""
    if number < 1:
        raise ValueError(f"target number must be 1 or more: {number}")


def latest_frame_path(frame_count: int) -> str:
    """frame_count 枚のフレームを送る要求で、最新のフレームの画像を指す state の中のパスを返す。

    `frames[<frame_count - 1>].image` の形 (例: 5 枚なら frames[4].image)。frame_count が 1 未満なら ValueError。
    """
    if frame_count < 1:
        raise ValueError(f"frame_count must be 1 or more: {frame_count}")
    return f"{FRAMES_STATE_KEY}[{frame_count - 1}].image"


def build_button_questions(allowed_buttons: Collection[str]) -> list[SurfaceQuestion]:
    """ボタンの問いを、controller_state.BUTTON_ORDER の順で、allowed_buttons にあるボタンだけ返す。

    name は button_<ボタン>、target はボタン名、型は choice、選択肢は BUTTON_CHOICES。
    文は「今、<BUTTON_LABELS の呼び名>をどうするか。」(例: 「今、A ボタンをどうするか。」)。
    allowed_buttons の中の BUTTON_ORDER に無い名前は無視する。
    """
    return [
        SurfaceQuestion(
            name=f"button_{button}",
            instructions=f"今、{BUTTON_LABELS[button]}をどうするか。",
            kind=QuestionKind.BUTTON,
            target=button,
            question_type=QuestionType.CHOICE,
            choices=BUTTON_CHOICES,
        )
        for button in BUTTON_ORDER
        if button in allowed_buttons
    ]


def build_stick_direction_questions() -> list[SurfaceQuestion]:
    """スティックの向きの問い 4 問を、STICKS の順のそれぞれに STICK_AXES の順で返す。

    name は stick_<スティック>_<軸>、target は <スティック>_<軸>、型は choice、選択肢は STICK_DIRECTION_CHOICES[軸]。
    文は「今、<スティックの呼び名>を<軸の呼び名>に倒すか。」(例: 「今、左スティックを左右に倒すか。」)。
    """
    return [
        SurfaceQuestion(
            name=f"stick_{stick}_{axis}",
            instructions=(
                f"今、{STICK_LABELS[stick]}を{STICK_AXIS_LABELS[axis]}に倒すか。"
            ),
            kind=QuestionKind.STICK_DIRECTION,
            target=f"{stick}_{axis}",
            question_type=QuestionType.CHOICE,
            choices=STICK_DIRECTION_CHOICES[axis],
        )
        for stick in STICKS
        for axis in STICK_AXES
    ]


def build_stick_strength_questions() -> list[SurfaceQuestion]:
    """スティックの強さの問い 12 問を、向きの問いの 4 軸のそれぞれに STICK_STRENGTH_LEVELS の順で返す。

    name は stick_<スティック>_<軸>_at_least_<段階>、target は <スティック>_<軸>_<段階>、型は noul。
    50 と 75 の文は「<スティックの呼び名>を<軸の呼び名>に倒す場合、傾きを <段階>% 以上にするべきか。」、
    100 の文は「<スティックの呼び名>を<軸の呼び名>に倒す場合、傾きをいっぱい (100%) にするべきか。」。
    """
    questions: list[SurfaceQuestion] = []
    for stick in STICKS:
        for axis in STICK_AXES:
            subject = f"{STICK_LABELS[stick]}を{STICK_AXIS_LABELS[axis]}に倒す場合"
            for level in STICK_STRENGTH_LEVELS:
                amount = "いっぱい (100%) " if level == 100 else f" {level}% 以上"
                questions.append(
                    SurfaceQuestion(
                        name=f"stick_{stick}_{axis}_at_least_{level}",
                        instructions=f"{subject}、傾きを{amount}にするべきか。",
                        kind=QuestionKind.STICK_STRENGTH,
                        target=f"{stick}_{axis}_{level}",
                        question_type=QuestionType.NOUL,
                    )
                )
    return questions


def build_advisor_questions() -> list[SurfaceQuestion]:
    """advisor 判定の問いを ADVISOR_KINDS の種類ごとに (ADVISOR_KINDS の順で) 返す。

    name は advisor_<種類の値>、target は種類の値、型は noul。文は ADVISOR_QUESTION_TEXTS[種類] の末尾に、
    ADVISOR_STATUS_RULE_TEXT の {kind} を種類の値に置き換えた共通の文を、間を空けずに足したもの
    (ADVISOR_KINDS に種類が増えても、同じ共通の文が付く)。
    """
    return [
        SurfaceQuestion(
            name=f"advisor_{kind.value}",
            instructions=(
                ADVISOR_QUESTION_TEXTS[kind]
                + ADVISOR_STATUS_RULE_TEXT.format(kind=kind.value)
            ),
            kind=QuestionKind.ADVISOR,
            target=kind.value,
            question_type=QuestionType.NOUL,
        )
        for kind in ADVISOR_KINDS
    ]


def build_info_label_questions(frame_path: str) -> list[SurfaceQuestion]:
    """情報ラベルの問い 5 問を INFO_LABEL_NAMES の順で返す。

    name は info_<名前>、target は名前、型は noul、文は INFO_LABEL_QUESTION_TEXTS[名前] の {frame} を frame_path
    (latest_frame_path の値) に置き換えたもの。
    """
    return [
        SurfaceQuestion(
            name=f"info_{name}",
            instructions=INFO_LABEL_QUESTION_TEXTS[name].format(frame=frame_path),
            kind=QuestionKind.INFO_LABEL,
            target=name,
            question_type=QuestionType.NOUL,
        )
        for name in INFO_LABEL_NAMES
    ]


def build_scene_change_question() -> SurfaceQuestion:
    """場面転換の問いを返す。name は SCENE_CHANGE_QUESTION_NAME、target は空文字列、型は noul、
    文は SCENE_CHANGE_QUESTION_TEXT。"""
    return SurfaceQuestion(
        name=SCENE_CHANGE_QUESTION_NAME,
        instructions=SCENE_CHANGE_QUESTION_TEXT,
        kind=QuestionKind.SCENE_CHANGE,
        target="",
        question_type=QuestionType.NOUL,
    )


def build_box_exists_questions(
    targets: Sequence[BoxTarget], frame_path: str
) -> list[SurfaceQuestion]:
    """box の存在の問いを、targets のすべての対象について、targets の順で返す。

    name は box_exists_<番号> (targets の中の 1 始まりの位置)、target は対象の名前、型は noul、
    文は BOX_EXISTS_QUESTION_TEXT の {frame} を frame_path に、{name} と {description} を対象の名前と説明に
    置き換えたもの。
    """
    return [
        SurfaceQuestion(
            name=box_exists_question_name(number),
            instructions=_target_text(BOX_EXISTS_QUESTION_TEXT, target, frame_path),
            kind=QuestionKind.BOX_EXISTS,
            target=target.name,
            question_type=QuestionType.NOUL,
        )
        for number, target in enumerate(targets, start=1)
    ]


def build_box_questions(
    targets: Sequence[BoxTarget], requery_names: Collection[str], frame_path: str
) -> list[SurfaceQuestion]:
    """box の問いを、targets のうち名前が requery_names にある対象について、targets の順で返す。

    name は box_<番号> (targets の中の 1 始まりの位置。box_exists_<番号> と同じ番号)、target は対象の名前、
    型は box、文は BOX_QUESTION_TEXT を build_box_exists_questions と同じ規則で置き換えたもの。
    requery_names の中の targets に無い名前は無視する。
    """
    return [
        SurfaceQuestion(
            name=box_question_name(number),
            instructions=_target_text(BOX_QUESTION_TEXT, target, frame_path),
            kind=QuestionKind.BOX,
            target=target.name,
            question_type=QuestionType.BOX,
        )
        for number, target in enumerate(targets, start=1)
        if target.name in requery_names
    ]


def _target_text(template: str, target: BoxTarget, frame_path: str) -> str:
    """template の {frame} を frame_path に、{name} と {description} を target の名前と説明に置き換えて返す。"""
    return template.format(
        frame=frame_path, name=target.name, description=target.description
    )


def build_surface_questions(
    allowed_buttons: Collection[str],
    targets: Sequence[BoxTarget],
    requery_names: Collection[str],
    frame_count: int,
) -> tuple[SurfaceQuestion, ...]:
    """1 回の要求の問いの一覧を、モジュールの docstring の表の順で返す。

    frame_path = latest_frame_path(frame_count) を、情報ラベル・box の存在・box の問いの文に使う。
    ボタンの問いは allowed_buttons のボタンだけ (HOME は --allow-home のときだけ allowed_buttons に入る)。
    """
    frame_path = latest_frame_path(frame_count)
    return (
        *build_button_questions(allowed_buttons),
        *build_stick_direction_questions(),
        *build_stick_strength_questions(),
        *build_advisor_questions(),
        *build_info_label_questions(frame_path),
        build_scene_change_question(),
        *build_box_exists_questions(targets, frame_path),
        *build_box_questions(targets, requery_names, frame_path),
    )
