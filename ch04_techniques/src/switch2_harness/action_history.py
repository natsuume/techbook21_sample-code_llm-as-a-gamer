"""行動履歴の要素 (action-history): look の後に表示する、直近の行動 (look と press) の行を作る。

行の組み立て (build_history_lines) は純粋関数で、2 枚の撮影で画面が変わったかは引数の関数 (frames_changed) で受け取る。
本番の frames_changed は frames_changed_on_disk が作り、作業ディレクトリーの PNG を読んで比べる。
履歴は events.jsonl のイベントから作るため、セッションをまたいで残る。

用語:
    行動: command が HISTORY_COMMANDS ("look" か "press") のイベント
    成功した look: command が "look" で ok が True、frame が None でないイベント。それ以外の look は失敗した look
    成功した press: command が "press" で ok が True のイベント。それ以外の press は失敗した press
    直前の撮影: press より前にある最後の成功した look の frame
    直後の撮影: press より後にある最初の成功した look の frame。無ければ今回の look の frame
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from switch2_harness.event_log import CommandEvent

# 表示する行動の数の上限
HISTORY_LIMIT = 20

# 行動として表示するイベントの command
HISTORY_COMMANDS: tuple[str, ...] = ("look", "press")

# 作業ディレクトリーからの相対パスの 2 枚の画像 (前, 後) を受け取り、画面が変わったなら True を返す関数の型
FramesChanged = Callable[[str, str], bool]


def build_history_lines(
    previous: Sequence[CommandEvent],
    now_time: float,
    current_frame: str,
    frames_changed: FramesChanged,
    limit: int = HISTORY_LIMIT,
) -> tuple[str, ...]:
    """今回の look の後に表示する、直近の行動の行を返す。

    previous は今回の look より前のイベント (events.jsonl の全件から今回の look を除いたもの)、
    now_time は今回の look のイベントの time、current_frame は今回の look の frame (例: "frames/000045.png")。
    previous は time の昇順に並べ替えてから (同じ時刻は元の順のまま) 使う。

    表示するのは、並べ替えた previous のうち行動の最後の limit 件 (古い順)。
    1 件も無ければ ("直近の行動: なし",) を返す。
    あれば、最初の行を `直近の行動 (古い順、最大 <limit> 件。秒数はこの撮影の何秒前か):` とし、続けて 1 件 1 行:
        成功した look: `- <秒> 秒前 look <frame のファイル名>`
        失敗した look: `- <秒> 秒前 look <reply>`
        成功した press: `- <秒> 秒前 press <args を半角空白でつないだもの>: 画面の変化 <あり|なし|不明>`
        失敗した press: `- <秒> 秒前 press <args を半角空白でつないだもの>: <reply>`
    <秒> は round(now_time - event.time) の整数。
    例: `- 95 秒前 look 000041.png`、`- 12 秒前 press hold LS:-1,0 500: 画面の変化 なし`。

    成功した press の画面の変化は、並べ替えた previous 全体 (表示する limit 件に限らない) から
    直前の撮影と直後の撮影を決める。直前の撮影が無ければ「不明」。あれば
    frames_changed(直前の撮影, 直後の撮影) が True なら「あり」、False なら「なし」。
    同じ (直前の撮影, 直後の撮影) の組について frames_changed を呼ぶのは 1 回だけにする
    (同じ 2 枚の look の間にある press は、どれも同じ結果になる)。
    """
    raise NotImplementedError


def frames_changed_on_disk(root: Path) -> FramesChanged:
    """作業ディレクトリー root の撮影の画像を読んで比べる FramesChanged を返す。

    返す関数は、2 つの相対パスの画像を screen_change.load_sample(root / <相対パス>) で読み、
    not screen_change.is_static(screen_change.change_ratio(<前の画像>, <後の画像>)) を返す。
    読んだ画像は返す関数の中の dict に相対パスごとに持ち、同じ相対パスの画像は 1 回だけ読む。
    """
    raise NotImplementedError
