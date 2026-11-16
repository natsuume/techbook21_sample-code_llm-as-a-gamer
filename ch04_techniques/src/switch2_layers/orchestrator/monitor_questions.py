"""Orchestrator 型の監視が Jev に送る問い (choice 1 問) と要求の state、答えから当たった条件を決める規則。I/O を持たない。

問い (build_monitor_question):
    ID は MONITOR_QUESTION_NAME、種類は QuestionKind.MONITOR、型は choice、target は監視の名前。
    選択肢は NONE_OPTION (どの条件にも当たらない) を先頭に置き、続けて条件の順に C0・C1… (case_option_name) とする。
    条件の選択肢の説明は、主ループが書いた条件の文をそのまま使う (文の中身は確かめない)。
    Jev は先頭の選択肢に寄ることがあるため (surface.surface_questions と同じ理由)、先頭を何もしない NONE にする。
要求の state (キーはこの順):
    {"instructions": MONITOR_INSTRUCTIONS, "frame": <画像の参照文字列>}
    画像は、キャプチャーの最新のフレーム 1 枚 (安全の監視と同じく、時刻や box を書き込まない幅 1280 の JPEG)。
    参照文字列は state の中に 1 回だけ置き、問いの文は画像を state の中のパス `frame` で指す。
当たった条件 (find_hit):
    確率がいちばん高い選択肢 (同じ確率なら選択肢の順で先のもの) が NONE_OPTION 以外で、その確率がしきい値を超えたら、
    その条件に当たったとする。ちょうどしきい値なら当たらない。
"""

from collections.abc import Mapping

from switch2_layers.orchestrator.decision_types import MonitorCreate
from switch2_layers.surface.surface_questions import SurfaceQuestion

# 問いの ID (要求の questions のキー、応答の answers のキー)
MONITOR_QUESTION_NAME = "monitor"
# どの条件にも当たらない選択肢の名前 (先頭に置く)
NONE_OPTION = "NONE"
NONE_OPTION_DESCRIPTION = "どの条件にも当たらない"
# 条件の選択肢の名前の接頭辞 (C0・C1…)
CASE_OPTION_PREFIX = "C"
# state の中で画像の参照文字列を置くキー (問いの文がこのパスで画像を指す)
FRAME_STATE_KEY = "frame"

# 要求の state の instructions に入れる、役割の説明
MONITOR_INSTRUCTIONS = (
    "あなたは、Nintendo Switch 2 のゲームを遊ぶエージェントの画面の監視です。"
    "コントローラーの操作は決めません。\n"
    "`frame` は、今の Nintendo Switch 2 の画面です。\n"
    "問いの選択肢は、画面の条件です。`frame` がどの条件に当たるかを答えます。"
    "どの条件にも当たらなければ NONE を選びます。"
)
# 問いの文
MONITOR_QUESTION_TEXT = (
    "`frame` は、次のどの条件に当たるか。どれにも当たらなければ NONE。"
)


def case_option_name(case_index: int) -> str:
    """条件の位置 case_index (0 始まり) の選択肢の名前 `C<case_index>` を返す。case_index が負なら ValueError。"""
    raise NotImplementedError


def monitor_option_names(case_count: int) -> tuple[str, ...]:
    """条件が case_count 個の問いの選択肢の名前 (NONE_OPTION, C0, C1, …) を、選択肢の順で返す。

    case_count が 1 未満なら ValueError。
    """
    raise NotImplementedError


def build_monitor_question(monitor: MonitorCreate) -> SurfaceQuestion:
    """監視 monitor の choice の問い 1 問を返す (モジュールの docstring の「問い」)。

    instructions は MONITOR_QUESTION_TEXT、選択肢は monitor_option_names(条件の数) の順で、NONE_OPTION の説明は
    NONE_OPTION_DESCRIPTION、C<i> の説明は monitor.cases[i].condition。
    """
    raise NotImplementedError


def build_monitor_state(reference: str) -> dict[str, object]:
    """要求の state {"instructions": MONITOR_INSTRUCTIONS, FRAME_STATE_KEY: reference} (キーはこの順) を返す。"""
    raise NotImplementedError


def find_hit(
    probabilities: Mapping[str, float], case_count: int, threshold: float
) -> int | None:
    """答えの確率 probabilities (選択肢の名前 → 確率) から、当たった条件の位置 (0 始まり) を返す。当たらなければ None。

    規則はモジュールの docstring の「当たった条件」のとおり。選択肢は monitor_option_names(case_count) の順に比べる。
    probabilities にどれかの選択肢の名前が無ければ KeyError。
    """
    raise NotImplementedError
