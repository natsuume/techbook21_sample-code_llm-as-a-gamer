"""middle の出力 (MiddleOutput) を、タスクのキューの部品 (tasks.task_board.TaskBoard) と実行ディレクトリーに書く。

書く順:
    1. 場面 ID を決める。場面名が一覧 (notes/scene_labels.json) にあればその ID、無ければ次の連番の新しい ID にする。
       ここでは一覧を書き換えない
    2. キューの部品を、次の順に操作する (時刻は generated_t。部品が、変わった場面の notes/scenes/<場面ID>/tasks.json・
       tasks.md と tasks.jsonl を書く):
       2a. 現在の場面を、出力で決まった場面にする (決まらなければ場面なし)
       2b. 取り消し: その回の呼ばれた理由に cancel_requested か stuck があるか、出力の is_stuck が真のときだけ、
           出力の cancel_task_ids で取り消す。それ以外の回の cancel_task_ids は使わない (middle/calls.jsonl に残すだけ)
       2c. 追加: 出力の場面が決まっていて、次のどちらかのときだけ、行き先を直した (middle.task_destination_fix)
           new_tasks を出力の場面のキューに足す (middle が入力で見たキューの末尾にだけ足すため)
             - 入力の場面 (前回の middle/state.json の scene_id) が出力の場面と同じ
             - 入力の場面が決まっていない (前回の middle/state.json が無い場合を含む。入力のキューは空に見える) で、
               2b の後の時点で出力の場面のキューが空
           それ以外 (場面が変わった回と、入力の場面が決まっていないのに出力の場面のキューにタスクがある回) は足さない。
           行き先の補正は、足さない回も計算する
       2d. 2b で 1 つ以上取り消し、2c の後に出力の場面のキューが空なら、trigger_notify("queue_empty") で middle を
           呼ぶきっかけを知らせる (出力の場面が決まらない回と、trigger_notify が None のときは知らせない)
    3. middle/state.json を置き換える
    4. 新しい場面なら、最後に notes/scene_labels.json に足す
surface が middle/state.json の新しい場面を読んだときに、その場面のキューがそろっているよう、2 を 3 より先に行う。
一覧に足すのを最後にするのは、途中で書き込みに失敗したときに、場面を一覧にだけ足した状態を残さないため。
失敗したときの挙動:
    - 2 か 3 で失敗したら、一覧は変わらない。次の回では同じ名前が再び新しい場面 (同じ次の連番の ID) として扱われ、
      new_scene になる (初めての場面の deep の要求が出る)。2 の途中で失敗したら、それまでのキューの操作は戻さない
    - 4 で失敗したら、middle/state.json には新しい場面が書かれているが、一覧には無い。次の回では同じ名前が再び
      新しい場面 (同じ ID) として扱われ、new_scene になる (deep の要求がもう一度出る)
止める合図 (stop) を渡すと、2 の前と、3・4 のそれぞれのファイルを書く直前に確かめ、立っていれば残りを書かずに抜ける
(書く順は変えず、それまでに書いたファイルとキューの操作は戻さない)。
middle/calls.jsonl の行は middle_loop.run_middle_step が書く (補正した new_tasks・直した内容・取り消しと追加を
使ったかは、戻り値の MiddleWriteResult で渡す)。
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.file_io import write_json_atomic
from switch2_layers.middle.middle_trigger import MiddleCallReason
from switch2_layers.middle.middle_types import (
    MiddleInput,
    MiddleOutput,
    MiddleState,
    middle_state_to_json,
)
from switch2_layers.middle.task_destination_fix import DestinationFix, fix_new_tasks
from switch2_layers.notes.note_paths import SCENE_LABELS_FILE
from switch2_layers.notes.scene_labels import (
    SceneLabel,
    add_scene_label,
    read_scene_labels,
    resolve_scene,
)
from switch2_layers.run_directory import RunPaths
from switch2_layers.tasks.task_board import TaskBoard
from switch2_layers.tasks.task_queue import NewTask

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

    state               middle/state.json に書いた値
    new_scene           一覧に新しく足した場面。足さなかったら None
    new_tasks           行き先を直した後の出力の new_tasks (足さなかった回も、直した後の値)
    destination_fixes   行き先を直した内容 (middle.task_destination_fix.fix_new_tasks の値。無ければ空)
    cancel_task_ids     出力の cancel_task_ids (使わなかった回も、出力の値のまま)
    cancel_used         cancel_task_ids で取り消しを行ったら True (書く順の 2b の条件に当たった回)
    new_tasks_used      new_tasks をキューに足したら True (書く順の 2c の条件に当たった回)
    """

    state: MiddleState
    new_scene: SceneLabel | None
    new_tasks: tuple[NewTask, ...]
    destination_fixes: tuple[DestinationFix, ...]
    cancel_task_ids: tuple[str, ...]
    cancel_used: bool
    new_tasks_used: bool


@dataclass(frozen=True)
class _TaskBoardUpdate:
    """書く順の 2 (キューの部品の操作) の結果。値は MiddleWriteResult の同じ名前の値。"""

    new_tasks: tuple[NewTask, ...]
    destination_fixes: tuple[DestinationFix, ...]
    cancel_used: bool
    new_tasks_used: bool


def write_middle_output(
    paths: RunPaths,
    middle_input: MiddleInput,
    output: MiddleOutput,
    generated_t: float,
    board: TaskBoard,
    reasons: Sequence[MiddleCallReason],
    trigger_notify: Callable[[MiddleCallReason], None] | None,
    stop: threading.Event | None = None,
) -> MiddleWriteResult | None:
    """output を、キューの部品 board と実行ディレクトリー paths に書く (モジュールの docstring の順)。

    reasons はその回の呼ばれた理由 (middle_input.reasons と同じ値)。trigger_notify は middle を呼ぶきっかけを知らせる
    関数 (run では middle.middle_trigger.MiddleTrigger.notify)。None なら知らせない。stop は止める合図。None なら
    合図を確かめない。

    1. output.scene_name を notes.scene_labels.normalize_scene_name で整えた名前 (前後の空白と末尾の場面 ID を
       除いたもの) が空でなければ、notes.scene_labels.read_scene_labels で一覧を読み、
       notes.scene_labels.resolve_scene(一覧, 整えた名前, first_seen_t=middle_input.t) で場面と、新しい場面かを決める
       (一覧は書き換えない)。scene_name が None か、整えた名前が空なら、場面 ID も場面名も None (一覧は書かない)
    2. stop を確かめてから、キューの部品を次の順に操作する (時刻はどれも generated_t):
       a. board.set_current_scene(1 の場面 ID か None, generated_t)
       b. reasons に cancel_requested か stuck があるか、output.is_stuck が真なら、
          board.cancel(output.cancel_task_ids, generated_t) で取り消す (cancel_used = True)。それ以外は呼ばない
          (cancel_used = False)
       c. middle.task_destination_fix.fix_new_tasks(output.new_tasks, middle_input.map_document) で行き先を直す
          (足さない回も計算する)。入力の場面を middle_input.previous_state の scene_id (previous_state が None なら
          None) とする。1 の場面 ID があり、次のどちらかのときだけ、board.add(1 の場面 ID, 直した new_tasks,
          generated_t) で足す (new_tasks_used = True):
            - 入力の場面が 1 の場面 ID と同じ
            - 入力の場面が None で、b の後の board.tasks(1 の場面 ID, generated_t) が空
          それ以外 (1 の場面 ID が無い回・入力の場面と 1 の場面 ID が違う回・入力の場面が None で 1 の場面のキューに
          タスクがある回) は足さない (new_tasks_used = False)
       d. b で 1 つ以上取り消し、c の後に 1 の場面 ID があって board.tasks(1 の場面 ID, generated_t) が空なら、
          trigger_notify("queue_empty") を呼ぶ (trigger_notify が None なら呼ばない)
    3. MiddleState(generated_t, based_on_t=middle_input.t, scene_id, scene_name (1 の場面の一覧の名前), is_stuck,
       stuck_description, surface_wait_s, timeline, surface_rows_offset=middle_input.surface_rows_offset,
       operations_rows_offset=middle_input.operations_rows_offset, tasks_rows_offset=middle_input.tasks_rows_offset,
       place_name, current_tile, press_home=output.press_home) を middle/state.json に置き換えて書く。
       place_name は output.place_name の前後の空白を除いたもの (None か、除いて空なら None)、current_tile は
       output.current_tile (place_name が None なら None)
    4. 新しい場面なら、notes.scene_labels.add_scene_label(notes/scene_labels.json, 新しい場面) で一覧に足す。
       足した場面が戻り値の new_scene
    2 の前と、3・4 のそれぞれのファイルを書く直前に stop を確かめ、立っていれば残りを書かずに None を返す
    (それより前に書いたファイルとキューの操作は戻さない)。
    書き込みとキューの部品の操作の例外はそのまま出す (それより前に書いたファイルとキューの操作は戻さない。
    失敗したときの挙動はモジュールの docstring)。
    戻り値の new_tasks・destination_fixes は 2c の値、cancel_task_ids は output.cancel_task_ids、cancel_used と
    new_tasks_used は 2b と 2c の値。
    """
    scene: SceneLabel | None = None
    is_new = False
    scene_name = (output.scene_name or "").strip()
    if scene_name:
        labels = read_scene_labels(paths.notes_dir / SCENE_LABELS_FILE)
        scene, is_new = resolve_scene(labels, scene_name, first_seen_t=middle_input.t)
    scene_id = None if scene is None else scene.id
    if _is_stopped(stop):
        return None
    update = _update_task_board(
        board, middle_input, output, scene_id, reasons, trigger_notify, generated_t
    )
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
        tasks_rows_offset=middle_input.tasks_rows_offset,
        place_name=place_name,
        current_tile=None if place_name is None else output.current_tile,
        press_home=output.press_home,
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
    return MiddleWriteResult(
        state=state,
        new_scene=new_scene,
        new_tasks=update.new_tasks,
        destination_fixes=update.destination_fixes,
        cancel_task_ids=output.cancel_task_ids,
        cancel_used=update.cancel_used,
        new_tasks_used=update.new_tasks_used,
    )


def _update_task_board(
    board: TaskBoard,
    middle_input: MiddleInput,
    output: MiddleOutput,
    scene_id: str | None,
    reasons: Sequence[MiddleCallReason],
    trigger_notify: Callable[[MiddleCallReason], None] | None,
    generated_t: float,
) -> _TaskBoardUpdate:
    """キューの部品を操作する (write_middle_output の 2 の a〜d)。scene_id は 1 で決めた場面 ID (決まらなければ None)。

    c の追加は、scene_id があり、入力の場面 (middle_input.previous_state の scene_id。previous_state が None なら None)
    が scene_id と同じか、入力の場面が None で b の後の scene_id のキューが空のときだけ行う。
    """
    # a. 現在の場面
    board.set_current_scene(scene_id, generated_t)
    # b. 取り消し (取り消しの要求か詰まりがある回だけ)
    cancel_used = "cancel_requested" in reasons or "stuck" in reasons or output.is_stuck
    canceled = board.cancel(output.cancel_task_ids, generated_t) if cancel_used else ()
    # c. 追加 (行き先の補正は、足さない回も計算する)
    new_tasks, destination_fixes = fix_new_tasks(
        output.new_tasks, middle_input.map_document
    )
    previous_state = middle_input.previous_state
    input_scene_id = None if previous_state is None else previous_state.scene_id
    new_tasks_used = scene_id is not None and (
        input_scene_id == scene_id
        or (input_scene_id is None and not board.tasks(scene_id, generated_t))
    )
    if scene_id is not None and new_tasks_used:
        board.add(scene_id, new_tasks, generated_t)
    # d. 取り消しでキューが空になったことの知らせ
    if (
        canceled
        and scene_id is not None
        and trigger_notify is not None
        and not board.tasks(scene_id, generated_t)
    ):
        trigger_notify("queue_empty")
    return _TaskBoardUpdate(
        new_tasks=new_tasks,
        destination_fixes=destination_fixes,
        cancel_used=cancel_used,
        new_tasks_used=new_tasks_used,
    )


def _is_stopped(stop: threading.Event | None) -> bool:
    """止める合図 stop が立っていれば True。stop が None (合図を確かめない) なら False。"""
    return stop is not None and stop.is_set()
