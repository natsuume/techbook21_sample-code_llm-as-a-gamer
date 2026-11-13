"""JSON Lines (1 行 1 件の JSON) のファイルを、改行で終わる行だけ読む。

記録のファイルは、第4章のエージェントが実行中に追記している途中のことがある。最後の行が改行で終わっていなければ
書いている途中とみなし、その行は読まない。読んだ位置 (バイトの位置) を返すため、次はその位置から続きを読める。
JSON として読めない行は読み飛ばし、その数を数える。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LineChunk:
    """ファイルから読んだ、改行で終わる行のまとまり。

    lines         読んだ行 (バイト列)。各行の末尾の改行 (\\n) と、その直前の \\r を除いたもの。ファイルの順
    next_offset   次に読み始めるバイトの位置 (読んだ最後の \\n の次のバイト。読んだ行が無ければ読み始めた位置)
    """

    lines: list[bytes]
    next_offset: int


@dataclass(frozen=True)
class ParsedLines:
    """JSON として読んだ行のまとまり。

    records       JSON の object として読めた行 (辞書)。ファイルの順
    skipped       読み飛ばした行の数 (UTF-8 でない行、JSON として読めない行、object でない値の行)。
                  空の行 (空白だけの行を含む) は数えない
    next_offset   次に読み始めるバイトの位置 (LineChunk.next_offset と同じ)
    """

    records: list[dict]
    skipped: int
    next_offset: int


def read_complete_lines(path: Path, offset: int = 0) -> LineChunk:
    """path をバイナリで開き、バイトの位置 offset から最後の \\n までにある行を読む。

    最後の \\n より後 (改行で終わっていない行) は読まず、next_offset はその手前 (最後の \\n の次のバイト) にする。
    offset から後に \\n が無ければ、lines は空のリスト、next_offset は offset。
    各行は末尾の \\n を除き、さらにその直前に \\r があれば除く (\\r\\n の行も読める)。空の行もそのまま入れる。
    ファイルが無ければ FileNotFoundError を、読めなければその OSError を、そのまま出す。
    """
    raise NotImplementedError


def parse_lines(chunk: LineChunk) -> ParsedLines:
    """chunk の各行を JSON として読み、object の行だけを records に入れる。

    各行は次の順に扱う:
        - 空白 (str.strip で除く文字) だけの行と空の行は、数えずに飛ばす
        - UTF-8 として読めない行 (UnicodeDecodeError) は読み飛ばし、skipped に数える
        - json.loads で読めない行 (ValueError) は読み飛ばし、skipped に数える
        - 読んだ値が object (dict) でない行 (配列や数など) は読み飛ばし、skipped に数える
    next_offset は chunk.next_offset をそのまま入れる。
    """
    raise NotImplementedError


def read_json_lines(path: Path, offset: int = 0) -> ParsedLines:
    """parse_lines(read_complete_lines(path, offset)) を返す。

    ファイルが無ければ FileNotFoundError を、読めなければその OSError を、そのまま出す。
    """
    raise NotImplementedError
