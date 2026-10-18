"""Elgato 4K X の映像を受け取る ffmpeg.exe のコマンドを組み立て、標準出力からフレームを読み取る。

デバイスからは NV12 をデバイスの解像度と device_fps で受け取り、ffmpeg の出力側のフィルター
(-vf select=...,scale=W:H) で output_fps に間引いて出力の解像度に縮小する。
出力は NV12 の rawvideo のままとし、WSL2 側で 1 フレーム分ずつ固定長で読み取る。
DirectShow デバイスはプロセス排他のため、実行前に Elgato Studio / OBS 等を終了しておく。
"""

from collections.abc import Iterator
from typing import IO

DEVICE_NAME = "Elgato 4K X"


def select_filter(output_fps: int) -> str:
    """時間を 1/output_fps 秒ごとの区間に分け、各区間で最初に届いたフレームだけを残す select フィルターを返す。

    fps フィルターは入力が途切れると前のフレームを複製して間隔を埋めるため使わない。
    select は届いたフレームを選ぶだけで、複製を作らない。
    区間の番号は floor(t * output_fps + 0.5) で求める。0.5 を足して区間の境界を半区間ずらし、
    1/60 秒刻みのフレームの時刻が境界に重なって浮動小数の誤差で区間がずれないようにする。
    式の中の , はフィルターの区切りと区別するため \\, と書く。
    """
    slot = f"floor(t*{output_fps}+0.5)"
    prev_slot = f"floor(prev_selected_t*{output_fps}+0.5)"
    return f"select='isnan(prev_selected_t)+gt({slot}\\,{prev_slot})'"


def build_command(
    ffmpeg: str,
    device_width: int,
    device_height: int,
    device_fps: int,
    output_width: int,
    output_height: int,
    output_fps: int,
) -> tuple[list[str], int]:
    """ffmpeg.exe のコマンドラインと、出力の 1 フレームのバイト数を返す。

    入力側 (dshow) は -video_size device_width x device_height、-framerate device_fps、-pixel_format nv12 で受け取る。
    出力側は -vf <select_filter(output_fps)>,scale=output_width:output_height で間引いて縮小し
    (出力の解像度がデバイスと同じでも scale を付ける)、-c:v rawvideo -pix_fmt nv12 で標準出力に書く。
    -fps_mode passthrough で、出力側でもフレームの複製や間引きをしない。
    1 フレームのバイト数は NV12 の output_width * output_height * 3 // 2 で、output_fps によらない。
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "repeat+warning",
        "-f",
        "dshow",
        "-video_size",
        f"{device_width}x{device_height}",
        "-framerate",
        str(device_fps),
        "-pixel_format",
        "nv12",
        "-i",
        f"video={DEVICE_NAME}",
        # フィルターはデコード済みのフレームにかかるため、-c:v copy ではなく rawvideo で出力する
        "-vf",
        f"{select_filter(output_fps)},scale={output_width}:{output_height}",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "rawvideo",
        "-pix_fmt",
        "nv12",
        "-f",
        "rawvideo",
        "-",
    ]
    # NV12 は輝度 (幅 x 高さ) の後に、縦横半分の色差が 2 成分並ぶ
    frame_bytes = output_width * output_height * 3 // 2
    return cmd, frame_bytes


def iter_raw_frames(stream: IO[bytes], frame_bytes: int) -> Iterator[bytes]:
    """固定長のフレームを 1 枚ずつ返す。1 フレーム分そろわないまま出力が終わったら止まる。"""
    while True:
        raw = stream.read(frame_bytes)
        if len(raw) < frame_bytes:
            return
        yield raw
