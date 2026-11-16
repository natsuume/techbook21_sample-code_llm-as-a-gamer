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
from pathlib import Path, PurePosixPath

import numpy as np

from switch2_harness.event_log import CommandEvent
from switch2_harness.screen_change import change_ratio, is_static, load_sample

# 表示する行動の数の上限
HISTORY_LIMIT = 20

# 行動として表示するイベントの command
HISTORY_COMMANDS: tuple[str, ...] = ("look", "press")

# 作業ディレクトリーからの相対パスの 2 枚の画像 (前, 後) を受け取り、画面が変わったなら True を返す関数の型
FramesChanged = Callable[[str, str], bool]

# 表示する行動が 1 件も無いときの行
NO_ACTIONS_LINE = "直近の行動: なし"


def _successful_look_frame(event: CommandEvent) -> str | None:
    """event が成功した look ならその frame を、そうでなければ None を返す。"""
    if event.command == "look" and event.ok:
        return event.frame
    return None


def _frame_before(ordered: Sequence[CommandEvent], index: int) -> str | None:
    """ordered[index] より前にある最後の成功した look の frame (直前の撮影) を返す。無ければ None。"""
    for event in reversed(ordered[:index]):
        frame = _successful_look_frame(event)
        if frame is not None:
            return frame
    return None


def _frame_after(
    ordered: Sequence[CommandEvent], index: int, current_frame: str
) -> str:
    """ordered[index] より後にある最初の成功した look の frame (直後の撮影) を返す。無ければ current_frame。"""
    for event in ordered[index + 1 :]:
        frame = _successful_look_frame(event)
        if frame is not None:
            return frame
    return current_frame


def _look_text(event: CommandEvent) -> str:
    """look の行の、秒数より後の部分を返す (成功なら frame のファイル名、失敗なら reply)。"""
    frame = _successful_look_frame(event)
    if frame is None:
        return f"look {event.reply}"
    return f"look {PurePosixPath(frame).name}"


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
    ordered = sorted(previous, key=lambda event: event.time)
    action_indexes = [
        index
        for index, event in enumerate(ordered)
        if event.command in HISTORY_COMMANDS
    ]
    shown_indexes = action_indexes[max(len(action_indexes) - limit, 0) :]
    if not shown_indexes:
        return (NO_ACTIONS_LINE,)
    # (直前の撮影, 直後の撮影) の組ごとの frames_changed の結果。同じ組は 1 回だけ比べる
    changed_by_pair: dict[tuple[str, str], bool] = {}

    def screen_change_word(index: int) -> str:
        before = _frame_before(ordered, index)
        if before is None:
            return "不明"
        pair = (before, _frame_after(ordered, index, current_frame))
        if pair not in changed_by_pair:
            changed_by_pair[pair] = frames_changed(*pair)
        return "あり" if changed_by_pair[pair] else "なし"

    lines = [f"直近の行動 (古い順、最大 {limit} 件。秒数はこの撮影の何秒前か):"]
    for index in shown_indexes:
        event = ordered[index]
        if event.command == "look":
            text = _look_text(event)
        elif event.ok:
            text = (
                f"press {' '.join(event.args)}: 画面の変化 {screen_change_word(index)}"
            )
        else:
            text = f"press {' '.join(event.args)}: {event.reply}"
        lines.append(f"- {round(now_time - event.time)} 秒前 {text}")
    return tuple(lines)


def frames_changed_on_disk(root: Path) -> FramesChanged:
    """作業ディレクトリー root の撮影の画像を読んで比べる FramesChanged を返す。

    返す関数は、2 つの相対パスの画像を screen_change.load_sample(root / <相対パス>) で読み、
    not screen_change.is_static(screen_change.change_ratio(<前の画像>, <後の画像>)) を返す。
    読んだ画像は返す関数の中の dict に相対パスごとに持ち、同じ相対パスの画像は 1 回だけ読む。
    """
    images: dict[str, np.ndarray] = {}

    def load(frame: str) -> np.ndarray:
        if frame not in images:
            images[frame] = load_sample(root / frame)
        return images[frame]

    def frames_changed(before: str, after: str) -> bool:
        return not is_static(change_ratio(load(before), load(after)))

    return frames_changed
