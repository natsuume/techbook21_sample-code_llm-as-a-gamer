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
from dataclasses import dataclass
from pathlib import Path

# 置き換えのための一時ファイルの名前は `.<置き換える先の名前>.<ランダムな文字列>.tmp`
# (先頭が TEMP_PREFIX、末尾が TEMP_SUFFIX)。文書を列挙する側は is_temporary_file_name でこの名前を除く
TEMP_PREFIX = "."
TEMP_SUFFIX = ".tmp"


def is_temporary_file_name(name: str) -> bool:
    """name (パスの要素 1 つ) が置き換えの一時ファイルの名前の規則 (先頭が `.` で末尾が `.tmp`) に当たれば True を返す。"""
    raise NotImplementedError


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """data を path に置き換えて書く。

    path と同じディレクトリーに一時ファイル (名前は `.<path の名前>.<ランダムな文字列>.tmp`。is_temporary_file_name が
    True になる名前) を作って data を書き、flush と os.fsync をしてから os.replace(一時ファイル, path) で置き換える。
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


@dataclass(frozen=True)
class JsonLinesChunk:
    """read_json_lines_from で読んだ分。

    rows         読んだ完全な行の JSON の object (書かれた順。読み飛ばす行の規則は read_json_lines と同じ)
    end_offset   読んだ最後の完全な行 (改行で終わる行) の末尾のバイト位置 (改行の次)。次はこの位置から読む。
                 完全な行が無ければ、読み始めた位置
    restarted    渡された位置がファイルの大きさを超えていた (または負だった) ため、先頭から読んだら True
    """

    rows: list[dict[str, object]]
    end_offset: int
    restarted: bool


def read_json_lines_from(path: Path, offset: int) -> JsonLinesChunk:
    """JSON Lines の path を、バイト位置 offset から読み、その時点でファイルにある完全な行 (改行で終わる行) をすべて返す。

    - 改行で終わらない最後の行 (書いている途中の行) は読まず、end_offset にも含めない (次に読むときに、完全な行として読む)
    - offset が負か、ファイルの大きさを超えていれば (ファイルを作り直した等)、先頭 (0) から読み、restarted を True にする
    - ファイルが無ければ、rows は空、end_offset は 0、restarted は offset > 0 なら True
    - 位置はバイトで数える (UTF-8 の多バイト文字を含む行でも、行の境目の位置になる)
    - 読み飛ばす行 (空白だけ・UTF-8 として読めない・JSON として読めない・object でない) も、完全な行なら end_offset を進める
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
