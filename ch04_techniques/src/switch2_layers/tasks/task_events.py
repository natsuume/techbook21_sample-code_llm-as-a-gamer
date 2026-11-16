"""短期タスクのキューの出来事の記録 tasks.jsonl の行と、記録の中で最も大きいタスクの ID の連番。

1 件を {"t", "event", "scene_id", "id", "text", "reason", "active_s"} (キーはこの順) の 1 行で追記する
(追記は tasks.task_board.TaskBoard がロックの中で行う)。
    event      added (足した)・dropped (足せなかった)・done (完了)・cancel_requested (取り消しの要求)・
               canceled (取り消し)
    id         タスクの ID。dropped では null
    text       タスクの文 (dropped では、足せなかった文を normalize_task_text で整えた後の文)
    reason     dropped のときの足せなかった理由 (empty・duplicate・limit)。ほかでは null
    active_s   done・cancel_requested・canceled のときの、その時刻での続いた秒数。ほかでは null
"""

from pathlib import Path
from typing import Literal

from switch2_layers.tasks.task_queue import DroppedText, TaskView

# tasks.jsonl の出来事の名前
TaskEventName = Literal["added", "dropped", "done", "cancel_requested", "canceled"]


def task_event_row(
    t: float,
    event: Literal["added", "done", "cancel_requested", "canceled"],
    task: TaskView,
) -> dict[str, object]:
    """タスクの出来事 event の tasks.jsonl の 1 行の辞書を返す。

    {"t": t, "event": event, "scene_id": task.scene_id, "id": task.id, "text": task.text, "reason": None,
     "active_s": event が added なら None、ほかは task.active_s} (キーはこの順)。
    """
    raise NotImplementedError


def dropped_event_row(
    t: float, scene_id: str, dropped: DroppedText
) -> dict[str, object]:
    """場面 scene_id に足せなかった文 dropped の tasks.jsonl の 1 行の辞書を返す。

    {"t": t, "event": "dropped", "scene_id": scene_id, "id": None, "text": dropped.text,
     "reason": dropped.reason, "active_s": None} (キーはこの順)。
    """
    raise NotImplementedError


def max_task_number_in_log(path: Path) -> int:
    """tasks.jsonl の path の各行の id のうち、タスクの ID の形のものの連番の最大を返す。

    file_io.read_json_lines(path) の各行の id に task_queue.task_id_number を当てる。当たる行が無ければ
    (ファイルが無いときを含む) 0。id が null の行や形の違う行は無視する。
    """
    raise NotImplementedError
