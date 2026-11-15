import json
from pathlib import Path

import cv2
import numpy as np

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.frame_store import (
    FrameRecord,
    FrameStore,
    frame_record_row,
    is_interval_due,
    latest_frame_t,
    read_frame_records,
)
from switch2_layers.frames.image_encoding import EncodedImage
from switch2_layers.run_directory import RunPaths


def make_run(tmp_path: Path) -> Path:
    """frames/interval/ と frames/surface/ だけを持つ実行ディレクトリーを作る (パッケージのコードは使わない)。"""
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    return root


def captured(t: float, width: int = 128, height: int = 72) -> CapturedFrame:
    return CapturedFrame(t=t, nv12=np.full((height * 3 // 2, width), 128, np.uint8))


def jpeg_image(width: int = 32, height: int = 18) -> EncodedImage:
    ok, encoded = cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8))
    assert ok
    return EncodedImage(jpeg=encoded.tobytes(), width=width, height=height)


def frames_rows(root: Path) -> list[dict]:
    path = root / "frames.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- 一定間隔の判定 ---


def test_is_interval_due():
    assert is_interval_due(None, 0.0, 5.0)
    assert is_interval_due(1.0, 1.2, 5.0)
    # 揺らぎを許す: 1 / 5 秒の 0.9 倍 (0.18 秒) たてば対象
    assert is_interval_due(1.0, 1.19, 5.0)
    assert is_interval_due(0.0, 0.18, 5.0)
    assert not is_interval_due(1.0, 1.17, 5.0)
    assert not is_interval_due(1.0, 1.1, 5.0)


# --- 一定間隔の保存 ---


def test_offer_interval_saves_frames_at_the_interval(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    results = [store.offer_interval(captured(t)) for t in [0.0, 0.1, 0.2, 0.3, 0.4]]
    assert results == [True, False, True, False, True]
    assert store.write_queued() == 3
    assert store.written == 3
    assert store.dropped == 0
    rows = frames_rows(root)
    assert [row["t"] for row in rows] == [0.0, 0.2, 0.4]
    assert all(row["kind"] == "interval" for row in rows)
    assert rows[1]["file"] == "frames/interval/000000200.jpg"


def test_interval_frames_are_shrunk_to_interval_width(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    store.offer_interval(captured(1.5, width=128, height=72))
    store.write_queued()
    row = frames_rows(root)[0]
    assert row == {
        "t": 1.5,
        "kind": "interval",
        "file": "frames/interval/000001500.jpg",
        "width": 64,
        "height": 36,
    }
    image = cv2.imread(str(root / row["file"]))
    assert image.shape[:2] == (36, 64)


def test_frames_jsonl_rows_point_to_written_images(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    for t in [0.0, 0.2, 0.4]:
        store.offer_interval(captured(t))
    store.save_surface(0.3, jpeg_image())
    store.write_queued()
    rows = frames_rows(root)
    assert len(rows) == 4
    for row in rows:
        assert (root / row["file"]).is_file()


def test_no_row_is_added_when_image_write_fails(tmp_path):
    root = make_run(tmp_path)
    # 画像を書くディレクトリーをファイルに置き換え、書き込みを失敗させる
    (root / "frames" / "interval").rmdir()
    (root / "frames" / "interval").write_text("not a directory", encoding="utf-8")
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    store.offer_interval(captured(0.0))
    store.save_surface(0.1, jpeg_image())
    assert store.write_queued() == 1
    assert store.dropped == 1
    rows = frames_rows(root)
    assert [row["kind"] for row in rows] == ["surface"]


# --- surface の画像の保存 ---


def test_save_surface_writes_image_and_row(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    image = jpeg_image(32, 18)
    assert store.save_surface(1.5, image) == "frames/surface/000001500.jpg"
    store.write_queued()
    assert (root / "frames" / "surface" / "000001500.jpg").read_bytes() == image.jpeg
    assert frames_rows(root) == [
        {
            "t": 1.5,
            "kind": "surface",
            "file": "frames/surface/000001500.jpg",
            "width": 32,
            "height": 18,
        }
    ]


def test_same_surface_frame_is_saved_only_once(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    image = jpeg_image()
    first = store.save_surface(2.0, image)
    second = store.save_surface(2.0, image)
    # 同じミリ秒の t は同じフレームとして扱う
    third = store.save_surface(2.0004, image)
    assert first == second == third == "frames/surface/000002000.jpg"
    store.write_queued()
    # 書いた後に同じフレームをもう一度送っても、保存し直さない
    assert store.save_surface(2.0, image) == "frames/surface/000002000.jpg"
    store.write_queued()
    assert store.written == 1
    assert [row["t"] for row in frames_rows(root)] == [2.0]


def test_different_surface_frames_are_saved_separately(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    for t in [1.0, 1.1, 1.2]:
        store.save_surface(t, jpeg_image())
    store.write_queued()
    assert sorted(p.name for p in (root / "frames" / "surface").iterdir()) == [
        "000001000.jpg",
        "000001100.jpg",
        "000001200.jpg",
    ]


# --- 上限付きのキューと捨てた件数 ---


def test_full_queue_drops_and_counts(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root),
        interval_fps=5.0,
        interval_width=64,
        capacity=1,
        start_thread=False,
    )
    assert store.offer_interval(captured(0.0)) is True
    # キューが満杯なので捨てる
    assert store.offer_interval(captured(0.2)) is False
    assert store.save_surface(0.3, jpeg_image()) is None
    assert store.dropped == 2
    store.write_queued()
    assert store.written == 1
    # 満杯で捨てた surface の画像は、次に渡されたときにもう一度保存を試みる
    assert store.save_surface(0.3, jpeg_image()) == "frames/surface/000000300.jpg"
    store.write_queued()
    assert store.written == 2


def test_dropped_interval_frame_still_advances_the_interval(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root),
        interval_fps=5.0,
        interval_width=64,
        capacity=1,
        start_thread=False,
    )
    store.offer_interval(captured(0.0))
    assert store.offer_interval(captured(0.2)) is False
    store.write_queued()
    # 0.2 は捨てたが、次の対象の起点は 0.2 に進んでいる
    assert store.offer_interval(captured(0.3)) is False
    assert store.offer_interval(captured(0.4)) is True


def test_close_counts_unwritten_items_and_stops_accepting(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    store.offer_interval(captured(0.0))
    store.save_surface(0.1, jpeg_image())
    store.close(timeout_s=0.1)
    # 書き出しスレッドが無いので、キューに残った分は捨てた件数に数える
    assert store.dropped == 2
    assert store.offer_interval(captured(1.0)) is False
    assert store.save_surface(1.0, jpeg_image()) is None
    assert store.dropped == 4
    store.close(timeout_s=0.1)
    assert store.dropped == 4


# --- frames.jsonl の読み込み ---


def test_frame_record_row():
    record = FrameRecord(
        t=1.5, kind="surface", file="frames/surface/000001500.jpg", width=32, height=18
    )
    assert frame_record_row(record) == {
        "t": 1.5,
        "kind": "surface",
        "file": "frames/surface/000001500.jpg",
        "width": 32,
        "height": 18,
    }


def test_read_frame_records_and_latest_frame_t(tmp_path):
    root = make_run(tmp_path)
    paths = RunPaths(root)
    assert read_frame_records(paths) == []
    assert latest_frame_t(paths) is None
    (root / "frames.jsonl").write_text(
        '{"t": 1.0, "kind": "interval", "file": "frames/interval/000001000.jpg", "width": 64, "height": 36}\n'
        '{"t": 0.5, "kind": "surface", "file": "frames/surface/000000500.jpg", "width": 32, "height": 18}\n'
        '{"t": "bad", "kind": "interval"}\n'
        '{"t": 9.0, "kind": "interval", "file": "frames/interval/000009000.jpg", "width"',
        encoding="utf-8",
    )
    assert read_frame_records(paths) == [
        FrameRecord(1.0, "interval", "frames/interval/000001000.jpg", 64, 36),
        FrameRecord(0.5, "surface", "frames/surface/000000500.jpg", 32, 18),
    ]
    assert latest_frame_t(paths) == 1.0
