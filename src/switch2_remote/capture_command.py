"""Elgato 4K X の映像を受け取る ffmpeg.exe のコマンドを組み立て、標準出力からフレームを読み取る。

デバイスの NV12 出力を無変換で転送し、WSL2 側で 1 フレーム分ずつ固定長で読み取る。
DirectShow デバイスはプロセス排他のため、実行前に Elgato Studio / OBS 等を終了しておく。
"""

from collections.abc import Iterator
from typing import IO

DEVICE_NAME = "Elgato 4K X"


def build_command(
    ffmpeg: str, width: int, height: int, fps: int
) -> tuple[list[str], int]:
    """NV12 無変換転送の ffmpeg.exe のコマンドラインと、1 フレームのバイト数を返す。"""
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "repeat+warning",
        "-f",
        "dshow",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-pixel_format",
        "nv12",
        "-i",
        f"video={DEVICE_NAME}",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "copy",
        "-f",
        "rawvideo",
        "-",
    ]
    # NV12 は輝度 (width x height) の後に、縦横半分の色差が 2 成分並ぶ
    frame_bytes = width * height * 3 // 2
    return cmd, frame_bytes


def iter_raw_frames(stream: IO[bytes], frame_bytes: int) -> Iterator[bytes]:
    """固定長のフレームを 1 枚ずつ返す。1 フレーム分そろわないまま出力が終わったら止まる。"""
    while True:
        raw = stream.read(frame_bytes)
        if len(raw) < frame_bytes:
            return
        yield raw
