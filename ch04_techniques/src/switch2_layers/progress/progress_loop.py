"""画面の停滞の計測のループ。frames.jsonl に足された一定間隔のフレームを読み、値を計算して progress/state.json に書く。

1 回分の処理 (ProgressLoop.step):
    1. frames.jsonl を、前回読んだ完全な行の末尾のバイト位置から読む (file_io.read_json_lines_from)。
       改行で終わらない最後の行 (書いている途中の行) は読まず、次の回に読む
    2. kind が "interval" の行を書かれた順に取り、画像 (frames/interval/ に保存した縮小画像の JPEG) を読んで
       cv2.imdecode で画像にし、ScreenProgressTracker.add_frame に渡す
    3. 1 枚以上渡したら、最後に渡したフレームの値を progress/state.json に置き換えの書き込みで書く
       (progress_state.write_progress_state)。渡したフレームが無ければ書かない (まだフレームが無いときは
       progress/state.json を作らない)
次の行は読み飛ばす (値を計算しない): kind が interval でない行、t が数でない行・前に渡したフレームの時刻以下の行、
file が文字列でない行、file がシンボリックリンクを解決した先で frames/interval/ の中を指さない行、画像が読めない・
JPEG として読めない行。
frames.jsonl の行は、画像を書き終えた後に足されるため (frames.frame_store)、行があれば画像は読める。
ここで読む画像は、frames/interval/ に保存したものそのもので、記録に後から計算を当てるときと同じ画像になる。

ループ (ProgressLoop.run) は、止める合図が立つまで step を繰り返し、各 step の後に poll_interval_s 秒待つ。
step の想定外の例外は、ループを止めずに logging で警告を書く (計測が止まっても、ゲームの操作は続ける)。
"""

import logging
import threading

from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.screen_progress import ScreenProgressTracker
from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# frames.jsonl に足された行を確かめる間隔 (秒)
POLL_INTERVAL_S = 0.2


class ProgressLoop:
    """画面の停滞の計測を繰り返す。step と run は 1 つのスレッドからだけ呼ぶ。

    paths             実行ディレクトリー (frames.jsonl・frames/interval/ を読み、progress/state.json を書く)
    tracker           値を計算するもの
    poll_interval_s   run が step の後に待つ秒数 (既定 POLL_INTERVAL_S)
    """

    def __init__(
        self,
        paths: RunPaths,
        tracker: ScreenProgressTracker,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        """frames.jsonl を先頭 (位置 0) から読む状態で作る。"""
        raise NotImplementedError

    def step(self) -> ProgressState | None:
        """1 回分を処理し (モジュールの docstring の 1〜3)、書いた値を返す。書かなければ None。"""
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(poll_interval_s) で待つ (スレッドの本体)。

        step の想定外の例外 (Exception) は外に出さず、logging で警告を書いて続ける。
        """
        raise NotImplementedError
