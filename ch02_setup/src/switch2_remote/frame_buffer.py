"""受信したフレームを処理レートに間引き、直近の一定時間分を保持する純粋なロジック。

間引きはフレーム番号ベースの累積方式で行う。フレームが 1 枚届くたびに累積値に process_fps を足し、
累積値が ffmpeg_fps 以上になったらそのフレームを残して累積値から ffmpeg_fps を引く。
これで ffmpeg_fps 枚につきちょうど process_fps 枚を、なるべく等間隔に残す (例: 30 枚中 10 枚、30 枚中 12 枚)。
累積値の初期値は ffmpeg_fps - process_fps とし、最初のフレームを必ず残す (起動直後になるべく早く 1 枚目を保持するため)。

FrameBuffer は (時刻, フレーム) の並びを持ち、追加のたびに保持時間より古い要素を先頭から捨てる。
時刻は呼び出し側から受け取り、このモジュールでは時計を読まない。
format_latest_age は、最新フレームの時刻から現在までの経過秒を status 用の文字列にする。
"""

from collections import deque


def initial_accumulator(ffmpeg_fps: int, process_fps: int) -> int:
    """累積値の初期値 (ffmpeg_fps - process_fps) を返す。処理レートを変えたときもこの値に戻す。"""
    return ffmpeg_fps - process_fps


def next_keep(acc: int, ffmpeg_fps: int, process_fps: int) -> tuple[int, bool]:
    """フレーム 1 枚分の間引きを判定し、新しい累積値と、このフレームを残すかを返す。"""
    acc += process_fps
    if acc >= ffmpeg_fps:
        return acc - ffmpeg_fps, True
    return acc, False


class FrameBuffer[T]:
    """直近 seconds 秒分のフレームを、追加した時刻とともに古い順に保持する。

    要素の寿命は時刻で決め、個数の上限は持たない (処理レートを変えても作り直さずに使えるようにするため)。
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.items: deque[tuple[float, T]] = deque()

    def append(self, now: float, frame: T) -> None:
        """時刻 now のフレームを追加し、now - seconds より古い要素を先頭から捨てる。"""
        self.items.append((now, frame))
        oldest_allowed = now - self.seconds
        # 追加したばかりの要素は必ず残るため、空になるまで捨て続けることはない
        while self.items[0][0] < oldest_allowed:
            self.items.popleft()

    def latest(self) -> T | None:
        """最後に追加したフレームを返す。空なら None を返す。"""
        if not self.items:
            return None
        return self.items[-1][1]

    def latest_time(self) -> float | None:
        """最後に追加したフレームの時刻を返す。空なら None を返す。"""
        if not self.items:
            return None
        return self.items[-1][0]

    def __len__(self) -> int:
        return len(self.items)


def format_latest_age(latest_time: float | None, now: float) -> str:
    """最新フレームの時刻 latest_time から now までの経過秒を `0.05s` の形 (小数第 2 位まで) で返す。

    latest_time が None (バッファーが空) なら `none` を返す。
    """
    if latest_time is None:
        return "none"
    return f"{now - latest_time:.2f}s"
