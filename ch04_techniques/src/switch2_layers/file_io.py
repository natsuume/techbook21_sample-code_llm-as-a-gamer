"""層の間の受け渡しに使うファイルの読み書き。

ファイルは 2 種類に分ける:
    置き換えて書くもの (JSON・Markdown・画像)
        同じディレクトリーの一時ファイルに書いてから os.replace で置き換える。
        読む側は、置き換える前の内容か置き換えた後の内容のどちらかだけを読み、書きかけの内容を読まない
    1 行ずつ追記するもの (JSON Lines)
        1 行分 (末尾の改行を含む) を 1 回の write で書いてから flush する。
        読む側は、改行で終わらない最後の行 (書いている途中の行) を読み飛ばす
文書の版の比較には SHA-256 (64 桁の小文字の 16 進) を使う。
"""

from collections.abc import Mapping
from pathlib import Path

TEMP_SUFFIX = ".tmp"  # 置き換えのための一時ファイルの名前の末尾


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """data を path に置き換えて書く。

    path と同じディレクトリーに一時ファイル (名前は `.<path の名前>.<ランダムな文字列>.tmp`) を作って data を書き、
    flush と os.fsync をしてから os.replace(一時ファイル, path) で置き換える。
    親のディレクトリーは作らない (無ければ FileNotFoundError)。
    書き込みか置き換えに失敗したら、一時ファイルを消してから例外をそのまま出す (path は変わらない)。
    """
    raise NotImplementedError


def write_text_atomic(path: Path, text: str) -> None:
    """text を UTF-8 にして write_bytes_atomic で path に置き換えて書く。"""
    raise NotImplementedError


def format_json(value: object) -> str:
    """value を置き換えて書く JSON の文字列にする。

    json.dumps(value, ensure_ascii=False, indent=2, default=str) の末尾に改行を足したもの。
    JSON にできない値 (Path など) は str() にする。
    """
    raise NotImplementedError


def write_json_atomic(path: Path, value: object) -> None:
    """format_json(value) を write_text_atomic で path に置き換えて書く。"""
    raise NotImplementedError


def format_json_line(row: Mapping[str, object]) -> str:
    """row を JSON Lines の 1 行にする。

    json.dumps(row, ensure_ascii=False, default=str) の末尾に改行を 1 つ足したもの (行の中に改行を含まない)。
    """
    raise NotImplementedError


def append_json_line(path: Path, row: Mapping[str, object]) -> None:
    """format_json_line(row) を path に追記し、flush する。

    ファイルが無ければ作る (親のディレクトリーは作らない)。1 行を 1 回の write で書く。
    複数のスレッドから同じファイルに書くときは、呼び出し側がロックで順番を守る。
    """
    raise NotImplementedError


def read_json_lines(path: Path) -> list[dict[str, object]]:
    """JSON Lines の path を読み、改行で終わる行の JSON の object を、書かれた順に返す。

    - ファイルが無ければ空のリストを返す
    - 改行で終わらない最後の行 (書いている途中の行) は読み飛ばす
    - 空白だけの行、UTF-8 として読めない行、JSON として読めない行、object でない行は読み飛ばす
    """
    raise NotImplementedError


def read_text_or_empty(path: Path) -> str:
    """path を UTF-8 で読んだ文字列を返す。無い・読めない (OSError、UnicodeDecodeError) ときは空文字列を返す。"""
    raise NotImplementedError


def sha256_bytes(data: bytes) -> str:
    """data の SHA-256 (hashlib.sha256) を 64 桁の小文字の 16 進で返す。"""
    raise NotImplementedError


def sha256_file(path: Path) -> str | None:
    """path のファイルの中身の SHA-256 (sha256_bytes) を返す。path が通常のファイルでなければ (無い場合を含む) None を返す。"""
    raise NotImplementedError
