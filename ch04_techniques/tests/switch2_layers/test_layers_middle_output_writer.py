import json
from pathlib import Path

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
]


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "middle").mkdir()
    (root / "notes" / "scene_labels.json").write_text(
        '{"scenes": []}', encoding="utf-8"
    )
    return root


def middle_input(t: float) -> MiddleInput:
    return MiddleInput(
        t=t,
        frames=(),
        surface_rows=(),
        operation_rows=(),
        scene_change_reported=False,
        scene_labels=(),
        previous_state=None,
        strategy="",
        current_tasks="",
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
    result = write_middle_output(RunPaths(root), middle_input(12.0), output("町"), 12.8)
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
    }


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
    assert read_json(root / "notes" / "scene_labels.json") == {"scenes": []}
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
