"""SystemScreenInput から、本体の画面の middle の gpt-6-luna の要求の JSON (Responses API の本文) を組み立てる。I/O を扱わない。

要求の形:
    {
      "model": luna_request.LUNA_MODEL,
      "reasoning": {"effort": SYSTEM_SCREEN_REASONING_EFFORT},
      "text": {"format": system_screen_schema.build_system_screen_text_format(screen_names)},
      "prompt_cache_options": {"mode": "explicit"},
      "input": [
        {"role": "developer", "content": [
          {"type": "input_text", "text": system_screen_instructions.SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
           "prompt_cache_breakpoint": {"mode": "explicit"}}]},
        {"role": "user", "content": [
          {"type": "input_text", "text": <節 1>}, ..., {"type": "input_text", "text": <節 9>},
          {"type": "input_text", "text": luna_request.frame_label(frame_t)},
          {"type": "input_image", "image_url": luna_request.jpeg_data_url(frame_jpeg)}]}
      ]
    }
モデルはゲームの画面の middle (luna_request) と同じにする。reasoning の effort は SYSTEM_SCREEN_REASONING_EFFORT (low) にする。
effort が none では、HOME メニューで選ばれている絵やアイコンを読み違えることが多いため (2026-11-17 に保存した画面の再生で確認)。developer メッセージは入力によらず
同じにし、その最後の (唯一の) 内容に明示のキャッシュの区切りを置く。user メッセージには区切りを置かない。
画像の detail は指定しない (既定の auto)。

user メッセージの節 (この順。各節は「## <USER_SECTION_TITLES の見出し>」の行で始まり、次の行から中身を書く):
    1. 現在の時刻             「t=<screen_input.t>」(frames.surface_frames.format_frame_label の形)
    2. 目的                   1 行目に目的の値 (prepare・recover・clean_up)、2 行目から
                              system_screen_instructions.goal_text(goal) の文
    3. 指定のソフト           screen_input.software_name (書かれたまま)
    4. 指定のユーザー         screen_input.user_name (書かれたまま)
    5. 照合の結果             describe_judgment(judged_screen, judged_frame_t)
    6. この段階のこれまでの呼び出し   describe_history(history) (新しい方から MAX_HISTORY_ENTRIES 件)
    7. この段階で送った A・B・X        describe_sent_buttons(history) (履歴のすべてから)
    8. deep の助言            screen_input.deep_advice (notes/advice.md の「本体の画面」の節の本文)。空なら EMPTY_TEXT
    9. 今の画像               IMAGE_SECTION_TEXT
節の後ろに、今の画像の時刻のラベル (frame_label(frame_t)) と画像 (input_image) を 1 組置く。
"""

import json
from collections.abc import Sequence

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.middle.luna_request import (
    LUNA_MODEL,
    frame_label,
    jpeg_data_url,
)
from switch2_layers.middle.system_screen_instructions import (
    SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
    goal_text,
)
from switch2_layers.middle.system_screen_schema import (
    build_system_screen_text_format,
    system_operation_to_json,
)
from switch2_layers.middle.system_screen_types import (
    DECISION_BUTTON,
    MAX_HISTORY_ENTRIES,
    NO_SCREEN_NAME,
    SystemScreenInput,
    SystemStepRecord,
)

# 本体の画面の middle の reasoning の effort (ゲームの画面の middle の luna_request.REASONING_EFFORT とは別に決める)
SYSTEM_SCREEN_REASONING_EFFORT = "low"
# user メッセージの節の見出し (この順に置く)
USER_SECTION_TITLES: tuple[str, ...] = (
    "現在の時刻",
    "目的",
    "指定のソフト",
    "指定のユーザー",
    "照合の結果",
    "この段階のこれまでの呼び出し",
    "この段階で送った A・B・X",
    "deep の助言",
    "今の画像",
)
EMPTY_TEXT = "(無い)"
# この段階で送った A・B・X の節に並べるボタン (決定・戻る・ソフトをおわる)。準備の手順は登録を確かめた B と、その後の
# A の前後で、片付けの手順は終了の確認を出した X で、次の操作を決めるため
LISTED_BUTTONS: tuple[str, ...] = (DECISION_BUTTON, "B", "X")
# この段階で送った A・B・X が無いときの、その節の中身
NO_SENT_BUTTONS_TEXT = "まだ送っていません"
# 照合の結果がまだ無いとき (judged_frame_t が None) の、照合の結果の節の中身
NO_JUDGMENT_TEXT = "照合の結果はまだ無い"
# 照合の結果が None (見本のどの画面でもない) のときに、画面の名前の後ろに添える説明
NO_SCREEN_NOTE = "見本のどの画面でもない"
IMAGE_SECTION_TEXT = "キャプチャーの最新のフレーム 1 枚。画像の直前に時刻のラベルがある"


def describe_judgment(judged_screen: str | None, judged_frame_t: float | None) -> str:
    """照合の結果の節の中身を返す。

    judged_frame_t が None なら NO_JUDGMENT_TEXT。そうでなければ次の 2 行:
        画面: <judged_screen>                 judged_screen が None なら「画面: none (<NO_SCREEN_NOTE>)」
        照合したフレーム: t=<judged_frame_t>  t は format_frame_label の形
    """
    if judged_frame_t is None:
        return NO_JUDGMENT_TEXT
    screen = (
        f"{NO_SCREEN_NAME} ({NO_SCREEN_NOTE})"
        if judged_screen is None
        else judged_screen
    )
    return f"画面: {screen}\n照合したフレーム: {format_frame_label(judged_frame_t)}"


def describe_history(history: Sequence[SystemStepRecord]) -> str:
    """この段階のこれまでの呼び出しの節の中身を返す。

    history のうち新しい方から system_screen_types.MAX_HISTORY_ENTRIES 件を、古い順に 1 件 1 行で書く
    (行は改行 1 つでつなぎ、末尾に改行を付けない)。history が空なら EMPTY_TEXT を返す。1 行の形:
        - t=<t>: 照合の結果 <judged_screen (None なら none)>、操作の列 <operations の JSON>、
          送った操作 <sent_count>/<operations の数>、<最後まで送った | 打ち切った理由 <cut_reason の値>>
    t は format_frame_label の形。操作の列の JSON は、system_screen_schema.system_operation_to_json の値のリストを
    json.dumps(..., ensure_ascii=False) にしたもの (出力の operations と同じ形)。
    """
    if not history:
        return EMPTY_TEXT
    return "\n".join(
        _describe_step(record) for record in list(history)[-MAX_HISTORY_ENTRIES:]
    )


def _describe_step(record: SystemStepRecord) -> str:
    """履歴の 1 件を、describe_history の 1 行の形にする。"""
    screen = NO_SCREEN_NAME if record.judged_screen is None else record.judged_screen
    operations = json.dumps(
        [system_operation_to_json(operation) for operation in record.operations],
        ensure_ascii=False,
    )
    result = (
        "最後まで送った"
        if record.cut_reason is None
        else f"打ち切った理由 {record.cut_reason.value}"
    )
    return (
        f"- {format_frame_label(record.t)}: 照合の結果 {screen}、操作の列 {operations}、"
        f"送った操作 {record.sent_count}/{len(record.operations)}、{result}"
    )


def describe_sent_buttons(history: Sequence[SystemStepRecord]) -> str:
    """この段階で送った A・B・X の節の中身を返す。

    history のすべて (MAX_HISTORY_ENTRIES 件に削る前のもの) を古い順に見て、各履歴の操作の列の先頭から sent_count 個
    (実際に送った操作) のうち、press の buttons にある LISTED_BUTTONS のボタンを、1 つ 1 行「t=<その呼び出しの時刻>
    <その操作の screen> で <ボタン>」で、送った順に並べる (1 つの press に LISTED_BUTTONS のボタンが 2 つ以上あれば、
    LISTED_BUTTONS の順に 1 行ずつ。行は改行 1 つでつなぎ、末尾に改行を付けない。t は format_frame_label の形)。
    打ち切りで送らなかった操作は入れない。cut_reason が send_failed のとき、送信に失敗した press は sent_count に
    含まれるため、届いたかもしれないものとして入る。1 件も無ければ NO_SENT_BUTTONS_TEXT を返す。
    """
    lines = [
        f"{format_frame_label(record.t)} {operation.screen} で {button}"
        for record in history
        for operation in record.operations[: record.sent_count]
        if operation.kind == "press"
        for button in LISTED_BUTTONS
        if button in operation.buttons
    ]
    return "\n".join(lines) if lines else NO_SENT_BUTTONS_TEXT


def build_user_sections(screen_input: SystemScreenInput) -> list[str]:
    """user メッセージの 9 つの節の文を、モジュールの docstring の順と形で返す (画像の組は含まない)。"""
    goal = screen_input.goal
    bodies = [
        format_frame_label(screen_input.t),
        f"{goal.value}\n{goal_text(goal)}",
        screen_input.software_name,
        screen_input.user_name,
        describe_judgment(screen_input.judged_screen, screen_input.judged_frame_t),
        describe_history(screen_input.history),
        describe_sent_buttons(screen_input.history),
        screen_input.deep_advice or EMPTY_TEXT,
        IMAGE_SECTION_TEXT,
    ]
    return [
        f"## {title}\n{body}"
        for title, body in zip(USER_SECTION_TITLES, bodies, strict=True)
    ]


def build_developer_message() -> dict[str, object]:
    """developer メッセージ {"role": "developer", "content": [{"type": "input_text",
    "text": SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS, "prompt_cache_breakpoint": {"mode": "explicit"}}]} を返す。
    """
    return {
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }


def build_user_message(screen_input: SystemScreenInput) -> dict[str, object]:
    """user メッセージ {"role": "user", "content": [...]} を返す。

    content は、build_user_sections の各節の input_text、続けて frame_label(frame_t) の input_text と
    jpeg_data_url(frame_jpeg) の input_image。prompt_cache_breakpoint は置かない。
    """
    content: list[dict[str, object]] = [
        {"type": "input_text", "text": text}
        for text in build_user_sections(screen_input)
    ]
    content.append({"type": "input_text", "text": frame_label(screen_input.frame_t)})
    content.append(
        {"type": "input_image", "image_url": jpeg_data_url(screen_input.frame_jpeg)}
    )
    return {"role": "user", "content": content}


def build_system_screen_payload(
    screen_input: SystemScreenInput, screen_names: Sequence[str]
) -> dict[str, object]:
    """screen_input と見本の画面の名前の一覧 screen_names から、要求の本文 (モジュールの docstring の形) を作って返す。

    screen_names の誤り ("none" がある・同じ名前が 2 つ以上ある) は、build_system_screen_text_format と同じく
    ValueError にする。
    """
    return {
        "model": LUNA_MODEL,
        "reasoning": {"effort": SYSTEM_SCREEN_REASONING_EFFORT},
        "text": {"format": build_system_screen_text_format(screen_names)},
        "prompt_cache_options": {"mode": "explicit"},
        "input": [build_developer_message(), build_user_message(screen_input)],
    }
