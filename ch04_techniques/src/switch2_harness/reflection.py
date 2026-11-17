"""要素「振り返り」(reflection) のうち、批評役の入力の組み立て、notes.md の節の置き換え、批評の間隔、look の知らせ。

流れ:
    run の間、reflection_critic が run の開始から REFLECTION_INTERVAL_S 秒ごとに批評役 (別の claude -p) を起動する。
    批評役には、直近 REFLECTION_INTERVAL_S 秒の撮影 (最大 MAX_CRITIC_FRAMES 枚。select_frames)・行動 (format_action_lines)・
    プレイ中のエージェントの発言 (assistant_texts)・notes.md を、1 つのユーザーのメッセージ (critic_input_line) で渡す。
    批評役の出力 (critique_from_result) は、ハーネスが notes.md の「振り返り」の節に、前の批評と置き換えて書き
    (replace_reflection_section)、records/events.jsonl に command が REFLECTION_COMMAND の行を足す。
    look は、最後の REFLECTION_COMMAND の行が最後の REFLECTION_NOTICE_COMMAND の行より後にあれば (notice_pending)、
    知らせの行 (NOTICE_LINE) を出し、REFLECTION_NOTICE_COMMAND の行を足す (build_notice_event)。

このモジュールはファイル・プロセス・時計に触れない (reflection_critic と commands が使う)。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from switch2_harness.event_log import CommandEvent

# 批評役を起動する間隔 (秒)。run の開始から数える
REFLECTION_INTERVAL_S = 300.0

# 批評役に渡す撮影の最大の枚数
MAX_CRITIC_FRAMES = 8

# notes.md の「振り返り」の節の見出しの行
REFLECTION_SECTION_HEADING = "## 振り返り"

# 批評を notes.md に書いたことを記録するイベントの command
REFLECTION_COMMAND = "reflection"

# look が知らせを出したことを記録するイベントの command
REFLECTION_NOTICE_COMMAND = "reflection-notice"

# 批評を書いた後の最初の look が出す行
NOTICE_LINE = "振り返り: notes.md の「振り返り」の節を書き換えました"

# 批評役への指示のテンプレートの、instructions/ の下のファイル名 ($game と $goal を string.Template で置換する)
CRITIC_INSTRUCTION_FILE = "reflection-critic.md"

# 批評役に渡す行動の command
ACTION_COMMANDS: tuple[str, ...] = ("look", "press")


@dataclass(frozen=True)
class CriticFrame:
    """批評役に渡す 1 枚の撮影。

    file_name: frames/ の下のファイル名 (例: "000123.png")
    offset_s: 区間の始まりから撮影までの秒数
    png_base64: PNG のバイト列を base64 にした ASCII の文字列
    """

    file_name: str
    offset_s: float
    png_base64: str


def format_offset(seconds: float) -> str:
    """区間の始まりからの秒数を `<分>:<秒 2 桁>` にする。小数は切り捨て、負の値は 0 とする。

    例: 0 → "0:00"、75.9 → "1:15"、300 → "5:00"、-3 → "0:00"。
    """
    raise NotImplementedError("Phase B で実装する")


def window_actions(
    events: Iterable[CommandEvent], window_start: float, window_end: float
) -> list[CommandEvent]:
    """events のうち、command が ACTION_COMMANDS にあり、window_start <= time <= window_end のものを、元の順で返す。

    成功・失敗のどちらも含める。
    """
    raise NotImplementedError("Phase B で実装する")


def select_frames(
    looks: Sequence[tuple[float, str]], max_count: int = MAX_CRITIC_FRAMES
) -> list[tuple[float, str]]:
    """撮影 looks ((時刻, frame) の組を時刻の順に並べたもの) から、批評役に渡す撮影を選んで、時刻の順に返す。

    - looks が max_count 枚以下なら、すべてを返す
    - そうでなければ、最初の撮影の時刻 first と最後の撮影の時刻 last のあいだに、等間隔の目標の時刻
      first + (last - first) * k / (max_count - 1) (k = 0, 1, ..., max_count - 1) を置き、目標ごとに、時刻の差が
      最も小さい撮影を選ぶ (差が同じなら looks の前のほう)。同じ撮影が複数の目標に選ばれたら 1 つにまとめる。
      k = max_count - 1 の目標は last なので、最後の撮影は必ず入る。撮影の時刻が偏っていると、max_count 枚より少なくなる
    - looks が空なら空のリストを返す。max_count は 2 以上として呼ぶ
    例: 時刻 0, 10, 20, ..., 90 の 10 枚から 4 枚なら、目標 0, 30, 60, 90 で 0, 30, 60, 90 の撮影。
    """
    raise NotImplementedError("Phase B で実装する")


def format_action_lines(
    actions: Iterable[CommandEvent], window_start: float
) -> list[str]:
    """批評役に渡す行動の行を返す。各行は、actions の順に次の形 (時刻は format_offset(<time> - window_start)):

        成功した look:  `- <時刻> look <frame のファイル名 (frames/ を除いた部分)>`
        失敗した look:  `- <時刻> look: <reply>`
        press:          `- <時刻> press <args を空白でつないだもの>: <reply>`
    例: `- 0:12 look 000123.png`、`- 0:15 press tap A: ok tap A`、`- 0:20 press tap HOME: error HOME is not allowed`。
    """
    raise NotImplementedError("Phase B で実装する")


def assistant_texts(lines: Iterable[str]) -> list[str]:
    """agent.jsonl (claude -p の stream-json) の行から、プレイ中のエージェントの発言を順に返す。

    JSON のオブジェクトとして読める行のうち、"type" が "assistant" で、"message" の "content" が配列の行について、
    配列の要素のうち "type" が "text" で "text" が文字列のものを、前後の空白を除いて返す (除いた後が空なら返さない)。
    JSON として読めない行、オブジェクトでない行、形が違う行は読み飛ばす。
    例: {"type":"assistant","message":{"content":[{"type":"text","text":" 右へ進みます\\n"},{"type":"tool_use",...}]}}
    の行からは "右へ進みます"。
    """
    raise NotImplementedError("Phase B で実装する")


def build_critic_content(
    instructions: str,
    frames: Sequence[CriticFrame],
    action_lines: Sequence[str],
    statements: Sequence[str],
    notes_text: str,
) -> list[dict[str, object]]:
    """批評役に渡すユーザーのメッセージの content (Messages API の content block の配列) を返す。

    順は次のとおり:
        1. {"type": "text", "text": instructions}
        2. {"type": "text", "text": "## 直近 5 分の撮影 (古い順)"}
        3. frames の各撮影 (i は 1 から) について、
           {"type": "text", "text": f"撮影 {i}: {file_name} ({format_offset(offset_s)})"} と
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_base64}}。
           frames が空なら、2. の代わりに {"type": "text", "text": "## 直近 5 分の撮影 (古い順)\\n\\n(なし)"} だけにする
        4. {"type": "text", "text": <次の 3 つを空行 1 つ ("\\n\\n") でつないだもの>}
           - "## 直近 5 分の行動 (時刻は 5 分の始まりからの経過)\\n\\n" + <action_lines を "\\n" でつないだもの。空なら "(なし)">
           - "## プレイヤーの発言 (古い順)\\n\\n" + <statements を "\\n\\n" でつないだもの。空なら "(なし)">
           - "## notes.md\\n\\n" + <notes_text の末尾の改行を除いたもの。空 (空白だけを含む) なら "(空)">
    """
    raise NotImplementedError("Phase B で実装する")


def critic_input_line(content: Sequence[dict[str, object]]) -> str:
    """批評役の標準入力 (--input-format stream-json) に書く 1 行 (末尾の改行を含む) を返す。

    {"type": "user", "message": {"role": "user", "content": <content のリスト>}} を json.dumps(..., ensure_ascii=False) にし、
    "\\n" を付ける。
    """
    raise NotImplementedError("Phase B で実装する")


def critique_from_result(lines: Iterable[str]) -> str | None:
    """批評役の出力 (stream-json) の行から、批評の本文を返す。

    JSON のオブジェクトとして読める行のうち、"type" が "result" の最後の行を見て、"subtype" が "success"、
    "is_error" が true でなく、"result" が文字列で、前後の空白を除いて空でなければ、前後の空白を除いた "result" を返す。
    そうでなければ (result の行が無いときを含む) None。
    """
    raise NotImplementedError("Phase B で実装する")


def section_body(minutes: int, critique: str) -> str:
    """notes.md の「振り返り」の節の、見出しの後に書く本文を返す。

    `(run の開始から <minutes> 分の時点の批評)` の行、空行、critique の前後の空白を除いたもの、の順に "\\n" でつなぐ
    (末尾に改行は付けない)。
    例: (10, "- 進んでいない\\n") → "(run の開始から 10 分の時点の批評)\\n\\n- 進んでいない"。
    """
    raise NotImplementedError("Phase B で実装する")


def replace_reflection_section(notes_text: str, body: str) -> str:
    """notes_text の「振り返り」の節を、見出しと body に置き換えた全文を返す。

    節は、行の末尾の空白を除くと REFLECTION_SECTION_HEADING に一致する最初の行から、その後で最初に `# ` か `## ` で
    始まる行の直前まで (無ければ最後まで)。
    - 節があれば、その範囲を `<見出し>\\n\\n<body>\\n` に置き換える。後ろに続く行があれば、間に空行を 1 つ置く
      (`<見出し>\\n\\n<body>\\n\\n<続く行>...`)。節より前の部分は変えない
    - 節が無ければ、notes_text の末尾の改行を除き、`\\n\\n<見出し>\\n\\n<body>\\n` を足す (notes_text が空なら
      `<見出し>\\n\\n<body>\\n` だけにする)
    結果は改行 1 つで終わる。
    例: ("# メモ\\n\\n## 操作方法\\n\\n- A\\n\\n## 振り返り\\n", "前") → "# メモ\\n\\n## 操作方法\\n\\n- A\\n\\n## 振り返り\\n\\n前\\n"。
    """
    raise NotImplementedError("Phase B で実装する")


def next_tick_index(
    run_start: float,
    now: float,
    last_index: int,
    interval_s: float = REFLECTION_INTERVAL_S,
) -> int:
    """次に批評役を起動する回の番号を返す。k 回目の起動の時刻は run_start + k * interval_s (k は 1 から)。

    last_index は前に起動した回の番号 (まだ起動していなければ 0)。
    last_index + 1 と、時刻 run_start + k * interval_s が now より後になる最小の k の、大きいほうを返す。
    前の批評が長引いて起動の時刻を過ぎた回は飛ばす (重なった回は起動しない)。
    例: run_start=0、interval_s=300 で、(now=10, last_index=0) → 1、(now=310, last_index=1) → 2、
    (now=650, last_index=1) → 3 (2 回目の 600 は過ぎたので飛ばす)。
    """
    raise NotImplementedError("Phase B で実装する")


def notice_pending(events: Sequence[CommandEvent]) -> bool:
    """look が振り返りの知らせを出すなら True を返す。

    events のうち command が REFLECTION_COMMAND の最後の行が、command が REFLECTION_NOTICE_COMMAND の最後の行より
    後ろ (リストの位置で比べる) にあれば True。REFLECTION_COMMAND の行が無ければ False。
    REFLECTION_NOTICE_COMMAND の行が無く REFLECTION_COMMAND の行があれば True。
    """
    raise NotImplementedError("Phase B で実装する")


def build_notice_event(look_time: float, frame: str) -> CommandEvent:
    """知らせを出したことの記録を返す。records/events.jsonl の、知らせを出した look の行 (停滞の知らせがあればその後) に足す。

    CommandEvent(time=look_time, command=REFLECTION_NOTICE_COMMAND, args=(), reply=NOTICE_LINE, ok=True,
    elapsed_s=0.0, frame=frame) を返す。
    """
    raise NotImplementedError("Phase B で実装する")


def build_reflection_event(time: float, index: int, elapsed_s: float) -> CommandEvent:
    """批評を notes.md に書いたことの記録を返す。

    CommandEvent(time=time, command=REFLECTION_COMMAND, args=(str(index),), reply="ok notes.md", ok=True,
    elapsed_s=elapsed_s, frame=None) を返す。index は批評の通し番号 (reflection_critic の記録の番号)、
    elapsed_s は批評役を起動してから書き終えるまでの秒数。
    """
    raise NotImplementedError("Phase B で実装する")
