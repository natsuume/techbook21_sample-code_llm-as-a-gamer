"""middle の出力 (MiddleOutput) を実行ディレクトリーに書く。

書く順:
    1. 場面 ID を決める。場面名が一覧 (notes/scene_labels.json) にあればその ID、無ければ次の連番の新しい ID にする。
       ここでは一覧を書き換えない
    2. 現在の場面の notes/scenes/<場面ID>/tasks.md を置き換える
    3. middle/state.json を置き換える
    4. 新しい場面なら、最後に notes/scene_labels.json に足す
surface が middle/state.json の新しい場面を読んだときに、その場面の tasks.md がそろっているよう、2 を 3 より先に書く。
一覧に足すのを最後にするのは、途中で書き込みに失敗したときに、場面を一覧にだけ足した状態を残さないため。
失敗したときの挙動:
    - 2 か 3 で失敗したら、一覧は変わらない。次の回では同じ名前が再び新しい場面 (同じ次の連番の ID) として扱われ、
      new_scene になる (初めての場面の deep の要求が出る)
    - 4 で失敗したら、middle/state.json には新しい場面が書かれているが、一覧には無い。次の回では同じ名前が再び
      新しい場面 (同じ ID) として扱われ、new_scene になる (deep の要求がもう一度出る)
止める合図 (stop) を渡すと、2〜4 のそれぞれのファイルを書く直前に確かめ、立っていれば残りを書かずに抜ける
(書く順は変えず、それまでに書いたファイルは戻さない)。
middle/calls.jsonl の行は middle_loop.run_middle_step が書く。
"""

import threading
from dataclasses import dataclass

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.file_io import write_json_atomic, write_text_atomic
from switch2_layers.middle.middle_types import (
    MiddleInput,
    MiddleOutput,
    MiddleState,
    middle_state_to_json,
)
from switch2_layers.middle.task_progress import first_task_of, first_task_since
from switch2_layers.notes.note_paths import SCENE_LABELS_FILE, scene_note
from switch2_layers.notes.scene_labels import (
    SceneLabel,
    add_scene_label,
    read_scene_labels,
    resolve_scene,
)
from switch2_layers.run_directory import RunPaths

# 初めての場面に名前が付いたときに deep を呼ぶ文書の種類 (その場面の操作説明と攻略情報と、ゲームの性質)。
# ゲームの性質は場面に依らないため、どの場面でも同じ notes/game.md の文書のジョブになる
NEW_SCENE_DEEP_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind.CONTROLS,
    DocumentKind.TIPS,
    DocumentKind.GAME,
)


@dataclass(frozen=True)
class MiddleWriteResult:
    """middle の出力を書いた結果。

    state       middle/state.json に書いた値
    new_scene   一覧に新しく足した場面。足さなかったら None
    """

    state: MiddleState
    new_scene: SceneLabel | None


def write_middle_output(
    paths: RunPaths,
    middle_input: MiddleInput,
    output: MiddleOutput,
    generated_t: float,
    stop: threading.Event | None = None,
) -> MiddleWriteResult | None:
    """output を実行ディレクトリー paths に書く (モジュールの docstring の順)。

    stop は止める合図。None なら合図を確かめない。

    1. output.scene_name が空でなければ (前後の空白を除いて)、notes.scene_labels.read_scene_labels で一覧を読み、
       notes.scene_labels.resolve_scene(一覧, scene_name, first_seen_t=middle_input.t) で場面と、新しい場面かを決める
       (一覧は書き換えない)。scene_name が None か空なら、場面 ID も場面名も None (tasks.md も一覧も書かない)
    2. 場面 ID があれば、notes/scenes/<場面ID>/tasks.md を output.tasks で置き換える (ディレクトリーが無ければ作る)
    3. MiddleState(generated_t, based_on_t=middle_input.t, scene_id, scene_name (前後の空白を除いたもの), is_stuck,
       stuck_description, surface_wait_s, timeline, surface_rows_offset=middle_input.surface_rows_offset,
       operations_rows_offset=middle_input.operations_rows_offset, first_task, first_task_since_t, place_name,
       current_tile) を middle/state.json に置き換えて書く。first_task は middle.task_progress.first_task_of(output.tasks)、
       first_task_since_t は middle.task_progress.first_task_since(middle_input.previous_state, first_task, generated_t,
       scene_id=今回の場面 ID) (first_task が空なら None。前回と今回の場面 ID がどちらも決まっていて違えば引き継がない)。
       place_name は output.place_name の前後の空白を除いたもの (None か、除いて空なら None)、current_tile は
       output.current_tile (place_name が None なら None)。
       場面が決まらない (tasks.md を書かない) 回も同じに計算する
    4. 新しい場面なら、notes.scene_labels.add_scene_label(notes/scene_labels.json, 新しい場面) で一覧に足す。
       足した場面が戻り値の new_scene
    2〜4 のそれぞれのファイルを書く直前に stop を確かめ、立っていれば残りを書かずに None を返す
    (それより前に書いたファイルは戻さない)。
    書き込みの例外はそのまま出す (それより前に書いたファイルは戻さない。失敗したときの挙動はモジュールの docstring)。
    """
    scene: SceneLabel | None = None
    is_new = False
    scene_name = (output.scene_name or "").strip()
    if scene_name:
        labels = read_scene_labels(paths.notes_dir / SCENE_LABELS_FILE)
        scene, is_new = resolve_scene(labels, scene_name, first_seen_t=middle_input.t)
    if scene is not None:
        if _is_stopped(stop):
            return None
        tasks_path = paths.notes_dir / scene_note(scene.id, "tasks")
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(tasks_path, output.tasks)
    first_task = first_task_of(output.tasks)
    scene_id = None if scene is None else scene.id
    place_name = (output.place_name or "").strip() or None
    state = MiddleState(
        generated_t=generated_t,
        based_on_t=middle_input.t,
        scene_id=scene_id,
        scene_name=None if scene is None else scene.name,
        is_stuck=output.is_stuck,
        stuck_description=output.stuck_description,
        surface_wait_s=output.surface_wait_s,
        timeline=output.timeline,
        surface_rows_offset=middle_input.surface_rows_offset,
        operations_rows_offset=middle_input.operations_rows_offset,
        first_task=first_task,
        first_task_since_t=first_task_since(
            middle_input.previous_state, first_task, generated_t, scene_id=scene_id
        ),
        place_name=place_name,
        current_tile=None if place_name is None else output.current_tile,
    )
    if _is_stopped(stop):
        return None
    write_json_atomic(paths.middle_state, middle_state_to_json(state))
    new_scene: SceneLabel | None = None
    if scene is not None and is_new:
        if _is_stopped(stop):
            return None
        add_scene_label(paths.notes_dir / SCENE_LABELS_FILE, scene)
        new_scene = scene
    return MiddleWriteResult(state=state, new_scene=new_scene)


def _is_stopped(stop: threading.Event | None) -> bool:
    """止める合図 stop が立っていれば True。stop が None (合図を確かめない) なら False。"""
    return stop is not None and stop.is_set()
