"""短期タスクのキューの出来事の記録 tasks.jsonl の行と、記録の中で最も大きいタスクの ID の連番。

1 件を {"t", "event", "scene_id", "id", "text", "reason", "active_s", "destination"} (キーはこの順) の 1 行で追記する
(追記は tasks.task_board.TaskBoard がロックの中で行う)。
    event      added (足した)・dropped (足せなかった)・done (完了)・cancel_requested (取り消しの要求)・
               canceled (取り消し)
    id         タスクの ID。dropped では null
    text       タスクの文 (dropped では、足せなかった文を normalize_task_text で整えた後の文)
    reason     dropped のときの足せなかった理由 (empty・duplicate・limit)。ほかでは null
    active_s   done・cancel_requested・canceled のときの、その時刻での続いた秒数。ほかでは null
    destination   added のときの、そのタスクの行き先 (task_files.destination_to_json の形。行き先が無ければ null)。
                  ほかの出来事と dropped では null
"""

from pathlib import Path
from typing import Literal

from switch2_layers.file_io import read_json_lines
from switch2_layers.tasks.task_files import destination_to_json
from switch2_layers.tasks.task_queue import DroppedText, TaskView, task_id_number

# tasks.jsonl の出来事の名前
TaskEventName = Literal["added", "dropped", "done", "cancel_requested", "canceled"]


def task_event_row(
    t: float,
    event: Literal["added", "done", "cancel_requested", "canceled"],
    task: TaskView,
) -> dict[str, object]:
    """タスクの出来事 event の tasks.jsonl の 1 行の辞書を返す。

    {"t": t, "event": event, "scene_id": task.scene_id, "id": task.id, "text": task.text, "reason": None,
     "active_s": event が added なら None、ほかは task.active_s,
     "destination": event が added なら task_files.destination_to_json(task.destination)、ほかは None}
    (キーはこの順)。
    """
    return {
        "t": t,
        "event": event,
        "scene_id": task.scene_id,
        "id": task.id,
        "text": task.text,
        "reason": None,
        "active_s": None if event == "added" else task.active_s,
        "destination": destination_to_json(task.destination)
        if event == "added"
        else None,
    }


def dropped_event_row(
    t: float, scene_id: str, dropped: DroppedText
) -> dict[str, object]:
    """場面 scene_id に足せなかった文 dropped の tasks.jsonl の 1 行の辞書を返す。

    {"t": t, "event": "dropped", "scene_id": scene_id, "id": None, "text": dropped.text,
     "reason": dropped.reason, "active_s": None, "destination": None} (キーはこの順)。
    """
    return {
        "t": t,
        "event": "dropped",
        "scene_id": scene_id,
        "id": None,
        "text": dropped.text,
        "reason": dropped.reason,
        "active_s": None,
        "destination": None,
    }


def max_task_number_in_log(path: Path) -> int:
    """tasks.jsonl の path の各行の id のうち、タスクの ID の形のものの連番の最大を返す。

    file_io.read_json_lines(path) の各行の id に task_queue.task_id_number を当てる。当たる行が無ければ
    (ファイルが無いときを含む) 0。id が null の行や形の違う行は無視する。
    """
    numbers = (task_id_number(row.get("id")) for row in read_json_lines(path))
    return max((number for number in numbers if number is not None), default=0)
