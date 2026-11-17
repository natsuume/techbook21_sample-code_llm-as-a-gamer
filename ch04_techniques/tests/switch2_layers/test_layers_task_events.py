import dataclasses
import json
from pathlib import Path
from typing import get_args

import pytest

from switch2_layers.notes.map_document import TilePosition
from switch2_layers.tasks.task_events import (
    TaskEventName,
    dropped_event_row,
    max_task_number_in_log,
    task_event_row,
)
from switch2_layers.tasks.task_queue import DroppedText, TaskDestination, TaskView

SCENE = "scene-001"
TASK = TaskView(
    id="task-012",
    text="宿屋に入る",
    scene_id=SCENE,
    added_t=1.0,
    active_s=3.5,
    cancel_requested_t=None,
    destination=TaskDestination(place="町", tile=TilePosition(x=3, y=4)),
)
ROW_KEYS = ["t", "event", "scene_id", "id", "text", "reason", "active_s", "destination"]


def log_row(event: str, task_id: object) -> dict[str, object]:
    """id だけを変えた tasks.jsonl の 1 行を返す。"""
    return {
        "t": 1.0,
        "event": event,
        "scene_id": SCENE,
        "id": task_id,
        "text": "宿屋に入る",
        "reason": "empty" if event == "dropped" else None,
        "active_s": None,
        "destination": None,
    }


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


# --- 出来事の名前 ---


def test_event_names():
    assert get_args(TaskEventName) == (
        "added",
        "dropped",
        "done",
        "cancel_requested",
        "canceled",
    )


# --- 行の中身 ---


def test_added_row_has_the_destination_and_no_active_seconds():
    row = task_event_row(1.0, "added", TASK)
    assert row == {
        "t": 1.0,
        "event": "added",
        "scene_id": SCENE,
        "id": "task-012",
        "text": "宿屋に入る",
        "reason": None,
        "active_s": None,
        "destination": {"place": "町", "tile": {"x": 3, "y": 4}},
    }
    assert list(row) == ROW_KEYS


def test_added_row_without_destination_has_null():
    row = task_event_row(1.0, "added", dataclasses.replace(TASK, destination=None))
    assert row["destination"] is None


@pytest.mark.parametrize("event", ["done", "cancel_requested", "canceled"])
def test_task_rows_after_adding_have_active_seconds(event):
    # 行き先は added の行だけに書き、ほかの出来事では null
    row = task_event_row(5.0, event, TASK)
    assert row == {
        "t": 5.0,
        "event": event,
        "scene_id": SCENE,
        "id": "task-012",
        "text": "宿屋に入る",
        "reason": None,
        "active_s": 3.5,
        "destination": None,
    }
    assert list(row) == ROW_KEYS


@pytest.mark.parametrize("reason", ["empty", "duplicate", "limit"])
def test_dropped_row_has_the_reason_and_no_id(reason):
    row = dropped_event_row(2.0, SCENE, DroppedText(text="宿屋に入る", reason=reason))
    assert row == {
        "t": 2.0,
        "event": "dropped",
        "scene_id": SCENE,
        "id": None,
        "text": "宿屋に入る",
        "reason": reason,
        "active_s": None,
        "destination": None,
    }
    assert list(row) == ROW_KEYS


# --- 記録の中で最も大きい連番 ---


def test_max_task_number_without_log_is_zero(tmp_path):
    assert max_task_number_in_log(tmp_path / "tasks.jsonl") == 0


def test_max_task_number_ignores_other_ids(tmp_path):
    path = tmp_path / "tasks.jsonl"
    write_rows(
        path,
        [
            log_row("added", "task-002"),
            log_row("dropped", None),
            log_row("done", "task-012"),
            # 形の違う ID (連番が 2 桁、数字でない、str でない) は無視する
            log_row("added", "task-99"),
            log_row("added", "task-abc"),
            log_row("added", 500),
            log_row("canceled", "task-003"),
        ],
    )
    assert max_task_number_in_log(path) == 12


def test_max_task_number_with_only_dropped_rows_is_zero(tmp_path):
    path = tmp_path / "tasks.jsonl"
    write_rows(path, [log_row("dropped", None), log_row("dropped", None)])
    assert max_task_number_in_log(path) == 0
