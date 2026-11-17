"""場面の短期タスクのキューの状態のファイル notes/scenes/<場面ID>/tasks.json と tasks.md の書き込みと、保存済みの
tasks.json の読み込み。

tasks.json の形 (キーはこの順):
    {"t": 書いた時刻, "tasks": [{"id", "text", "added_t", "active_s", "cancel_requested_t"}, ...]}
    tasks は未完了のタスクをキューの順に並べたもの (無ければ空の配列)。active_s は書いた時刻までの続いた秒数、
    cancel_requested_t は取り消しの要求の時刻 (要求が無ければ null)
tasks.md の形:
    未完了のタスクを、キューの順に `- <文>` の行で並べたもの。無ければ空のファイル
どちらも置き換えの書き込み (file_io.write_json_atomic・write_text_atomic) で書く。
"""

import json
from collections.abc import Sequence
from pathlib import Path

from switch2_layers.file_io import write_json_atomic, write_text_atomic
from switch2_layers.notes.note_paths import (
    SCENES_DIR,
    is_scene_id,
    scene_note,
    tasks_json_note,
)
from switch2_layers.tasks.task_queue import TaskView


def tasks_json_value(tasks: Sequence[TaskView], t: float) -> dict[str, object]:
    """時刻 t に書く tasks.json の辞書を返す。

    {"t": t, "tasks": [{"id", "text", "added_t", "active_s", "cancel_requested_t"}, ...]} (キーはこの順)。
    tasks の各項目は TaskView の同じ名前の値で、tasks の順に並べる。tasks が空なら空の配列。
    """
    return {
        "t": t,
        "tasks": [
            {
                "id": task.id,
                "text": task.text,
                "added_t": task.added_t,
                "active_s": task.active_s,
                "cancel_requested_t": task.cancel_requested_t,
            }
            for task in tasks
        ],
    }


def tasks_markdown(tasks: Sequence[TaskView]) -> str:
    """tasks.md の中身を返す。各タスクを `- <text>\\n` の行にして tasks の順につなげたもの。tasks が空なら空文字列。"""
    return "".join(f"- {task.text}\n" for task in tasks)


def write_scene_task_files(
    notes_dir: Path, scene_id: str, tasks: Sequence[TaskView], t: float
) -> None:
    """場面 scene_id のキューの状態 tasks を、時刻 t の tasks.json と tasks.md に書く。

    notes_dir / scenes/<scene_id>/ を mkdir(parents=True, exist_ok=True) で作ってから、
    notes_dir / note_paths.tasks_json_note(scene_id) に file_io.write_json_atomic(tasks_json_value(tasks, t)) で、
    続けて notes_dir / note_paths.scene_note(scene_id, "tasks") に file_io.write_text_atomic(tasks_markdown(tasks))
    で書く。既にあるファイルは置き換える。scene_id が場面 ID の形でなければ ValueError。
    書き込みの例外はそのまま出す。
    """
    # tasks_json_note が場面 ID の形を確かめるため、ディレクトリーを作る前に呼ぶ
    json_path = notes_dir / tasks_json_note(scene_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(json_path, tasks_json_value(tasks, t))
    write_text_atomic(notes_dir / scene_note(scene_id, "tasks"), tasks_markdown(tasks))


def read_saved_tasks(notes_dir: Path) -> list[TaskView]:
    """notes_dir の下に保存された tasks.json から、未完了のタスクを読む。

    notes_dir/scenes/ の下の、名前が場面 ID の形 (note_paths.is_scene_id) のディレクトリーを名前の順に見て、
    tasks.json があれば、その tasks の各項目を TaskView (scene_id はディレクトリーの名前、ほかは項目の同じ名前の値)
    にする。ディレクトリーの順、配列の順に並べて返す。
    notes_dir/scenes/ が無ければ空のリスト。tasks.json の読み込みの例外 (壊れた JSON など) はそのまま出す。
    """
    scenes_dir = notes_dir / SCENES_DIR
    if not scenes_dir.is_dir():
        return []
    saved: list[TaskView] = []
    for directory in sorted(scenes_dir.iterdir(), key=lambda path: path.name):
        scene_id = directory.name
        if not (directory.is_dir() and is_scene_id(scene_id)):
            continue
        json_path = notes_dir / tasks_json_note(scene_id)
        if not json_path.is_file():
            continue
        value = json.loads(json_path.read_text(encoding="utf-8"))
        for item in value["tasks"]:
            saved.append(
                TaskView(
                    id=item["id"],
                    text=item["text"],
                    scene_id=scene_id,
                    added_t=item["added_t"],
                    active_s=item["active_s"],
                    cancel_requested_t=item["cancel_requested_t"],
                )
            )
    return saved
