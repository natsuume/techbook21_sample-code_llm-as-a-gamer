"""安全の監視が Jev に送る問い (3 つの noul と、本体の画面を見本と照合する 1 つの choice) と、要求の state。

問いの名前と、真になる画面 (並びはこの順):
    online            オンラインのロビー・対戦・マッチング、ほかのプレイヤーの情報、通報、投稿のどれかの画面
    purchase          ショップ・購入・課金の画面
    system_settings   本体の設定、またはセーブデータの削除などのデータの管理の画面
    system_screen     (choice) 今のフレームが、どの見本の本体の画面か
online・purchase・system_settings (SAFETY_QUESTION_NAMES) は noul で、止める条件はこの 3 つの問いだけで決める
(safety.safety_judge)。問いの名前は、要求の questions の ID、safety.jsonl の probabilities のキー、止まる理由に書く
名前を兼ねる。
system_screen (SYSTEM_SCREEN_QUESTION_NAME) は choice で、選択肢は none (どの見本の画面でもない) を先頭に、見本の名前を
system_screen.system_screen_references.SYSTEM_SCREEN_REFERENCES の順に並べる (選択肢の説明も同じところの文)。
照合の結果は記録して安全の監視の外に渡すだけで、止める条件には使わない。

要求の state (キーはこの順):
    {"instructions": SAFETY_INSTRUCTIONS, "frame": <今のフレームの参照文字列>,
     "references": {<見本の名前>: <見本の画像の参照文字列>, ...}}
references のキーは見本の名前で、SYSTEM_SCREEN_NAMES の順。画像は、キャプチャーの最新のフレーム 1 枚 (時刻や box を
書き込まない) と、見本の画像 (見本 1 つに 1 枚)。参照文字列は書くたびに同じ画像が足されるため、state の中に 1 回ずつ
だけ置き、問いの文と選択肢の説明は画像を state の中のパス (`frame`、`references`、`references.<名前>`) で指す。
"""

from collections.abc import Mapping

from switch2_layers.surface.surface_questions import (
    Choice,
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)
from switch2_layers.system_screen.system_screen_references import (
    NONE_CHOICE,
    NONE_CHOICE_DESCRIPTION,
    SYSTEM_SCREEN_NAMES,
    SYSTEM_SCREEN_REFERENCES,
)

ONLINE = "online"
PURCHASE = "purchase"
SYSTEM_SETTINGS = "system_settings"
# 止める条件に使う問いの名前 (この順に問い、止まる理由に複数の問いが当たったときは、この順で先のものを書く)
SAFETY_QUESTION_NAMES: tuple[str, ...] = (
    ONLINE,
    PURCHASE,
    SYSTEM_SETTINGS,
)
# 本体の画面を見本と照合する choice の問いの名前 (SAFETY_QUESTION_NAMES の後に置く)
SYSTEM_SCREEN_QUESTION_NAME = "system_screen"

# state の中で今のフレームの参照文字列を置くキー (問いの文がこのパスで画像を指す)
FRAME_STATE_KEY = "frame"
# state の中で見本の画像の参照文字列 (見本の名前 → 参照文字列) を置くキー
REFERENCES_STATE_KEY = "references"

# 要求の state の instructions に入れる、役割の説明
SAFETY_INSTRUCTIONS = (
    "あなたは、Nintendo Switch 2 のゲームを遊ぶエージェントの安全の監視です。"
    "コントローラーの操作は決めません。\n"
    "`frame` は、今の Nintendo Switch 2 の画面です。"
    "`references` は、本体の画面の見本です (本体の画面の一部を切り出したもの)。\n"
    "各問いには、`frame` がその問いの画面である確率で答えます。"
)

# 止める条件に使う問いの名前 → 問いの文
SAFETY_QUESTION_TEXTS: dict[str, str] = {
    ONLINE: (
        "`frame` は、オンラインのロビー・対戦・マッチング、ほかのプレイヤーの情報、通報、投稿の"
        "どれかの画面か。"
    ),
    PURCHASE: "`frame` は、ショップ・購入・課金の画面か。",
    SYSTEM_SETTINGS: (
        "`frame` は、本体の設定、またはセーブデータの削除などのデータの管理の画面か。"
    ),
}
# 本体の画面を見本と照合する choice の問いの文
SYSTEM_SCREEN_QUESTION_TEXT = "`frame` は、`references` のどの見本の本体の画面か。"


def build_safety_questions() -> tuple[SurfaceQuestion, ...]:
    """安全の問い 4 問を、SAFETY_QUESTION_NAMES の 3 問、SYSTEM_SCREEN_QUESTION_NAME の順で返す。

    SAFETY_QUESTION_NAMES の問い: name と target は問いの名前、instructions は SAFETY_QUESTION_TEXTS[名前]、
        kind は QuestionKind.SAFETY、型は QuestionType.NOUL (選択肢は無い)
    SYSTEM_SCREEN_QUESTION_NAME の問い: name と target は SYSTEM_SCREEN_QUESTION_NAME、instructions は
        SYSTEM_SCREEN_QUESTION_TEXT、kind は QuestionKind.SAFETY、型は QuestionType.CHOICE。選択肢は
        Choice(system_screen_references.NONE_CHOICE, NONE_CHOICE_DESCRIPTION) を先頭に、
        SYSTEM_SCREEN_REFERENCES の順の Choice(見本の name, 見本の description)
    """
    noul_questions = tuple(
        SurfaceQuestion(
            name=name,
            instructions=SAFETY_QUESTION_TEXTS[name],
            kind=QuestionKind.SAFETY,
            target=name,
            question_type=QuestionType.NOUL,
        )
        for name in SAFETY_QUESTION_NAMES
    )
    system_screen_choices = (
        Choice(NONE_CHOICE, NONE_CHOICE_DESCRIPTION),
        *(
            Choice(reference.name, reference.description)
            for reference in SYSTEM_SCREEN_REFERENCES
        ),
    )
    system_screen_question = SurfaceQuestion(
        name=SYSTEM_SCREEN_QUESTION_NAME,
        instructions=SYSTEM_SCREEN_QUESTION_TEXT,
        kind=QuestionKind.SAFETY,
        target=SYSTEM_SCREEN_QUESTION_NAME,
        question_type=QuestionType.CHOICE,
        choices=system_screen_choices,
    )
    return (*noul_questions, system_screen_question)


def build_safety_state(
    frame_reference: str, reference_references: Mapping[str, str]
) -> dict[str, object]:
    """要求の state (モジュールの docstring の形。キーは instructions・frame・references の順) を返す。

    frame_reference は今のフレームの参照文字列、reference_references は 見本の名前 → 見本の画像の参照文字列。
    どれも要求の assets に登録した画像の参照文字列。references の値は reference_references の並びに依らず、
    SYSTEM_SCREEN_NAMES の順に置く。reference_references のキーが SYSTEM_SCREEN_NAMES の名前の集合と違えば
    (足りない名前か、見本に無い名前があれば) ValueError。
    """
    if set(reference_references) != set(SYSTEM_SCREEN_NAMES):
        raise ValueError(
            "references must have exactly the system screen names "
            f"{list(SYSTEM_SCREEN_NAMES)}: {list(reference_references)}"
        )
    return {
        "instructions": SAFETY_INSTRUCTIONS,
        FRAME_STATE_KEY: frame_reference,
        REFERENCES_STATE_KEY: {
            name: reference_references[name] for name in SYSTEM_SCREEN_NAMES
        },
    }
