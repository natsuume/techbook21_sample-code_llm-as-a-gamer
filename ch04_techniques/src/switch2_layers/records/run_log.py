"""実行の記録 run.jsonl。1 件を {"t": 秒, "event": 名前, ...} の 1 行で追記する。

surface・middle・deep のスケジューラー・deep のジョブ・終了処理の各スレッドから書くため、追記は 1 つのロックの中で行う。
"""

from collections.abc import Callable
from pathlib import Path

from switch2_layers.run_clock import RunClock

# event の名前
EVENT_START = "start"  # 起動。args (起動引数) と run_dir を含む
EVENT_STOP = "stop"  # 停止。reason (止まった理由) を含む
EVENT_SURFACE_ERROR = "surface_error"  # surface の decide の失敗
EVENT_MIDDLE_ERROR = "middle_error"  # middle の 1 回分の失敗
EVENT_DEEP_START = "deep_start"  # deep のジョブの開始
# deep のジョブに止める要求を出した (タイムアウト・終了時)
EVENT_DEEP_STOP = "deep_stop"
EVENT_DEEP_END = "deep_end"  # deep のジョブの終了 (kill を含む)
EVENT_DEEP_SKIPPED = "deep_skipped"  # deep を呼ぶ要求を捨てた
EVENT_FRAMES_DROPPED = "frames_dropped"  # 画像の保存を捨てた件数 (終了時)


class RunLog:
    """run.jsonl に 1 件ずつ追記する。

    path    run.jsonl のパス (親のディレクトリーは呼び出し側が作る)
    clock   t を付ける時計
    echo    要約の 1 行 (`<t> <event> <キー>=<値> ...`) を受け取る関数。None (既定) なら画面に出さない
    """

    def __init__(
        self,
        path: Path,
        clock: RunClock,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        """属性を持つ。ファイルはまだ作らない。"""
        raise NotImplementedError

    def write(self, event: str, /, **fields: object) -> None:
        """{"t": clock.now(), "event": event, **fields} を 1 行追記する (file_io.append_json_line)。

        event は位置専用の引数にし、fields に "event" のキーを渡しても引数の名前と衝突しないようにする。
        複数のスレッドから呼んでよい (追記と echo はロックの中で行う)。
        fields に "t" か "event" のキーがあれば ValueError にする (何も書かない)。
        JSON にできない値 (Path など) は文字列にする。
        """
        raise NotImplementedError
