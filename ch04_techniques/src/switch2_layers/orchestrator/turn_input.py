"""主ループの 1 ターンの入力 (テキストと画像のユーザーメッセージ 1 つ) を組み立てる。

ファイルを読む関数 (list_note_files・read_turn_images) のほかは、I/O を扱わない。

テキスト (build_turn_text) は、次の節を順に改行でつないだもの:
    ## 時刻                         t=<今の時刻> (frames.surface_frames.format_frame_label の形)
    ## 前の判断の後の出来事           describe_events
    ## 主ループの操作                 describe_running
    ## 画面の停滞 (コードの計測)       describe_progress_for_turn
    ## 前の JSON の誤り               describe_decision_error
    ## 今の監視                       describe_monitor
    ## 今のマクロ                     describe_macros
    ## 古いフレーム                   FRAMES_GUIDE_TEXT
セッションの最初の入力では、この前に build_session_opening_text の節を置く (人の初期指示・notes/ のファイルの一覧・
notes/handoff.md の中身)。
画像 (select_turn_frames): 前の入力を送った時刻から今までの一定間隔のフレームから、均等に最大 TURN_MAX_IMAGES 枚
(frames/interval/ に保存した幅 640 の JPEG をそのまま使う)。セッションの最初のターンは最新の 1 枚。
メッセージの内容 (build_turn_content): テキストのブロックの後に、画像ごとに時刻のラベルのテキストのブロックと画像の
ブロックを置く。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.frames.frame_store import FrameRecord
from switch2_layers.orchestrator.decision_types import (
    Action,
    DecisionError,
    MonitorCreate,
)
from switch2_layers.orchestrator.orchestrator_events import OrchestratorEvents
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.run_directory import RunPaths

# 1 ターンに送る画像の最大の枚数
TURN_MAX_IMAGES = 3
# 節の中身が無いときに書く文
NONE_TEXT = "無し"
# 古いフレームの節の文
FRAMES_GUIDE_TEXT = (
    "frames/interval/ に一定間隔のフレーム (<tミリ秒9桁>.jpg) がある。"
    "読んだ画像もコンテキストに残るため、必要なときだけ Read で読む"
)
# 画像のブロックの media_type
IMAGE_MEDIA_TYPE = "image/jpeg"


@dataclass(frozen=True)
class TurnImage:
    """入力に入れる画像 1 枚。

    t      フレームの時刻
    file   画像の実行ディレクトリーからの相対パス (frames.jsonl の file)
    jpeg   画像のバイト列 (保存したものをそのまま使う)
    """

    t: float
    file: str
    jpeg: bytes


@dataclass(frozen=True)
class TurnContext:
    """ふつうのターンのテキストの材料。

    now_t            今の時刻 (入力を作る時刻)
    events           前の判断以降の出来事と、今のマクロと監視 (OrchestratorController.take_events の結果)
    progress         画面の停滞の計測の最新の値 (progress/state.json)。無ければ None
    previous_error   前のターンの JSON の誤り。前のターンに誤りが無かった・前のターンが無ければ None
    """

    now_t: float
    events: OrchestratorEvents
    progress: ProgressState | None
    previous_error: DecisionError | None


@dataclass(frozen=True)
class SessionOpening:
    """セッションの最初の入力に足す材料。

    initial_instruction   人の初期指示。無ければ None
    note_files            notes/ のファイルの notes/ からの相対パス (list_note_files の結果)
    handoff_text          notes/handoff.md の中身 (無ければ空文字列)
    """

    initial_instruction: str | None
    note_files: tuple[str, ...]
    handoff_text: str


def select_turn_frames(
    records: Sequence[FrameRecord], previous_sent_t: float | None, now_t: float
) -> list[FrameRecord]:
    """frames.jsonl の記録 records から、入力に入れるフレームを選んで t の古い順に返す。

    対象は kind が "interval" で t <= now_t のもの (t の順に並べる)。
    - previous_sent_t が None (セッションの最初のターン): 対象のうち最新の 1 枚
    - そうでなければ、previous_sent_t < t <= now_t のものを frames.surface_frames.pick_evenly(…, TURN_MAX_IMAGES) で
      選ぶ (最も古いものと最新のものを含め、添字で等間隔)
    - その範囲に 1 枚も無ければ、対象のうち最新の 1 枚 (前の入力に入れたものと同じでもよい)
    対象が 1 枚も無ければ空のリスト。
    """
    raise NotImplementedError


def read_turn_images(
    paths: RunPaths, records: Sequence[FrameRecord]
) -> list[TurnImage]:
    """records の各画像を読み、TurnImage のリストを records の順に返す。

    画像のファイルが読めないもの (OSError) と、file のシンボリックリンクを解決した先が frames/interval/ を解決した
    先の下に無いものは除く (frames.jsonl の行が書き換えられても、実行ディレクトリーの外のファイルを送らないため。
    middle の入力と同じ規則)。
    """
    raise NotImplementedError


def action_to_json(action: Action) -> dict[str, object]:
    """操作 1 つを、主ループが書く JSON の形の辞書にする (今の監視とマクロの一覧に使う)。

    PressAction       {"type": "press", "buttons": [...]}。sticks が空でなければ
                      "sticks": {"left" | "right": {"x": x, "y": y}} を、seconds が None でなければ "seconds" を足す
    WaitAction        {"type": "wait", "seconds": 秒}
    ProcedureAction   {"type": "procedure", "name": 名前, "args": {引数}}
    MacroAction       {"type": "macro", "name": 名前}
    """
    raise NotImplementedError


def describe_events(events: OrchestratorEvents) -> str:
    """前の判断の後の出来事を、1 件 1 行の文にする (改行でつなぐ)。1 件も無ければ NONE_TEXT。

    次の順に書く:
        条件の当たりごとに   監視 <monitor> の条件 C<case_index> (<condition>) に <count> 回当たり、操作 <actions を
                             action_to_json の JSON の配列にしたもの> を実行した
        監視の終わりごとに   監視 <name> が終わった (<reason>): <detail>
        捨てた操作が 1 以上   新しい actions で捨てた前の操作: <discarded_action_count> 個
        外したボタンがあれば  押せずに外したボタン: <blocked_buttons を ", " でつないだもの>
    JSON は json.dumps(…, ensure_ascii=False)。
    """
    raise NotImplementedError


def describe_running(events: OrchestratorEvents) -> str:
    """主ループの操作の状態を 2 行の文にする: `実行中: はい` か `実行中: いいえ` と、`残りの操作: <remaining_action_count> 個`。"""
    raise NotImplementedError


def describe_progress_for_turn(progress: ProgressState | None, now_t: float) -> str:
    """画面の停滞の計測の値を文にする。

    progress が None か、now_t - progress.t が deep.deep_scheduler.PROGRESS_STATE_MAX_AGE_S を超える (古い) なら
    progress.progress_text.NO_PROGRESS_TEXT (ちょうどその秒数なら使う。古い値の扱いは deep のスケジューラーと同じ)。
    そうでなければ `計測した時刻: <format_frame_label(progress.t)>` の行と、
    progress.progress_text.describe_progress_values(progress) の 2 行。
    """
    raise NotImplementedError


def describe_decision_error(error: DecisionError | None) -> str:
    """前の JSON の誤りを文にする。None なら NONE_TEXT、そうでなければ説明ごとに `- <説明>` の行。"""
    raise NotImplementedError


def describe_monitor(monitor: MonitorCreate | None) -> str:
    """今の監視を JSON の文にする。None なら NONE_TEXT。

    {"name": 名前, "cases": [{"condition": 条件の文, "actions": [action_to_json の辞書, ...], "repeat": ...}, ...]} を
    json.dumps(…, ensure_ascii=False) にしたもの。
    """
    raise NotImplementedError


def describe_macros(macros: Mapping[str, Sequence[Action]]) -> str:
    """今のマクロを JSON の文にする。空なら NONE_TEXT。

    {名前: [action_to_json の辞書, ...]} (名前の順) を json.dumps(…, ensure_ascii=False) にしたもの。
    """
    raise NotImplementedError


def build_turn_text(context: TurnContext) -> str:
    """ふつうのターンのテキスト (モジュールの docstring の節の並び) を返す。"""
    raise NotImplementedError


def list_note_files(notes_dir: Path) -> tuple[str, ...]:
    """notes_dir の下の通常のファイルの、notes_dir からの / 区切りの相対パスを名前の順に返す。

    os.walk(followlinks=False) でたどり、シンボリックリンクと、置き換えの一時ファイル
    (file_io.is_temporary_file_name) は含めない。notes_dir が無ければ空のタプル。
    """
    raise NotImplementedError


def build_session_opening_text(opening: SessionOpening) -> str:
    """セッションの最初の入力の、ふつうのターンのテキストの前に置く節を返す。次の節を順に改行でつなぐ:

    ## 人の初期指示                  opening.initial_instruction (None ならこの節を書かない)
    ## notes/ のファイル             note_files ごとに `- <相対パス>` の行 (無ければ NONE_TEXT)
    ## 引き継ぎ (notes/handoff.md)   handoff_text (空か空白だけなら NONE_TEXT)
    """
    raise NotImplementedError


def image_label(image: TurnImage) -> str:
    """画像の前に置く時刻のラベル `<format_frame_label(image.t)> の画面 (<image.file>)` を返す。"""
    raise NotImplementedError


def build_turn_content(
    text: str, images: Sequence[TurnImage]
) -> list[dict[str, object]]:
    """ユーザーメッセージの内容のブロックの列を返す。

    [{"type": "text", "text": text}] の後に、images の順に、画像ごとに
    {"type": "text", "text": image_label(画像)} と
    {"type": "image", "source": {"type": "base64", "media_type": IMAGE_MEDIA_TYPE, "data": <jpeg の base64 (ASCII)>}}
    を足す。
    """
    raise NotImplementedError
