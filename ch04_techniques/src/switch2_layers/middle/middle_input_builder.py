"""middle の入力 (MiddleInput) を、実行ディレクトリーのファイルだけから作る。

run の middle のループと middle-once は、同じ build_middle_input で入力を作る。
読むファイル:
    frames.jsonl と frames/interval/     直近 window_s 秒の一定間隔のフレームから最大 MIDDLE_MAX_FRAMES 枚
    surface.jsonl・operations.jsonl      前回の middle の実行以降の行 (場面転換の印 scene_changed を含む)
    notes/scene_labels.json              場面ラベルの一覧
    middle/state.json                    前回の middle の出力
    notes/game.md                        ゲームの性質
    notes/strategy.md                    大方針
    notes/scenes/<場面ID>/tasks.md        現在の場面の短期タスクリスト
    notes/scenes/<場面ID>/controls.md     現在の場面の操作説明 (無いか空なら notes/common/controls.md)
    notes/map/map.json                   マップ (場所とつながりの一覧)。場所の名前の一覧にも使う
    middle/map_records.jsonl             middle のマップの記録。記録の場所の名前を、場所の名前の一覧に使う
    progress/state.json                  画面の停滞の計測の最新の値 (progress.progress_loop が書く)

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

import json
from collections.abc import Sequence
from pathlib import Path

from switch2_layers.file_io import read_json_lines_from
from switch2_layers.frames.frame_store import FrameRecord, read_frame_records
from switch2_layers.frames.surface_frames import pick_evenly
from switch2_layers.middle.map_records import read_recorded_place_names
from switch2_layers.middle.middle_types import (
    MiddleFrame,
    MiddleInput,
    MiddleState,
    middle_state_from_json,
)
from switch2_layers.middle.place_names import collect_place_names
from switch2_layers.notes.map_document import read_map_document
from switch2_layers.notes.note_paths import (
    GAME_NOTE,
    SCENE_LABELS_FILE,
    STRATEGY_NOTE,
    scene_note,
)
from switch2_layers.notes.note_reader import read_note_text
from switch2_layers.notes.scene_labels import read_scene_labels
from switch2_layers.notes.surface_documents import read_surface_documents
from switch2_layers.progress.progress_state import read_progress_state
from switch2_layers.run_directory import RunPaths

MIDDLE_MAX_FRAMES = 4  # middle に渡すフレームの最大の枚数


def read_middle_state(path: Path) -> MiddleState | None:
    """middle/state.json の path を読み、MiddleState を返す。

    無い・読めない・JSON として壊れている・middle_types.middle_state_from_json が ValueError のときは None。
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return middle_state_from_json(value)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def has_recorded_frame(paths: RunPaths) -> bool:
    """frames.jsonl に 1 行以上の記録があれば True を返す (middle のループの最初の実行の条件)。"""
    return bool(read_frame_records(paths))


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
    in_window = sorted(
        (
            record
            for record in records
            if record.kind == "interval" and now_t - window_s <= record.t <= now_t
        ),
        key=lambda record: record.t,
    )
    return pick_evenly(in_window, max_count)


def build_middle_input(paths: RunPaths, now_t: float, window_s: float) -> MiddleInput:
    """時刻 now_t の middle の入力を、実行ディレクトリー paths のファイルだけから作る (モジュールの docstring)。

    - frames: select_interval_frames(read_frame_records(paths), now_t, window_s) の各画像を読んだ MiddleFrame。
      画像のファイルが読めないものと、file がシンボリックリンクを解決した先で frames/interval/ の中を指さないものは除く
      (frames.jsonl の行が書き換えられても、実行ディレクトリーの外のファイルを middle に送らないため)
    - previous_state: read_middle_state(paths.middle_state)
    - surface_rows と surface_rows_offset: file_io.read_json_lines_from(surface.jsonl, 前回の位置) の rows と end_offset。
      前回の位置は previous_state.surface_rows_offset (previous_state が None なら 0)。位置がファイルの大きさを超えていれば
      read_json_lines_from が先頭から読む。行の時刻 (t_received) では選ばない
    - operation_rows と operations_rows_offset: operations.jsonl について同じ (前回の位置は previous_state.operations_rows_offset)
    - scene_change_reported: surface_rows のどれかの scene_changed が True なら True
    - scene_labels: notes.scene_labels.read_scene_labels(notes/scene_labels.json)
    - game: notes/game.md の本文 (無ければ空文字列。本文は読んだまま (前後の空白も除かない) にする)
    - strategy: notes/strategy.md (無ければ空文字列)
    - current_tasks: previous_state の scene_id の tasks.md (場面が無いか、文書が無ければ空文字列)
    - current_controls: previous_state の scene_id (previous_state が None なら None) の操作説明。
      notes.surface_documents.read_surface_documents(notes_dir, scene_id).controls と同じ規則で、場面の controls.md が
      あって空 (空白だけを含む) でなければその本文、そうでなければ notes/common/controls.md の本文 (どちらも読めなければ
      空文字列)。本文は読んだまま (前後の空白も除かない) にする
    - map_document: notes.map_document.read_map_document(notes_dir)。無い・読めない・形に合わないときは None (マップなし。
      middle の実行は失敗にしない)
    - place_names: middle.place_names.collect_place_names(map_document,
      middle.map_records.read_recorded_place_names(middle/map_records.jsonl))
    - progress: progress.progress_state.read_progress_state(progress/state.json)。無い・読めない・形に合わないときは
      None (値なし。middle の実行は失敗にしない)
    ファイルが無いときは、それぞれ空 (0 枚・0 行・空のタプル・空文字列・位置 0・None) にする。
    game・strategy・current_tasks・map.json の文書は notes.note_reader で読み、シンボリックリンクを解決した先が
    notes/ の中の通常のファイルでなければ、無い文書 (空文字列。map.json は None) として扱う。UTF-8 として読めない
    game・strategy・current_tasks も空文字列にする。
    """
    previous_state = read_middle_state(paths.middle_state)
    surface_chunk = read_json_lines_from(
        paths.surface_log,
        0 if previous_state is None else previous_state.surface_rows_offset,
    )
    operations_chunk = read_json_lines_from(
        paths.operations_log,
        0 if previous_state is None else previous_state.operations_rows_offset,
    )
    current_tasks = ""
    if previous_state is not None and previous_state.scene_id is not None:
        current_tasks = read_note_text(
            paths.notes_dir, scene_note(previous_state.scene_id, "tasks")
        )
    # 操作説明は、surface が読むものと同じ文書にする (surface と同じ関数で選ぶ)
    current_controls = read_surface_documents(
        paths.notes_dir, None if previous_state is None else previous_state.scene_id
    ).controls
    map_document = read_map_document(paths.notes_dir)
    place_names = collect_place_names(
        map_document, read_recorded_place_names(paths.middle_map_records)
    )
    return MiddleInput(
        t=now_t,
        frames=tuple(_read_frames(paths, now_t, window_s)),
        surface_rows=tuple(surface_chunk.rows),
        operation_rows=tuple(operations_chunk.rows),
        scene_change_reported=any(
            row.get("scene_changed") is True for row in surface_chunk.rows
        ),
        scene_labels=tuple(read_scene_labels(paths.notes_dir / SCENE_LABELS_FILE)),
        previous_state=previous_state,
        strategy=read_note_text(paths.notes_dir, STRATEGY_NOTE),
        current_tasks=current_tasks,
        surface_rows_offset=surface_chunk.end_offset,
        operations_rows_offset=operations_chunk.end_offset,
        current_controls=current_controls,
        place_names=tuple(place_names),
        map_document=map_document,
        game=read_note_text(paths.notes_dir, GAME_NOTE),
        progress=read_progress_state(paths.progress_state),
    )


def _read_frames(paths: RunPaths, now_t: float, window_s: float) -> list[MiddleFrame]:
    """middle に渡すフレームを選んで画像を読む。

    画像のファイルが読めないものと、file が frames/interval/ の中を指さないもの (シンボリックリンクを解決した先で
    確かめる) は除く。frames.jsonl の行を書き換えられても、実行ディレクトリーの外のファイルを middle に送らないため。
    """
    interval_dir = paths.interval_frames_dir.resolve()
    frames: list[MiddleFrame] = []
    for record in select_interval_frames(read_frame_records(paths), now_t, window_s):
        image_path = (paths.root / record.file).resolve()
        if interval_dir not in image_path.parents:
            continue
        try:
            jpeg = image_path.read_bytes()
        except OSError:
            continue
        frames.append(MiddleFrame(t=record.t, file=record.file, jpeg=jpeg))
    return frames
