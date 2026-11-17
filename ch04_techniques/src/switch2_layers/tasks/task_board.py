"""短期タスクのキューを 1 つのロックの中で操作し、ファイルと記録を書く部品 (TaskBoard) と、保存済みの実行ディレクトリー
からの作り直し (restore_task_board)。

TaskBoard は task_queue.SceneTaskQueues を持ち、すべての操作 (状態の変更、場面のファイル tasks.json・tasks.md の
書き込み、tasks.jsonl の追記) を 1 つの threading.Lock の中で行う。複数のスレッド (surface と middle) から呼んでよい。
1 つの操作の中の順は、状態の変更 → 変わった場面のファイル (task_files.write_scene_task_files) → tasks.jsonl の追記
(file_io.append_json_line)。キューが変わらない操作は、ファイルも記録も書かない。
書き込みの例外はそのまま出す。
"""

import threading
from collections.abc import Mapping, Sequence

from switch2_layers.file_io import append_json_line
from switch2_layers.run_directory import RunPaths
from switch2_layers.tasks.task_events import (
    dropped_event_row,
    max_task_number_in_log,
    task_event_row,
)
from switch2_layers.tasks.task_files import read_saved_tasks, write_scene_task_files
from switch2_layers.tasks.task_queue import (
    AddResult,
    NewTask,
    SceneTaskQueues,
    TaskView,
    task_id_number,
)


class TaskBoard:
    """短期タスクのキューと、その状態のファイルと出来事の記録をまとめて扱う。複数のスレッドから呼んでよい。

    paths    実行ディレクトリーのパス。場面のファイルは paths.notes_dir の下に、出来事は paths.tasks_log に書く
    """

    def __init__(self, paths: RunPaths, queues: SceneTaskQueues | None = None) -> None:
        """paths と queues (None なら空の SceneTaskQueues()) を持つ。ファイルは書かない。"""
        self.paths = paths
        self._queues = SceneTaskQueues() if queues is None else queues
        self._lock = threading.Lock()

    def add(self, scene_id: str, new_tasks: Sequence[NewTask], t: float) -> AddResult:
        """SceneTaskQueues.add(scene_id, new_tasks, t) で足し、その結果を返す (行き先は中身を確かめずに持つ)。

        1 つでも足したら、その場面のファイルを書く (時刻 t のキューの状態)。tasks.jsonl には入力の順に、足した組は
        task_events.task_event_row(t, "added", 足したタスク)、足せなかった組は
        task_events.dropped_event_row(t, scene_id, 足せなかった文) を 1 行ずつ追記する。
        1 つも足さなければ、場面のファイルは書かず、dropped の行だけを追記する。
        """
        with self._lock:
            result = self._queues.add(scene_id, new_tasks, t)
            if result.added:
                self._write_scene(scene_id, t)
            for outcome in result.outcomes:
                if isinstance(outcome, TaskView):
                    self._append(task_event_row(t, "added", outcome))
                else:
                    self._append(dropped_event_row(t, scene_id, outcome))
            return result

    def complete(self, scene_id: str, task_id: str, t: float) -> bool:
        """SceneTaskQueues.complete(scene_id, task_id, t) で完了にし、完了にしたかを返す。

        完了にしたら、その場面のファイルを書き、task_events.task_event_row(t, "done", 外したタスク) を追記して True。
        しなければ何も書かずに False。
        """
        with self._lock:
            done = self._queues.complete(scene_id, task_id, t)
            if done is None:
                return False
            self._write_scene(scene_id, t)
            self._append(task_event_row(t, "done", done))
            return True

    def request_cancel(self, scene_id: str, task_id: str, t: float) -> bool:
        """SceneTaskQueues.request_cancel(scene_id, task_id, t) で取り消しの要求を付け、新しく付けたかを返す。

        新しく付けたら、その場面のファイルを書き (cancel_requested_t が変わるため)、
        task_events.task_event_row(t, "cancel_requested", 要求を付けたタスク) を追記して True。
        でなければ何も書かずに False。
        """
        with self._lock:
            requested = self._queues.request_cancel(scene_id, task_id, t)
            if requested is None:
                return False
            self._write_scene(scene_id, t)
            self._append(task_event_row(t, "cancel_requested", requested))
            return True

    def cancel(self, task_ids: Sequence[str], t: float) -> tuple[TaskView, ...]:
        """SceneTaskQueues.cancel(task_ids, t) で取り消し、取り消したタスクの t での見え方を取り消した順に返す。

        取り消したタスクがあれば、変わった場面をすべて (取り消したタスクの並びで最初に出た順に) 書き、
        取り消したタスクの順に task_events.task_event_row(t, "canceled", 取り消したタスク) を追記する。
        未完了のタスクが無くなった場面も、`"tasks": []` の tasks.json と空の tasks.md を書く。
        1 つも取り消さなければ何も書かない。
        """
        with self._lock:
            canceled = self._queues.cancel(task_ids, t)
            # dict.fromkeys で、取り消したタスクの場面を、最初に出た順に重ならないように並べる
            for scene_id in dict.fromkeys(task.scene_id for task in canceled):
                self._write_scene(scene_id, t)
            for task in canceled:
                self._append(task_event_row(t, "canceled", task))
            return canceled

    @property
    def current_scene_id(self) -> str | None:
        """ロックの中で SceneTaskQueues.current_scene_id (現在の場面 ID。場面なしなら None) を返す。"""
        raise NotImplementedError

    def set_current_scene(self, scene_id: str | None, t: float) -> None:
        """SceneTaskQueues.set_current_scene(scene_id, t) で現在の場面を変える。ファイルも記録も書かない。"""
        with self._lock:
            self._queues.set_current_scene(scene_id, t)

    def oldest_active_s(self, scene_id: str, t: float) -> float | None:
        """SceneTaskQueues.oldest_active_s(scene_id, t) を返す。ファイルも記録も書かない。"""
        with self._lock:
            return self._queues.oldest_active_s(scene_id, t)

    def tasks(self, scene_id: str, t: float) -> tuple[TaskView, ...]:
        """SceneTaskQueues.tasks(scene_id, t) を返す。ファイルも記録も書かない。"""
        with self._lock:
            return self._queues.tasks(scene_id, t)

    def _write_scene(self, scene_id: str, t: float) -> None:
        """場面 scene_id の時刻 t のキューの状態を、その場面の tasks.json と tasks.md に書く (ロックの中で呼ぶ)。"""
        write_scene_task_files(
            self.paths.notes_dir, scene_id, self._queues.tasks(scene_id, t), t
        )

    def _append(self, row: Mapping[str, object]) -> None:
        """tasks.jsonl に row を 1 行追記する (ロックの中で呼ぶ)。"""
        append_json_line(self.paths.tasks_log, row)


def restore_task_board(paths: RunPaths) -> TaskBoard:
    """保存済みの実行ディレクトリー paths の tasks.json からキューを作り直し、TaskBoard にして返す。

    task_files.read_saved_tasks(paths.notes_dir) で読んだタスクを、それぞれの続いた秒数 (active_s) を引き継いで
    SceneTaskQueues に入れる。次に振る ID の連番は
    max(読んだタスクの ID の連番, task_events.max_task_number_in_log(paths.tasks_log), 0) + 1。
    現在の場面は場面なし。何も無い実行ディレクトリーなら空のキューで、次の ID は task-001。ファイルは書かない。
    """
    saved = read_saved_tasks(paths.notes_dir)
    saved_numbers = (task_id_number(task.id) for task in saved)
    largest_saved = max(
        (number for number in saved_numbers if number is not None), default=0
    )
    largest = max(largest_saved, max_task_number_in_log(paths.tasks_log))
    return TaskBoard(paths, SceneTaskQueues(saved, next_number=largest + 1))
