"""撮影した PNG を保存するパス (frames/ の中の連番) を決める。

ファイル名は番号を 6 桁でゼロ埋めした `000001.png` の形で、既存の最大の番号 + 1 を次の番号にする。
"""

from collections.abc import Iterable
from pathlib import Path

FRAME_NUMBER_DIGITS = 6
FRAME_SUFFIX = ".png"


def frame_number(file_name: str) -> int | None:
    """連番の PNG のファイル名から番号を返す。連番の PNG でなければ None。

    拡張子が .png で、拡張子を除いた部分がすべて ASCII の数字のものを連番の PNG とする。
    例: "000012.png" は 12、"notes.txt" と "abc.png" と "12a.png" は None。
    """
    raise NotImplementedError


def next_frame_number(file_names: Iterable[str]) -> int:
    """file_names のうち連番の PNG の最大の番号 + 1 を返す。連番の PNG が無ければ 1。"""
    raise NotImplementedError


def frame_file_name(number: int) -> str:
    """番号を FRAME_NUMBER_DIGITS 桁でゼロ埋めしたファイル名を返す (例: 7 は "000007.png")。"""
    raise NotImplementedError


def next_frame_path(frames_dir: Path) -> Path:
    """frames_dir の中のファイル名から次の連番を決め、frames_dir/<ファイル名> を返す。

    ファイルは作らない。frames_dir は存在するディレクトリーを渡す。
    """
    raise NotImplementedError
