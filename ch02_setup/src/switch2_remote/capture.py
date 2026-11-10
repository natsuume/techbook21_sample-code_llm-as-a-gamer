"""ffmpeg.exe を起動したまま保持し、処理レートに間引いたフレームを直近の一定時間分保持して撮影する。

デバイスから DEVICE_WIDTH x DEVICE_HEIGHT、DEVICE_FPS で受け取った映像を、ffmpeg の出力側で
ffmpeg_fps に間引いて出力の解像度 (output_width x output_height) に縮小する。
受信したフレームをさらに process_fps に間引いて (frame_buffer.next_keep)、
直近 buffer_seconds 秒分を FrameBuffer に保持する。ffmpeg_fps は起動後に変えない。
shot はバッファーの最新フレームを待たずに PNG 保存する。
"""

import subprocess
import threading
import time

import cv2
import numpy as np

from switch2_remote.capture_command import build_command, iter_raw_frames
from switch2_remote.ffmpeg_locator import find_ffmpeg
from switch2_remote.frame_buffer import (
    FrameBuffer,
    format_latest_age,
    initial_accumulator,
    next_keep,
)

DEVICE_FPS = 60  # デバイスから受け取るフレームレート (dshow の -framerate)
# デバイスから受け取る解像度 (dshow の -video_size)
DEVICE_WIDTH, DEVICE_HEIGHT = 1280, 720


class Capture:
    """ffmpeg.exe からフレームを読み続け、間引いたフレームのリングバッファーと受信状況を保持する。

    フレームの読み取りと ffmpeg の stderr の読み捨ては、それぞれバックグラウンドのスレッドで行う。
    状態の読み書きは 1 つのロックの中で行う。

    主な属性:
        ffmpeg_fps    ffmpeg の出力のフレームレート。起動後は変えない (commands.parse_fps の上限に使う)
        process_fps   バッファーに残すフレームレート。set_process_fps で変える
        output_width, output_height  ffmpeg の出力の解像度。起動後は変えない
    """

    def __init__(
        self,
        ffmpeg_fps: int,
        process_fps: int,
        buffer_seconds: int,
        output_width: int,
        output_height: int,
    ):
        """ffmpeg.exe を起動し、フレームの読み取りと stderr の読み捨てのスレッドを始める。

        バッファーの時刻には time.monotonic() を使う。
        """
        cmd, frame_bytes = build_command(
            find_ffmpeg(),
            DEVICE_WIDTH,
            DEVICE_HEIGHT,
            DEVICE_FPS,
            output_width,
            output_height,
            ffmpeg_fps,
        )
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=8 << 20,
            cwd="/mnt/c",
        )
        self.frame_bytes = frame_bytes
        self.output_width = output_width
        self.output_height = output_height
        self.ffmpeg_fps = ffmpeg_fps
        self.process_fps = process_fps
        self.lock = threading.Lock()
        self.buffer: FrameBuffer[np.ndarray] = FrameBuffer(buffer_seconds)
        self.accumulator = initial_accumulator(ffmpeg_fps, process_fps)
        self.frame_no = 0
        self.ffmpeg_drops = 0
        self.ended = False
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_frames(self):
        """フレームを受信するたびに受信数を数え、間引きで残すフレームだけを NV12 の配列としてバッファーに追加する。

        出力が終わったら ended を True にする。
        """
        for raw in iter_raw_frames(self.proc.stdout, self.frame_bytes):
            with self.lock:
                self.frame_no += 1
                self.accumulator, keep = next_keep(
                    self.accumulator, self.ffmpeg_fps, self.process_fps
                )
                if keep:
                    nv12 = np.frombuffer(raw, np.uint8).reshape(
                        self.output_height * 3 // 2, self.output_width
                    )
                    self.buffer.append(time.monotonic(), nv12)
        with self.lock:
            self.ended = True

    def _read_stderr(self):
        # 読み捨てないとパイプが詰まり ffmpeg が止まる。フレーム落ちの警告だけ数える
        for line in self.proc.stderr:
            if b"frame dropped" in line:
                with self.lock:
                    self.ffmpeg_drops += 1

    def set_process_fps(self, fps: int) -> str:
        """処理レートを fps に変え、累積値を initial_accumulator(ffmpeg_fps, fps) に戻す。

        fps の範囲 (1〜ffmpeg_fps) は呼び出し側 (commands.parse_fps) で確かめる。
        応答: ok fps=<fps>
        """
        with self.lock:
            self.process_fps = fps
            self.accumulator = initial_accumulator(self.ffmpeg_fps, fps)
        return f"ok fps={fps}"

    def shot(self, path: str) -> str:
        """バッファーの最新フレームを待たずに path へ PNG 保存する。

        応答: 成功時は `ok <path>`。ffmpeg が終了していれば `error capture ended`、
        バッファーが空なら `error no frame yet`、書き込みに失敗したら `error failed to write <path>`
        """
        with self.lock:
            ended = self.ended
            nv12 = self.buffer.latest()
        if ended:
            return "error capture ended"
        if nv12 is None:
            return "error no frame yet"
        # 色変換と書き込みはロックの外で行い、その間もフレームの読み取りを止めない
        if not cv2.imwrite(path, cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)):
            return f"error failed to write {path}"
        return f"ok {path}"

    def status(self) -> str:
        """受信状況を 1 行で返す。

        応答: ok frames=<受信フレーム数> buffered=<バッファー内の枚数> latest_age=<秒>s ffmpeg_fps=<N>
        process_fps=<N> ffmpeg_drops=<N> ended=<bool>
        latest_age は最新フレームを追加してからの経過秒 (小数第 2 位まで)。バッファーが空なら none
        """
        with self.lock:
            latest_age = format_latest_age(self.buffer.latest_time(), time.monotonic())
            return (
                f"ok frames={self.frame_no} buffered={len(self.buffer)} "
                f"latest_age={latest_age} "
                f"ffmpeg_fps={self.ffmpeg_fps} process_fps={self.process_fps} "
                f"ffmpeg_drops={self.ffmpeg_drops} ended={self.ended}"
            )

    def close(self):
        self.proc.kill()
        self.proc.wait()
