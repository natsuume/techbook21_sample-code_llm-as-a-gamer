import json
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.frame_store import (
    FrameRecord,
    FrameStore,
    advance_interval_schedule,
    frame_record_row,
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

CAPTURE_FPS = (
    10  # キャプチャーがフレームを残すレート (switch2_remote.capture の処理レート)
)
DURATION_S = 60  # レートを確かめる区間の長さ (秒)


def capture_times(start: float, count: int) -> list[float]:
    """start から 0.1 秒ごとの、ミリ秒に丸めた count 個のフレームの時刻。"""
    return [round(start + i / CAPTURE_FPS, 3) for i in range(count)]


def saved_times(times: list[float], interval_fps: float) -> list[float]:
    """advance_interval_schedule で、times のうち保存の対象になるフレームの時刻を返す。"""
    next_due: float | None = None
    saved = []
    for t in times:
        due, next_due = advance_interval_schedule(next_due, t, interval_fps)
        if due:
            saved.append(t)
    return saved


def test_first_frame_is_always_saved():
    due, next_due = advance_interval_schedule(None, 3.0, 5.0)
    assert due is True
    assert next_due == pytest.approx(3.2)


def test_schedule_advances_from_previous_due_time_not_from_frame_time():
    # 予定時刻 1.0 に 0.05 秒遅れたフレームを保存しても、次の予定時刻は 1.05 + 0.2 ではなく 1.0 + 0.2
    due, next_due = advance_interval_schedule(1.0, 1.05, 5.0)
    assert due is True
    assert next_due == pytest.approx(1.2)
    # 予定時刻の前のフレームは対象でなく、予定時刻は変わらない
    assert advance_interval_schedule(1.2, 1.1, 5.0) == (False, 1.2)


def test_rounded_frame_time_just_before_due_time_counts():
    # t はミリ秒に丸めた値なので、予定時刻の 0.5 ミリ秒前までは予定時刻に達したものとして扱う
    due, _ = advance_interval_schedule(0.30000000000000004, 0.3, 10.0)
    assert due is True
    assert advance_interval_schedule(0.3, 0.299, 10.0)[0] is False


@pytest.mark.parametrize("interval_fps", [3, 5, 7, 9, 10])
def test_saved_rate_matches_interval_fps(interval_fps):
    times = capture_times(0.0, DURATION_S * CAPTURE_FPS)
    saved = saved_times(times, interval_fps)
    assert abs(len(saved) - DURATION_S * interval_fps) <= 1
    assert saved[0] == 0.0


@pytest.mark.parametrize("interval_fps", [5, 9, 10])
def test_saved_rate_matches_interval_fps_with_arrival_jitter(interval_fps):
    # キャプチャーの時刻は受信した時刻なので、0.1 秒の格子から前後に最大 10 ミリ秒ずれる
    jitter = random.Random(0)
    times = [
        round(i / CAPTURE_FPS + jitter.uniform(-0.01, 0.01), 3)
        for i in range(1, DURATION_S * CAPTURE_FPS + 1)
    ]
    saved = saved_times(times, interval_fps)
    assert abs(len(saved) - DURATION_S * interval_fps) <= 2


def test_frame_just_before_due_time_is_followed_by_next_frame_without_reset():
    # 予定時刻 1.0 の直前 (0.995) のフレームを逃しても、次の 1.098 を保存し、次の予定時刻は 1.1 のまま進む
    assert advance_interval_schedule(1.0, 0.995, 10.0) == (False, 1.0)
    due, next_due = advance_interval_schedule(1.0, 1.098, 10.0)
    assert due is True
    assert next_due == pytest.approx(1.1)


def test_no_burst_after_frames_stop_and_resume():
    # 0〜2 秒のフレームの後、10 秒まで途切れてから再開する
    times = capture_times(0.0, 21) + capture_times(10.0, 21)
    saved = saved_times(times, 5.0)
    after_gap = [t for t in saved if t >= 10.0]
    # 途切れた分をまとめて保存せず、再開したフレームから 0.2 秒ごとに保存する
    assert after_gap[:3] == [10.0, 10.2, 10.4]
    assert len([t for t in saved if 10.0 <= t < 10.2]) == 1


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


def test_failed_surface_write_releases_reservation(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    surface_dir = root / "frames" / "surface"
    # 画像を書くディレクトリーをファイルに置き換え、書き込みを失敗させる
    surface_dir.rmdir()
    surface_dir.write_text("not a directory", encoding="utf-8")
    image = jpeg_image()
    assert store.save_surface(3.0, image) == "frames/surface/000003000.jpg"
    assert store.write_queued() == 0
    assert store.dropped == 1
    # 書けなかった t は予約から外し、後の要求で入れ直す
    surface_dir.unlink()
    surface_dir.mkdir()
    assert store.save_surface(3.0, image) == "frames/surface/000003000.jpg"
    assert store.write_queued() == 1
    assert (surface_dir / "000003000.jpg").read_bytes() == image.jpeg
    assert [row["t"] for row in frames_rows(root)] == [3.0]


def test_surface_frames_discarded_at_close_are_not_written(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    # 予約したパスを返すが、書く前に close で捨てられると画像は書かれない
    assert store.save_surface(4.0, jpeg_image()) == "frames/surface/000004000.jpg"
    store.close(timeout_s=0.1)
    assert store.dropped == 1
    assert not (root / "frames" / "surface" / "000004000.jpg").exists()


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
    # 0.2 は捨てたが、次の予定時刻は 0.4 に進んでいる
    assert store.offer_interval(captured(0.3)) is False
    assert store.offer_interval(captured(0.4)) is True


def test_offer_interval_follows_schedule_for_rate_not_dividing_capture_rate(tmp_path):
    root = make_run(tmp_path)
    store = FrameStore(
        RunPaths(root), interval_fps=3.0, interval_width=64, start_thread=False
    )
    times = capture_times(0.0, 30)
    offered = [t for t in times if store.offer_interval(captured(t))]
    # 3 fps: 予定時刻 0・1/3・2/3・1・… 以後の最初のフレームを保存し、3 秒で 9 枚
    assert offered == [0.0, 0.4, 0.7, 1.0, 1.4, 1.7, 2.0, 2.4, 2.7]


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
