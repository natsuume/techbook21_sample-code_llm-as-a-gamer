"""系列情報の「画面の変化」(screen の節)。最新の画面が、いつからほぼ変わっていないかを追う。

キャプチャーのスレッドが、処理したフレーム (間引いた後のフレーム) を 1 枚ずつ ScreenChangeTracker.feed に渡す。
基準のフレームと比べた縮小グレー画像の差 (image_metrics.mean_abs_diff) がしきい値以上になったら、そのフレームを
新しい基準にし、その時刻を「画面が変わった時刻」にする。基準と同じに見える画面が続いている間は、変わった時刻を保つ。
実機の記録 (2026-11-14 の実行) では、行き詰まっていた場面の止まっている画面の差は 0.8 以下 (時間の表示などの
小さな変化を含む)、歩いたりスクロールしたりしている画面の差は 4 以上だったため、しきい値の既定値を 2.0 にしている。
敵が動くゲームの画面 (何も押さなかった区間) では、主人公が止まっていても差が 1.2〜2.1 になった区間がある
(画面全体の変化を見るため、主人公が動いたかどうかは分からない)。
feed はキャプチャーのスレッド、static_since は Pipeline のスレッドから呼ぶため、読み書きは 1 つのロックの中で行う。
"""

import threading

import numpy as np

from switch2_agent.frame_history import TimedFrame
from switch2_agent.image_metrics import mean_abs_diff
from switch2_agent.timeline.moments import format_moment

DEFAULT_SCREEN_CHANGE_THRESHOLD = 2.0  # 画面が変わったとみなす差 (0〜255) の既定値
# 画面が変わってからこの秒数に満たなければ、「直前まで変わっていた」と書く
STATIC_REPORT_MIN_S = 2.0


class ScreenChangeTracker:
    """画面が最後に変わった時刻を持つ。

    属性:
        threshold    画面が変わったとみなす差の下限 (この値以上なら変わった)
        lock         reference と changed_at の読み書きを守るロック
        reference    基準のフレームの縮小グレー画像。まだフレームを受け取っていなければ None
        changed_at   基準のフレームの時刻 (画面が最後に変わった時刻)。まだフレームを受け取っていなければ None
    """

    def __init__(self, threshold: float = DEFAULT_SCREEN_CHANGE_THRESHOLD):
        """まだフレームを受け取っていない状態で作る。"""
        self.threshold = threshold
        self.lock = threading.Lock()
        self.reference: np.ndarray | None = None
        self.changed_at: float | None = None

    def feed(self, frame: TimedFrame) -> None:
        """frame を受け取る。

        最初のフレームなら、それを基準にする。そうでなければ、基準との差
        mean_abs_diff(reference, frame.gray_small) が threshold 以上のときだけ、frame を新しい基準にし、
        changed_at を frame.time にする。
        """
        with self.lock:
            if (
                self.reference is None
                or mean_abs_diff(self.reference, frame.gray_small) >= self.threshold
            ):
                self.reference = frame.gray_small
                self.changed_at = frame.time

    def static_since(self) -> float | None:
        """画面が最後に変わった時刻 (その時刻から、画面がほぼ変わっていない) を返す。まだフレームが無ければ None。"""
        with self.lock:
            return self.changed_at


def format_screen_change(static_since: float | None, now: float, origin: float) -> str:
    """画面の変化の節の文を返す。static_since が None (まだフレームが無い) なら空文字を返す。

    1 行目は見出し `## 画面の変化`。経過秒 elapsed = max(0, now - static_since) が STATIC_REPORT_MIN_S 未満なら
    `- 最新の画面は、直前まで変わっていた (<elapsed> 秒前までの画面と違う)`、
    そうでなければ `- 最新の画面は、<format_moment(static_since)> から、ほぼ変わっていない (縮小した画像どうしの差がほとんど無い)`
    を書く (秒は小数第 1 位まで)。Pipeline が now を取った後に、キャプチャーのスレッドが新しいフレームを渡すと、
    static_since が now より後になることがあるため、elapsed を 0 で下に切る。
    """
    if static_since is None:
        return ""
    elapsed = max(0.0, now - static_since)
    if elapsed < STATIC_REPORT_MIN_S:
        body = (
            f"- 最新の画面は、直前まで変わっていた ({elapsed:.1f} 秒前までの画面と違う)"
        )
    else:
        body = (
            f"- 最新の画面は、{format_moment(static_since, now, origin)} から、"
            "ほぼ変わっていない (縮小した画像どうしの差がほとんど無い)"
        )
    return "## 画面の変化\n" + body
