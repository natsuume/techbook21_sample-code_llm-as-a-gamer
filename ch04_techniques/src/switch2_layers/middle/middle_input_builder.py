"""middle の入力 (MiddleInput) を、実行ディレクトリーのファイルだけから作る。

run の middle のループと middle-once は、同じ build_middle_input で入力を作る。
読むファイル:
    frames.jsonl と frames/interval/     直近 window_s 秒の一定間隔のフレームから最大 MIDDLE_MAX_FRAMES 枚
    surface.jsonl・operations.jsonl      前回の middle の実行以降の行 (場面転換の印 scene_changed を含む)
    notes/scene_labels.json              場面ラベルの一覧
    middle/state.json                    前回の middle の出力
    notes/strategy.md                    大方針
    notes/scenes/<場面ID>/tasks.md        現在の場面の短期タスクリスト

「前回の middle の実行以降」の行の決め方 (surface.jsonl と operations.jsonl のそれぞれで同じ):
    - 行の時刻ではなく、ファイルのバイト位置で区切る。前回の middle/state.json の surface_rows_offset・
      operations_rows_offset の位置から、その時点でファイルにある完全な行 (改行で終わる行) をすべて読む
      (file_io.read_json_lines_from)。上限の時刻は設けない
    - 改行で終わらない最後の行 (書いている途中の行) は読まず、位置にも含めない。次の回に完全な行として読む
    - 今回読んだ完全な行の末尾の位置を、今回の MiddleInput の surface_rows_offset・operations_rows_offset にし、
      出力を書くときに middle/state.json に記録する。読んだ行が無ければ位置は変わらない
    - 前回の middle/state.json が無い・壊れているときは、先頭 (位置 0) から読む。記録した位置がファイルの大きさを
      超えるとき (ファイルを作り直した等) も先頭から読む。このとき、前に読んだ行をもう一度入力に含めることがある
時刻で区切らないのは次の理由による:
    - surface は t_received を決めた後、操作の送信 (ブリッジの ack の待ち) を挟んでから行を追記するため、入力を作った時点で
      「t_received が入力の時刻以前なのに、まだ書かれていない行」がありうる
    - operations.jsonl には同じ t (ミリ秒) の行がありうるため、時刻の最大値より後を読む方法では、同じ t の 2 行の
      書き込みの間に読むと、後の行がどの入力にも入らない
analyze か出力の書き込みが失敗して middle/state.json を書かなかった回は位置を進めないため、その回に読んだ行は
次の入力にも含める。
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
    - surface_rows と surface_rows_offset: file_io.read_json_lines_from(surface.jsonl, 前回の位置) の rows と end_offset。
      前回の位置は previous_state.surface_rows_offset (previous_state が None なら 0)。位置がファイルの大きさを超えていれば
      read_json_lines_from が先頭から読む。行の時刻 (t_received) では選ばない
    - operation_rows と operations_rows_offset: operations.jsonl について同じ (前回の位置は previous_state.operations_rows_offset)
    - scene_change_reported: surface_rows のどれかの scene_changed が True なら True
    - scene_labels: notes.scene_labels.read_scene_labels(notes/scene_labels.json)
    - strategy: notes/strategy.md (無ければ空文字列)
    - current_tasks: previous_state の scene_id の tasks.md (場面が無いか、文書が無ければ空文字列)
    ファイルが無いときは、それぞれ空 (0 枚・0 行・空のタプル・空文字列・位置 0) にする。
    """
    raise NotImplementedError
