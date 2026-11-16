import json
import shutil
from pathlib import Path

import pytest

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_output_writer import (
    NEW_SCENE_DEEP_KINDS,
    write_middle_output,
)
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput, MiddleState
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.run_directory import RunPaths

STATE_KEYS = [
    "generated_t",
    "based_on_t",
    "scene_id",
    "scene_name",
    "is_stuck",
    "stuck_description",
    "surface_wait_s",
    "timeline",
    "surface_rows_offset",
    "operations_rows_offset",
    "first_task",
    "first_task_since_t",
]
EMPTY_LABELS = {"scenes": []}


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "middle").mkdir()
    (root / "notes" / "scene_labels.json").write_text(
        '{"scenes": []}', encoding="utf-8"
    )
    return root


def middle_input(
    t: float,
    surface_rows_offset: int = 0,
    operations_rows_offset: int = 0,
    previous_state: MiddleState | None = None,
) -> MiddleInput:
    return MiddleInput(
        t=t,
        frames=(),
        surface_rows=(),
        operation_rows=(),
        scene_change_reported=False,
        scene_labels=(),
        previous_state=previous_state,
        strategy="",
        current_tasks="",
        surface_rows_offset=surface_rows_offset,
        operations_rows_offset=operations_rows_offset,
    )


def previous_state(first_task: str, first_task_since_t: float | None) -> MiddleState:
    """前回の middle/state.json の値 (最初のタスクの文と、その文になった時刻だけを変える)。"""
    return MiddleState(
        generated_t=7.5,
        based_on_t=7.0,
        scene_id="scene-001",
        scene_name="町",
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        surface_rows_offset=0,
        operations_rows_offset=0,
        first_task=first_task,
        first_task_since_t=first_task_since_t,
    )


def output(
    scene_name: str | None, tasks: str = "- 宿屋に入る\n", wait: float = 0.5
) -> MiddleOutput:
    return MiddleOutput(
        scene_name=scene_name,
        is_stuck=True,
        stuck_description="扉の前で止まっている",
        surface_wait_s=wait,
        timeline="## 直近\n町",
        tasks=tasks,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_new_scene_deep_kinds_are_controls_and_tips():
    assert NEW_SCENE_DEEP_KINDS == (DocumentKind.CONTROLS, DocumentKind.TIPS)


def test_first_scene_name_adds_label_tasks_and_state(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root), middle_input(12.0, 210, 84), output("町"), 12.8
    )
    assert result.new_scene == SceneLabel("scene-001", "町", 12.0)
    assert result.state == MiddleState(
        generated_t=12.8,
        based_on_t=12.0,
        scene_id="scene-001",
        scene_name="町",
        is_stuck=True,
        stuck_description="扉の前で止まっている",
        surface_wait_s=0.5,
        timeline="## 直近\n町",
        surface_rows_offset=210,
        operations_rows_offset=84,
        first_task="宿屋に入る",
        first_task_since_t=12.8,
    )
    assert read_json(root / "notes" / "scene_labels.json") == {
        "scenes": [{"id": "scene-001", "name": "町", "first_seen_t": 12.0}]
    }
    assert (root / "notes" / "scenes" / "scene-001" / "tasks.md").read_text(
        encoding="utf-8"
    ) == "- 宿屋に入る\n"
    state = read_json(root / "middle" / "state.json")
    assert list(state) == STATE_KEYS
    assert state == {
        "generated_t": 12.8,
        "based_on_t": 12.0,
        "scene_id": "scene-001",
        "scene_name": "町",
        "is_stuck": True,
        "stuck_description": "扉の前で止まっている",
        "surface_wait_s": 0.5,
        "timeline": "## 直近\n町",
        "surface_rows_offset": 210,
        "operations_rows_offset": 84,
        "first_task": "宿屋に入る",
        "first_task_since_t": 12.8,
    }


def test_row_offsets_of_input_are_written_to_state(tmp_path):
    root = make_run(tmp_path)
    write_middle_output(RunPaths(root), middle_input(3.0), output(None), 3.5)
    state = read_json(root / "middle" / "state.json")
    assert state["surface_rows_offset"] == 0
    assert state["operations_rows_offset"] == 0


def test_known_scene_name_is_not_added_again(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_middle_output(paths, middle_input(1.0), output("町", tasks="- 古い\n"), 1.5)
    labels_before = (root / "notes" / "scene_labels.json").read_bytes()
    result = write_middle_output(
        paths, middle_input(6.0), output(" 町 ", tasks="- 新しい\n"), 6.5
    )
    assert result.new_scene is None
    assert result.state.scene_id == "scene-001"
    assert result.state.scene_name == "町"
    assert (root / "notes" / "scene_labels.json").read_bytes() == labels_before
    # 短期タスクリストは置き換える
    assert (root / "notes" / "scenes" / "scene-001" / "tasks.md").read_text(
        encoding="utf-8"
    ) == "- 新しい\n"


def test_another_new_scene_gets_next_id(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_middle_output(paths, middle_input(1.0), output("町"), 1.5)
    result = write_middle_output(paths, middle_input(6.0), output("草原"), 6.5)
    assert result.new_scene == SceneLabel("scene-002", "草原", 6.0)
    assert (root / "notes" / "scenes" / "scene-002" / "tasks.md").exists()


def test_no_scene_name_writes_only_state(tmp_path):
    root = make_run(tmp_path)
    for name in [None, "", "   "]:
        result = write_middle_output(
            RunPaths(root), middle_input(2.0), output(name), 2.5
        )
        assert result.new_scene is None
        assert result.state.scene_id is None
        assert result.state.scene_name is None
        state = read_json(root / "middle" / "state.json")
        assert state["scene_id"] is None
        assert state["scene_name"] is None
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS
    assert not (root / "notes" / "scenes").exists()


def test_surface_wait_is_written_as_is(tmp_path):
    # 範囲に収めるのは読む側 (surface)
    root = make_run(tmp_path)
    write_middle_output(RunPaths(root), middle_input(2.0), output("町", wait=5.0), 2.5)
    assert read_json(root / "middle" / "state.json")["surface_wait_s"] == 5.0


def test_state_is_replaced_without_temp_files(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_middle_output(paths, middle_input(1.0), output("町"), 1.5)
    write_middle_output(paths, middle_input(6.0), output("町"), 6.5)
    assert read_json(root / "middle" / "state.json")["generated_t"] == 6.5
    assert sorted(p.name for p in (root / "middle").iterdir()) == ["state.json"]


# --- 最初のタスクの文と、その文になった時刻 ---
# 前回の時刻 (3.0)・入力の時刻 (12.0)・書いた時刻 (12.8) をすべて違う値にして、どの時刻を使ったかを見分ける


def test_without_previous_state_first_task_starts_at_generated_t(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0),
        output("町", tasks="# タスク\n\n- 宿屋に入る\n- 主人に話しかける\n"),
        12.8,
    )
    assert result.state.first_task == "宿屋に入る"
    assert result.state.first_task_since_t == 12.8


def test_same_first_task_keeps_previous_time(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output("町", tasks="- 宿屋に入る\n- 主人に話しかける\n"),
        12.8,
    )
    assert result.state.first_task == "宿屋に入る"
    assert result.state.first_task_since_t == 3.0
    state = read_json(root / "middle" / "state.json")
    assert state["first_task"] == "宿屋に入る"
    assert state["first_task_since_t"] == 3.0


def test_first_task_differing_only_in_whitespace_keeps_previous_time(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output("町", tasks="- 宿屋 に　入る\n"),
        12.8,
    )
    # 文は今回の出力のもの、時刻は前回のもの
    assert result.state.first_task == "宿屋 に　入る"
    assert result.state.first_task_since_t == 3.0


def test_changed_first_task_starts_at_generated_t(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output("町", tasks="- 主人に話しかける\n- 宿屋に入る\n"),
        12.8,
    )
    assert result.state.first_task == "主人に話しかける"
    assert result.state.first_task_since_t == 12.8
    state = read_json(root / "middle" / "state.json")
    assert state["first_task"] == "主人に話しかける"
    assert state["first_task_since_t"] == 12.8


def test_previous_state_without_time_starts_at_generated_t(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", None)),
        output("町", tasks="- 宿屋に入る\n"),
        12.8,
    )
    assert result.state.first_task == "宿屋に入る"
    assert result.state.first_task_since_t == 12.8


def test_first_task_is_recorded_without_scene(tmp_path):
    # 場面が決まらない (tasks.md を書かない) 回も、最初のタスクとその時刻を計算して state.json に書く
    root = make_run(tmp_path)
    same = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output(None, tasks="- 宿屋に入る\n"),
        12.8,
    )
    assert same.state.scene_id is None
    assert same.state.first_task == "宿屋に入る"
    assert same.state.first_task_since_t == 3.0
    state = read_json(root / "middle" / "state.json")
    assert list(state) == STATE_KEYS
    assert state["first_task"] == "宿屋に入る"
    assert state["first_task_since_t"] == 3.0
    changed = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output(None, tasks="- 外に出る\n"),
        12.8,
    )
    assert changed.state.first_task == "外に出る"
    assert changed.state.first_task_since_t == 12.8
    assert not (root / "notes" / "scenes").exists()


def test_tasks_without_bullet_record_empty_first_task(tmp_path):
    root = make_run(tmp_path)
    result = write_middle_output(
        RunPaths(root),
        middle_input(12.0, previous_state=previous_state("宿屋に入る", 3.0)),
        output("町", tasks="タスクは無い\n"),
        12.8,
    )
    assert result.state.first_task == ""
    assert result.state.first_task_since_t == 12.8


# --- 書き込みに失敗したとき (一覧に足すのは最後) ---


def test_failed_tasks_write_does_not_add_scene_label(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # notes/scenes をファイルにして、tasks.md の書き込みを失敗させる
    (root / "notes" / "scenes").write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        write_middle_output(paths, middle_input(1.0), output("町"), 1.5)
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS
    assert not (root / "middle" / "state.json").exists()
    # 書けるようになった次の回では、同じ名前が新しい場面 (同じ ID) として扱われる
    (root / "notes" / "scenes").unlink()
    result = write_middle_output(paths, middle_input(6.0), output("町"), 6.5)
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)
    assert read_json(root / "notes" / "scene_labels.json") == {
        "scenes": [{"id": "scene-001", "name": "町", "first_seen_t": 6.0}]
    }


def test_failed_state_write_does_not_add_scene_label(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # middle/state.json をディレクトリーにして、state.json の置き換えを失敗させる
    (root / "middle" / "state.json").mkdir()
    with pytest.raises(OSError):
        write_middle_output(paths, middle_input(1.0), output("町"), 1.5)
    # tasks.md は書いた後だが、一覧には足していない
    assert (root / "notes" / "scenes" / "scene-001" / "tasks.md").exists()
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS
    shutil.rmtree(root / "middle" / "state.json")
    result = write_middle_output(paths, middle_input(6.0), output("町"), 6.5)
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)
    assert result.state.scene_id == "scene-001"


def test_failed_label_write_keeps_scene_new_for_next_call(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # scene_labels.json をディレクトリーにして、一覧の書き込み (最後) を失敗させる
    (root / "notes" / "scene_labels.json").unlink()
    (root / "notes" / "scene_labels.json").mkdir()
    with pytest.raises(OSError):
        write_middle_output(paths, middle_input(1.0), output("町"), 1.5)
    # tasks.md と state.json は書き終えている
    assert read_json(root / "middle" / "state.json")["scene_id"] == "scene-001"
    (root / "notes" / "scene_labels.json").rmdir()
    result = write_middle_output(paths, middle_input(6.0), output("町"), 6.5)
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)
