"""grid (マス目型) のマップの場所 1 つの中で、現在地のマスから目標のマスまでの最短の経路を計算する。I/O を扱わない。

座標と tiles の読み方は notes.map_document のモジュールの docstring のとおり (x は右に、y は下に増える。マス (x, y) の
文字は tiles[y - origin.y] の x - origin.x 文字目)。
経路の決め方 (find_grid_route):
    - 上下左右の隣のマスへ 1 マスずつ動く。通れるマスは PASSABLE_TILES (. と E)。目標のマスは、文字によらず入ってよい
    - tiles の範囲の外へは動かない。開始のマスが範囲の外なら経路なし (None)。開始のマスの文字は問わない
    - 場所の blocked・landmarks の座標は使わない (通れるかは tiles の文字だけで決める)
    - 最短の経路のうち、向きを変える回数の少ないものを選ぶ。それも同じなら、1 マスずつの移動の向きの列を
      MoveDirection の並び (上・下・左・右) の順で先頭から比べ、最も前のものを選ぶ (同じ入力なら同じ経路になる)
経路を求める目標の並べ方 (list_route_targets):
    1. 場所の exits のうち、座標が tiles の範囲の中にあるもの (exits の順)
    2. from が場所の name か aliases (前後の空白を除いて比べる) で、from_tile が tiles の範囲の中にあるつながり
       (connections の順)。from_tile が 1 の出口のどれかと同じマスのつながりは目標にせず、その出口の行き先として添える
    合わせて最大 MAX_ROUTE_TARGETS 件 (先頭から)。
場所をまたぐ経路 (つながりをたどる経路) は計算しない。
"""

from dataclasses import dataclass
from enum import StrEnum

from switch2_layers.notes.map_document import (
    TILE_EXIT,
    TILE_PASSABLE,
    GridMapDocument,
    GridMapPlace,
    TilePosition,
)

# 経路の途中で通れるマスの文字 (目標のマスは、文字によらず入ってよい)
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
        raise NotImplementedError


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
    raise NotImplementedError


def find_grid_place(document: GridMapDocument, place_name: str) -> GridMapPlace | None:
    """document の places から、name か aliases が place_name と一致する最初の場所を返す。無ければ None。

    前後の空白を除いて比べる (place_name も name も aliases も)。places の順に、各場所の name、続けて aliases を見る。
    place_name が前後の空白を除いて空なら None。
    """
    raise NotImplementedError


def find_grid_route(
    place: GridMapPlace, start: TilePosition, goal: TilePosition
) -> GridRoute | None:
    """場所 place の中で、マス start からマス goal までの最短の経路を返す (経路の決め方はモジュールの docstring)。

    次のときは None (経路なし) を返す:
        - start が tiles の範囲の外 (tiles が空のときを含む)
        - goal が tiles の範囲の外
        - 通れるマス (PASSABLE_TILES) だけを通っては goal にたどり着けない
    start と goal が同じマスなら、手順が空 (0 マス) の経路を返す。
    """
    raise NotImplementedError


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
    raise NotImplementedError
