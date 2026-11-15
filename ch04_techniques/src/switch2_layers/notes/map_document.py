"""マップの場所とつながりの一覧 notes/map/map.json の形 (MapDocument) と、その読み取り。

map.json は deep の map の種類のジョブが書き、middle が入力として読む。形:
    {"places": [{"name": 文字列, "aliases": [文字列, ...], "description": 文字列, "exits": [文字列, ...],
                 "blocked": [文字列, ...], "state": 文字列, "changes_on_entry": 真偽}, ...],
     "connections": [{"from": 文字列, "to": 文字列, "how": 文字列}, ...]}
- 最上位は object で、places と connections の 2 つの配列を持つ。最上位のほかのキーは無視する
- places の各要素は、上の 7 つのキーをすべて持つ object。name は空でない (前後の空白を除いても空でない) 文字列
- connections の各要素は、上の 3 つのキーをすべて持つ object。from と to は空でない (前後の空白を除いても空でない) 文字列
- 要素の object のほかのキーも無視する
形に合わない要素が 1 つでもあれば、ファイル全体を形に合わないものとして扱う (parse_map_document が ValueError)。
middle の入力には、読めない・形に合わない map.json を「マップなし」(None) として渡す (read_map_document)。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MapPlace:
    """map.json の places の要素 1 つ (場所 1 つ)。

    name               場所の名前 (空でない)
    aliases            同じ場所の別の名前 (deep が重複をまとめたときに残す、middle が前に使った名前)
    description        場所の説明
    exits              出口の説明
    blocked            通れない位置の説明
    state              状態 (訪問済みか、出口を通ったかなど)
    changes_on_entry   入るたびに構造が変わる場所なら True (この場所のつながりを、次に入ったときの手順に使わない)
    """

    name: str
    aliases: tuple[str, ...]
    description: str
    exits: tuple[str, ...]
    blocked: tuple[str, ...]
    state: str
    changes_on_entry: bool


@dataclass(frozen=True)
class MapConnection:
    """場所から場所への移動 1 つ。map.json の connections の要素と、middle の出力の new_connections の要素。

    from_place   移る前の場所の名前 (JSON のキーは from)
    to_place     移った後の場所の名前 (JSON のキーは to)
    how          移り方 (出た位置・方向・直前の操作の説明)
    """

    from_place: str
    to_place: str
    how: str


@dataclass(frozen=True)
class MapDocument:
    """map.json の中身 (場所とつながりの一覧)。

    places        場所 (map.json の places の順)
    connections   つながり (map.json の connections の順)
    """

    places: tuple[MapPlace, ...]
    connections: tuple[MapConnection, ...]


def parse_map_document(value: object) -> MapDocument:
    """map.json を JSON として読んだ値 value を、形 (モジュールの docstring) を確かめて MapDocument にする。

    形に合わなければ ValueError にする:
        - value が object でない。places か connections が無いか、配列でない
        - places の要素が object でない、7 つのキーのどれかが無い、型が合わない (name・description・state は文字列、
          aliases・exits・blocked は文字列の配列、changes_on_entry は真偽)、name が空 (前後の空白を除くと空)
        - connections の要素が object でない、3 つのキーのどれかが無い、型が合わない (from・to・how は文字列)、
          from か to が空 (前後の空白を除くと空)
    最上位と要素の object のほかのキーは無視する。値は書かれたまま (前後の空白も除かない) にし、配列はタプルにする。
    空の places・connections は形に合う。
    """
    raise NotImplementedError


def read_map_document(notes_dir: Path) -> MapDocument | None:
    """notes_dir の map.json (note_paths.MAP_JSON_NOTE) を読み、MapDocument を返す。

    文書は notes.note_reader.read_note_bytes で読む (シンボリックリンクを解決した先が notes/ の中の通常のファイルで
    なければ、無い文書として扱う)。次のときは None (マップなし) を返し、例外は出さない:
        - 無い文書 (read_note_bytes が None)
        - UTF-8 として読めない、JSON として読めない
        - parse_map_document が ValueError (形に合わない要素が 1 つでもある)
    """
    raise NotImplementedError


def map_document_to_json(document: MapDocument) -> dict[str, object]:
    """MapDocument を map.json の形の辞書に戻す (middle の入力に、マップの全体を JSON として渡すため)。

    キーの順は、最上位が places・connections、places の要素が name・aliases・description・exits・blocked・state・
    changes_on_entry、connections の要素が from・to・how。タプルはリストにする。
    parse_map_document(map_document_to_json(document)) は document と等しい。
    """
    raise NotImplementedError
