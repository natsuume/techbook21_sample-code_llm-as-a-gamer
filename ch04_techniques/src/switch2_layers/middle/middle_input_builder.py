"""middle の入力 (MiddleInput) を、実行ディレクトリーのファイルだけから作る。

run の middle のループと middle-once は、同じ build_middle_input で入力を作る。
読むファイル:
    frames.jsonl と frames/interval/     直近 window_s 秒の一定間隔のフレームから最大 MIDDLE_MAX_FRAMES 枚
    surface.jsonl・operations.jsonl      前回の middle の実行以降の行 (場面転換の印 scene_changed を含む)
    notes/scene_labels.json              場面ラベルの一覧
    middle/state.json                    前回の middle の出力
    notes/strategy.md                    大方針
    notes/scenes/<場面ID>/tasks.md        現在の場面の短期タスクリスト
「前回の middle の実行以降」は、前回の middle/state.json の based_on_t より後 (書けた最後の出力の入力の時刻より後)。
analyze が失敗して middle/state.json を書かなかった回の行は、次の入力にも含める。
"""

from collections.abc import Sequence
from pathlib import Path

from switch2_layers.frames.frame_store import FrameRecord
from switch2_layers.middle.middle_types import MiddleInput, MiddleState
from switch2_layers.run_directory import RunPaths

MIDDLE_MAX_FRAMES = 4  # middle に渡すフレームの最大の枚数


def read_middle_state(path: Path) -> MiddleState | None:
    """middle/state.json の path を読み、MiddleState を返す。

    無い・読めない・JSON として壊れている・middle_types.middle_state_from_json が ValueError のときは None。
    """
    raise NotImplementedError


def has_recorded_frame(paths: RunPaths) -> bool:
    """frames.jsonl に 1 行以上の記録があれば True を返す (middle のループの最初の実行の条件)。"""
    raise NotImplementedError


def select_interval_frames(
    records: Sequence[FrameRecord],
    now_t: float,
    window_s: float,
    max_count: int = MIDDLE_MAX_FRAMES,
) -> list[FrameRecord]:
    """frames.jsonl の記録 records から、middle に渡す一定間隔のフレームを選んで t の古い順に返す。

    kind が "interval" で now_t - window_s <= t <= now_t (両端を含む) のものを t の順に並べ、
    max_count 枚以下ならすべて、多ければ frames.surface_frames.select_surface_frames と同じ添字の規則
    (最新と最も古いものを含め、添字で等間隔) で max_count 枚を選ぶ。
    """
    raise NotImplementedError


def build_middle_input(paths: RunPaths, now_t: float, window_s: float) -> MiddleInput:
    """時刻 now_t の middle の入力を、実行ディレクトリー paths のファイルだけから作る (モジュールの docstring)。

    - frames: select_interval_frames(read_frame_records(paths), now_t, window_s) の各画像を読んだ MiddleFrame。
      画像のファイルが読めないものは除く
    - previous_state: read_middle_state(paths.middle_state)
    - surface_rows: surface.jsonl の行のうち、previous_state.based_on_t < t_received <= now_t のもの
      (previous_state が None なら t_received <= now_t のすべて。t_received が数でない行は除く)
    - operation_rows: operations.jsonl の行のうち、同じ範囲の t のもの
    - scene_change_reported: surface_rows のどれかの scene_changed が True なら True
    - scene_labels: notes.scene_labels.read_scene_labels(notes/scene_labels.json)
    - strategy: notes/strategy.md (無ければ空文字列)
    - current_tasks: previous_state の scene_id の tasks.md (場面が無いか、文書が無ければ空文字列)
    ファイルが無いときは、それぞれ空 (0 枚・0 行・空のタプル・空文字列) にする。
    """
    raise NotImplementedError
