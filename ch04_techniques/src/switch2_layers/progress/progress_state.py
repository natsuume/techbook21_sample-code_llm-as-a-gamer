"""画面の停滞の値 (ProgressState) と、その受け渡しのファイル progress/state.json の読み書き。

progress/state.json の形 (キーはこの順):
    {"t": 最新のフレームの時刻, "static_s": 画面が変わっていない秒数, "no_novel_s": 新しい画面が出ていない秒数,
     "window_s": 新しい画面かを決めるときに見る範囲 (直近の秒数)}
時刻と秒数は、ほかの記録と同じく小数第 3 位 (ミリ秒) に丸めた値 (run_clock.round_to_milliseconds)。
書く側 (progress_loop) は、ほかの層の間のファイルと同じく置き換えの書き込み (file_io.write_json_atomic) で書く。
まだフレームが無いときはファイルを作らない。読む側は、ファイルが無い・読めないときは値が無いもの (None) として扱う。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProgressState:
    """画面の停滞の値。

    t            値を計算した最新のフレームの時刻 (実行の経過秒)
    static_s     画面が変わっていない秒数 (screen_progress.ScreenProgressTracker の static_s)
    no_novel_s   新しい画面が出ていない秒数 (screen_progress.ScreenProgressTracker の no_novel_s)
    window_s     新しい画面かを決めるときに見る範囲 (直近の秒数。--progress-window)
    """

    t: float
    static_s: float
    no_novel_s: float
    window_s: float


def progress_state_to_json(state: ProgressState) -> dict[str, float]:
    """ProgressState を progress/state.json の辞書 {"t", "static_s", "no_novel_s", "window_s"} (この順) にする。

    値は run_clock.round_to_milliseconds で小数第 3 位に丸める。
    """
    raise NotImplementedError


def progress_state_from_json(value: object) -> ProgressState:
    """progress/state.json を読んだ値を ProgressState にする。

    value が object でない、4 つのキーのどれかが無い、値が数 (int・float。bool を除く) でない、有限でない
    (nan・inf) ときは ValueError にする。ほかのキーは無視する。
    """
    raise NotImplementedError


def write_progress_state(path: Path, state: ProgressState) -> None:
    """progress_state_to_json(state) を path に置き換えの書き込み (file_io.write_json_atomic) で書く。

    親のディレクトリーは作らない (無ければ FileNotFoundError)。
    """
    raise NotImplementedError


def read_progress_state(path: Path) -> ProgressState | None:
    """progress/state.json の path を読み、ProgressState を返す。

    無い・読めない (OSError・UnicodeDecodeError)・JSON として壊れている・progress_state_from_json が ValueError の
    ときは None (値が無い) を返す。
    """
    raise NotImplementedError
