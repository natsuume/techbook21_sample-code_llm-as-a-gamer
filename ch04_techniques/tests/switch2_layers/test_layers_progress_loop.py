import json
import threading
from pathlib import Path

import cv2
import numpy as np

from switch2_layers.progress.progress_loop import POLL_INTERVAL_S, ProgressLoop
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.screen_progress import ScreenProgressTracker
from switch2_layers.run_directory import RunPaths

WINDOW_S = 300.0


def make_run(tmp_path: Path) -> Path:
    """空の実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    (root / "progress").mkdir()
    return root


def striped_screen(phase: int) -> np.ndarray:
    """縦じまの BGR の画面 (phase で、しまの位置をずらす)。平らな画像は dHash がどれも同じになるため、模様を付ける。"""
    columns = np.arange(144)
    values = np.where(((columns // 16) + phase) % 2 == 0, 40, 200).astype(np.uint8)
    gray = np.tile(values, (80, 1))
    return np.stack([gray, gray, gray], axis=2)


def jpeg_of(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def frame_file(kind: str, t: float) -> str:
    return f"frames/{kind}/{round(t * 1000):09d}.jpg"


def append_row(root: Path, row: dict, newline: bool = True) -> None:
    with (root / "frames.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(row) + ("\n" if newline else ""))


def add_frame(
    root: Path, t: float, phase: int = 0, kind: str = "interval", newline: bool = True
) -> None:
    """画像のファイルを書いてから、frames.jsonl に行を足す (frames.frame_store と同じ順)。"""
    file = frame_file(kind, t)
    (root / file).write_bytes(jpeg_of(striped_screen(phase)))
    append_row(
        root,
        {"t": t, "kind": kind, "file": file, "width": 144, "height": 80},
        newline=newline,
    )


def make_loop(root: Path) -> ProgressLoop:
    return ProgressLoop(RunPaths(root), ScreenProgressTracker(WINDOW_S))


def read_state(root: Path) -> dict:
    return json.loads((root / "progress" / "state.json").read_text(encoding="utf-8"))


def test_poll_interval():
    assert POLL_INTERVAL_S == 0.2


def test_no_frames_writes_nothing(tmp_path):
    root = make_run(tmp_path)
    loop = make_loop(root)
    # frames.jsonl がまだ無い
    assert loop.step() is None
    (root / "frames.jsonl").write_text("", encoding="utf-8")
    assert loop.step() is None
    assert not (root / "progress" / "state.json").exists()


def test_interval_frames_are_measured_and_written(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 0.0)
    add_frame(root, 1.5)
    add_frame(root, 3.0)
    loop = make_loop(root)
    state = loop.step()
    # t=3.0 は 2 秒より前の t=0.0 と比べて同じ画面。最後の新しい画面は、比べる相手が無かった t=1.5
    assert state == ProgressState(
        t=3.0, static_s=3.0, no_novel_s=1.5, window_s=WINDOW_S
    )
    assert read_state(root) == {
        "t": 3.0,
        "static_s": 3.0,
        "no_novel_s": 1.5,
        "window_s": WINDOW_S,
    }


def test_only_new_rows_are_read_on_the_next_step(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 0.0)
    loop = make_loop(root)
    assert loop.step() == ProgressState(0.0, 0.0, 0.0, WINDOW_S)
    # 足された行が無ければ書かない
    assert loop.step() is None
    add_frame(root, 2.5, phase=1)
    add_frame(root, 5.0, phase=0)
    # 新しい行だけを、続けて計算する (t=2.5 は新しい画面、t=5.0 は前に見た画面)
    assert loop.step() == ProgressState(5.0, 0.0, 2.5, WINDOW_S)
    assert read_state(root)["t"] == 5.0


def test_surface_frames_are_ignored(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 0.1, kind="surface")
    loop = make_loop(root)
    assert loop.step() is None
    assert not (root / "progress" / "state.json").exists()
    add_frame(root, 0.2)
    add_frame(root, 0.3, phase=1, kind="surface")
    assert loop.step() == ProgressState(0.2, 0.0, 0.0, WINDOW_S)


def test_partial_line_is_read_after_it_is_completed(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 0.0)
    # 書いている途中の行 (改行で終わらない)
    add_frame(root, 3.0, newline=False)
    loop = make_loop(root)
    assert loop.step() == ProgressState(0.0, 0.0, 0.0, WINDOW_S)
    with (root / "frames.jsonl").open("a", encoding="utf-8") as file:
        file.write("\n")
    assert loop.step() == ProgressState(3.0, 3.0, 3.0, WINDOW_S)


def test_unusable_rows_are_skipped(tmp_path):
    root = make_run(tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(jpeg_of(striped_screen(1)))
    rows = [
        # 画像が無い
        {"t": 0.0, "kind": "interval", "file": frame_file("interval", 0.0)},
        # JPEG として読めない
        {"t": 0.1, "kind": "interval", "file": frame_file("interval", 0.1)},
        # frames/interval/ の外を指す
        {"t": 0.2, "kind": "interval", "file": "../outside.jpg"},
        # file が文字列でない・t が数でない
        {"t": 0.3, "kind": "interval", "file": 3},
        {"t": "0.4", "kind": "interval", "file": frame_file("interval", 0.4)},
    ]
    (root / frame_file("interval", 0.1)).write_bytes(b"not a jpeg")
    (root / frame_file("interval", 0.4)).write_bytes(jpeg_of(striped_screen(1)))
    for row in rows:
        append_row(root, row)
    loop = make_loop(root)
    assert loop.step() is None
    assert not (root / "progress" / "state.json").exists()
    add_frame(root, 0.5)
    # 読み飛ばした行は計算に入らず、t=0.5 が最初のフレームになる
    assert loop.step() == ProgressState(0.5, 0.0, 0.0, WINDOW_S)


def test_rows_not_after_the_previous_frame_are_skipped(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 1.0)
    # 同じ t の 2 行目は、別のファイルの違う画面を指す (1 行目の画像は書き換えない)
    other = "frames/interval/other-screen.jpg"
    (root / other).write_bytes(jpeg_of(striped_screen(1)))
    append_row(root, {"t": 1.0, "kind": "interval", "file": other})
    add_frame(root, 0.5, phase=1)
    add_frame(root, 4.0)
    loop = make_loop(root)
    # t が前に渡したフレームの時刻以下の 2 行は読み飛ばし、違う画面を計算に入れない
    assert loop.step() == ProgressState(4.0, 3.0, 3.0, WINDOW_S)


def test_run_returns_when_stopped_and_continues_after_errors(tmp_path):
    root = make_run(tmp_path)
    loop = ProgressLoop(RunPaths(root), ScreenProgressTracker(WINDOW_S), 0.0)
    stop = threading.Event()
    calls: list[int] = []

    def step() -> ProgressState | None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unexpected")
        stop.set()
        return None

    loop.step = step  # type: ignore[method-assign]
    loop.run(stop)
    # 想定外の例外の後も続け、止める合図で戻った
    assert len(calls) == 2


def test_run_does_nothing_after_stop(tmp_path):
    root = make_run(tmp_path)
    add_frame(root, 0.0)
    loop = make_loop(root)
    stop = threading.Event()
    stop.set()
    loop.run(stop)
    assert not (root / "progress" / "state.json").exists()
