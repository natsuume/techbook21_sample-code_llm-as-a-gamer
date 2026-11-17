"""委任型の委任のループが Jev に送る問い (choice 1 問) と要求の state、答えから選んだ選択肢を決める規則。I/O を持たない。

問い (build_delegate_question):
    ID は DELEGATE_QUESTION_NAME、種類は QuestionKind.DELEGATE、型は choice、target は空文字列。
    選択肢は NONE_OPTION (何もしない) を先頭に置き、続けて主ループが書いた行動 (DelegateChoice) をその順に、最後に
    RETURN_CONTROL_OPTION (操作を返す) を置く (delegate_option_names)。行動の選択肢の名前はマクロの名前
    (DelegateChoice.name)、説明は主ループが書いた説明の文をそのまま使う (文の中身は確かめない)。
    Jev は先頭の選択肢に寄ることがあるため (surface.surface_questions と同じ理由)、先頭を何もしない NONE_OPTION にする。
    行動の名前に NONE_OPTION と RETURN_CONTROL_OPTION は使えない (decision_parser.parse_decision が誤りにする)。
要求の state (キーはこの順):
    {"instructions": DELEGATE_INSTRUCTIONS,
     "tasks": [タスクの文, ...],
     "cautions": [注意事項の文, ...],
     "recent_actions": [{"name": 行動の名前, "t": <時刻のラベル>}, ...],
     "frames": [{"t": <時刻のラベル>, "image": <画像の参照文字列>}, ...]}
    recent_actions は、今の委任で直近に選んで始めた行動 (古い順。最大 MAX_RECENT_PICKS 件)。
    frames は、送る画像 (古い順)。時刻のラベルは frames.surface_frames.format_frame_label の形 (t=12.345)。
    画像の参照文字列は state の中に 1 回ずつだけ置き、問いの文は画像を state の中のパス `frames` で指す。
選んだ選択肢 (choose_option):
    確率がいちばん高い選択肢。同じ確率なら、選択肢の順で先のもの。
    ただし、いちばん高いのが NONE_OPTION で、その確率が NONE_MIN_PROBABILITY より小さいときは、行動の選択肢
    (NONE_OPTION と RETURN_CONTROL_OPTION 以外) のうち確率がいちばん高いもの (同じ確率なら先のもの) を選ぶ。
    行動の確率は選択肢の数だけ分かれ、何もしない側は NONE_OPTION の 1 つに集まるため、いちばん高い 1 つだけで
    決めると、行動の確率の和が大きくても NONE_OPTION が選ばれ、何もしない時間が続くため。
    RETURN_CONTROL_OPTION は代わりに選ばない (操作を返すのは、RETURN_CONTROL_OPTION がいちばん高いときだけ)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.orchestrator.decision_types import DelegateChoice
from switch2_layers.orchestrator.orchestrator_events import DelegatePick
from switch2_layers.surface.surface_questions import (
    Choice,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)

# 問いの ID (要求の questions のキー、応答の answers のキー)
DELEGATE_QUESTION_NAME = "delegate"
# 何もしない選択肢の名前 (先頭に置く)。実行中の行動を続ける
NONE_OPTION = "none"
NONE_OPTION_DESCRIPTION = "何もしない (実行中の行動を続ける)"
# 操作を返す選択肢の名前 (最後に置く)
RETURN_CONTROL_OPTION = "return_control"
RETURN_CONTROL_OPTION_DESCRIPTION = (
    "操作を返す (リアルタイムの場面が終わり、会話・選ぶ画面・結果の表示などになった)"
)
# 行動の名前に使えない選択肢の名前
RESERVED_OPTION_NAMES: tuple[str, ...] = (NONE_OPTION, RETURN_CONTROL_OPTION)
# NONE_OPTION がいちばん高いときに、そのまま NONE_OPTION を選ぶ確率の下限 (これより小さければ行動を選ぶ)
NONE_MIN_PROBABILITY = 0.5
# state の recent_actions に入れる行動の最大の件数
MAX_RECENT_PICKS = 5
# state の中で画像の参照文字列を置くキー (問いの文がこのパスで画像を指す)
FRAMES_STATE_KEY = "frames"

# 要求の state の instructions に入れる、役割の説明
DELEGATE_INSTRUCTIONS = (
    "あなたは、Nintendo Switch 2 のゲームのリアルタイムの場面で、コントローラーの操作を任されたエージェントです。\n"
    "`frames` は直近の画面を古い順に並べたもので、最後が今の画面です。`t` はその画面を撮った時刻 (秒) です。\n"
    "`tasks` は、この場面でやることです (先頭が最も重要)。`cautions` は、気をつけることです。\n"
    "`recent_actions` は、あなたが直近に選んで始めた行動と、始めた時刻です。\n"
    "問いの選択肢から、tasks を進めるために今とる行動を 1 つ選びます。"
    "行動を選ぶと、実行中の行動の残りを捨てて、その行動を始めます。"
    "none は何もせず、実行中の行動を続けます。\n"
    "リアルタイムの場面が終わったら、return_control を選びます。"
)
# 問いの文
DELEGATE_QUESTION_TEXT = (
    "`frames` の最後の画面で、tasks を進めるために今とる行動はどれか。"
    "何もしないなら none、リアルタイムの場面が終わったなら return_control。"
)


@dataclass(frozen=True)
class DelegateStateFrame:
    """要求の state の frames に置く画像 1 枚。

    t           画像の元のフレームの時刻 (実行の経過秒)
    reference   画像の参照文字列 (jev_client.JevImage.reference)
    """

    t: float
    reference: str


def delegate_option_names(choices: Sequence[DelegateChoice]) -> tuple[str, ...]:
    """行動 choices の問いの選択肢の名前 (NONE_OPTION, 行動の名前..., RETURN_CONTROL_OPTION) を、選択肢の順で返す。

    choices が空なら ValueError。
    """
    if not choices:
        raise ValueError("delegate choices must not be empty")
    return (NONE_OPTION, *(choice.name for choice in choices), RETURN_CONTROL_OPTION)


def build_delegate_question(choices: Sequence[DelegateChoice]) -> SurfaceQuestion:
    """行動 choices の choice の問い 1 問を返す (モジュールの docstring の「問い」)。

    instructions は DELEGATE_QUESTION_TEXT、選択肢は delegate_option_names(choices) の順で、NONE_OPTION の説明は
    NONE_OPTION_DESCRIPTION、行動の説明は DelegateChoice.description、RETURN_CONTROL_OPTION の説明は
    RETURN_CONTROL_OPTION_DESCRIPTION。choices が空なら ValueError。
    """
    if not choices:
        raise ValueError("delegate choices must not be empty")
    options = (
        Choice(NONE_OPTION, NONE_OPTION_DESCRIPTION),
        *(Choice(choice.name, choice.description) for choice in choices),
        Choice(RETURN_CONTROL_OPTION, RETURN_CONTROL_OPTION_DESCRIPTION),
    )
    return SurfaceQuestion(
        name=DELEGATE_QUESTION_NAME,
        instructions=DELEGATE_QUESTION_TEXT,
        kind=QuestionKind.DELEGATE,
        target="",
        question_type=QuestionType.CHOICE,
        choices=options,
    )


def build_delegate_state(
    tasks: Sequence[str],
    cautions: Sequence[str],
    recent_picks: Sequence[DelegatePick],
    frames: Sequence[DelegateStateFrame],
) -> dict[str, object]:
    """要求の state (モジュールの docstring の「要求の state」。キーはその順) を返す。

    recent_picks と frames は古い順に受け取り、その順に並べる。recent_picks が MAX_RECENT_PICKS 件を超えれば、
    新しいものから MAX_RECENT_PICKS 件だけを入れる。
    """
    newest_picks = list(recent_picks)[-MAX_RECENT_PICKS:] if recent_picks else []
    return {
        "instructions": DELEGATE_INSTRUCTIONS,
        "tasks": list(tasks),
        "cautions": list(cautions),
        "recent_actions": [
            {"name": pick.name, "t": format_frame_label(pick.t)}
            for pick in newest_picks
        ],
        FRAMES_STATE_KEY: [
            {"t": format_frame_label(frame.t), "image": frame.reference}
            for frame in frames
        ],
    }


def choose_option(
    probabilities: Mapping[str, float], option_names: Sequence[str]
) -> str:
    """答えの確率 probabilities (選択肢の名前 → 確率) から、選んだ選択肢の名前を返す (モジュールの docstring の
    「選んだ選択肢」)。

    option_names の順に比べる。probabilities にどれかの選択肢の名前が無ければ KeyError、option_names が空なら
    ValueError。
    """
    if not option_names:
        raise ValueError("option names must not be empty")
    best = highest_option(probabilities, option_names)
    if best != NONE_OPTION or probabilities[NONE_OPTION] >= NONE_MIN_PROBABILITY:
        return best
    action_names = [name for name in option_names if name not in RESERVED_OPTION_NAMES]
    if not action_names:
        return best
    return highest_option(probabilities, action_names)


def highest_option(probabilities: Mapping[str, float], names: Sequence[str]) -> str:
    """names のうち、probabilities の確率がいちばん高い名前を返す。同じ確率なら names の順で先のもの。"""
    best = names[0]
    for name in names:
        # 同じ確率なら、選択肢の順で先のものを残す
        if probabilities[name] > probabilities[best]:
            best = name
    return best
