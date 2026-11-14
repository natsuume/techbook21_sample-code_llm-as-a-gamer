"""JSON Lines (1 行 1 件の JSON) のファイルを、改行で終わる行だけ読む。

記録のファイルは、第4章のエージェントが実行中に追記している途中のことがある。最後の行が改行で終わっていなければ
書いている途中とみなし、その行は読まない。読んだ位置 (バイトの位置) を返すため、次はその位置から続きを読める。
JSON として読めない行は読み飛ばし、その数を数える。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

# JSON の空白 (json.loads が値の前後で読み飛ばす文字)
JSON_WHITESPACE = " \t\n\r"
# 行を読む JSON の decoder。数万行の記録を読むときに json.loads が 1 行ごとに行う処理 (引数の確認や decoder の選択、
# 正規表現での空白の読み飛ばし) を省くため、1 つを作って raw_decode を直接呼ぶ
_JSON_DECODER = json.JSONDecoder()


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


def is_json_number(value: object) -> TypeGuard[int | float]:
    """JSON から読んだ値 value が数 (int か float。真偽値の bool は除く) なら True を返す。

    json.loads は true・false を bool (int の派生の型) にするため、数かどうかを isinstance(value, int) だけで
    判定すると、真偽値も数とみなしてしまう。それを除くための判定。
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def number_or_none(value: object) -> float | None:
    """JSON から読んだ値 value が数 (is_json_number) なら float にして返し、そうでなければ None を返す。"""
    return float(value) if is_json_number(value) else None


def is_json_scalar(value: object) -> bool:
    """JSON から読んだ値 value が配列 (list) でも object (dict) でもなければ True を返す。

    JSON から読んだ値のうち、辞書や集合のキーにできない (hash できない) のは list と dict だけ。要求の number や
    問いの name などをキーに使う前に、この判定でキーにできない値を除く (除いた値は、無いものとして扱う)。
    """
    return not isinstance(value, list | dict)


def read_complete_lines(path: Path, offset: int = 0) -> LineChunk:
    """path をバイナリで開き、バイトの位置 offset から最後の \\n までにある行を読む。

    最後の \\n より後 (改行で終わっていない行) は読まず、next_offset はその手前 (最後の \\n の次のバイト) にする。
    offset から後に \\n が無ければ、lines は空のリスト、next_offset は offset。
    各行は末尾の \\n を除き、さらにその直前に \\r があれば除く (\\r\\n の行も読める)。空の行もそのまま入れる。
    ファイルが無ければ FileNotFoundError を、読めなければその OSError を、そのまま出す。
    """
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return LineChunk(lines=[], next_offset=offset)
    lines = [line.removesuffix(b"\r") for line in data[:last_newline].split(b"\n")]
    return LineChunk(lines=lines, next_offset=offset + last_newline + 1)


def parse_lines(chunk: LineChunk) -> ParsedLines:
    """chunk の各行を JSON として読み、object の行だけを records に入れる。

    各行は次の順に扱う:
        - 空白 (str.strip で除く文字) だけの行と空の行は、数えずに飛ばす
        - UTF-8 として読めない行 (UnicodeDecodeError) は読み飛ばし、skipped に数える
        - JSON として読めない行 (json.loads と同じ規則。_load_json の ValueError) は読み飛ばし、skipped に数える
        - 読んだ値が object (dict) でない行 (配列や数など) は読み飛ばし、skipped に数える
    next_offset は chunk.next_offset をそのまま入れる。
    """
    records: list[dict] = []
    skipped = 0
    for line in chunk.lines:
        # 空白だけかどうかは文字列にしてから判定するため、先に UTF-8 として読む
        # (UTF-8 として読めない行は、空白だけの行ではない)
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        if not text.strip():
            continue
        try:
            value = _load_json(text)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        records.append(value)
    return ParsedLines(records=records, skipped=skipped, next_offset=chunk.next_offset)


def _load_json(text: str) -> object:
    """text を json.loads と同じ規則で JSON として読んだ値を返す。読めなければ ValueError を出す。

    値の前後の JSON の空白 (JSON_WHITESPACE) を除き、json.JSONDecoder.raw_decode で読む。値として読めなければ
    raw_decode の json.JSONDecodeError (ValueError の一種) をそのまま出し、値の後に空白以外が残れば ValueError を出す。
    """
    stripped = text.strip(JSON_WHITESPACE)
    value, end = _JSON_DECODER.raw_decode(stripped)
    if end != len(stripped):
        raise ValueError("extra data after a JSON value")
    return value


def read_json_lines(path: Path, offset: int = 0) -> ParsedLines:
    """parse_lines(read_complete_lines(path, offset)) を返す。

    ファイルが無ければ FileNotFoundError を、読めなければその OSError を、そのまま出す。
    """
    return parse_lines(read_complete_lines(path, offset))
