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
middle/calls.jsonl の行は middle_loop.run_middle_step が書く。
"""

from dataclasses import dataclass

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.file_io import write_json_atomic, write_text_atomic
from switch2_layers.middle.middle_types import (
    MiddleInput,
    MiddleOutput,
    MiddleState,
    middle_state_to_json,
)
from switch2_layers.notes.note_paths import SCENE_LABELS_FILE, scene_note
from switch2_layers.notes.scene_labels import (
    SceneLabel,
    add_scene_label,
    read_scene_labels,
    resolve_scene,
)
from switch2_layers.run_directory import RunPaths

# 初めての場面に名前が付いたときに deep を呼ぶ文書の種類 (その場面の操作説明と攻略情報)
NEW_SCENE_DEEP_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind.CONTROLS,
    DocumentKind.TIPS,
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
) -> MiddleWriteResult:
    """output を実行ディレクトリー paths に書く (モジュールの docstring の順)。

    1. output.scene_name が空でなければ (前後の空白を除いて)、notes.scene_labels.read_scene_labels で一覧を読み、
       notes.scene_labels.resolve_scene(一覧, scene_name, first_seen_t=middle_input.t) で場面と、新しい場面かを決める
       (一覧は書き換えない)。scene_name が None か空なら、場面 ID も場面名も None (tasks.md も一覧も書かない)
    2. 場面 ID があれば、notes/scenes/<場面ID>/tasks.md を output.tasks で置き換える (ディレクトリーが無ければ作る)
    3. MiddleState(generated_t, based_on_t=middle_input.t, scene_id, scene_name (前後の空白を除いたもの), is_stuck,
       stuck_description, surface_wait_s, timeline, surface_rows_offset=middle_input.surface_rows_offset,
       operations_rows_offset=middle_input.operations_rows_offset) を middle/state.json に置き換えて書く
    4. 新しい場面なら、notes.scene_labels.add_scene_label(notes/scene_labels.json, 新しい場面) で一覧に足す。
       足した場面が戻り値の new_scene
    書き込みの例外はそのまま出す (それより前に書いたファイルは戻さない。失敗したときの挙動はモジュールの docstring)。
    """
    scene: SceneLabel | None = None
    is_new = False
    scene_name = (output.scene_name or "").strip()
    if scene_name:
        labels = read_scene_labels(paths.notes_dir / SCENE_LABELS_FILE)
        scene, is_new = resolve_scene(labels, scene_name, first_seen_t=middle_input.t)
    if scene is not None:
        tasks_path = paths.notes_dir / scene_note(scene.id, "tasks")
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(tasks_path, output.tasks)
    state = MiddleState(
        generated_t=generated_t,
        based_on_t=middle_input.t,
        scene_id=None if scene is None else scene.id,
        scene_name=None if scene is None else scene.name,
        is_stuck=output.is_stuck,
        stuck_description=output.stuck_description,
        surface_wait_s=output.surface_wait_s,
        timeline=output.timeline,
        surface_rows_offset=middle_input.surface_rows_offset,
        operations_rows_offset=middle_input.operations_rows_offset,
    )
    write_json_atomic(paths.middle_state, middle_state_to_json(state))
    new_scene: SceneLabel | None = None
    if scene is not None and is_new:
        add_scene_label(paths.notes_dir / SCENE_LABELS_FILE, scene)
        new_scene = scene
    return MiddleWriteResult(state=state, new_scene=new_scene)
