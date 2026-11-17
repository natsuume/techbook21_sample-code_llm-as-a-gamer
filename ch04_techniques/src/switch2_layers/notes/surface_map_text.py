"""surface の要求の state の map に入れる、現在地の場所のマップの文字列を作る。I/O を扱わない。

文字列は、読んだマップ (notes.map_document の MapDocument か GridMapDocument)、現在地の場所の名前とマスとその時刻
(middle/state.json の place_name・current_tile・based_on_t)、先頭のタスクの行き先 (tasks.task_queue.TaskDestination)
から作る (build_surface_map_text)。行を "\\n" でつなぎ、末尾に改行を付けない。

空文字列 (マップを渡さない) にするとき:
    - マップが無い (document が None)
    - 現在地の場所の名前が無い (place_name が None)
    - 現在地の場所がマップに無い (find_map_place が None)
以下、現在地の場所 (current) は find_map_place(document, place_name) が返した場所、現在地の名前の集合 (current_names)
は current の name と aliases を前後の空白を除いたものの集合。
    - 行き先の場所が現在地の場所: destination が None でなく、destination.place を前後の空白を除いたものが
      current_names に入る
    - 行き先の場所が別の場所: destination が None でなく、上に当たらない
    - 行き先の場所の name: find_map_place(document, destination.place) が場所を返せばその name、返さなければ
      destination.place (書かれたまま)
    - 現在地から出るつながり: document.connections のうち、from を前後の空白を除いたものが current_names に入るもの

grid (GridMapDocument) の行 (この順):
    1. `場所: <current.name>`
    2. current_tile と based_on_t がどちらも None でなければ
       `主人公 (P): x=<x>, y=<y> (<frames.surface_frames.format_frame_label(based_on_t)> の画面での位置。
       その後に動いていることがある)` (1 行。format_frame_label の戻り値は `t=12.345` の形)。それ以外は UNKNOWN_PLAYER_LINE
    3. 目標の行 (下の「grid の目標の決め方」)。目標のマスがあれば `目標 (G): x=<x>, y=<y> <説明>` (説明が空文字列なら
       `目標 (G): x=<x>, y=<y>` で、末尾に空白を付けない)。目標なしなら NO_GOAL_LINE か
       `目標: 無い (行き先の場所: <行き先の場所の name>)`
    4. current.changes_on_entry が真のときだけ CHANGES_ON_ENTRY_LINE
    5. current.tiles が空でなければ GRID_LEGEND_LINE。空なら 5〜7 の代わりに NO_TILES_LINE の 1 行
    6. `図の左上のマスは x=<origin.x>, y=<origin.y>。x は右へ、y は下へ増える`
    7. tiles の各行 (tiles の順)。目標のマスの文字を GOAL_TILE に、続けて主人公のマスの文字を PLAYER_TILE に置き換える
       (同じマスなら PLAYER_TILE)。マスが tiles の範囲の外 (notes.grid_route.tile_at が None) なら置き換えない。
       主人公のマスは、2 の行に座標を書いたとき (current_tile と based_on_t がどちらもあるとき) だけ置き換える
    8. current.exits ごとに `出口: x=<x>, y=<y> <description>` (exits の順)
    9. 現在地から出るつながりのうち from_tile が None でないものごとに
       `つながり: x=<from_tile.x>, y=<from_tile.y> <how> (行き先の場所: <to>)` (connections の順。to は書かれたまま)
    10. current.landmarks ごとに `目印: x=<x>, y=<y> <description>` (landmarks の順)
    current.blocked と current.description・state は書かない。
grid の目標の決め方:
    - destination が None → 目標なし。3 の行は NO_GOAL_LINE
    - 行き先の場所が現在地の場所:
        - destination.tile が None → 目標なし。3 の行は NO_GOAL_LINE
        - tile がある → 目標のマスは tile。説明は、次の順で最初に当たったもの: current.exits の中で座標が tile と
          同じ最初の要素の description → 現在地から出るつながりのうち from_tile が tile と同じ最初のものの how →
          current.landmarks の中で座標が tile と同じ最初の要素の description。どれにも当たらなければ空文字列
    - 行き先の場所が別の場所: target = find_map_place(document, destination.place) として、
        - 次のどれかなら目標なし。3 の行は `目標: 無い (行き先の場所: <行き先の場所の name>)`:
          current.changes_on_entry が真 / target が None / notes.cross_place_route.find_cross_place_routes(document,
          current) の中に destination が target (is で比べる) の道筋が無い / その道筋の最初の区間 (legs[0]) の
          connection.from_tile が None
        - それ以外 → 目標のマスは legs[0].connection.from_tile。説明は
          `<legs[0].connection.how> (行き先の場所: <target.name>)` (target.name はタスクの最終の行き先の場所の name で、
          多区間の道筋では、最初のつながりの to と違うことがある)

free (MapDocument) の行 (この順):
    1. `場所: <current.name>`
    2. `説明: <current.description>`
    3. current.exits ごとに `出口: <要素>` (exits の順)
    4. current.blocked ごとに `通れない位置: <要素>` (blocked の順)
    5. `状態: <current.state>`
    6. current.changes_on_entry が真のときだけ CHANGES_ON_ENTRY_LINE
    7. 行き先の場所が別の場所のときだけ `行き先の場所: <行き先の場所の name>`
    free では current_tile と based_on_t を使わない (主人公の位置と目標の印は無い)。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.notes.cross_place_route import find_cross_place_routes
from switch2_layers.notes.grid_route import tile_at
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    MapDocument,
    MapPlace,
    TilePosition,
)
from switch2_layers.tasks.task_queue import TaskDestination

# grid のマスの図で、主人公のマスと目標のマスに置き換える文字
PLAYER_TILE = "P"
GOAL_TILE = "G"
# grid の主人公の行で、主人公の位置 (current_tile か based_on_t) が無いときの行
UNKNOWN_PLAYER_LINE = "主人公の位置: 分からない"
# grid の目標の行で、目標が無いときの行 (行き先の場所が別の場所のときは、後ろに行き先の場所を添える)
NO_GOAL_LINE = "目標: 無い"
# 現在地の場所が、入るたびに構造が変わる (changes_on_entry が真) ときの行
CHANGES_ON_ENTRY_LINE = "この場所は、入るたびに構造が変わる"
# grid のマスの図の凡例の行
GRID_LEGEND_LINE = "凡例: # 通れない / . 通れる / E 出口 / ? 未確認 / P 主人公 / G 目標"
# grid のマスの図が空 (tiles が空の配列) のときの、凡例・左上のマスの行・図の代わりの行
NO_TILES_LINE = "マスの図: まだ無い"


def find_map_place(
    document: MapDocument | GridMapDocument, place_name: str
) -> MapPlace | GridMapPlace | None:
    """document の places から、name か aliases が place_name と一致する最初の場所を返す。無ければ None。

    notes.grid_route.find_grid_place と同じ規則で、free と grid のどちらのマップにも使う: 前後の空白を除いて比べる
    (place_name も name も aliases も)。places の順に、各場所の name、続けて aliases を見る。place_name が前後の空白を
    除いて空なら None。返すのは document.places の要素そのもの。
    """
    return _find_place(document.places, place_name)


def build_surface_map_text(
    document: MapDocument | GridMapDocument | None,
    place_name: str | None,
    current_tile: TilePosition | None,
    based_on_t: float | None,
    destination: TaskDestination | None,
) -> str:
    """surface の state の map に入れる、現在地の場所のマップの文字列を返す (行の形と規則はモジュールの docstring)。

    document        読んだマップ (notes.map_document.read_map_document の戻り値)。マップが無ければ None
    place_name      現在地の場所の名前 (middle/state.json の place_name)。無ければ None
    current_tile    現在地のマス (middle/state.json の current_tile)。無ければ None。grid だけで使う
    based_on_t      place_name と current_tile を出した middle の出力がもとにした画面の時刻 (middle/state.json の
                    based_on_t)。無ければ None。grid だけで使う
    destination     先頭のタスクの行き先。先頭のタスクが無いか、行き先が無ければ None
    document・place_name が None のときと、現在地の場所がマップに無いときは空文字列を返す。
    """
    if document is None or place_name is None:
        return ""
    # マップの型ごとに場所を探し、場所の型 (GridMapPlace か MapPlace) を保ったまま行を作る
    if isinstance(document, GridMapDocument):
        grid_place = _find_place(document.places, place_name)
        if grid_place is None:
            return ""
        lines = _grid_lines(document, grid_place, current_tile, based_on_t, destination)
        return "\n".join(lines)
    free_place = _find_place(document.places, place_name)
    if free_place is None:
        return ""
    return "\n".join(_free_lines(document, free_place, destination))


@dataclass(frozen=True)
class _Goal:
    """grid の目標 (モジュールの docstring の「grid の目標の決め方」の結果)。

    tile   目標のマス。目標なしなら None
    line   目標の行 (grid の行の 3)
    """

    tile: TilePosition | None
    line: str


def _find_place[Place: MapPlace | GridMapPlace](
    places: Sequence[Place], place_name: str
) -> Place | None:
    """places から、name か aliases が place_name と一致する最初の場所を返す (find_map_place の規則)。無ければ None。"""
    wanted = place_name.strip()
    if not wanted:
        return None
    for place in places:
        if wanted in _stripped_names(place):
            return place
    return None


def _stripped_names(place: MapPlace | GridMapPlace) -> set[str]:
    """場所 place の name と aliases を、前後の空白を除いた形の集合にする (現在地の名前の集合)。"""
    return {place.name.strip(), *(alias.strip() for alias in place.aliases)}


def _destination_name(
    document: MapDocument | GridMapDocument, destination: TaskDestination
) -> str:
    """行き先の場所の name を返す。マップに無い場所なら destination.place (書かれたまま)。"""
    target = find_map_place(document, destination.place)
    return destination.place if target is None else target.name


def _grid_lines(
    document: GridMapDocument,
    current: GridMapPlace,
    current_tile: TilePosition | None,
    based_on_t: float | None,
    destination: TaskDestination | None,
) -> list[str]:
    """grid の現在地の場所 current の行 (モジュールの docstring の grid の 1〜10) を返す。"""
    names = _stripped_names(current)
    # 現在地から出るつながりのうち、出たマスのあるもの (出たマス, つながり)
    connections = [
        (from_tile, connection)
        for connection in document.connections
        if connection.from_place.strip() in names
        and (from_tile := connection.from_tile) is not None
    ]
    # 主人公のマスは、座標と時刻がどちらもあるときだけ書いて置き換える
    player = None if based_on_t is None else current_tile
    goal = _grid_goal(document, current, names, connections, destination)
    if player is None or based_on_t is None:
        player_line = UNKNOWN_PLAYER_LINE
    else:
        player_line = (
            f"主人公 (P): x={player.x}, y={player.y} ({format_frame_label(based_on_t)} "
            "の画面での位置。その後に動いていることがある)"
        )
    lines = [f"場所: {current.name}", player_line, goal.line]
    if current.changes_on_entry:
        lines.append(CHANGES_ON_ENTRY_LINE)
    if current.tiles:
        lines.append(GRID_LEGEND_LINE)
        lines.append(
            f"図の左上のマスは x={current.origin.x}, y={current.origin.y}。"
            "x は右へ、y は下へ増える"
        )
        lines += _marked_tiles(current, player, goal.tile)
    else:
        lines.append(NO_TILES_LINE)
    lines += [
        f"出口: x={mark.position.x}, y={mark.position.y} {mark.description}"
        for mark in current.exits
    ]
    lines += [
        f"つながり: x={from_tile.x}, y={from_tile.y} {connection.how} "
        f"(行き先の場所: {connection.to_place})"
        for from_tile, connection in connections
    ]
    lines += [
        f"目印: x={mark.position.x}, y={mark.position.y} {mark.description}"
        for mark in current.landmarks
    ]
    return lines


def _grid_goal(
    document: GridMapDocument,
    current: GridMapPlace,
    names: set[str],
    connections: Sequence[tuple[TilePosition, MapConnection]],
    destination: TaskDestination | None,
) -> _Goal:
    """grid の目標を、モジュールの docstring の「grid の目標の決め方」で決める。

    names は現在地の名前の集合、connections は現在地から出るつながりのうち出たマスのあるもの (出たマス, つながり)。
    """
    if destination is None:
        return _Goal(tile=None, line=NO_GOAL_LINE)
    if destination.place.strip() in names:
        tile = destination.tile
        if tile is None:
            return _Goal(tile=None, line=NO_GOAL_LINE)
        # 出口 → つながりの移り方 → 目印 の順で、座標が同じ最初のものの説明
        descriptions = [
            *(mark.description for mark in current.exits if mark.position == tile),
            *(
                connection.how
                for from_tile, connection in connections
                if from_tile == tile
            ),
            *(mark.description for mark in current.landmarks if mark.position == tile),
        ]
        return _Goal(tile=tile, line=_goal_line(tile, next(iter(descriptions), "")))
    target = _find_place(document.places, destination.place)
    no_goal = _Goal(
        tile=None,
        line=f"{NO_GOAL_LINE} (行き先の場所: {_destination_name(document, destination)})",
    )
    if current.changes_on_entry or target is None:
        return no_goal
    route = next(
        (
            found
            for found in find_cross_place_routes(document, current)
            if found.destination is target
        ),
        None,
    )
    if route is None:
        return no_goal
    first = route.legs[0].connection
    if first.from_tile is None:
        return no_goal
    # 説明には、タスクの最終の行き先の場所の name を書く (多区間の道筋では、最初のつながりの to と違うことがある)
    description = f"{first.how} (行き先の場所: {target.name})"
    return _Goal(tile=first.from_tile, line=_goal_line(first.from_tile, description))


def _goal_line(tile: TilePosition, description: str) -> str:
    """目標のマスがあるときの行 `目標 (G): x=<x>, y=<y> <説明>` を返す。説明が空なら末尾に空白を付けない。"""
    line = f"目標 (G): x={tile.x}, y={tile.y}"
    return f"{line} {description}" if description else line


def _marked_tiles(
    place: GridMapPlace, player: TilePosition | None, goal: TilePosition | None
) -> list[str]:
    """place.tiles の各行の、目標のマスを GOAL_TILE に、続けて主人公のマスを PLAYER_TILE に置き換えたものを返す。

    同じマスなら PLAYER_TILE。None のマスと、tiles の範囲の外 (tile_at が None) のマスは置き換えない。
    """
    rows = [list(row) for row in place.tiles]
    for position, character in ((goal, GOAL_TILE), (player, PLAYER_TILE)):
        if position is not None and tile_at(place, position) is not None:
            rows[position.y - place.origin.y][position.x - place.origin.x] = character
    return ["".join(row) for row in rows]


def _free_lines(
    document: MapDocument, current: MapPlace, destination: TaskDestination | None
) -> list[str]:
    """free の現在地の場所 current の行 (モジュールの docstring の free の 1〜7) を返す。"""
    lines = [
        f"場所: {current.name}",
        f"説明: {current.description}",
        *(f"出口: {item}" for item in current.exits),
        *(f"通れない位置: {item}" for item in current.blocked),
        f"状態: {current.state}",
    ]
    if current.changes_on_entry:
        lines.append(CHANGES_ON_ENTRY_LINE)
    names = _stripped_names(current)
    # 行き先の場所が別の場所のときだけ書く
    if destination is not None and destination.place.strip() not in names:
        lines.append(f"行き先の場所: {_destination_name(document, destination)}")
    return lines
