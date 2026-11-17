import dataclasses
import json
import shutil
import threading
from pathlib import Path

import pytest

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.middle.middle_output_writer import (
    NEW_SCENE_DEEP_KINDS,
    MiddleWriteResult,
    write_middle_output,
)
from switch2_layers.middle.middle_trigger import MiddleCallReason
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput, MiddleState
from switch2_layers.middle.task_destination_fix import DestinationFix
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapDocument,
    TileMark,
    TilePosition,
)
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.run_directory import RunPaths
from switch2_layers.tasks.task_board import TaskBoard
from switch2_layers.tasks.task_queue import NewTask, TaskDestination

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
    "tasks_rows_offset",
    "place_name",
    "current_tile",
    "press_home",
]
EMPTY_LABELS = {"scenes": []}
SCENE = "scene-001"
OTHER = "scene-002"


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "notes" / "common").mkdir(parents=True)
    (root / "middle").mkdir()
    (root / "notes" / "scene_labels.json").write_text(
        '{"scenes": []}', encoding="utf-8"
    )
    return root


def write_labels(root: Path, *names: str) -> None:
    """notes/scene_labels.json に、names の場面を scene-001 から順に書く。"""
    scenes = [
        {"id": f"scene-{number:03d}", "name": name, "first_seen_t": 1.0}
        for number, name in enumerate(names, start=1)
    ]
    (root / "notes" / "scene_labels.json").write_text(
        json.dumps({"scenes": scenes}, ensure_ascii=False), encoding="utf-8"
    )


def middle_input(
    t: float,
    surface_rows_offset: int = 0,
    operations_rows_offset: int = 0,
    previous_state: MiddleState | None = None,
    tasks_rows_offset: int = 0,
    reasons: tuple[MiddleCallReason, ...] = ("interval",),
    map_document: MapDocument | GridMapDocument | None = None,
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
        task_queue=(),
        task_events=(),
        surface_rows_offset=surface_rows_offset,
        operations_rows_offset=operations_rows_offset,
        tasks_rows_offset=tasks_rows_offset,
        reasons=reasons,
        map_document=map_document,
    )


def previous_state(scene_id: str | None = SCENE) -> MiddleState:
    """前回の middle/state.json の値 (場面 ID だけを変える)。"""
    return MiddleState(
        generated_t=7.5,
        based_on_t=7.0,
        scene_id=scene_id,
        scene_name=None if scene_id is None else "町",
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        surface_rows_offset=0,
        operations_rows_offset=0,
        tasks_rows_offset=0,
    )


def output(
    scene_name: str | None,
    wait: float = 0.5,
    new_tasks: tuple[NewTask, ...] = (),
    cancel_task_ids: tuple[str, ...] = (),
    is_stuck: bool = False,
) -> MiddleOutput:
    return MiddleOutput(
        scene_name=scene_name,
        is_stuck=is_stuck,
        stuck_description="扉の前で止まっている" if is_stuck else "",
        surface_wait_s=wait,
        timeline="## 直近\n町",
        new_tasks=new_tasks,
        cancel_task_ids=cancel_task_ids,
    )


class NotifyRecorder:
    """trigger_notify の偽物。知らされた理由を順に記録する。"""

    def __init__(self) -> None:
        self.reasons: list[MiddleCallReason] = []

    def __call__(self, reason: MiddleCallReason) -> None:
        self.reasons.append(reason)


def write(
    paths: RunPaths,
    the_input: MiddleInput,
    the_output: MiddleOutput,
    generated_t: float,
    board: TaskBoard | None = None,
    trigger_notify: NotifyRecorder | None = None,
    stop: threading.Event | None = None,
) -> MiddleWriteResult | None:
    """write_middle_output を呼ぶ (reasons は入力の reasons。board が None なら空のキューの部品)。"""
    return write_middle_output(
        paths,
        the_input,
        the_output,
        generated_t,
        TaskBoard(paths) if board is None else board,
        the_input.reasons,
        trigger_notify,
        stop=stop,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tasks(*texts: str) -> tuple[NewTask, ...]:
    """行き先の無い、文だけの追加の組を返す。"""
    return tuple(NewTask(text=text) for text in texts)


def board_with_tasks(paths: RunPaths, *texts: str, scene_id: str = SCENE) -> TaskBoard:
    """scene_id のキューに texts を t=1.0 で足したキューの部品を返す (現在の場面は場面なし)。"""
    board = TaskBoard(paths)
    board.add(scene_id, tasks(*texts), 1.0)
    return board


def task_ids(board: TaskBoard, scene_id: str = SCENE) -> list[str]:
    return [task.id for task in board.tasks(scene_id, 20.0)]


def test_new_scene_deep_kinds_are_controls_tips_and_game():
    # ゲームの性質は最後。場面に依らないため、どの場面の new_scene でも同じ文書を求める
    assert NEW_SCENE_DEEP_KINDS == (
        DocumentKind.CONTROLS,
        DocumentKind.TIPS,
        DocumentKind.GAME,
    )


def test_first_scene_name_adds_label_and_state(tmp_path):
    root = make_run(tmp_path)
    result = write(
        RunPaths(root),
        middle_input(12.0, 210, 84, tasks_rows_offset=33),
        output("町", is_stuck=True),
        12.8,
    )
    assert result is not None
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
        tasks_rows_offset=33,
    )
    assert read_json(root / "notes" / "scene_labels.json") == {
        "scenes": [{"id": "scene-001", "name": "町", "first_seen_t": 12.0}]
    }
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
        "tasks_rows_offset": 33,
        # 場所を出力しない回は null
        "place_name": None,
        "current_tile": None,
        "press_home": False,
    }
    # 足すタスクの無い回は、場面のファイルを書かない (tasks.md は書かない)
    assert not (root / "notes" / "scenes").exists()


def test_row_offsets_of_input_are_written_to_state(tmp_path):
    root = make_run(tmp_path)
    write(RunPaths(root), middle_input(3.0, tasks_rows_offset=12), output(None), 3.5)
    state = read_json(root / "middle" / "state.json")
    assert state["surface_rows_offset"] == 0
    assert state["operations_rows_offset"] == 0
    assert state["tasks_rows_offset"] == 12


def test_known_scene_name_is_not_added_again(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write(paths, middle_input(1.0), output("町"), 1.5)
    labels_before = (root / "notes" / "scene_labels.json").read_bytes()
    result = write(paths, middle_input(6.0), output(" 町 "), 6.5)
    assert result is not None
    assert result.new_scene is None
    assert result.state.scene_id == "scene-001"
    assert result.state.scene_name == "町"
    assert (root / "notes" / "scene_labels.json").read_bytes() == labels_before


def test_another_new_scene_gets_next_id(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write(paths, middle_input(1.0), output("町"), 1.5)
    result = write(paths, middle_input(6.0), output("草原"), 6.5)
    assert result is not None
    assert result.new_scene == SceneLabel("scene-002", "草原", 6.0)


def test_no_scene_name_writes_only_state(tmp_path):
    root = make_run(tmp_path)
    for name in [None, "", "   "]:
        result = write(RunPaths(root), middle_input(2.0), output(name), 2.5)
        assert result is not None
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
    write(RunPaths(root), middle_input(2.0), output("町", wait=5.0), 2.5)
    assert read_json(root / "middle" / "state.json")["surface_wait_s"] == 5.0


def test_state_is_replaced_without_temp_files(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write(paths, middle_input(1.0), output("町"), 1.5)
    write(paths, middle_input(6.0), output("町"), 6.5)
    assert read_json(root / "middle" / "state.json")["generated_t"] == 6.5
    assert sorted(p.name for p in (root / "middle").iterdir()) == ["state.json"]


# --- キューの部品の操作: 現在の場面 ---


def test_current_scene_is_set_to_the_output_scene(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    write(paths, middle_input(1.0), output("町"), 1.5, board=board)
    assert board.current_scene_id == SCENE
    # 場面が決まらない回は、場面なしにする
    write(paths, middle_input(2.0), output(None), 2.5, board=board)
    assert board.current_scene_id is None
    write(paths, middle_input(3.0), output("町"), 3.5, board=board)
    assert board.current_scene_id == SCENE


# --- キューの部品の操作: 追加 ---


def test_new_tasks_are_added_to_the_output_scene_at_generated_t(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    new_tasks = tasks("宿屋に入っている", "会話の枠が閉じている")
    result = write(
        paths, middle_input(12.0), output("町", new_tasks=new_tasks), 12.8, board=board
    )
    assert result is not None
    views = board.tasks(SCENE, 20.0)
    assert [view.text for view in views] == ["宿屋に入っている", "会話の枠が閉じている"]
    assert [view.added_t for view in views] == [12.8, 12.8]
    # 現在の場面を設定してから足すため、足した時刻から続いた秒数が進む
    assert [view.active_s for view in views] == [7.2, 7.2]
    assert result.new_tasks == new_tasks
    assert result.new_tasks_used is True
    assert result.cancel_used is False
    assert result.destination_fixes == ()
    # tasks.md はキューの部品が書く一覧になる
    assert (root / "notes" / "scenes" / SCENE / "tasks.md").read_text(
        encoding="utf-8"
    ) == "- 宿屋に入っている\n- 会話の枠が閉じている\n"


# マス目型のマップ (町の出口 (3, 4)・目印 (5, 5)、町から出るつながりの出たマス (0, 9))
GRID_MAP = GridMapDocument(
    places=(
        GridMapPlace(
            name="町",
            aliases=("町の中",),
            description="",
            state="",
            changes_on_entry=False,
            origin=TilePosition(0, 0),
            tiles=(),
            exits=(TileMark(TilePosition(3, 4), "宿屋の扉"),),
            blocked=(),
            landmarks=(TileMark(TilePosition(5, 5), "噴水"),),
        ),
    ),
    connections=(),
)


def test_destinations_are_fixed_with_the_input_map_before_adding(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    new_tasks = (
        NewTask(
            text="宿屋に入っている",
            destination=TaskDestination(place="町の中", tile=TilePosition(3, 4)),
        ),
        NewTask(
            text="店の前にいる",
            destination=TaskDestination(place="町", tile=TilePosition(9, 9)),
        ),
        NewTask(text="会話の枠が閉じている"),
    )
    result = write(
        paths,
        middle_input(12.0, map_document=GRID_MAP),
        output("町", new_tasks=new_tasks),
        12.8,
        board=board,
    )
    assert result is not None
    fixed = (
        NewTask(
            text="宿屋に入っている",
            destination=TaskDestination(place="町", tile=TilePosition(3, 4)),
        ),
        NewTask(
            text="店の前にいる", destination=TaskDestination(place="町", tile=None)
        ),
        NewTask(text="会話の枠が閉じている"),
    )
    assert result.new_tasks == fixed
    assert result.destination_fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="町の中", tile=TilePosition(3, 4)),
            after=TaskDestination(place="町", tile=TilePosition(3, 4)),
            reason="alias_place",
        ),
        DestinationFix(
            index=1,
            before=TaskDestination(place="町", tile=TilePosition(9, 9)),
            after=TaskDestination(place="町", tile=None),
            reason="tile_not_listed",
        ),
    )
    # キューには直した行き先で足す
    assert [view.destination for view in board.tasks(SCENE, 13.0)] == [
        task.destination for task in fixed
    ]


def test_destinations_without_map_become_null(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    destination = TaskDestination(place="町", tile=None)
    result = write(
        paths,
        middle_input(12.0),
        output(
            "町", new_tasks=(NewTask(text="宿屋に入っている", destination=destination),)
        ),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.new_tasks == (NewTask(text="宿屋に入っている", destination=None),)
    assert result.destination_fixes == (
        DestinationFix(index=0, before=destination, after=None, reason="no_map"),
    )
    assert [view.destination for view in board.tasks(SCENE, 13.0)] == [None]


def test_new_tasks_are_not_added_without_scene(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    destination = TaskDestination(place="町", tile=None)
    new_tasks = (NewTask(text="宿屋に入っている", destination=destination),)
    result = write(
        paths, middle_input(12.0), output(None, new_tasks=new_tasks), 12.8, board=board
    )
    assert result is not None
    assert result.new_tasks_used is False
    # 足さない回も、行き先の補正は計算する
    assert result.new_tasks == (NewTask(text="宿屋に入っている", destination=None),)
    assert [fix.reason for fix in result.destination_fixes] == ["no_map"]
    assert not (root / "notes" / "scenes").exists()
    assert not paths.tasks_log.exists()


def test_new_tasks_are_not_added_when_the_scene_changed(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町", "草原")
    board = TaskBoard(paths)
    result = write(
        paths,
        middle_input(12.0, previous_state=previous_state(SCENE)),
        output("草原", new_tasks=tasks("草原の北にいる")),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.state.scene_id == OTHER
    assert result.new_tasks_used is False
    assert board.tasks(OTHER, 13.0) == ()
    # 現在の場面は、場面が変わった回も出力の場面にする
    assert board.current_scene_id == OTHER


def test_new_tasks_are_added_when_the_input_scene_is_the_output_scene(tmp_path):
    # 入力の場面 (前回の middle/state.json の場面) と同じ場面なら、入力で見たキューの末尾に足す
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    result = write(
        paths,
        middle_input(12.0, previous_state=previous_state(SCENE)),
        output("町", new_tasks=tasks("主人と話している")),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.new_tasks_used is True
    assert [view.text for view in board.tasks(SCENE, 13.0)] == [
        "宿屋に入っている",
        "主人と話している",
    ]


@pytest.mark.parametrize("previous", [None, previous_state(None)])
def test_new_tasks_are_added_without_input_scene_when_the_queue_is_empty(
    tmp_path, previous
):
    # 前回の middle/state.json が無いか、前回の場面が決まっていない。出力の場面のキューが空なら足す
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = TaskBoard(paths)
    result = write(
        paths,
        middle_input(12.0, previous_state=previous),
        output("町", new_tasks=tasks("宿屋に入っている")),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.new_tasks_used is True
    assert [view.text for view in board.tasks(SCENE, 13.0)] == ["宿屋に入っている"]


@pytest.mark.parametrize("previous", [None, previous_state(None)])
def test_new_tasks_are_not_added_without_input_scene_when_the_queue_has_tasks(
    tmp_path, previous
):
    # 入力のキューは空に見えるが、出力の場面には middle が見ていないタスクがある: その末尾には足さない
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    result = write(
        paths,
        middle_input(12.0, previous_state=previous),
        output("町", new_tasks=tasks("主人と話している")),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.new_tasks_used is False
    assert task_ids(board) == ["task-001"]
    # 行き先の補正は、足さない回も計算する
    assert result.new_tasks == tasks("主人と話している")


def test_queue_emptied_by_cancel_counts_as_empty_without_input_scene(tmp_path):
    # 入力の場面が決まっていなくても、取り消しの後に出力の場面のキューが空なら足す
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    result = write(
        paths,
        middle_input(12.0, reasons=("cancel_requested",)),
        output(
            "町", new_tasks=tasks("主人と話している"), cancel_task_ids=("task-001",)
        ),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.cancel_used is True
    assert result.new_tasks_used is True
    assert task_ids(board) == ["task-002"]


# --- キューの部品の操作: 取り消し ---


@pytest.mark.parametrize(
    ("reasons", "is_stuck", "used"),
    [
        (("interval",), False, False),
        (("queue_empty", "scene_change"), False, False),
        (("cancel_requested",), False, True),
        (("stuck",), False, True),
        (("queue_empty", "stuck"), False, True),
        (("interval",), True, True),
    ],
)
def test_cancel_task_ids_are_used_only_with_cancel_reasons_or_is_stuck(
    tmp_path, reasons, is_stuck, used
):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている", "会話の枠が閉じている")
    result = write(
        paths,
        middle_input(12.0, reasons=reasons),
        output("町", cancel_task_ids=("task-001",), is_stuck=is_stuck),
        12.8,
        board=board,
    )
    assert result is not None
    assert result.cancel_used is used
    # 使わなかった回も、出力の cancel_task_ids は結果に残す (middle/calls.jsonl に書く)
    assert result.cancel_task_ids == ("task-001",)
    expected = ["task-002"] if used else ["task-001", "task-002"]
    assert task_ids(board) == expected


def test_cancel_is_done_before_adding(tmp_path):
    # 未完了のタスクが上限 (5 個) の場面でも、取り消してから足すので、空いた分を足せる
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "A", "B", "C")
    board.add(SCENE, tasks("D", "E"), 1.0)
    result = write(
        paths,
        middle_input(12.0, previous_state=previous_state(SCENE), reasons=("stuck",)),
        output("町", new_tasks=tasks("F"), cancel_task_ids=("task-001",)),
        12.8,
        board=board,
    )
    assert result is not None
    assert task_ids(board) == [
        "task-002",
        "task-003",
        "task-004",
        "task-005",
        "task-006",
    ]
    rows = [
        json.loads(line)
        for line in paths.tasks_log.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows[-2:]] == ["canceled", "added"]
    assert [row["t"] for row in rows[-2:]] == [12.8, 12.8]


# --- キューの部品の操作: キューが空になったことの知らせ ---


def test_queue_empty_is_notified_when_cancel_empties_the_output_scene(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    notify = NotifyRecorder()
    write(
        paths,
        middle_input(12.0, reasons=("cancel_requested",)),
        output("町", cancel_task_ids=("task-001",)),
        12.8,
        board=board,
        trigger_notify=notify,
    )
    assert board.tasks(SCENE, 13.0) == ()
    assert notify.reasons == ["queue_empty"]


@pytest.mark.parametrize(
    ("reasons", "new_tasks", "cancel_task_ids"),
    [
        # 取り消しの後に足して、キューが空でない
        (("cancel_requested",), tasks("会話の枠が閉じている"), ("task-001",)),
        # 取り消したタスクが無い (未知の ID)
        (("cancel_requested",), (), ("task-099",)),
        # 取り消しを使わない回
        (("interval",), (), ("task-001",)),
    ],
)
def test_queue_empty_is_not_notified_otherwise(
    tmp_path, reasons, new_tasks, cancel_task_ids
):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    notify = NotifyRecorder()
    write(
        paths,
        middle_input(12.0, reasons=reasons),
        output("町", new_tasks=new_tasks, cancel_task_ids=cancel_task_ids),
        12.8,
        board=board,
        trigger_notify=notify,
    )
    assert notify.reasons == []


def test_queue_empty_without_trigger_notify_is_not_an_error(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    write_labels(root, "町")
    board = board_with_tasks(paths, "宿屋に入っている")
    result = write(
        paths,
        middle_input(12.0, reasons=("cancel_requested",)),
        output("町", cancel_task_ids=("task-001",)),
        12.8,
        board=board,
        trigger_notify=None,
    )
    assert result is not None
    assert result.cancel_used is True
    assert board.tasks(SCENE, 13.0) == ()


# --- 止める合図 ---


def test_stop_before_the_queue_operations_writes_nothing(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    stop = threading.Event()
    stop.set()
    result = write(
        paths,
        middle_input(12.0, reasons=("stuck",)),
        output(
            "町", new_tasks=tasks("宿屋に入っている"), cancel_task_ids=("task-001",)
        ),
        12.8,
        board=board,
        stop=stop,
    )
    assert result is None
    assert board.current_scene_id is None
    assert board.tasks(SCENE, 13.0) == ()
    assert not (root / "middle" / "state.json").exists()
    assert not paths.tasks_log.exists()
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS


# --- 書き込みに失敗したとき (一覧に足すのは最後) ---


def test_failed_queue_operation_does_not_add_scene_label(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # notes/scenes をファイルにして、キューの部品の場面のファイルの書き込みを失敗させる
    (root / "notes" / "scenes").write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        write(
            paths,
            middle_input(1.0),
            output("町", new_tasks=tasks("宿屋に入っている")),
            1.5,
        )
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS
    assert not (root / "middle" / "state.json").exists()
    # 書けるようになった次の回では、同じ名前が新しい場面 (同じ ID) として扱われる
    (root / "notes" / "scenes").unlink()
    result = write(paths, middle_input(6.0), output("町"), 6.5)
    assert result is not None
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)
    assert read_json(root / "notes" / "scene_labels.json") == {
        "scenes": [{"id": "scene-001", "name": "町", "first_seen_t": 6.0}]
    }


def test_failed_state_write_does_not_add_scene_label(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    board = TaskBoard(paths)
    # middle/state.json をディレクトリーにして、state.json の置き換えを失敗させる
    (root / "middle" / "state.json").mkdir()
    with pytest.raises(OSError):
        write(
            paths,
            middle_input(1.0),
            output("町", new_tasks=tasks("宿屋に入っている")),
            1.5,
            board=board,
        )
    # キューの部品の操作は済んだ後だが、一覧には足していない
    assert [view.text for view in board.tasks(SCENE, 2.0)] == ["宿屋に入っている"]
    assert read_json(root / "notes" / "scene_labels.json") == EMPTY_LABELS
    shutil.rmtree(root / "middle" / "state.json")
    result = write(paths, middle_input(6.0), output("町"), 6.5, board=board)
    assert result is not None
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)
    assert result.state.scene_id == "scene-001"


def test_failed_label_write_keeps_scene_new_for_next_call(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    # scene_labels.json をディレクトリーにして、一覧の書き込み (最後) を失敗させる
    (root / "notes" / "scene_labels.json").unlink()
    (root / "notes" / "scene_labels.json").mkdir()
    with pytest.raises(OSError):
        write(paths, middle_input(1.0), output("町"), 1.5)
    # state.json は書き終えている
    assert read_json(root / "middle" / "state.json")["scene_id"] == "scene-001"
    (root / "notes" / "scene_labels.json").rmdir()
    result = write(paths, middle_input(6.0), output("町"), 6.5)
    assert result is not None
    assert result.new_scene == SceneLabel("scene-001", "町", 6.0)


# --- 現在地の場所とマス ---


def place_output(
    place_name: str | None, current_tile: TilePosition | None
) -> MiddleOutput:
    return dataclasses.replace(
        output("町"), place_name=place_name, current_tile=current_tile
    )


def test_place_name_and_current_tile_are_written_to_state(tmp_path):
    root = make_run(tmp_path)
    result = write(
        RunPaths(root),
        middle_input(12.0),
        place_output(" 家の 1 階 ", TilePosition(7, -1)),
        12.8,
    )
    assert result is not None
    # place_name は前後の空白を除いたもの、current_tile は出力の値
    assert result.state.place_name == "家の 1 階"
    assert result.state.current_tile == TilePosition(7, -1)
    state = read_json(root / "middle" / "state.json")
    assert list(state) == STATE_KEYS
    assert state["place_name"] == "家の 1 階"
    assert state["current_tile"] == {"x": 7, "y": -1}
    assert list(state["current_tile"]) == ["x", "y"]


def test_place_name_without_current_tile_is_written_with_null_tile(tmp_path):
    root = make_run(tmp_path)
    result = write(
        RunPaths(root), middle_input(12.0), place_output("家の 1 階", None), 12.8
    )
    assert result is not None
    assert result.state.place_name == "家の 1 階"
    assert result.state.current_tile is None
    state = read_json(root / "middle" / "state.json")
    assert state["place_name"] == "家の 1 階"
    assert state["current_tile"] is None


@pytest.mark.parametrize("place_name", [None, "", "   "])
def test_current_tile_without_place_name_is_null(tmp_path, place_name):
    # place_name が null なら、出力の current_tile があっても null にする
    root = make_run(tmp_path)
    result = write(
        RunPaths(root),
        middle_input(12.0),
        place_output(place_name, TilePosition(7, 1)),
        12.8,
    )
    assert result is not None
    assert result.state.place_name is None
    assert result.state.current_tile is None
    state = read_json(root / "middle" / "state.json")
    assert list(state) == STATE_KEYS
    assert state["place_name"] is None
    assert state["current_tile"] is None


def test_place_is_written_without_scene(tmp_path):
    # 場面が決まらない回も、場所とマスを書く
    root = make_run(tmp_path)
    output_without_scene = dataclasses.replace(
        place_output("家の 1 階", TilePosition(2, 3)), scene_name=None
    )
    result = write(RunPaths(root), middle_input(12.0), output_without_scene, 12.8)
    assert result is not None
    assert result.state.scene_id is None
    assert result.state.place_name == "家の 1 階"
    assert result.state.current_tile == TilePosition(2, 3)
    assert not (root / "notes" / "scenes").exists()


@pytest.mark.parametrize("press_home", [True, False])
def test_press_home_is_written_to_state(tmp_path, press_home):
    root = make_run(tmp_path)
    result = write(
        RunPaths(root),
        middle_input(12.0),
        dataclasses.replace(output("町"), press_home=press_home),
        12.8,
    )
    assert result is not None
    assert result.state.press_home is press_home
    state = read_json(root / "middle" / "state.json")
    assert list(state) == STATE_KEYS
    assert state["press_home"] is press_home
