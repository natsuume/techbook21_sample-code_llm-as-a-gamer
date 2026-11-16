"""grid (マス目型) のマップの場所 1 つの中で、現在地のマスから目標のマスまでの最短の経路を計算する。I/O を扱わない。

座標と tiles の読み方は notes.map_document のモジュールの docstring のとおり (x は右に、y は下に増える。マス (x, y) の
文字は tiles[y - origin.y] の x - origin.x 文字目)。
経路の決め方 (find_grid_route):
    - 上下左右の隣のマスへ 1 マスずつ動く。通れるマスは、文字が PASSABLE_TILES (. と E) で、場所の blocked の座標に
      無いマス。目標のマスは、文字と blocked によらず入ってよい
    - tiles の範囲の外へは動かない。開始のマスが範囲の外なら経路なし (None)。開始のマスの文字と blocked は問わない
    - 場所の landmarks の座標は使わない
    - 最短の経路のうち、向きを変える回数の少ないものを選ぶ。それも同じなら、1 マスずつの移動の向きの列を
      MoveDirection の並び (上・下・左・右) の順で先頭から比べ、最も前のものを選ぶ (同じ入力なら同じ経路になる)
経路を求める目標の並べ方 (list_route_targets):
    1. 場所の exits のうち、座標が tiles の範囲の中にあるもの (exits の順)
    2. from が場所の name か aliases (前後の空白を除いて比べる) で、from_tile が tiles の範囲の中にあるつながり
       (connections の順)。from_tile が 1 の出口のどれかと同じマスのつながりは目標にせず、その出口の行き先として添える
    合わせて最大 MAX_ROUTE_TARGETS 件 (先頭から)。
場所をまたぐ経路 (つながりをたどる経路) は計算しない。
"""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from switch2_layers.notes.map_document import (
    TILE_EXIT,
    TILE_PASSABLE,
    GridMapDocument,
    GridMapPlace,
    TilePosition,
)

# 経路の途中で通れるマスの文字 (目標のマスは、文字によらず入ってよい。blocked の座標のマスは、文字によらず通れない)
PASSABLE_TILES: frozenset[str] = frozenset({TILE_PASSABLE, TILE_EXIT})
# 経路を求める目標の最大の件数
MAX_ROUTE_TARGETS = 10


class MoveDirection(StrEnum):
    """1 マスの移動の向き。並びの順 (上・下・左・右) は、同じ長さで向きを変える回数も同じ経路から 1 つを選ぶ順。"""

    UP = "up"  # y が 1 減る
    DOWN = "down"  # y が 1 増える
    LEFT = "left"  # x が 1 減る
    RIGHT = "right"  # x が 1 増える


@dataclass(frozen=True)
class RouteStep:
    """経路の手順 1 つ (同じ向きに続く移動をまとめたもの)。

    direction   移動の向き
    tiles       その向きに進むマス数 (1 以上)
    """

    direction: MoveDirection
    tiles: int


@dataclass(frozen=True)
class GridRoute:
    """開始のマスから目標のマスまでの経路。

    steps   手順 (移動の順)。隣り合う手順の向きは違う。開始と目標が同じマスなら空
    """

    steps: tuple[RouteStep, ...]

    @property
    def total_tiles(self) -> int:
        """合計のマス数 (steps の tiles の和。steps が空なら 0)。"""
        return sum(step.tiles for step in self.steps)


class RouteTargetKind(StrEnum):
    """経路を求める目標の種類。"""

    EXIT = "exit"  # 場所の exits の出口
    CONNECTION = "connection"  # from_tile のあるつながり


@dataclass(frozen=True)
class RouteTarget:
    """経路を求める目標 1 つ。

    kind           目標の種類
    position       目標のマス (出口の座標か、つながりの from_tile)
    description    出口の description か、つながりの how (書かれたまま)
    destinations   行き先の場所の名前 (書かれたまま)。出口は、from_tile がその出口と同じマスのつながりの to
                   (connections の順)。無ければ空。つながりは、その to の 1 つ
    """

    kind: RouteTargetKind
    position: TilePosition
    description: str
    destinations: tuple[str, ...]


def tile_at(place: GridMapPlace, position: TilePosition) -> str | None:
    """場所 place のマス position の文字 (tiles の 1 文字) を返す。tiles の範囲の外なら None。

    範囲の中: 0 <= position.y - origin.y < tiles の行数 かつ 0 <= position.x - origin.x < その行の文字数。
    tiles が空なら、どのマスも範囲の外。
    """
    row = position.y - place.origin.y
    column = position.x - place.origin.x
    if 0 <= row < len(place.tiles) and 0 <= column < len(place.tiles[row]):
        return place.tiles[row][column]
    return None


def find_grid_place(document: GridMapDocument, place_name: str) -> GridMapPlace | None:
    """document の places から、name か aliases が place_name と一致する最初の場所を返す。無ければ None。

    前後の空白を除いて比べる (place_name も name も aliases も)。places の順に、各場所の name、続けて aliases を見る。
    place_name が前後の空白を除いて空なら None。
    """
    wanted = place_name.strip()
    if not wanted:
        return None
    for place in document.places:
        if wanted in _stripped_names(place):
            return place
    return None


def find_grid_route(
    place: GridMapPlace, start: TilePosition, goal: TilePosition
) -> GridRoute | None:
    """場所 place の中で、マス start からマス goal までの最短の経路を返す (経路の決め方はモジュールの docstring)。

    次のときは None (経路なし) を返す:
        - start が tiles の範囲の外 (tiles が空のときを含む)
        - goal が tiles の範囲の外
        - 通れるマス (文字が PASSABLE_TILES で、blocked の座標に無いマス) だけを通っては goal にたどり着けない
    start と goal が同じマスなら、手順が空 (0 マス) の経路を返す。
    """
    if tile_at(place, start) is None or tile_at(place, goal) is None:
        return None
    cells = _RouteCells(
        passable=_passable_cells(place),
        start=(start.x, start.y),
        goal=(goal.x, goal.y),
    )
    distances = _distances_to_goal(cells)
    if cells.start not in distances:
        return None
    turns_left = _fewest_turns_to_goal(cells, distances)
    # 開始のマスから、残りの向きを変える回数が最も少なくなる隣のマスへ、1 マスずつ進む。
    # 同じ回数の隣が複数あるときは、MoveDirection の並びで前の向きを選ぶ (向きの列を先頭から比べたときに最も前になる)
    directions = list(MoveDirection)
    moves: list[MoveDirection] = []
    cell = cells.start
    last: int | None = None
    while cell != cells.goal:
        best: tuple[int, int, tuple[int, int]] | None = None
        for index, neighbor in _next_cells(cell, cells, distances):
            turned = 0 if last is None or last == index else 1
            cost = turned + turns_left[neighbor][index]
            if best is None or cost < best[0]:
                best = (cost, index, neighbor)
        if best is None:
            return None
        _, last, cell = best
        moves.append(directions[last])
    return GridRoute(steps=_merge_moves(tuple(moves)))


def list_route_targets(
    document: GridMapDocument, place: GridMapPlace
) -> tuple[RouteTarget, ...]:
    """場所 place (document の場所の 1 つ) で経路を求める目標を、モジュールの docstring の順で最大
    MAX_ROUTE_TARGETS 件返す。

    - 出口の目標: place.exits のうち tile_at が None でないもの。destinations は、document.connections のうち
      from が place の場所で、from_tile がその出口と同じマスのつながりの to (connections の順)
    - つながりの目標: document.connections のうち、from が place の場所で、from_tile が None でなく tile_at が
      None でなく、どの出口の目標とも同じマスでないもの。destinations は (to,)
    from が place の場所かは、from を前後の空白を除いたものが、place の name か aliases を前後の空白を除いたものの
    どれかと一致するかで決める。
    """
    names = _stripped_names(place)
    connections = [
        connection
        for connection in document.connections
        if connection.from_place.strip() in names
        and connection.from_tile is not None
        and tile_at(place, connection.from_tile) is not None
    ]
    exits = [mark for mark in place.exits if tile_at(place, mark.position) is not None]
    exit_positions = {mark.position for mark in exits}
    targets = [
        RouteTarget(
            kind=RouteTargetKind.EXIT,
            position=mark.position,
            description=mark.description,
            destinations=tuple(
                connection.to_place
                for connection in connections
                if connection.from_tile == mark.position
            ),
        )
        for mark in exits
    ]
    targets += [
        RouteTarget(
            kind=RouteTargetKind.CONNECTION,
            position=connection.from_tile,
            description=connection.how,
            destinations=(connection.to_place,),
        )
        for connection in connections
        if connection.from_tile is not None
        and connection.from_tile not in exit_positions
    ]
    return tuple(targets[:MAX_ROUTE_TARGETS])


# 向きごとの 1 マスの移動 (x の増分, y の増分)
_DIRECTION_DELTAS: dict[MoveDirection, tuple[int, int]] = {
    MoveDirection.UP: (0, -1),
    MoveDirection.DOWN: (0, 1),
    MoveDirection.LEFT: (-1, 0),
    MoveDirection.RIGHT: (1, 0),
}


def _stripped_names(place: GridMapPlace) -> set[str]:
    """場所 place の name と aliases を、前後の空白を除いた形の集合にする。"""
    return {place.name.strip(), *(alias.strip() for alias in place.aliases)}


@dataclass(frozen=True)
class _RouteCells:
    """経路の計算に使うマス。座標は (x, y) のタプル。

    passable   経路の途中で入れるマス (文字が PASSABLE_TILES で、blocked の座標に無いマス)
    start      開始のマス (tiles の範囲の中。文字と blocked は問わない)
    goal       目標のマス (tiles の範囲の中。文字と blocked によらず入れる)
    """

    passable: frozenset[tuple[int, int]]
    start: tuple[int, int]
    goal: tuple[int, int]


def _passable_cells(place: GridMapPlace) -> frozenset[tuple[int, int]]:
    """場所 place の tiles の範囲の中で、文字が PASSABLE_TILES で、blocked の座標に無いマスの集合を返す。"""
    blocked = {(mark.position.x, mark.position.y) for mark in place.blocked}
    return frozenset(
        (place.origin.x + column, place.origin.y + row)
        for row, line in enumerate(place.tiles)
        for column, character in enumerate(line)
        if character in PASSABLE_TILES
        and (place.origin.x + column, place.origin.y + row) not in blocked
    )


def _distances_to_goal(cells: _RouteCells) -> dict[tuple[int, int], int]:
    """各マスから goal までの最短のマス数を、goal から逆向きに幅優先で数えて返す。

    数えるのは、goal と、passable のマスと、start (start からは先へ数えない。start が passable でなければ、
    経路の途中には使わないため)。start を数えたところで止める (start より遠いマスは経路に使わない)。
    goal にたどり着けないマスは含めない。
    """
    distances = {cells.goal: 0}
    queue = deque([cells.goal])
    while queue:
        cell = queue.popleft()
        for dx, dy in _DIRECTION_DELTAS.values():
            neighbor = (cell[0] + dx, cell[1] + dy)
            if neighbor in distances:
                continue
            if neighbor == cells.start:
                distances[neighbor] = distances[cell] + 1
                return distances
            if neighbor in cells.passable:
                distances[neighbor] = distances[cell] + 1
                queue.append(neighbor)
    return distances


def _next_cells(
    cell: tuple[int, int],
    cells: _RouteCells,
    distances: dict[tuple[int, int], int],
) -> Iterator[tuple[int, tuple[int, int]]]:
    """マス cell から、goal までのマス数が 1 減る隣のマスを、(MoveDirection の並びの番号, 隣のマス) で順に返す。

    入れるのは goal か passable のマスだけ。
    """
    for index, (dx, dy) in enumerate(_DIRECTION_DELTAS.values()):
        neighbor = (cell[0] + dx, cell[1] + dy)
        if (neighbor == cells.goal or neighbor in cells.passable) and distances.get(
            neighbor
        ) == distances[cell] - 1:
            yield index, neighbor


def _fewest_turns_to_goal(
    cells: _RouteCells, distances: dict[tuple[int, int], int]
) -> dict[tuple[int, int], tuple[int, ...]]:
    """start からの最短の経路に乗るマスごとに、そのマスへ着いた向き (MoveDirection の並びの番号) ごとの、goal まで
    最短のマス数で進むときの、向きを変える回数の最小を返す (goal では 0)。

    start から _next_cells をたどって着けるマスを集め、goal に近い順に計算する。
    """
    on_route = {cells.start}
    queue = deque([cells.start])
    while queue:
        cell = queue.popleft()
        for _, neighbor in _next_cells(cell, cells, distances):
            if neighbor not in on_route:
                on_route.add(neighbor)
                queue.append(neighbor)
    turns_left: dict[tuple[int, int], tuple[int, ...]] = {}
    direction_count = len(_DIRECTION_DELTAS)
    for cell in sorted(on_route, key=distances.__getitem__):
        if cell == cells.goal:
            turns_left[cell] = (0,) * direction_count
            continue
        options = [
            (index, turns_left[neighbor][index])
            for index, neighbor in _next_cells(cell, cells, distances)
        ]
        turns_left[cell] = tuple(
            min((0 if index == arrived else 1) + turns for index, turns in options)
            for arrived in range(direction_count)
        )
    return turns_left


def _merge_moves(moves: tuple[MoveDirection, ...]) -> tuple[RouteStep, ...]:
    """1 マスずつの移動の向きの列 moves を、同じ向きに続く移動をまとめた手順にする。"""
    steps: list[RouteStep] = []
    for direction in moves:
        if steps and steps[-1].direction is direction:
            steps[-1] = RouteStep(direction, steps[-1].tiles + 1)
        else:
            steps.append(RouteStep(direction, 1))
    return tuple(steps)
