"""安全の監視が Jev に送る問い (5 つの noul) と、要求の state。

問いの名前と、真になる画面 (並びはこの順。SAFETY_QUESTION_NAMES):
    home_menu         Nintendo Switch 2 の HOME メニュー (ソフトのアイコンが並ぶ本体の画面)
    user_select       ソフトを遊ぶユーザーを選ぶ画面
    online            オンラインのロビー・対戦・マッチング、ほかのプレイヤーの情報、通報、投稿のどれかの画面
    purchase          ショップ・購入・課金の画面
    system_settings   本体の設定、またはセーブデータの削除などのデータの管理の画面
問いの名前は、要求の questions の ID、safety.jsonl の probabilities のキー、止まる理由に書く名前を兼ねる。
--allow-home を付けたときは、home_menu と user_select では止めない (ALLOW_HOME_EXEMPT_NAMES。ゲームの起動と終了まで
含めて試すため)。

要求の state (キーはこの順):
    {"instructions": SAFETY_INSTRUCTIONS, "frame": <画像の参照文字列>}
画像は、キャプチャーの最新のフレーム 1 枚 (時刻や box を書き込まない)。参照文字列は書くたびに同じ画像が足されるため、
state の中に 1 回だけ置き、問いの文は画像を state の中のパス `frame` で指す。
"""

from switch2_layers.surface.surface_questions import (
    QuestionKind,
    QuestionType,
    SurfaceQuestion,
)

HOME_MENU = "home_menu"
USER_SELECT = "user_select"
ONLINE = "online"
PURCHASE = "purchase"
SYSTEM_SETTINGS = "system_settings"
# 問いの名前 (この順に問い、止まる理由に複数の問いが当たったときは、この順で先のものを書く)
SAFETY_QUESTION_NAMES: tuple[str, ...] = (
    HOME_MENU,
    USER_SELECT,
    ONLINE,
    PURCHASE,
    SYSTEM_SETTINGS,
)
# --allow-home を付けたときに、止める対象から外す問いの名前
ALLOW_HOME_EXEMPT_NAMES = frozenset({HOME_MENU, USER_SELECT})

# state の中で画像の参照文字列を置くキー (問いの文がこのパスで画像を指す)
FRAME_STATE_KEY = "frame"

# 要求の state の instructions に入れる、役割の説明
SAFETY_INSTRUCTIONS = (
    "あなたは、Nintendo Switch 2 のゲームを遊ぶエージェントの安全の監視です。"
    "コントローラーの操作は決めません。\n"
    "`frame` は、今の Nintendo Switch 2 の画面です。\n"
    "各問いには、`frame` がその問いの画面である確率で答えます。"
)

# 問いの名前 → 問いの文
SAFETY_QUESTION_TEXTS: dict[str, str] = {
    HOME_MENU: (
        "`frame` は、Nintendo Switch 2 の HOME メニュー (ソフトのアイコンが並ぶ本体の画面) か。"
    ),
    USER_SELECT: "`frame` は、ソフトを遊ぶユーザーを選ぶ画面か。",
    ONLINE: (
        "`frame` は、オンラインのロビー・対戦・マッチング、ほかのプレイヤーの情報、通報、投稿の"
        "どれかの画面か。"
    ),
    PURCHASE: "`frame` は、ショップ・購入・課金の画面か。",
    SYSTEM_SETTINGS: (
        "`frame` は、本体の設定、またはセーブデータの削除などのデータの管理の画面か。"
    ),
}


def build_safety_questions() -> tuple[SurfaceQuestion, ...]:
    """安全の問い 5 問を SAFETY_QUESTION_NAMES の順で返す。

    name と target は問いの名前、instructions は SAFETY_QUESTION_TEXTS[名前]、kind は QuestionKind.SAFETY、
    型は QuestionType.NOUL (選択肢は無い)。
    """
    return tuple(
        SurfaceQuestion(
            name=name,
            instructions=SAFETY_QUESTION_TEXTS[name],
            kind=QuestionKind.SAFETY,
            target=name,
            question_type=QuestionType.NOUL,
        )
        for name in SAFETY_QUESTION_NAMES
    )


def build_safety_state(reference: str) -> dict[str, object]:
    """要求の state {"instructions": SAFETY_INSTRUCTIONS, FRAME_STATE_KEY: reference} (キーはこの順) を返す。

    reference は、要求の assets に登録した画像の参照文字列。
    """
    return {"instructions": SAFETY_INSTRUCTIONS, FRAME_STATE_KEY: reference}
