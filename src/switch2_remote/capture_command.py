"""Elgato 4K X の映像を受け取る ffmpeg.exe のコマンドを組み立て、標準出力からフレームを読み取る。

デバイスからは NV12 を device_fps で受け取り、ffmpeg の出力側のフィルター (-vf fps=N) で output_fps に間引く。
出力は NV12 の rawvideo のままとし、WSL2 側で 1 フレーム分ずつ固定長で読み取る。
DirectShow デバイスはプロセス排他のため、実行前に Elgato Studio / OBS 等を終了しておく。
"""

from collections.abc import Iterator
from typing import IO

DEVICE_NAME = "Elgato 4K X"


def build_command(
    ffmpeg: str, width: int, height: int, device_fps: int, output_fps: int
) -> tuple[list[str], int]:
    """ffmpeg.exe のコマンドラインと、1 フレームのバイト数を返す。

    入力側 (dshow) は -framerate device_fps と -pixel_format nv12 で受け取り、
    出力側は -vf fps=output_fps で間引いて -c:v rawvideo -pix_fmt nv12 で標準出力に書く。
    1 フレームのバイト数は NV12 の width * height * 3 // 2 で、output_fps によらない。
    """
    raise NotImplementedError


def iter_raw_frames(stream: IO[bytes], frame_bytes: int) -> Iterator[bytes]:
    """固定長のフレームを 1 枚ずつ返す。1 フレーム分そろわないまま出力が終わったら止まる。"""
    while True:
        raw = stream.read(frame_bytes)
        if len(raw) < frame_bytes:
            return
        yield raw
