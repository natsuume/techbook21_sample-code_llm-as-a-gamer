import json
import threading
from pathlib import Path

import pytest

from switch2_layers.file_io import read_json_lines
from switch2_layers.notes.note_paths import scene_note, tasks_json_note
from switch2_layers.run_directory import RunPaths
from switch2_layers.tasks.task_board import TaskBoard, restore_task_board
from switch2_layers.tasks.task_queue import (
    AddResult,
    DroppedText,
    SceneTaskQueues,
    TaskView,
)

SCENE = "scene-001"
OTHER = "scene-002"


def tasks_json_path(paths: RunPaths, scene_id: str) -> Path:
    return paths.notes_dir / tasks_json_note(scene_id)


def tasks_md_path(paths: RunPaths, scene_id: str) -> Path:
    return paths.notes_dir / scene_note(scene_id, "tasks")


def read_tasks_json(paths: RunPaths, scene_id: str) -> object:
    return json.loads(tasks_json_path(paths, scene_id).read_text(encoding="utf-8"))


def read_tasks_md(paths: RunPaths, scene_id: str) -> str:
    return tasks_md_path(paths, scene_id).read_text(encoding="utf-8")


def json_item(
    task_id: str,
    text: str,
    added_t: float,
    active_s: float,
    cancel_requested_t: float | None = None,
) -> dict[str, object]:
    """tasks.json の tasks の 1 項目を返す。"""
    return {
        "id": task_id,
        "text": text,
        "added_t": added_t,
        "active_s": active_s,
        "cancel_requested_t": cancel_requested_t,
    }


def event_row(
    t: float,
    event: str,
    scene_id: str,
    task_id: str | None,
    text: str,
    reason: str | None = None,
    active_s: float | None = None,
) -> dict[str, object]:
    """tasks.jsonl の 1 行を返す。"""
    return {
        "t": t,
        "event": event,
        "scene_id": scene_id,
        "id": task_id,
        "text": text,
        "reason": reason,
        "active_s": active_s,
    }


def snapshot(paths: RunPaths) -> dict[str, bytes]:
    """実行ディレクトリーの下のファイルの、相対パスと中身の対応を返す。"""
    return {
        paths.relative(path): path.read_bytes()
        for path in sorted(paths.root.rglob("*"))
        if path.is_file()
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_log(paths: RunPaths, rows: list[dict[str, object]]) -> None:
    paths.tasks_log.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


# --- 追加 ---


def test_add_writes_scene_files_and_added_rows(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    result = board.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    assert result == AddResult(
        outcomes=(
            TaskView(
                id="task-001",
                text="宿屋に入る",
                scene_id=SCENE,
                added_t=1.0,
                active_s=0.0,
                cancel_requested_t=None,
            ),
            TaskView(
                id="task-002",
                text="主人に話しかける",
                scene_id=SCENE,
                added_t=1.0,
                active_s=0.0,
                cancel_requested_t=None,
            ),
        )
    )
    assert read_tasks_json(paths, SCENE) == {
        "t": 1.0,
        "tasks": [
            json_item("task-001", "宿屋に入る", 1.0, 0.0),
            json_item("task-002", "主人に話しかける", 1.0, 0.0),
        ],
    }
    assert read_tasks_md(paths, SCENE) == "- 宿屋に入る\n- 主人に話しかける\n"
    assert read_json_lines(paths.tasks_log) == [
        event_row(1.0, "added", SCENE, "task-001", "宿屋に入る"),
        event_row(1.0, "added", SCENE, "task-002", "主人に話しかける"),
    ]


def test_later_add_writes_the_active_seconds_at_its_time(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る"], 1.0)
    board.add(SCENE, ["主人に話しかける"], 4.0)
    assert read_tasks_json(paths, SCENE) == {
        "t": 4.0,
        "tasks": [
            json_item("task-001", "宿屋に入る", 1.0, 3.0),
            json_item("task-002", "主人に話しかける", 4.0, 0.0),
        ],
    }
    assert read_tasks_md(paths, SCENE) == "- 宿屋に入る\n- 主人に話しかける\n"
    assert read_json_lines(paths.tasks_log) == [
        event_row(1.0, "added", SCENE, "task-001", "宿屋に入る"),
        event_row(4.0, "added", SCENE, "task-002", "主人に話しかける"),
    ]


def test_add_logs_dropped_texts_in_input_order(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    result = board.add(
        SCENE, ["宿屋に入る", " ", "宿屋に 入る", "主人に話しかける"], 1.0
    )
    assert result.dropped == (
        DroppedText(text="", reason="empty"),
        DroppedText(text="宿屋に 入る", reason="duplicate"),
    )
    assert read_json_lines(paths.tasks_log) == [
        event_row(1.0, "added", SCENE, "task-001", "宿屋に入る"),
        event_row(1.0, "dropped", SCENE, None, "", reason="empty"),
        event_row(1.0, "dropped", SCENE, None, "宿屋に 入る", reason="duplicate"),
        event_row(1.0, "added", SCENE, "task-002", "主人に話しかける"),
    ]


def test_add_without_added_texts_writes_only_dropped_rows(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    result = board.add(SCENE, ["", "  "], 1.0)
    assert result.added == ()
    assert not tasks_json_path(paths, SCENE).exists()
    assert not tasks_md_path(paths, SCENE).exists()
    assert read_json_lines(paths.tasks_log) == [
        event_row(1.0, "dropped", SCENE, None, "", reason="empty"),
        event_row(1.0, "dropped", SCENE, None, "", reason="empty"),
    ]
    # タスクのある場面でも、1 つも足さなければ場面のファイルは書かない
    board.add(SCENE, ["宿屋に入る"], 2.0)
    json_before = tasks_json_path(paths, SCENE).read_bytes()
    md_before = tasks_md_path(paths, SCENE).read_bytes()
    board.add(SCENE, ["宿屋に入る"], 5.0)
    assert tasks_json_path(paths, SCENE).read_bytes() == json_before
    assert tasks_md_path(paths, SCENE).read_bytes() == md_before
    assert read_json_lines(paths.tasks_log)[-1] == event_row(
        5.0, "dropped", SCENE, None, "宿屋に入る", reason="duplicate"
    )


# --- 完了 ---


def test_complete_writes_scene_files_and_done_row(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    assert board.complete(SCENE, "task-001", 4.0) is True
    assert read_tasks_json(paths, SCENE) == {
        "t": 4.0,
        "tasks": [json_item("task-002", "主人に話しかける", 1.0, 3.0)],
    }
    assert read_tasks_md(paths, SCENE) == "- 主人に話しかける\n"
    assert read_json_lines(paths.tasks_log)[-1] == event_row(
        4.0, "done", SCENE, "task-001", "宿屋に入る", active_s=3.0
    )
    # 最後のタスクを完了にすると、空の配列と空のファイル
    assert board.complete(SCENE, "task-002", 6.0) is True
    assert read_tasks_json(paths, SCENE) == {"t": 6.0, "tasks": []}
    assert read_tasks_md(paths, SCENE) == ""
    assert read_json_lines(paths.tasks_log)[-1] == event_row(
        6.0, "done", SCENE, "task-002", "主人に話しかける", active_s=5.0
    )


# --- 取り消しの要求 ---


def test_request_cancel_writes_scene_files_and_row(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    assert board.request_cancel(SCENE, "task-001", 3.0) is True
    assert read_tasks_json(paths, SCENE) == {
        "t": 3.0,
        "tasks": [
            json_item("task-001", "宿屋に入る", 1.0, 2.0, cancel_requested_t=3.0),
            json_item("task-002", "主人に話しかける", 1.0, 2.0),
        ],
    }
    assert read_tasks_md(paths, SCENE) == "- 宿屋に入る\n- 主人に話しかける\n"
    assert read_json_lines(paths.tasks_log)[-1] == event_row(
        3.0, "cancel_requested", SCENE, "task-001", "宿屋に入る", active_s=2.0
    )


# --- 取り消し ---


def test_cancel_across_scenes_writes_both_scenes(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    board.add(OTHER, ["町を出る"], 2.0)
    assert board.cancel(["task-003", "task-001"], 5.0) == (
        TaskView(
            id="task-003",
            text="町を出る",
            scene_id=OTHER,
            added_t=2.0,
            active_s=0.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-001",
            text="宿屋に入る",
            scene_id=SCENE,
            added_t=1.0,
            active_s=4.0,
            cancel_requested_t=None,
        ),
    )
    # 未完了のタスクが無くなった場面も書く
    assert read_tasks_json(paths, OTHER) == {"t": 5.0, "tasks": []}
    assert read_tasks_md(paths, OTHER) == ""
    assert read_tasks_json(paths, SCENE) == {
        "t": 5.0,
        "tasks": [json_item("task-002", "主人に話しかける", 1.0, 4.0)],
    }
    assert read_tasks_md(paths, SCENE) == "- 主人に話しかける\n"
    assert read_json_lines(paths.tasks_log)[-2:] == [
        event_row(5.0, "canceled", OTHER, "task-003", "町を出る", active_s=0.0),
        event_row(5.0, "canceled", SCENE, "task-001", "宿屋に入る", active_s=4.0),
    ]


# --- キューが変わらない操作 ---


def test_operations_without_changes_write_nothing(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る", "主人に話しかける"], 1.0)
    board.request_cancel(SCENE, "task-001", 2.0)
    before = snapshot(paths)
    # 先頭でない、ほかの場面
    assert board.complete(SCENE, "task-002", 5.0) is False
    assert board.complete(OTHER, "task-001", 5.0) is False
    # 2 回目の要求、先頭でない
    assert board.request_cancel(SCENE, "task-001", 6.0) is False
    assert board.request_cancel(SCENE, "task-002", 6.0) is False
    # 当たらない取り消し
    assert board.cancel(["task-099"], 7.0) == ()
    # 現在の場面の設定と、状態の読み出し
    board.set_current_scene(OTHER, 8.0)
    board.set_current_scene(None, 9.0)
    assert [view.active_s for view in board.tasks(SCENE, 10.0)] == [7.0, 7.0]
    assert board.oldest_active_s(SCENE, 10.0) == 7.0
    assert snapshot(paths) == before


def test_new_board_and_set_current_scene_write_nothing(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    board.set_current_scene(SCENE, 1.0)
    assert board.tasks(SCENE, 2.0) == ()
    assert board.oldest_active_s(SCENE, 2.0) is None
    assert list(tmp_path.iterdir()) == []


# --- 状態の読み出し ---


def test_tasks_and_oldest_active_s(tmp_path):
    board = TaskBoard(RunPaths(tmp_path))
    board.set_current_scene(SCENE, 0.0)
    board.add(SCENE, ["宿屋に入る"], 1.0)
    board.add(SCENE, ["主人に話しかける"], 2.0)
    assert board.tasks(SCENE, 5.0) == (
        TaskView(
            id="task-001",
            text="宿屋に入る",
            scene_id=SCENE,
            added_t=1.0,
            active_s=4.0,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-002",
            text="主人に話しかける",
            scene_id=SCENE,
            added_t=2.0,
            active_s=3.0,
            cancel_requested_t=None,
        ),
    )
    assert board.oldest_active_s(SCENE, 5.0) == 4.0
    assert board.oldest_active_s(OTHER, 5.0) is None


def test_board_uses_the_given_queues(tmp_path):
    saved = TaskView(
        id="task-004",
        text="宿屋に入る",
        scene_id=SCENE,
        added_t=1.0,
        active_s=2.0,
        cancel_requested_t=None,
    )
    board = TaskBoard(RunPaths(tmp_path), SceneTaskQueues([saved], next_number=5))
    assert board.tasks(SCENE, 3.0) == (saved,)
    assert board.add(SCENE, ["主人に話しかける"], 3.0).added[0].id == "task-005"


# --- 書き込みの失敗 ---


def test_scene_file_write_error_is_raised(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    # tasks.json の場所をディレクトリーにして、置き換えを失敗させる
    tasks_json_path(paths, SCENE).mkdir(parents=True)
    with pytest.raises(OSError):
        board.add(SCENE, ["宿屋に入る"], 1.0)


def test_log_write_error_is_raised(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    # tasks.jsonl の場所をディレクトリーにして、追記を失敗させる
    paths.tasks_log.mkdir()
    with pytest.raises(OSError):
        board.add(SCENE, ["宿屋に入る"], 1.0)


# --- 複数のスレッド ---


def test_adds_from_many_threads_get_unique_ids(tmp_path):
    paths = RunPaths(tmp_path)
    board = TaskBoard(paths)
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    results: list[AddResult | None] = [None] * thread_count

    def add_from_thread(index: int) -> None:
        barrier.wait(timeout=10.0)
        results[index] = board.add(
            f"scene-{index + 1:03d}", [f"タスク {index}"], float(index)
        )

    threads = [
        threading.Thread(target=add_from_thread, args=(index,))
        for index in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    added_ids = [
        view.id for result in results if result is not None for view in result.added
    ]
    assert sorted(added_ids) == [f"task-{n:03d}" for n in range(1, thread_count + 1)]
    rows = read_json_lines(paths.tasks_log)
    assert len(rows) == thread_count
    assert sorted(str(row["id"]) for row in rows) == sorted(added_ids)


# --- 作り直し ---


def test_restore_task_board_from_saved_run_directory(tmp_path):
    paths = RunPaths(tmp_path)
    write_json(
        tasks_json_path(paths, SCENE),
        {
            "t": 40.0,
            "tasks": [
                json_item("task-003", "宿屋に入る", 10.0, 12.5),
                json_item(
                    "task-005", "主人に話しかける", 11.0, 4.0, cancel_requested_t=30.0
                ),
            ],
        },
    )
    write_json(
        tasks_json_path(paths, OTHER),
        {"t": 40.0, "tasks": [json_item("task-004", "町を出る", 12.0, 2.0)]},
    )
    # 記録の中で最も大きい連番は、完了済みの task-007
    write_log(
        paths,
        [
            event_row(10.0, "added", SCENE, "task-003", "宿屋に入る"),
            event_row(11.0, "added", SCENE, "task-005", "主人に話しかける"),
            event_row(12.0, "added", OTHER, "task-004", "町を出る"),
            event_row(13.0, "added", OTHER, "task-007", "地図を見る"),
            event_row(13.0, "dropped", OTHER, None, "", reason="empty"),
            event_row(20.0, "done", OTHER, "task-007", "地図を見る", active_s=7.0),
        ],
    )
    before = snapshot(paths)
    board = restore_task_board(paths)
    # ファイルは書かない
    assert snapshot(paths) == before
    assert board.tasks(SCENE, 50.0) == (
        TaskView(
            id="task-003",
            text="宿屋に入る",
            scene_id=SCENE,
            added_t=10.0,
            active_s=12.5,
            cancel_requested_t=None,
        ),
        TaskView(
            id="task-005",
            text="主人に話しかける",
            scene_id=SCENE,
            added_t=11.0,
            active_s=4.0,
            cancel_requested_t=30.0,
        ),
    )
    assert board.tasks(OTHER, 50.0) == (
        TaskView(
            id="task-004",
            text="町を出る",
            scene_id=OTHER,
            added_t=12.0,
            active_s=2.0,
            cancel_requested_t=None,
        ),
    )
    # 現在の場面は場面なしで始め、続いた秒数は進まない
    assert board.oldest_active_s(SCENE, 80.0) == 12.5
    board.set_current_scene(SCENE, 80.0)
    assert [view.active_s for view in board.tasks(SCENE, 83.0)] == [15.5, 7.0]
    assert board.tasks(OTHER, 83.0)[0].active_s == 2.0
    # 次の ID は、記録の中で最も大きい連番の次
    assert board.add(OTHER, ["宿屋に戻る"], 84.0).added[0].id == "task-008"


def test_restore_uses_the_largest_number_in_saved_tasks(tmp_path):
    paths = RunPaths(tmp_path)
    write_json(
        tasks_json_path(paths, SCENE),
        {"t": 40.0, "tasks": [json_item("task-009", "宿屋に入る", 10.0, 12.5)]},
    )
    # tasks.jsonl が無い
    board = restore_task_board(paths)
    assert board.add(SCENE, ["主人に話しかける"], 50.0).added[0].id == "task-010"


def test_restore_empty_run_directory(tmp_path):
    paths = RunPaths(tmp_path)
    board = restore_task_board(paths)
    assert board.tasks(SCENE, 1.0) == ()
    assert list(tmp_path.iterdir()) == []
    assert board.add(SCENE, ["宿屋に入る"], 1.0).added[0].id == "task-001"
