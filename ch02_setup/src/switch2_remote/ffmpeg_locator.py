"""Windows 側にインストールした ffmpeg.exe を WSL2 から探す。

winget (`winget install --scope user Gyan.FFmpeg`) で入れた ffmpeg.exe を、
実行中の Windows ユーザー自身の %LOCALAPPDATA% 配下から探して最新版を選ぶ。
"""

import glob
import os
import re
import subprocess

FFMPEG_GLOB_UNDER_LOCALAPPDATA = (
    "Microsoft/WinGet/Packages/Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"
)
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def windows_localappdata() -> str:
    """実行中の Windows ユーザー自身の %LOCALAPPDATA% を WSL パスで返す (他ユーザーのプロファイルは探索しない)。

    cmd.exe はコンソールコードページで出力するため、UTF-8 出力を明示できる PowerShell 経由で取得する。
    """
    win_path = (
        subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::Out.Write($env:LOCALAPPDATA)",
            ],
            capture_output=True,
            check=True,
            cwd="/mnt/c",
        )
        .stdout.decode("utf-8")
        .strip()
    )
    return subprocess.run(
        ["wslpath", "-u", win_path], capture_output=True, text=True, check=True
    ).stdout.strip()


def version_key(ffmpeg_path: str) -> tuple[int, ...]:
    """`ffmpeg-9.0.1-full_build` のようなディレクトリ名から数値の版番号を取り出し、数値順で比較できる key にする。"""
    build_dir = os.path.basename(os.path.dirname(os.path.dirname(ffmpeg_path)))
    match = re.search(r"ffmpeg-(\d+(?:\.\d+)*)", build_dir)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def find_ffmpeg() -> str:
    """自ユーザーの winget 導入先から最新版の ffmpeg.exe を決定的に選ぶ。"""
    candidates = sorted(
        glob.glob(os.path.join(windows_localappdata(), FFMPEG_GLOB_UNDER_LOCALAPPDATA)),
        key=version_key,
    )
    if not candidates:
        raise SystemExit(
            "ffmpeg.exe が見つからない。Windows 側で `winget install --scope user Gyan.FFmpeg` を実行する"
        )
    return candidates[-1]
