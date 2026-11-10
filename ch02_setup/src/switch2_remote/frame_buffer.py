"""受信したフレームを処理レートに間引き、残したフレームのうち最新の 1 枚を持つ純粋なロジック。

間引きはフレーム番号ベースの累積方式で行う。フレームが 1 枚届くたびに累積値に process_fps を足し、
累積値が ffmpeg_fps 以上になったらそのフレームを残して累積値から ffmpeg_fps を引く。
これで ffmpeg_fps 枚につきちょうど process_fps 枚を、なるべく等間隔に残す (例: 30 枚中 10 枚、30 枚中 12 枚)。
累積値の初期値は ffmpeg_fps - process_fps とし、最初のフレームを必ず残す (起動直後になるべく早く 1 枚目を保持するため)。

LatestFrame は最後に渡されたフレームとその時刻だけを持ち、渡されるたびに置き換える。
時刻は呼び出し側から受け取り、このモジュールでは時計を読まない。
format_latest_age は、最新フレームの時刻から現在までの経過秒を status 用の文字列にする。
"""


def initial_accumulator(ffmpeg_fps: int, process_fps: int) -> int:
    """累積値の初期値 (ffmpeg_fps - process_fps) を返す。処理レートを変えたときもこの値に戻す。"""
    return ffmpeg_fps - process_fps


def next_keep(acc: int, ffmpeg_fps: int, process_fps: int) -> tuple[int, bool]:
    """フレーム 1 枚分の間引きを判定し、新しい累積値と、このフレームを残すかを返す。"""
    acc += process_fps
    if acc >= ffmpeg_fps:
        return acc - ffmpeg_fps, True
    return acc, False


class LatestFrame[T]:
    """最後に渡されたフレームを、渡された時刻とともに 1 枚だけ持つ。"""

    def __init__(self):
        self.frame: T | None = None
        self.frame_time: float | None = None

    def update(self, now: float, frame: T) -> None:
        """持っているフレームと時刻を、時刻 now のフレームで置き換える。"""
        self.frame = frame
        self.frame_time = now

    def latest(self) -> T | None:
        """最後に渡されたフレームを返す。まだ渡されていなければ None を返す。"""
        return self.frame

    def latest_time(self) -> float | None:
        """最後に渡されたフレームの時刻を返す。まだ渡されていなければ None を返す。"""
        return self.frame_time


def format_latest_age(latest_time: float | None, now: float) -> str:
    """最新フレームの時刻 latest_time から now までの経過秒を `0.05s` の形 (小数第 2 位まで) で返す。

    latest_time が None (まだフレームが無い) なら `none` を返す。
    """
    if latest_time is None:
        return "none"
    return f"{now - latest_time:.2f}s"
