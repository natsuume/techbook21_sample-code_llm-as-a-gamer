import json
from pathlib import Path

import pytest

from switch2_layers.tasks.task_files import (
    read_saved_tasks,
    tasks_json_value,
    tasks_markdown,
    write_scene_task_files,
)
from switch2_layers.tasks.task_queue import TaskView

SCENE = "scene-001"
OTHER = "scene-002"
TASKS = (
    TaskView(
        id="task-001",
        text="宿屋に入る",
        scene_id=SCENE,
        added_t=1.0,
        active_s=2.5,
        cancel_requested_t=None,
    ),
    TaskView(
        id="task-002",
        text="主人に話しかける",
        scene_id=SCENE,
        added_t=1.5,
        active_s=2.0,
        cancel_requested_t=3.0,
    ),
)
# TASKS の tasks.json の tasks の項目
TASK_ITEMS = [
    {
        "id": "task-001",
        "text": "宿屋に入る",
        "added_t": 1.0,
        "active_s": 2.5,
        "cancel_requested_t": None,
    },
    {
        "id": "task-002",
        "text": "主人に話しかける",
        "added_t": 1.5,
        "active_s": 2.0,
        "cancel_requested_t": 3.0,
    },
]
TASKS_MD = "- 宿屋に入る\n- 主人に話しかける\n"


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_saved_tasks(
    notes_dir: Path, directory_name: str, items: list[dict[str, object]]
) -> None:
    """notes_dir/scenes/<directory_name>/tasks.json に、items を tasks に持つ保存済みの状態を書く。"""
    directory = notes_dir / "scenes" / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tasks.json").write_text(
        json.dumps({"t": 9.0, "tasks": items}, ensure_ascii=False), encoding="utf-8"
    )


# --- tasks.json と tasks.md の中身 ---


def test_tasks_json_value_has_the_keys_in_order():
    value = tasks_json_value(TASKS, 4.0)
    assert value == {"t": 4.0, "tasks": TASK_ITEMS}
    assert list(value) == ["t", "tasks"]
    items = value["tasks"]
    assert isinstance(items, list)
    for item in items:
        assert list(item) == ["id", "text", "added_t", "active_s", "cancel_requested_t"]


def test_tasks_json_value_without_tasks():
    assert tasks_json_value((), 4.0) == {"t": 4.0, "tasks": []}


def test_tasks_markdown():
    assert tasks_markdown(TASKS) == TASKS_MD
    assert tasks_markdown(()) == ""


# --- 場面のファイルの書き込み ---


def test_write_scene_task_files_creates_the_scene_directory(tmp_path):
    notes = tmp_path / "notes"
    write_scene_task_files(notes, SCENE, TASKS, 4.0)
    scene_dir = notes / "scenes" / SCENE
    assert read_json(scene_dir / "tasks.json") == {"t": 4.0, "tasks": TASK_ITEMS}
    assert (scene_dir / "tasks.md").read_text(encoding="utf-8") == TASKS_MD


def test_write_scene_task_files_replaces_existing_files(tmp_path):
    notes = tmp_path / "notes"
    scene_dir = notes / "scenes" / SCENE
    write_scene_task_files(notes, SCENE, TASKS, 4.0)
    write_scene_task_files(notes, SCENE, TASKS[1:], 5.0)
    assert read_json(scene_dir / "tasks.json") == {"t": 5.0, "tasks": TASK_ITEMS[1:]}
    assert (scene_dir / "tasks.md").read_text(
        encoding="utf-8"
    ) == "- 主人に話しかける\n"
    # 未完了のタスクが無ければ、空の配列と空のファイル
    write_scene_task_files(notes, SCENE, (), 6.0)
    assert read_json(scene_dir / "tasks.json") == {"t": 6.0, "tasks": []}
    assert (scene_dir / "tasks.md").read_text(encoding="utf-8") == ""
    # 置き換えの書き込みの一時ファイルを残さない
    assert sorted(child.name for child in scene_dir.iterdir()) == [
        "tasks.json",
        "tasks.md",
    ]


def test_write_scene_task_files_raises_write_errors(tmp_path):
    notes = tmp_path / "notes"
    # tasks.json の場所をディレクトリーにして、置き換えを失敗させる
    (notes / "scenes" / SCENE / "tasks.json").mkdir(parents=True)
    with pytest.raises(OSError):
        write_scene_task_files(notes, SCENE, TASKS, 4.0)


# --- 保存済みの tasks.json の読み込み ---


def test_read_saved_tasks_reads_scenes_in_name_order(tmp_path):
    notes = tmp_path / "notes"
    write_saved_tasks(
        notes,
        OTHER,
        [
            {
                "id": "task-003",
                "text": "町を出る",
                "added_t": 6.0,
                "active_s": 1.5,
                "cancel_requested_t": None,
            }
        ],
    )
    write_saved_tasks(notes, SCENE, TASK_ITEMS)
    assert read_saved_tasks(notes) == [
        *TASKS,
        TaskView(
            id="task-003",
            text="町を出る",
            scene_id=OTHER,
            added_t=6.0,
            active_s=1.5,
            cancel_requested_t=None,
        ),
    ]


def test_read_saved_tasks_skips_other_directories(tmp_path):
    notes = tmp_path / "notes"
    write_saved_tasks(notes, SCENE, TASK_ITEMS)
    # 名前が場面 ID の形でないディレクトリーは読まない
    other_item = {
        "id": "task-009",
        "text": "町を出る",
        "added_t": 6.0,
        "active_s": 1.5,
        "cancel_requested_t": None,
    }
    write_saved_tasks(notes, "scene-01", [other_item])
    write_saved_tasks(notes, "other", [other_item])
    # tasks.json の無い場面と、未完了のタスクの無い場面
    (notes / "scenes" / "scene-003").mkdir()
    (notes / "scenes" / "scene-003" / "tasks.md").write_text(
        "- 町を出る\n", encoding="utf-8"
    )
    write_saved_tasks(notes, "scene-004", [])
    assert read_saved_tasks(notes) == list(TASKS)


def test_read_saved_tasks_without_scenes_directory(tmp_path):
    notes = tmp_path / "notes"
    assert read_saved_tasks(notes) == []
    notes.mkdir()
    assert read_saved_tasks(notes) == []


def test_written_files_can_be_read_back(tmp_path):
    notes = tmp_path / "notes"
    write_scene_task_files(notes, SCENE, TASKS, 4.0)
    assert read_saved_tasks(notes) == list(TASKS)
