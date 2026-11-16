"""マップの場所とつながりの一覧 notes/map/map.json の形 (MapDocument と GridMapDocument) と、その読み取り。

map.json は deep の map の種類のジョブが書き、middle が入力として読む。最上位の layout でマップの型を決める:
    - "free" (自由型): MapDocument。出口と通れない位置を文で持つ
    - "grid" (マス目型): GridMapDocument。場所ごとに、マスの図 (tiles) と座標で出口・通れないマス・目印を持つ
layout が無い map.json は free として読む。

free の形:
    {"layout": "free",
     "places": [{"name": 文字列, "aliases": [文字列, ...], "description": 文字列, "exits": [文字列, ...],
                 "blocked": [文字列, ...], "state": 文字列, "changes_on_entry": 真偽}, ...],
     "connections": [{"from": 文字列, "to": 文字列, "how": 文字列}, ...]}
grid の形:
    {"layout": "grid",
     "places": [{"name": 文字列, "aliases": [文字列, ...], "description": 文字列, "state": 文字列,
                 "changes_on_entry": 真偽, "origin": {"x": 整数, "y": 整数}, "tiles": [文字列, ...],
                 "exits": [{"x": 整数, "y": 整数, "description": 文字列}, ...],
                 "blocked": [{"x": 整数, "y": 整数, "description": 文字列}, ...],
                 "landmarks": [{"x": 整数, "y": 整数, "description": 文字列}, ...]}, ...],
     "connections": [{"from": 文字列, "to": 文字列, "how": 文字列,
                      "from_tile": {"x": 整数, "y": 整数} か null, "to_tile": {"x": 整数, "y": 整数} か null}, ...]}
- 最上位は object で、places と connections の 2 つの配列を持つ。layout は省けるキーで、あれば "grid" か "free" の
  文字列 (大文字と小文字を区別する)。最上位のほかのキーは無視する
- places の各要素は、その型の形のキー (free は 7 つ、grid は 10 個) をすべて持つ object。connections の各要素も、
  その型の形のキー (free は 3 つ、grid は 5 つ) をすべて持つ object。name・from・to は空でない (前後の空白を除いても
  空でない) 文字列
- grid の座標は、x が右に、y が下に増える整数。マス (x, y) の文字は tiles[y - origin.y] の x - origin.x 文字目
  (0 から数える)。origin は tiles の 1 行目の 1 文字目のマスの座標
- grid の tiles の各行は同じ文字数で、文字は TILE_CHARACTERS (# . E ?) だけ。まだ図を描けない場所は空の配列
- grid の exits・blocked・landmarks の座標は、tiles の範囲の外でもよい。from_tile は出たマス (from の場所の座標)、
  to_tile は着いたマス (to の場所の座標)
- 整数は JSON の小数点を含まない数に限る (1.0 と真偽は形に合わない)
- 要素の object と座標の object のほかのキーも無視する
layout が grid なら、すべての場所とつながりを grid の形で確かめ、free なら free の形で確かめる。形に合わない要素が
1 つでもあれば、ファイル全体を形に合わないものとして扱う (parse_map_document が ValueError)。
middle の入力には、読めない・形に合わない map.json を「マップなし」(None) として渡す (read_map_document)。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from switch2_layers.notes.note_paths import MAP_JSON_NOTE
from switch2_layers.notes.note_reader import read_note_bytes

# grid のマスの図 (tiles) の文字
TILE_BLOCKED = "#"  # 通れない
TILE_PASSABLE = "."  # 通れる
TILE_EXIT = "E"  # 出口
TILE_UNKNOWN = "?"  # まだ確かめていない
TILE_CHARACTERS: frozenset[str] = frozenset(
    {TILE_BLOCKED, TILE_PASSABLE, TILE_EXIT, TILE_UNKNOWN}
)


class MapLayout(StrEnum):
    """マップの型 (map.json の最上位の layout の値)。"""

    GRID = "grid"  # マス目型
    FREE = "free"  # 自由型


@dataclass(frozen=True)
class TilePosition:
    """grid のマスの座標。x は右に、y は下に増える整数。

    JSON では {"x": 整数, "y": 整数} (tile_position_to_json)。map.json の origin・from_tile・to_tile、
    middle の出力の current_tile・from_tile・to_tile・tile、middle/state.json と middle/map_records.jsonl の座標に使う。
    """

    x: int
    y: int


@dataclass(frozen=True)
class TileMark:
    """grid の場所の exits・blocked・landmarks の要素 1 つ (座標の付いた説明)。

    position      マスの座標 (JSON では x と y のキー)。tiles の範囲の外でもよい
    description   説明
    """

    position: TilePosition
    description: str


@dataclass(frozen=True)
class MapPlace:
    """free の map.json の places の要素 1 つ (場所 1 つ)。

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
class GridMapPlace:
    """grid の map.json の places の要素 1 つ (場所 1 つ)。

    name・aliases・description・state・changes_on_entry   MapPlace と同じ
    origin       tiles の 1 行目の 1 文字目のマスの座標
    tiles        マスの図 (1 行が 1 つの y。各行は同じ文字数で、文字は TILE_CHARACTERS)。まだ図を描けなければ空
    exits        出口 (座標と説明)
    blocked      通れないマス (座標と説明)
    landmarks    目印 (座標と説明)
    """

    name: str
    aliases: tuple[str, ...]
    description: str
    state: str
    changes_on_entry: bool
    origin: TilePosition
    tiles: tuple[str, ...]
    exits: tuple[TileMark, ...]
    blocked: tuple[TileMark, ...]
    landmarks: tuple[TileMark, ...]


@dataclass(frozen=True)
class MapConnection:
    """場所から場所への移動 1 つ。map.json の connections の要素と、middle の出力の new_connections の要素。

    from_place   移る前の場所の名前 (JSON のキーは from)
    to_place     移った後の場所の名前 (JSON のキーは to)
    how          移り方 (出た位置・方向・直前の操作の説明)
    from_tile    出たマス (from の場所の座標)。分からなければ None
    to_tile      着いたマス (to の場所の座標)。分からなければ None
    free の map.json のつながりは from_tile と to_tile を持たない (読むと None。JSON に戻すときは書かない)。
    from_tile・to_tile の既定値は、座標を持たないつながり (free のマップやテストなど) のため。
    """

    from_place: str
    to_place: str
    how: str
    from_tile: TilePosition | None = None
    to_tile: TilePosition | None = None


@dataclass(frozen=True)
class MapDocument:
    """free (自由型) の map.json の中身 (場所とつながりの一覧)。

    places        場所 (map.json の places の順)
    connections   つながり (map.json の connections の順。from_tile と to_tile は None)
    layout        マップの型 (常に MapLayout.FREE。インスタンスの値ではなくクラスの値)
    """

    places: tuple[MapPlace, ...]
    connections: tuple[MapConnection, ...]
    layout: ClassVar[MapLayout] = MapLayout.FREE


@dataclass(frozen=True)
class GridMapDocument:
    """grid (マス目型) の map.json の中身 (場所とつながりの一覧)。

    places        場所 (map.json の places の順)
    connections   つながり (map.json の connections の順)
    layout        マップの型 (常に MapLayout.GRID。インスタンスの値ではなくクラスの値)
    """

    places: tuple[GridMapPlace, ...]
    connections: tuple[MapConnection, ...]
    layout: ClassVar[MapLayout] = MapLayout.GRID


def tile_position_to_json(position: TilePosition) -> dict[str, int]:
    """座標 position を JSON の形 {"x": x, "y": y} (この順のキー) にする。"""
    return {"x": position.x, "y": position.y}


def parse_map_document(value: object) -> MapDocument | GridMapDocument:
    """map.json を JSON として読んだ値 value を、形 (モジュールの docstring) を確かめて MapDocument か GridMapDocument
    にする。

    layout が無ければ free、"free" なら free、"grid" なら grid の形で確かめる。free なら MapDocument、grid なら
    GridMapDocument を返す。
    形に合わなければ ValueError にする:
        - value が object でない。places か connections が無いか、配列でない
        - layout があり、文字列でないか、"grid"・"free" (大文字と小文字を区別する) のどちらでもない
        - free: places の要素が object でない、7 つのキーのどれかが無い、型が合わない (name・description・state は
          文字列、aliases・exits・blocked は文字列の配列、changes_on_entry は真偽)、name が空 (前後の空白を除くと空)
        - free: connections の要素が object でない、3 つのキーのどれかが無い、型が合わない (from・to・how は文字列)、
          from か to が空 (前後の空白を除くと空)
        - grid: places の要素が object でない、10 個のキーのどれかが無い、型が合わない (name・aliases・description・
          state・changes_on_entry は free と同じ。origin は座標の object、tiles は文字列の配列、exits・blocked・
          landmarks は座標と説明の object の配列)、name が空
        - grid: tiles の行の文字数が同じでない、TILE_CHARACTERS 以外の文字がある
        - grid: connections の要素が object でない、5 つのキーのどれかが無い、型が合わない (from・to・how は free と
          同じ。from_tile・to_tile は座標の object か null)、from か to が空
        - grid: 座標の object (origin・from_tile・to_tile と、exits・blocked・landmarks の要素) に x か y が無いか、
          整数でない (真偽と小数 (1.0 を含む) は整数でない)。exits・blocked・landmarks の要素に description が無いか、
          文字列でない
    座標は負でもよい。tiles の空の配列は形に合う (各行が空文字列の配列も、文字数がそろっていれば形に合う)。
    最上位と要素と座標の object のほかのキーは無視する (free の要素の from_tile・origin なども無視する)。
    値は書かれたまま (前後の空白も除かない) にし、配列はタプルにする。空の places・connections は形に合う。
    """
    top = _object(value, "map.json")
    layout = _layout(top)
    places = _array(top, "places", "map.json")
    connections = _array(top, "connections", "map.json")
    if layout is MapLayout.GRID:
        return GridMapDocument(
            places=tuple(_grid_place(item) for item in places),
            connections=tuple(_grid_connection(item) for item in connections),
        )
    return MapDocument(
        places=tuple(_free_place(item) for item in places),
        connections=tuple(_free_connection(item) for item in connections),
    )


def read_map_document(notes_dir: Path) -> MapDocument | GridMapDocument | None:
    """notes_dir の map.json (note_paths.MAP_JSON_NOTE) を読み、MapDocument か GridMapDocument を返す。

    文書は notes.note_reader.read_note_bytes で読む (シンボリックリンクを解決した先が notes/ の中の通常のファイルで
    なければ、無い文書として扱う)。次のときは None (マップなし) を返し、例外は出さない:
        - 無い文書 (read_note_bytes が None)
        - UTF-8 として読めない、JSON として読めない
        - parse_map_document が ValueError (形に合わない要素が 1 つでもある)
    """
    data = read_note_bytes(notes_dir, MAP_JSON_NOTE)
    if data is None:
        return None
    try:
        return parse_map_document(json.loads(data.decode("utf-8")))
    except ValueError:
        # UnicodeDecodeError と json.JSONDecodeError も ValueError
        return None


def map_document_to_json(document: MapDocument | GridMapDocument) -> dict[str, object]:
    """MapDocument か GridMapDocument を map.json の形の辞書に戻す (middle の入力に、マップの全体を JSON として
    渡すため)。

    最上位は layout (document.layout の値)・places・connections の順のキーにする。要素のキーの順:
        free   places の要素は name・aliases・description・exits・blocked・state・changes_on_entry、
               connections の要素は from・to・how (from_tile と to_tile は書かない)
        grid   places の要素は name・aliases・description・state・changes_on_entry・origin・tiles・exits・blocked・
               landmarks、connections の要素は from・to・how・from_tile・to_tile
    座標は tile_position_to_json の形 (None は null)、exits・blocked・landmarks の要素は x・y・description の順の
    キーにする。タプルはリストにする。parse_map_document(map_document_to_json(document)) は document と等しい。
    """
    if isinstance(document, GridMapDocument):
        return {
            "layout": document.layout.value,
            "places": [_grid_place_to_json(place) for place in document.places],
            "connections": [
                {
                    "from": connection.from_place,
                    "to": connection.to_place,
                    "how": connection.how,
                    "from_tile": _optional_tile_position_to_json(connection.from_tile),
                    "to_tile": _optional_tile_position_to_json(connection.to_tile),
                }
                for connection in document.connections
            ],
        }
    return {
        "layout": document.layout.value,
        "places": [
            {
                "name": place.name,
                "aliases": list(place.aliases),
                "description": place.description,
                "exits": list(place.exits),
                "blocked": list(place.blocked),
                "state": place.state,
                "changes_on_entry": place.changes_on_entry,
            }
            for place in document.places
        ],
        "connections": [
            {
                "from": connection.from_place,
                "to": connection.to_place,
                "how": connection.how,
            }
            for connection in document.connections
        ],
    }


def _grid_place_to_json(place: GridMapPlace) -> dict[str, object]:
    """grid の場所 place を map.json の places の要素の形の辞書にする (map_document_to_json のキーの順)。"""
    return {
        "name": place.name,
        "aliases": list(place.aliases),
        "description": place.description,
        "state": place.state,
        "changes_on_entry": place.changes_on_entry,
        "origin": tile_position_to_json(place.origin),
        "tiles": list(place.tiles),
        "exits": [_tile_mark_to_json(mark) for mark in place.exits],
        "blocked": [_tile_mark_to_json(mark) for mark in place.blocked],
        "landmarks": [_tile_mark_to_json(mark) for mark in place.landmarks],
    }


def _tile_mark_to_json(mark: TileMark) -> dict[str, object]:
    """座標の付いた説明 mark を {"x": x, "y": y, "description": 説明} (この順のキー) にする。"""
    return {**tile_position_to_json(mark.position), "description": mark.description}


def _optional_tile_position_to_json(
    position: TilePosition | None,
) -> dict[str, int] | None:
    """座標 position を tile_position_to_json の形にする。None はそのまま None (JSON の null)。"""
    return None if position is None else tile_position_to_json(position)


# 以下の読み取りは、契約 (parse_map_document) どおり型の誤りも ValueError にする。
# 型が合えば値を返し、合わなければ if の後で ValueError を出す。where は、誤りの説明に入れる、確かめている値の名前


def _layout(top: Mapping[str, object]) -> MapLayout:
    """map.json の最上位の object top の layout を MapLayout にする。無ければ FREE。

    文字列でないか、MapLayout の値 (大文字と小文字を区別する) でなければ ValueError。
    """
    if "layout" not in top:
        return MapLayout.FREE
    item = top["layout"]
    if isinstance(item, str) and item in {layout.value for layout in MapLayout}:
        return MapLayout(item)
    raise ValueError(f"'layout' in map.json must be 'grid' or 'free': {item!r}")


def _free_place(item: object) -> MapPlace:
    """free の places の要素 item を MapPlace にする。形に合わなければ ValueError。"""
    value = _object(item, "an element of places")
    return MapPlace(
        name=_non_blank_string(value, "name", "a place"),
        aliases=_string_array(value, "aliases", "a place"),
        description=_string(value, "description", "a place"),
        exits=_string_array(value, "exits", "a place"),
        blocked=_string_array(value, "blocked", "a place"),
        state=_string(value, "state", "a place"),
        changes_on_entry=_boolean(value, "changes_on_entry", "a place"),
    )


def _free_connection(item: object) -> MapConnection:
    """free の connections の要素 item を MapConnection (from_tile と to_tile は None) にする。形に合わなければ
    ValueError。"""
    value = _object(item, "an element of connections")
    return MapConnection(
        from_place=_non_blank_string(value, "from", "a connection"),
        to_place=_non_blank_string(value, "to", "a connection"),
        how=_string(value, "how", "a connection"),
    )


def _grid_place(item: object) -> GridMapPlace:
    """grid の places の要素 item を GridMapPlace にする。形に合わなければ ValueError。"""
    value = _object(item, "an element of places")
    where = "a place"
    return GridMapPlace(
        name=_non_blank_string(value, "name", where),
        aliases=_string_array(value, "aliases", where),
        description=_string(value, "description", where),
        state=_string(value, "state", where),
        changes_on_entry=_boolean(value, "changes_on_entry", where),
        origin=_tile_position(_value(value, "origin", where), f"'origin' in {where}"),
        tiles=_tiles(value, where),
        exits=_tile_marks(value, "exits", where),
        blocked=_tile_marks(value, "blocked", where),
        landmarks=_tile_marks(value, "landmarks", where),
    )


def _grid_connection(item: object) -> MapConnection:
    """grid の connections の要素 item を MapConnection にする。形に合わなければ ValueError。"""
    value = _object(item, "an element of connections")
    where = "a connection"
    return MapConnection(
        from_place=_non_blank_string(value, "from", where),
        to_place=_non_blank_string(value, "to", where),
        how=_string(value, "how", where),
        from_tile=_optional_tile_position(value, "from_tile", where),
        to_tile=_optional_tile_position(value, "to_tile", where),
    )


def _tiles(value: Mapping[str, object], where: str) -> tuple[str, ...]:
    """value["tiles"] がマスの図 (各行が同じ文字数で、文字が TILE_CHARACTERS だけの文字列の配列) なら、タプルにして
    返す。それ以外は ValueError。空の配列は形に合う。"""
    rows = _string_array(value, "tiles", where)
    if len({len(row) for row in rows}) > 1:
        raise ValueError(
            f"rows of 'tiles' in {where} must have the same length: {rows!r}"
        )
    if all(character in TILE_CHARACTERS for row in rows for character in row):
        return rows
    raise ValueError(f"'tiles' in {where} must use only # . E ?: {rows!r}")


def _tile_marks(
    value: Mapping[str, object], key: str, where: str
) -> tuple[TileMark, ...]:
    """value[key] が {"x": 整数, "y": 整数, "description": 文字列} の object の配列なら、TileMark のタプルにして返す。
    それ以外は ValueError。"""
    item_where = f"an element of {key!r} in {where}"
    marks: list[TileMark] = []
    for item in _array(value, key, where):
        mark = _object(item, item_where)
        marks.append(
            TileMark(
                position=_tile_position(mark, item_where),
                description=_string(mark, "description", item_where),
            )
        )
    return tuple(marks)


def _optional_tile_position(
    value: Mapping[str, object], key: str, where: str
) -> TilePosition | None:
    """value[key] が null なら None、座標の object なら TilePosition を返す。無いか、どちらでもなければ ValueError。"""
    item = _value(value, key, where)
    if item is None:
        return None
    return _tile_position(item, f"{key!r} in {where}")


def _tile_position(item: object, where: str) -> TilePosition:
    """item が x と y に整数 (bool と小数を除く) を持つ object なら TilePosition を返す。それ以外は ValueError。

    object のほかのキー (exits などの要素の description を含む) は見ない。
    """
    value = _object(item, where)
    return TilePosition(x=_integer(value, "x", where), y=_integer(value, "y", where))


def _integer(value: Mapping[str, object], key: str, where: str) -> int:
    """value[key] が整数 (bool を除く) ならその値を返す。無いか整数でなければ ValueError。

    JSON の 1.0 は json.loads で float になるため、整数としない。
    """
    item = _value(value, key, where)
    if isinstance(item, int) and not isinstance(item, bool):
        return item
    raise ValueError(f"{key!r} in {where} must be an integer: {item!r}")


def _object(item: object, where: str) -> Mapping[str, object]:
    """item が JSON の object (dict) ならそのまま返す。それ以外は ValueError。"""
    if isinstance(item, dict):
        return item
    raise ValueError(f"{where} must be a JSON object: {item!r}")


def _value(value: Mapping[str, object], key: str, where: str) -> object:
    """value[key] を返す。key が無ければ ValueError。"""
    if key in value:
        return value[key]
    raise ValueError(f"{key!r} is missing in {where}")


def _array(value: Mapping[str, object], key: str, where: str) -> list[object]:
    """value[key] が配列 (list) ならその値を返す。無いか配列でなければ ValueError。"""
    item = _value(value, key, where)
    if isinstance(item, list):
        return item
    raise ValueError(f"{key!r} in {where} must be an array: {item!r}")


def _string(value: Mapping[str, object], key: str, where: str) -> str:
    """value[key] が文字列ならその値を返す。無いか文字列でなければ ValueError。"""
    item = _value(value, key, where)
    if isinstance(item, str):
        return item
    raise ValueError(f"{key!r} in {where} must be a string: {item!r}")


def _non_blank_string(value: Mapping[str, object], key: str, where: str) -> str:
    """value[key] が、前後の空白を除いても空でない文字列なら、書かれたままの値を返す。それ以外は ValueError。"""
    item = _string(value, key, where)
    if item.strip():
        return item
    raise ValueError(f"{key!r} in {where} must not be empty")


def _string_array(value: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    """value[key] が文字列の配列なら、タプルにして返す。無いか文字列の配列でなければ ValueError。"""
    items = _array(value, key, where)
    strings = tuple(item for item in items if isinstance(item, str))
    if len(strings) == len(items):
        return strings
    raise ValueError(f"{key!r} in {where} must be an array of strings: {items!r}")


def _boolean(value: Mapping[str, object], key: str, where: str) -> bool:
    """value[key] が真偽ならその値を返す。無いか真偽でなければ ValueError。"""
    item = _value(value, key, where)
    if isinstance(item, bool):
        return item
    raise ValueError(f"{key!r} in {where} must be a boolean: {item!r}")
