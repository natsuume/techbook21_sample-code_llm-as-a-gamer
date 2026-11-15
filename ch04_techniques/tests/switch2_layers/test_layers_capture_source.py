import threading
from pathlib import Path

import numpy as np
from switch2_remote.frame_buffer import LatestFrame

from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore
from switch2_layers.hardware.capture_source import (
    CaptureSource,
    LatestCapture,
    SwitchCaptureReader,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths

ORIGIN = 1000.0  # 偽の time.monotonic の起点


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeReader:
    """LatestFrameReader の偽物。latest に入れた値を返す。"""

    def __init__(self):
        self.latest: LatestCapture | None = None
        self.ended = False

    def read_latest(self) -> LatestCapture | None:
        return self.latest

    def is_ended(self) -> bool:
        return self.ended


class FakeCapture:
    """switch2_remote.capture.Capture の公開の属性 (lock・latest_frame・ended) だけを持つ偽物。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.latest_frame: LatestFrame[np.ndarray] = LatestFrame()
        self.ended = False


def nv12() -> np.ndarray:
    return np.full((54, 64), 128, np.uint8)


def make_run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    (root / "frames" / "surface").mkdir()
    return root


# --- SwitchCaptureReader (第 2 章の Capture の公開の属性から読む) ---


def test_switch_capture_reader_reads_latest_frame():
    capture = FakeCapture()
    reader = SwitchCaptureReader(capture)
    assert reader.read_latest() is None
    frame = nv12()
    capture.latest_frame.update(1001.5, frame)
    latest = reader.read_latest()
    assert latest is not None
    assert latest.monotonic_time == 1001.5
    assert latest.nv12 is frame


def test_switch_capture_reader_reads_ended():
    capture = FakeCapture()
    reader = SwitchCaptureReader(capture)
    assert reader.is_ended() is False
    capture.ended = True
    assert reader.is_ended() is True


# --- CaptureSource.poll_once ---


def test_poll_once_passes_new_frames_to_buffer_store_and_callback(tmp_path):
    root = make_run(tmp_path)
    reader = FakeReader()
    clock = RunClock(FakeMonotonic(ORIGIN))
    buffer = FrameBuffer()
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    received: list[CapturedFrame] = []
    source = CaptureSource(
        reader, clock, buffer, store, on_frame=received.append, start_thread=False
    )
    assert source.poll_once() is None
    assert source.latest_t() is None

    frame = nv12()
    reader.latest = LatestCapture(monotonic_time=ORIGIN + 1.2345, nv12=frame)
    captured = source.poll_once()
    assert captured is not None
    assert captured.t == 1.235
    assert captured.nv12 is frame
    assert buffer.latest() == captured
    assert received == [captured]
    assert source.latest_t() == 1.235
    # 一定間隔の保存にも渡している
    assert store.write_queued() == 1
    assert (root / "frames" / "interval" / "000001235.jpg").is_file()


def test_poll_once_ignores_the_same_frame(tmp_path):
    root = make_run(tmp_path)
    reader = FakeReader()
    clock = RunClock(FakeMonotonic(ORIGIN))
    buffer = FrameBuffer()
    store = FrameStore(
        RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
    )
    source = CaptureSource(reader, clock, buffer, store, start_thread=False)
    reader.latest = LatestCapture(monotonic_time=ORIGIN + 1.0, nv12=nv12())
    assert source.poll_once() is not None
    assert source.poll_once() is None
    assert len(buffer.frames()) == 1
    reader.latest = LatestCapture(monotonic_time=ORIGIN + 1.1, nv12=nv12())
    assert source.poll_once() is not None
    assert [item.t for item in buffer.frames()] == [1.0, 1.1]


def test_close_without_thread(tmp_path):
    root = make_run(tmp_path)
    source = CaptureSource(
        FakeReader(),
        RunClock(FakeMonotonic(ORIGIN)),
        FrameBuffer(),
        FrameStore(
            RunPaths(root), interval_fps=5.0, interval_width=64, start_thread=False
        ),
        start_thread=False,
    )
    source.close()
