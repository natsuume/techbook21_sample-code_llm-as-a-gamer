import numpy as np

from switch2_layers.frames.frame_buffer import (
    DEFAULT_BUFFER_SECONDS,
    CapturedFrame,
    FrameBuffer,
    frame_size,
)


def nv12(width: int = 64, height: int = 36) -> np.ndarray:
    return np.full((height * 3 // 2, width), 128, np.uint8)


def frame(t: float) -> CapturedFrame:
    return CapturedFrame(t=t, nv12=nv12())


def test_default_buffer_is_longer_than_surface_window():
    assert DEFAULT_BUFFER_SECONDS >= 2.0


def test_frame_size():
    assert frame_size(CapturedFrame(t=0.0, nv12=nv12(64, 36))) == (64, 36)
    assert frame_size(CapturedFrame(t=0.0, nv12=nv12(1280, 720))) == (1280, 720)


def test_empty_buffer():
    buffer = FrameBuffer()
    assert buffer.frames() == []
    assert buffer.latest() is None


def test_add_keeps_frames_in_order():
    buffer = FrameBuffer()
    frames = [frame(0.1), frame(0.2), frame(0.3)]
    for item in frames:
        assert buffer.add(item) is True
    assert buffer.frames() == frames
    assert buffer.latest() == frames[-1]


def test_add_drops_frames_older_than_max_age():
    buffer = FrameBuffer(max_age_s=3.0)
    for t in [0.0, 0.5, 1.0, 2.0, 3.0, 4.0]:
        buffer.add(frame(t))
    # 4.0 - 3.0 = 1.0 より小さい t を捨てる (ちょうど 1.0 は残す)
    assert [item.t for item in buffer.frames()] == [1.0, 2.0, 3.0, 4.0]


def test_add_ignores_frames_not_newer_than_latest():
    buffer = FrameBuffer()
    buffer.add(frame(1.0))
    assert buffer.add(frame(1.0)) is False
    assert buffer.add(frame(0.5)) is False
    assert [item.t for item in buffer.frames()] == [1.0]


def test_frames_returns_a_copy():
    buffer = FrameBuffer()
    buffer.add(frame(1.0))
    copied = buffer.frames()
    copied.clear()
    assert len(buffer.frames()) == 1
