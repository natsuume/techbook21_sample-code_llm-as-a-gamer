"""画面の停滞の値 (ProgressState) と、その受け渡しのファイル progress/state.json の読み書き。

progress/state.json の形 (キーはこの順):
    {"t": 最新のフレームの時刻, "static_s": 画面が変わっていない秒数, "no_novel_s": 新しい画面が出ていない秒数,
     "window_s": 新しい画面かを決めるときに見る範囲 (直近の秒数)}
時刻と秒数は、ほかの記録と同じく小数第 3 位 (ミリ秒) に丸めた値 (run_clock.round_to_milliseconds)。
書く側 (progress_loop) は、ほかの層の間のファイルと同じく置き換えの書き込み (file_io.write_json_atomic) で書く。
まだフレームが無いときはファイルを作らない。読む側は、ファイルが無い・読めないときは値が無いもの (None) として扱う。
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.file_io import write_json_atomic
from switch2_layers.run_clock import round_to_milliseconds

# progress/state.json のキー (この順に書く)
PROGRESS_STATE_KEYS: tuple[str, ...] = ("t", "static_s", "no_novel_s", "window_s")


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
    return {
        "t": round_to_milliseconds(state.t),
        "static_s": round_to_milliseconds(state.static_s),
        "no_novel_s": round_to_milliseconds(state.no_novel_s),
        "window_s": round_to_milliseconds(state.window_s),
    }


def progress_state_from_json(value: object) -> ProgressState:
    """progress/state.json を読んだ値を ProgressState にする。

    value が object でない、4 つのキーのどれかが無い、値が数 (int・float。bool を除く) でない、有限でない
    (nan・inf) ときは ValueError にする。ほかのキーは無視する。
    """
    # 契約どおり型の誤りも ValueError にする (型が合えば値を返し、合わなければ if の後で ValueError を出す)
    if isinstance(value, dict):
        t, static_s, no_novel_s, window_s = (
            _finite_number(value, key) for key in PROGRESS_STATE_KEYS
        )
        return ProgressState(
            t=t, static_s=static_s, no_novel_s=no_novel_s, window_s=window_s
        )
    raise ValueError("progress state must be a JSON object")


def _finite_number(value: Mapping[str, object], key: str) -> float:
    """value[key] が有限の数 (bool を除く) ならその値を返す。キーが無い・それ以外は ValueError。"""
    if key not in value:
        raise ValueError(f"progress state has no {key!r}")
    item = value[key]
    if isinstance(item, int | float) and not isinstance(item, bool):
        number = float(item)
        if math.isfinite(number):
            return number
    raise ValueError(f"{key!r} in progress state must be a finite number: {item!r}")


def write_progress_state(path: Path, state: ProgressState) -> None:
    """progress_state_to_json(state) を path に置き換えの書き込み (file_io.write_json_atomic) で書く。

    親のディレクトリーは作らない (無ければ FileNotFoundError)。
    """
    write_json_atomic(path, progress_state_to_json(state))


def read_progress_state(path: Path) -> ProgressState | None:
    """progress/state.json の path を読み、ProgressState を返す。

    無い・読めない (OSError・UnicodeDecodeError)・JSON として壊れている・progress_state_from_json が ValueError の
    ときは None (値が無い) を返す。
    """
    try:
        return progress_state_from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
