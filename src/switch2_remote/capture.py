"""ffmpeg.exe を起動したまま保持し、最新フレームと静止判定から画面を撮影する。"""

import subprocess
import threading
import time

import cv2
import numpy as np

from switch2_remote.capture_command import build_command, iter_raw_frames
from switch2_remote.ffmpeg_locator import find_ffmpeg
from switch2_remote.still_detection import motion_between, next_still_count

WIDTH, HEIGHT = 1280, 720
FPS = 60
ANALYSIS_SIZE = (320, 180)  # 静止判定に使う縮小サイズ
# 静止とみなす連続フレーム数。ゲーム画面の一瞬の静止を撮影の合図にしないための窓
STABLE_FRAMES = 10
SHOT_MIN_DELAY = (
    0.2  # 入力直後の、まだ画面が変わり始めていない静止を拾わないための待ち (秒)
)


class Capture:
    """ffmpeg.exe からフレームを読み続け、最新フレームと連続静止フレーム数を保持する。"""

    def __init__(self):
        cmd, frame_bytes = build_command(find_ffmpeg(), WIDTH, HEIGHT, FPS)
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=8 << 20,
            cwd="/mnt/c",
        )
        self.frame_bytes = frame_bytes
        self.lock = threading.Lock()
        self.latest: np.ndarray | None = None
        self.frame_no = 0
        self.still_count = 0
        self.ffmpeg_drops = 0
        self.ended = False
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_frames(self):
        prev_small = None
        for raw in iter_raw_frames(self.proc.stdout, self.frame_bytes):
            nv12 = np.frombuffer(raw, np.uint8).reshape(HEIGHT * 3 // 2, WIDTH)
            # NV12 の先頭 HEIGHT 行は輝度 (グレースケール) なので、色変換せずに静止判定に使える
            small = cv2.resize(
                nv12[:HEIGHT], ANALYSIS_SIZE, interpolation=cv2.INTER_AREA
            )
            motion = motion_between(prev_small, small)
            prev_small = small
            with self.lock:
                self.latest = nv12
                self.frame_no += 1
                self.still_count = next_still_count(self.still_count, motion)
        with self.lock:
            self.ended = True

    def _read_stderr(self):
        # 読み捨てないとパイプが詰まり ffmpeg が止まる。フレーム落ちの警告だけ数える
        for line in self.proc.stderr:
            if b"frame dropped" in line:
                with self.lock:
                    self.ffmpeg_drops += 1

    def shot(self, path: str, timeout: float) -> str:
        start = time.monotonic()
        with self.lock:
            request_frame = self.frame_no
        time.sleep(SHOT_MIN_DELAY)
        # 待ちの間に届いたフレームを静止の数に含めないよう、静止を数える起点は待ちの後に取る
        with self.lock:
            still_start_frame = self.frame_no
        settled = False
        nv12 = None
        while time.monotonic() - start < timeout:
            with self.lock:
                if self.ended:
                    return "error capture ended"
                settled = (
                    self.frame_no - still_start_frame >= STABLE_FRAMES
                    and self.still_count >= STABLE_FRAMES
                )
                if settled:
                    # 静止を確かめたフレームそのものを保存するため、同じロックの中で取り出す
                    nv12 = self.latest
                    break
            time.sleep(0.02)
        if nv12 is None:
            with self.lock:
                # 待ち時間が SHOT_MIN_DELAY 以下だとループを通らないため、ここでも終了を確かめる
                if self.ended:
                    return "error capture ended"
                # キャプチャが止まっている場合に要求前の古い画面を返さない
                if self.frame_no == request_frame:
                    return "error no new frame since request"
                nv12 = self.latest
        if not cv2.imwrite(path, cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)):
            return f"error failed to write {path}"
        return f"ok {path} settled={settled} waited={time.monotonic() - start:.2f}s"

    def status(self) -> str:
        with self.lock:
            return f"ok frames={self.frame_no} ffmpeg_drops={self.ffmpeg_drops} ended={self.ended}"

    def close(self):
        self.proc.kill()
        self.proc.wait()
