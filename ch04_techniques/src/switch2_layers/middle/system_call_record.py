"""middle/system_calls.jsonl の行 (本体の画面の middle の呼び出しの行と、操作の列の実行の結果の行) を作る。I/O を扱わない。

1 つのファイルに 2 種類の行を書き、kind で見分ける。

呼び出しの行 (system_call_row。呼び出しごとに 1 行):
    {"kind": "call", "t_sent", "t_received", "goal", "frame_t", "judged_screen", "judged_frame_t", "output", "error",
     "usage", "cost_usd", "response", "error_body_head"}
    output は system_screen_schema.system_output_to_json の形。失敗した行では null で、error に説明を書く。
    usage・cost_usd・response・error_body_head は、LunaSystemScreenMiddle.take_call_details の値
    (luna_middle.build_call_details の形)。値が無い (フェイクの middle など) キーは null。

実行の結果の行 (system_run_row。操作の列を送り終えたら (打ち切りを含む) 1 行):
    {"kind": "run", "t", "sent_count", "cut_index", "cut_reason", "blocked_buttons": [ボタン名...]}
    cut_index と cut_reason は、最後まで送ったら null。blocked_buttons は pad_action.sort_buttons の順。

ファイルに書く処理 (いつ書くか) は、行を作るこのモジュールの外で行う。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from switch2_layers.middle.system_screen_types import (
    SystemRunResult,
    SystemScreenInput,
    SystemScreenOutput,
)

CALL_ROW_KIND = "call"
RUN_ROW_KIND = "run"


@dataclass(frozen=True)
class SystemCallRecord:
    """本体の画面の middle の 1 回の呼び出しの記録。

    t_sent         decide を呼んだ時刻
    t_received     decide が戻った (または例外を出した) 時刻
    screen_input   渡した入力 (行の goal・frame_t・judged_screen・judged_frame_t に使う)
    output         decide の出力。失敗したら None
    error          失敗の説明。成功なら None
    details        middle の記録 (take_call_details の値。usage など)。無ければ空。既定値の空は、記録を持たない
                   middle (フェイクなど) のため
    """

    t_sent: float
    t_received: float
    screen_input: SystemScreenInput
    output: SystemScreenOutput | None
    error: str | None
    details: Mapping[str, object] = field(default_factory=dict)


def system_call_row(record: SystemCallRecord) -> dict[str, object]:
    """呼び出しの行の辞書を、モジュールの docstring のキーの順で返す。

    goal は screen_input.goal の値 (文字列)、frame_t・judged_screen・judged_frame_t は screen_input の値。
    usage・cost_usd・response・error_body_head は details の値 (キーが無ければ None)。details のほかのキーは入れない。
    """
    raise NotImplementedError


def system_run_row(t: float, result: SystemRunResult) -> dict[str, object]:
    """実行の結果の行の辞書を、モジュールの docstring のキーの順で返す。

    t は行の時刻 (送り終えた時刻)。cut_reason は値 (文字列) か None、blocked_buttons は
    pad_action.sort_buttons(result.blocked_buttons) のリスト。
    """
    raise NotImplementedError
