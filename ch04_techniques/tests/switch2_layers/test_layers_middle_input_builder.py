import json
from pathlib import Path

from switch2_layers.frames.frame_store import FrameKind, FrameRecord
from switch2_layers.middle.middle_input_builder import (
    MIDDLE_MAX_FRAMES,
    build_middle_input,
    has_recorded_frame,
    read_middle_state,
    select_interval_frames,
)
from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.notes.scene_labels import SceneLabel
from switch2_layers.run_directory import RunPaths

STATE = {
    "generated_t": 5.5,
    "based_on_t": 5.0,
    "scene_id": "scene-001",
    "scene_name": "町",
    "is_stuck": True,
    "stuck_description": "扉の前で止まっている",
    "surface_wait_s": 1.0,
    "timeline": "## 直近",
    "surface_rows_until_t": 5.0,
    "operations_rows_until_t": 5.0,
}


def make_run(tmp_path: Path) -> Path:
    """空の実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    (root / "notes" / "common").mkdir(parents=True)
    (root / "middle").mkdir()
    return root


def write_jsonl(path: Path, rows: list[dict], partial_line: str = "") -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text + partial_line, encoding="utf-8")


def frame_name(t: float) -> str:
    return f"{round(t * 1000):09d}.jpg"


def write_frames(root: Path, interval_times: list[float], surface_times=()) -> None:
    """画像のファイルと frames.jsonl の行を書く。画像の中身は b"jpeg-<t>"。"""
    rows = []
    for kind, times in [("interval", interval_times), ("surface", surface_times)]:
        for t in times:
            file = f"frames/{kind}/{frame_name(t)}"
            (root / file).write_bytes(f"jpeg-{t}".encode())
            rows.append({"t": t, "kind": kind, "file": file, "width": 64, "height": 36})
    rows.sort(key=lambda row: row["t"])
    write_jsonl(root / "frames.jsonl", rows)


def surface_row(t_received: float, scene_changed: bool = False) -> dict:
    return {
        "t_sent": t_received - 0.1,
        "t_received": t_received,
        "scene_changed": scene_changed,
    }


def test_max_frames_follows_the_issue():
    assert MIDDLE_MAX_FRAMES == 4


# --- 最初の実行の条件 ---


def test_has_recorded_frame(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    assert has_recorded_frame(paths) is False
    (root / "frames.jsonl").write_text("", encoding="utf-8")
    assert has_recorded_frame(paths) is False
    # 書いている途中の行はまだ載っていない
    (root / "frames.jsonl").write_text(
        '{"t": 0.1, "kind": "interval"', encoding="utf-8"
    )
    assert has_recorded_frame(paths) is False
    write_frames(root, [0.1])
    assert has_recorded_frame(paths) is True


# --- 前回の middle/state.json ---


def test_read_middle_state(tmp_path):
    path = tmp_path / "state.json"
    assert read_middle_state(path) is None
    path.write_text(json.dumps(STATE), encoding="utf-8")
    assert read_middle_state(path) == MiddleState(**STATE)
    path.write_text("{broken", encoding="utf-8")
    assert read_middle_state(path) is None
    path.write_text(json.dumps({**STATE, "is_stuck": "yes"}), encoding="utf-8")
    assert read_middle_state(path) is None


# --- フレームの選び方 ---


def records(times: list[float], kind: FrameKind = "interval") -> list[FrameRecord]:
    return [
        FrameRecord(t, kind, f"frames/{kind}/{frame_name(t)}", 64, 36) for t in times
    ]


def test_select_interval_frames_uses_window_and_kind():
    all_records = records([0.0, 9.0, 10.0, 15.0, 20.0, 21.0]) + records(
        [19.0], "surface"
    )
    selected = select_interval_frames(all_records, now_t=20.0, window_s=10.0)
    assert [record.t for record in selected] == [10.0, 15.0, 20.0]


def test_select_interval_frames_thins_to_four_including_latest():
    selected = select_interval_frames(
        records([float(t) for t in range(10, 21)]), now_t=20.0, window_s=10.0
    )
    assert [record.t for record in selected] == [10.0, 13.0, 17.0, 20.0]


def test_select_interval_frames_of_nothing():
    assert select_interval_frames([], now_t=20.0, window_s=10.0) == []


# --- 入力の作り方 ---


def test_empty_run_gives_empty_input(tmp_path):
    root = make_run(tmp_path)
    middle_input = build_middle_input(RunPaths(root), now_t=3.0, window_s=10.0)
    assert middle_input.t == 3.0
    assert middle_input.frames == ()
    assert middle_input.surface_rows == ()
    assert middle_input.operation_rows == ()
    assert middle_input.scene_change_reported is False
    assert middle_input.scene_labels == ()
    assert middle_input.previous_state is None
    assert middle_input.strategy == ""
    assert middle_input.current_tasks == ""
    assert middle_input.surface_rows_until_t is None
    assert middle_input.operations_rows_until_t is None


def test_frames_are_read_from_saved_interval_images(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [float(t) for t in range(22)], surface_times=[19.5])
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert [frame.t for frame in middle_input.frames] == [10.0, 13.0, 17.0, 20.0]
    assert middle_input.frames[-1].file == "frames/interval/000020000.jpg"
    assert middle_input.frames[-1].jpeg == b"jpeg-20.0"


def test_frames_with_missing_files_are_skipped(tmp_path):
    root = make_run(tmp_path)
    write_frames(root, [18.0, 19.0, 20.0])
    (root / "frames" / "interval" / "000019000.jpg").unlink()
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert [frame.t for frame in middle_input.frames] == [18.0, 20.0]


def test_rows_since_previous_middle_run(tmp_path):
    root = make_run(tmp_path)
    (root / "middle" / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
    write_jsonl(
        root / "surface.jsonl",
        [
            surface_row(4.0),
            surface_row(5.0),
            surface_row(6.0),
            surface_row(7.0),
            surface_row(30.0),
        ],
    )
    write_jsonl(
        root / "operations.jsonl",
        [
            {"t": 4.9, "source": "apply"},
            {"t": 5.1, "source": "apply"},
            {"t": 7.5, "source": "neutral"},
        ],
    )
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    # 前回までに読んだ行の時刻 (5.0) より後で、今回の t (20.0) 以前の行
    assert [row["t_received"] for row in middle_input.surface_rows] == [6.0, 7.0]
    assert [row["t"] for row in middle_input.operation_rows] == [5.1, 7.5]
    assert middle_input.previous_state == MiddleState(**STATE)
    # 次の入力の下限は、今回読んだ行の時刻の最大値 (20.0 より後の 30.0 の行は読んでいない)
    assert middle_input.surface_rows_until_t == 7.0
    assert middle_input.operations_rows_until_t == 7.5


def test_lower_bound_comes_from_rows_read_not_from_input_time(tmp_path):
    # 前回の入力を作った時刻 (based_on_t = 10.0) より前の t_received を持つ行でも、前回の入力の後に追記されて
    # まだ読んでいなければ (前回読んだ行の最大 6.0 より後なら)、今回の入力に含める
    root = make_run(tmp_path)
    state = {
        **STATE,
        "based_on_t": 10.0,
        "surface_rows_until_t": 6.0,
        "operations_rows_until_t": 6.5,
    }
    (root / "middle" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    write_jsonl(
        root / "surface.jsonl",
        [surface_row(6.0), surface_row(7.0, scene_changed=True), surface_row(12.0)],
    )
    write_jsonl(
        root / "operations.jsonl",
        [{"t": 6.5}, {"t": 8.0}, {"t": 11.0}],
    )
    middle_input = build_middle_input(RunPaths(root), now_t=15.0, window_s=10.0)
    assert [row["t_received"] for row in middle_input.surface_rows] == [7.0, 12.0]
    assert [row["t"] for row in middle_input.operation_rows] == [8.0, 11.0]
    # 遅れて書かれた行の場面転換の印も middle に届く
    assert middle_input.scene_change_reported is True
    assert middle_input.surface_rows_until_t == 12.0
    assert middle_input.operations_rows_until_t == 11.0


def test_bounds_neither_move_back_nor_forward_without_new_rows(tmp_path):
    root = make_run(tmp_path)
    state = {
        **STATE,
        "based_on_t": 10.0,
        "surface_rows_until_t": 6.0,
        "operations_rows_until_t": None,
    }
    (root / "middle" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    # 読んだことのある行と、今回の t より新しい行だけがある
    write_jsonl(
        root / "surface.jsonl", [surface_row(5.0), surface_row(6.0), surface_row(25.0)]
    )
    write_jsonl(root / "operations.jsonl", [{"t": 21.0}])
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert middle_input.surface_rows == ()
    assert middle_input.operation_rows == ()
    # 入力を作った時刻 (20.0) にも、今回の t より新しい行の時刻にも進めない
    assert middle_input.surface_rows_until_t == 6.0
    assert middle_input.operations_rows_until_t is None


def test_null_bounds_in_previous_state_read_all_rows(tmp_path):
    root = make_run(tmp_path)
    state = {
        **STATE,
        "based_on_t": 10.0,
        "surface_rows_until_t": None,
        "operations_rows_until_t": None,
    }
    (root / "middle" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    write_jsonl(root / "surface.jsonl", [surface_row(4.0), surface_row(11.0)])
    write_jsonl(root / "operations.jsonl", [{"t": 3.0}])
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert [row["t_received"] for row in middle_input.surface_rows] == [4.0, 11.0]
    assert [row["t"] for row in middle_input.operation_rows] == [3.0]
    assert middle_input.surface_rows_until_t == 11.0
    assert middle_input.operations_rows_until_t == 3.0


def test_all_rows_without_previous_state(tmp_path):
    root = make_run(tmp_path)
    write_jsonl(
        root / "surface.jsonl",
        [surface_row(1.0), surface_row(2.0), {"t_received": "bad"}],
        partial_line='{"t_received": 3.0',
    )
    write_jsonl(root / "operations.jsonl", [{"t": 0.5}, {"t": 25.0}])
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert [row["t_received"] for row in middle_input.surface_rows] == [1.0, 2.0]
    assert [row["t"] for row in middle_input.operation_rows] == [0.5]
    # 書いている途中の行 (3.0) と、時刻が数でない行は、下限の計算にも使わない
    assert middle_input.surface_rows_until_t == 2.0
    assert middle_input.operations_rows_until_t == 0.5


def test_broken_previous_state_is_treated_as_missing(tmp_path):
    root = make_run(tmp_path)
    (root / "middle" / "state.json").write_text("{broken", encoding="utf-8")
    write_jsonl(root / "surface.jsonl", [surface_row(1.0)])
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert middle_input.previous_state is None
    assert len(middle_input.surface_rows) == 1


def test_scene_change_mark_is_reported(tmp_path):
    root = make_run(tmp_path)
    write_jsonl(
        root / "surface.jsonl",
        [surface_row(1.0), surface_row(2.0, scene_changed=True), surface_row(3.0)],
    )
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert middle_input.scene_change_reported is True
    assert middle_input.surface_rows[1]["scene_changed"] is True


def test_labels_strategy_and_current_tasks(tmp_path):
    root = make_run(tmp_path)
    (root / "middle" / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
    (root / "notes" / "scene_labels.json").write_text(
        json.dumps(
            {"scenes": [{"id": "scene-001", "name": "町", "first_seen_t": 2.0}]}
        ),
        encoding="utf-8",
    )
    (root / "notes" / "strategy.md").write_text(
        "# 大方針\n\n町を出る\n", encoding="utf-8"
    )
    scene_dir = root / "notes" / "scenes" / "scene-001"
    scene_dir.mkdir(parents=True)
    (scene_dir / "tasks.md").write_text("- 宿屋に入る\n", encoding="utf-8")
    other_dir = root / "notes" / "scenes" / "scene-002"
    other_dir.mkdir(parents=True)
    (other_dir / "tasks.md").write_text("- 別の場面\n", encoding="utf-8")
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert middle_input.scene_labels == (SceneLabel("scene-001", "町", 2.0),)
    assert middle_input.strategy == "# 大方針\n\n町を出る\n"
    assert middle_input.current_tasks == "- 宿屋に入る\n"


def test_current_tasks_without_scene_is_empty(tmp_path):
    root = make_run(tmp_path)
    (root / "middle" / "state.json").write_text(
        json.dumps({**STATE, "scene_id": None, "scene_name": None}), encoding="utf-8"
    )
    middle_input = build_middle_input(RunPaths(root), now_t=20.0, window_s=10.0)
    assert middle_input.current_tasks == ""
