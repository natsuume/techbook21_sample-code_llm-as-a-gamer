"""SystemScreenInput から、本体の画面の middle の gpt-6-luna の要求の JSON (Responses API の本文) を組み立てる。I/O を扱わない。

要求の形:
    {
      "model": luna_request.LUNA_MODEL,
      "reasoning": {"effort": luna_request.REASONING_EFFORT},
      "text": {"format": system_screen_schema.build_system_screen_text_format(screen_names)},
      "prompt_cache_options": {"mode": "explicit"},
      "input": [
        {"role": "developer", "content": [
          {"type": "input_text", "text": system_screen_instructions.SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS,
           "prompt_cache_breakpoint": {"mode": "explicit"}}]},
        {"role": "user", "content": [
          {"type": "input_text", "text": <節 1>}, ..., {"type": "input_text", "text": <節 7>},
          {"type": "input_text", "text": luna_request.frame_label(frame_t)},
          {"type": "input_image", "image_url": luna_request.jpeg_data_url(frame_jpeg)}]}
      ]
    }
モデルと reasoning の effort は、ゲームの画面の middle (luna_request) と同じにする。developer メッセージは入力によらず
同じにし、その最後の (唯一の) 内容に明示のキャッシュの区切りを置く。user メッセージには区切りを置かない。
画像の detail は指定しない (既定の auto)。

user メッセージの節 (この順。各節は「## <USER_SECTION_TITLES の見出し>」の行で始まり、次の行から中身を書く):
    1. 現在の時刻             「t=<screen_input.t>」(frames.surface_frames.format_frame_label の形)
    2. 目的                   1 行目に目的の値 (prepare・recover・clean_up)、2 行目から
                              system_screen_instructions.goal_text(goal) の文
    3. 指定のソフト           screen_input.software_name (書かれたまま)
    4. 指定のユーザー         screen_input.user_name (書かれたまま)
    5. 照合の結果             describe_judgment(judged_screen, judged_frame_t)
    6. この段階のこれまでの呼び出し   describe_history(history)
    7. 今の画像               IMAGE_SECTION_TEXT
節の後ろに、今の画像の時刻のラベル (frame_label(frame_t)) と画像 (input_image) を 1 組置く。
"""

from collections.abc import Sequence

from switch2_layers.middle.system_screen_types import (
    SystemScreenInput,
    SystemStepRecord,
)

# user メッセージの節の見出し (この順に置く)
USER_SECTION_TITLES: tuple[str, ...] = (
    "現在の時刻",
    "目的",
    "指定のソフト",
    "指定のユーザー",
    "照合の結果",
    "この段階のこれまでの呼び出し",
    "今の画像",
)
EMPTY_TEXT = "(無い)"
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
    raise NotImplementedError


def describe_history(history: Sequence[SystemStepRecord]) -> str:
    """この段階のこれまでの呼び出しの節の中身を返す。

    history のうち新しい方から system_screen_types.MAX_HISTORY_ENTRIES 件を、古い順に 1 件 1 行で書く
    (行は改行 1 つでつなぎ、末尾に改行を付けない)。history が空なら EMPTY_TEXT を返す。1 行の形:
        - t=<t>: 照合の結果 <judged_screen (None なら none)>、操作の列 <operations の JSON>、
          送った操作 <sent_count>/<operations の数>、<最後まで送った | 打ち切った理由 <cut_reason の値>>
    t は format_frame_label の形。操作の列の JSON は、system_screen_schema.system_operation_to_json の値のリストを
    json.dumps(..., ensure_ascii=False) にしたもの (出力の operations と同じ形)。
    """
    raise NotImplementedError


def build_user_sections(screen_input: SystemScreenInput) -> list[str]:
    """user メッセージの 7 つの節の文を、モジュールの docstring の順と形で返す (画像の組は含まない)。"""
    raise NotImplementedError


def build_developer_message() -> dict[str, object]:
    """developer メッセージ {"role": "developer", "content": [{"type": "input_text",
    "text": SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS, "prompt_cache_breakpoint": {"mode": "explicit"}}]} を返す。
    """
    raise NotImplementedError


def build_user_message(screen_input: SystemScreenInput) -> dict[str, object]:
    """user メッセージ {"role": "user", "content": [...]} を返す。

    content は、build_user_sections の各節の input_text、続けて frame_label(frame_t) の input_text と
    jpeg_data_url(frame_jpeg) の input_image。prompt_cache_breakpoint は置かない。
    """
    raise NotImplementedError


def build_system_screen_payload(
    screen_input: SystemScreenInput, screen_names: Sequence[str]
) -> dict[str, object]:
    """screen_input と見本の画面の名前の一覧 screen_names から、要求の本文 (モジュールの docstring の形) を作って返す。

    screen_names の誤り ("none" がある・同じ名前が 2 つ以上ある) は、build_system_screen_text_format と同じく
    ValueError にする。
    """
    raise NotImplementedError
