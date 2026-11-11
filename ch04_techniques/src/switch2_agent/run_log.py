"""実行のログを JSON Lines (1 行 1 件の JSON) のファイルに追記する。

Pipeline のスレッドと advisor のスレッドの両方から書くため、書き込みは 1 つのロックの中で行う。
"""

import threading
from collections.abc import Callable
from pathlib import Path


def format_wall_time(now: float) -> str:
    """壁時計の時刻 now (time.time の値) を `HH:MM:SS.mmm` (time.strftime("%H:%M:%S") とミリ秒 3 桁) にする。

    ミリ秒は now の小数部を 1000 倍して切り捨てた値。
    """
    raise NotImplementedError


def summarize_event(event: dict) -> str:
    """event を、画面に出す要約の 1 行 (改行を含まない) にする。

    `<event["event"]> <キー>=<値> <キー>=<値> …` の形 (event のキーの順。event キー自体は先頭にだけ書く)。
    event キーが無ければ `event` と書く。
    """
    raise NotImplementedError


class RunLog:
    """ログのファイル path に、1 件ずつ JSON の行を追記する。

    path   追記するファイル (親のディレクトリーは呼び出し側が作る)
    echo   要約の 1 行を受け取る関数 (既定 print)。None なら画面に出さない
    """

    path: Path
    echo: Callable[[str], None] | None
    lock: threading.Lock

    def __init__(self, path: Path, echo: Callable[[str], None] | None = print):
        """属性を入れる。ファイルはまだ開かない。"""
        raise NotImplementedError

    def write(self, event: dict) -> None:
        """event の先頭に "time" (format_wall_time(time.time())) を足した辞書を、JSON の 1 行として path に追記する。

        JSON は ensure_ascii=False で書き、JSON にできない値は str にする。ファイルは UTF-8 で開く。
        echo があれば `<time> <summarize_event(event)>` の 1 行を渡す。追記と echo はロックの中で行う。
        """
        raise NotImplementedError
