import io
import re
import time

import pytest

from switch2_remote import capture, frame_buffer
from switch2_remote.capture import Capture

FRAME_BYTES = 1280 * 720 * 3 // 2  # 1280x720 の NV12 の 1 フレームのバイト数
WAIT_TIMEOUT = 5.0  # 読み取りのスレッドが ffmpeg の出力を最後まで読むのを待つ上限 (秒)
POLL_INTERVAL = 0.01  # 読み取りが終わったかを確かめ直す間隔 (秒)


def nv12_frame(index: int) -> bytes:
    """index ごとに中身の異なる 1280x720 の NV12 の 1 フレーム (全バイトが index + 1) を返す。"""
    return bytes([index + 1]) * FRAME_BYTES


class FakeFfmpegProcess:
    """subprocess.Popen の代わり。標準出力から決まったバイト列を読ませ、標準エラーは空にする。"""

    def __init__(self, stdout: bytes):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(b"")
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return -9 if self.killed else 0


@pytest.fixture
def ffmpeg(monkeypatch):
    """capture の find_ffmpeg と subprocess.Popen を偽物に差し替え、Popen の呼び出しを記録する。

    起動する ffmpeg の標準出力は ffmpeg.stdout (既定は空) を読ませる。
    """

    class Calls:
        def __init__(self):
            self.args: list[list[str]] = []
            self.stdout = b""

    calls = Calls()

    def fake_popen(args, **kwargs):
        calls.args.append(list(args))
        return FakeFfmpegProcess(calls.stdout)

    monkeypatch.setattr(capture, "find_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr(capture.subprocess, "Popen", fake_popen)
    return calls


def wait_until_ended(cap: Capture) -> str:
    """status() が ended=True になるまで POLL_INTERVAL 秒ごとに確かめ直し、そのときの status() を返す。

    WAIT_TIMEOUT 秒を過ぎても ended=True にならなければ、テストを失敗させる。
    """
    deadline = time.monotonic() + WAIT_TIMEOUT
    while True:
        status = cap.status()
        if "ended=True" in status:
            return status
        if time.monotonic() >= deadline:
            pytest.fail(f"capture did not end within {WAIT_TIMEOUT} seconds: {status}")
        time.sleep(POLL_INTERVAL)


def test_status_without_frames_has_no_buffered_count(ffmpeg):
    cap = Capture(ffmpeg_fps=30, process_fps=10)
    status = wait_until_ended(cap)
    assert status == (
        "ok frames=0 latest_age=none ffmpeg_fps=30 process_fps=10 "
        "ffmpeg_drops=0 ended=True"
    )
    cap.close()


def test_status_after_four_frames_has_no_buffered_count(ffmpeg):
    ffmpeg.stdout = b"".join(nv12_frame(i) for i in range(4))
    cap = Capture(ffmpeg_fps=30, process_fps=10)
    status = wait_until_ended(cap)
    assert "buffered" not in status
    assert re.fullmatch(
        r"ok frames=4 latest_age=\d+\.\d{2}s ffmpeg_fps=30 process_fps=10 "
        r"ffmpeg_drops=0 ended=True",
        status,
    )
    cap.close()


def test_capture_holds_only_newest_kept_frame_in_latest_frame(ffmpeg, monkeypatch):
    # Capture は、間引きで残したフレームのうち最新の 1 枚を frame_buffer.LatestFrame で持つ
    assert capture.LatestFrame is frame_buffer.LatestFrame
    created = []

    class RecordingLatestFrame(frame_buffer.LatestFrame):
        """作られたインスタンスと、update で渡されたフレームの数を記録する LatestFrame。"""

        def __init__(self):
            super().__init__()
            self.update_count = 0
            created.append(self)

        def update(self, now, frame):
            self.update_count += 1
            super().update(now, frame)

    monkeypatch.setattr(capture, "LatestFrame", RecordingLatestFrame)
    ffmpeg.stdout = b"".join(nv12_frame(i) for i in range(4))
    cap = Capture(ffmpeg_fps=30, process_fps=10)
    wait_until_ended(cap)
    # 30fps から 10fps への間引きでは 4 枚中 0 枚目と 3 枚目を残し、持つのは最後に残した 3 枚目だけ
    assert len(created) == 1
    latest_frame = created[0]
    assert latest_frame.update_count == 2
    assert latest_frame.latest().tobytes() == nv12_frame(3)
    assert latest_frame.latest_time() is not None
    cap.close()
