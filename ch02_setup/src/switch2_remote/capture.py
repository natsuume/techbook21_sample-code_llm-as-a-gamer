"""ffmpeg.exe を起動したまま保持し、処理レートに間引いたフレームのうち最新の 1 枚を保持して撮影する。

デバイスから DEVICE_WIDTH x DEVICE_HEIGHT、device_fps (DEVICE_FPS_CHOICES のどれか、既定 DEFAULT_DEVICE_FPS) で
受け取った映像を、ffmpeg の出力側で ffmpeg_fps に間引く。出力の解像度はデバイスと同じ DEVICE_WIDTH x DEVICE_HEIGHT。
受信したフレームをさらに process_fps に間引いて (frame_buffer.next_keep)、
残したフレームのうち最新の 1 枚を LatestFrame に持つ。ffmpeg_fps は起動後に変えない。
shot は持っている最新フレームを、新しいフレームを待たずに PNG 保存する。
"""

import subprocess
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np

from switch2_remote.capture_command import build_command, iter_raw_frames
from switch2_remote.ffmpeg_locator import find_ffmpeg
from switch2_remote.frame_buffer import (
    LatestFrame,
    format_latest_age,
    initial_accumulator,
    next_keep,
)

# デバイスから受け取るフレームレート (dshow の -framerate) として選べる値と、その既定値。
# PC との USB の接続の規格によっては、デバイスが 1280x720・NV12 を 30fps までしか出さないため、30 も選べるようにする
DEVICE_FPS_CHOICES = (30, 60)
DEFAULT_DEVICE_FPS = 60
# デバイスから受け取る解像度 (dshow の -video_size)
DEVICE_WIDTH, DEVICE_HEIGHT = 1280, 720


class Capture:
    """ffmpeg.exe からフレームを読み続け、間引いたフレームのうち最新の 1 枚と受信状況を保持する。

    フレームの読み取りと ffmpeg の stderr の読み捨ては、それぞれバックグラウンドのスレッドで行う。
    状態の読み書きは 1 つのロックの中で行う。

    主な属性:
        device_fps    デバイスから受け取るフレームレート。起動後は変えない
        ffmpeg_fps    ffmpeg の出力のフレームレート。起動後は変えない (commands.parse_fps の上限に使う)
        process_fps   間引いて残すフレームレート。set_process_fps で変える
    """

    def __init__(
        self,
        ffmpeg_fps: int,
        process_fps: int,
        new_session: bool = False,
        device_fps: int = DEFAULT_DEVICE_FPS,
        on_preview: Callable[[np.ndarray], None] | None = None,
    ):
        """ffmpeg.exe を起動し、フレームの読み取りと stderr の読み捨てのスレッドを始める。

        on_preview があれば、ffmpeg の全出力フレームを渡す。読み取りスレッドから呼ぶため、表示側は参照を受け取るだけにする。
        最新フレームの時刻には time.monotonic() を使う。
        new_session が True なら、ffmpeg を新しいセッション (プロセスグループ) で起動する。端末の Ctrl+C の
        SIGINT が ffmpeg に届かなくなるため、呼び出し側は Ctrl+C の後も撮影を続けられる。ffmpeg は close で終わらせる。
        device_fps はデバイスから受け取るフレームレート (DEVICE_FPS_CHOICES のどれか)。値と ffmpeg_fps 以上であることは
        呼び出し側 (daemon.parse_args など) で確かめる。デバイスが出せない値では ffmpeg.exe が開けずに終わり、ended が True になる。
        """
        cmd, frame_bytes = build_command(
            find_ffmpeg(),
            DEVICE_WIDTH,
            DEVICE_HEIGHT,
            device_fps,
            ffmpeg_fps,
        )
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=8 << 20,
            cwd="/mnt/c",
            start_new_session=new_session,
        )
        self.on_preview = on_preview
        self.frame_bytes = frame_bytes
        self.device_fps = device_fps
        self.ffmpeg_fps = ffmpeg_fps
        self.process_fps = process_fps
        self.lock = threading.Lock()
        self.latest_frame: LatestFrame[np.ndarray] = LatestFrame()
        self.accumulator = initial_accumulator(ffmpeg_fps, process_fps)
        self.frame_no = 0
        self.ffmpeg_drops = 0
        self.ended = False
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_frames(self):
        """フレームを受信するたびに受信数を数え、間引きで残すフレームだけを NV12 の配列にして最新フレームを置き換える。

        出力が終わったら ended を True にする。
        """
        for raw in iter_raw_frames(self.proc.stdout, self.frame_bytes):
            with self.lock:
                self.frame_no += 1
                self.accumulator, keep = next_keep(
                    self.accumulator, self.ffmpeg_fps, self.process_fps
                )
                if keep or self.on_preview is not None:
                    nv12 = np.frombuffer(raw, np.uint8).reshape(
                        DEVICE_HEIGHT * 3 // 2, DEVICE_WIDTH
                    )
                if keep:
                    self.latest_frame.update(time.monotonic(), nv12)
            if self.on_preview is not None:
                self.on_preview(nv12)
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
        """持っている最新フレームを、新しいフレームを待たずに path へ PNG 保存する。

        応答: 成功時は `ok <path>`。ffmpeg が終了していれば `error capture ended`、
        まだフレームが無ければ `error no frame yet`、書き込みに失敗したら `error failed to write <path>`
        """
        with self.lock:
            ended = self.ended
            nv12 = self.latest_frame.latest()
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

        応答: ok frames=<受信フレーム数> latest_age=<秒>s ffmpeg_fps=<N>
        process_fps=<N> ffmpeg_drops=<N> ended=<bool>
        latest_age は最新フレームを残してからの経過秒 (小数第 2 位まで)。まだフレームが無ければ none
        """
        with self.lock:
            latest_age = format_latest_age(
                self.latest_frame.latest_time(), time.monotonic()
            )
            return (
                f"ok frames={self.frame_no} latest_age={latest_age} "
                f"ffmpeg_fps={self.ffmpeg_fps} process_fps={self.process_fps} "
                f"ffmpeg_drops={self.ffmpeg_drops} ended={self.ended}"
            )

    def close(self):
        self.proc.kill()
        self.proc.wait()
