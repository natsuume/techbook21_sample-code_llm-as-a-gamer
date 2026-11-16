"""middle の user メッセージの節「現在地からの経路 (コードの計算)」の中身を作る。I/O を扱わない。

前回の middle の出力 (middle/state.json) の現在地の場所 (place_name) とマス (current_tile) と、マップ (map.json) から、
grid のマップのときだけ、現在地のマスから出口とつながりのマスまでの最短の経路を書く (経路の計算は notes.grid_route)。

経路を書くときの中身 (行は改行 1 つでつなぎ、末尾に改行を付けない):
    前回の middle の出力 (入力の時刻 t=<based_on_t>) の現在地: <place_name> のマス (x=<x>, y=<y>)
    - <種類>「<説明>」(x=<x>, y=<y>)。行き先: <行き先 1>、<行き先 2>。手順: <経路>
    ...
- 1 行目の <place_name> は前回の出力の place_name。マップの場所を aliases で見つけたとき (場所の name を前後の空白を
  除いたものが place_name と違うとき) は、<place_name> の後に「 (マップの場所「<name>」)」を足す
- 2 行目からは目標 (notes.grid_route.list_route_targets の順) ごとに 1 行。<種類> は出口なら「出口」、つながりなら
  「つながり」。<説明> は出口の description かつながりの how。行き先が無ければ「。行き先: ...」の部分を書かない
- <経路> は format_route の文 (例: 「上に 3 マス → 左に 2 マス (合計 5 マス)」)
- t は frames.surface_frames.format_frame_label の形。名前と説明は、マップと前回の出力に書かれたまま
次のときは、経路を書かずに理由を 1 行だけ書く (上から順に確かめ、最初に当たったもの):
    1. マップなし                                      ROUTE_NO_MAP_TEXT
    2. マップの layout が free                         ROUTE_FREE_LAYOUT_TEXT
    3. 前回の出力が無い                                ROUTE_NO_PREVIOUS_OUTPUT_TEXT
    4. 前回の出力の place_name か current_tile が None  ROUTE_NO_CURRENT_TILE_TEXT
    5. 場所がマップに無い (name と aliases で探す)     「前回の middle の出力の現在地「<place_name>」がマップの場所
                                                       (name と aliases) に無いため、経路を計算しない」
    6. 場所の tiles が空                               「マップの場所「<name>」のマスの図 (tiles) が空のため、
                                                       経路を計算しない」
    7. 開始のマス (current_tile) が tiles の範囲の外   「現在地のマス (x=<x>, y=<y>) がマップの場所「<name>」の
                                                       マスの図 (tiles) の範囲の外のため、経路を計算しない」
    8. 目標が無い                                      「マップの場所「<name>」に、マスの図 (tiles) の範囲の中の
                                                       出口とつながりが無いため、経路を計算しない」
<name> はマップの場所の name (書かれたまま)。
"""

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.notes.grid_route import (
    GridRoute,
    MoveDirection,
    RouteTarget,
    RouteTargetKind,
    find_grid_place,
    find_grid_route,
    list_route_targets,
    tile_at,
)
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapDocument,
    TilePosition,
)

# 節の見出し (luna_request.USER_SECTION_TITLES の「マップ」の直後に置く)
ROUTE_SECTION_TITLE = "現在地からの経路 (コードの計算)"
# 経路を書かないときの理由の行 (場所の名前や座標を含まないもの)
ROUTE_NO_MAP_TEXT = "マップが無いため、経路を計算しない"
ROUTE_FREE_LAYOUT_TEXT = "マップが自由型 (layout が free) のため、経路を計算しない"
ROUTE_NO_PREVIOUS_OUTPUT_TEXT = "前回の middle の出力が無いため、経路を計算しない"
ROUTE_NO_CURRENT_TILE_TEXT = (
    "前回の middle の出力に、現在地の場所 (place_name) かマス (current_tile) が無いため、"
    "経路を計算しない"
)
# 経路が無い目標の手順の文
NO_ROUTE_TEXT = "経路なし (通れるマスだけではたどり着けない)"
# 手順の向きの書き方
DIRECTION_LABELS: dict[MoveDirection, str] = {
    MoveDirection.UP: "上",
    MoveDirection.DOWN: "下",
    MoveDirection.LEFT: "左",
    MoveDirection.RIGHT: "右",
}
# 目標の種類の書き方
_TARGET_KIND_LABELS: dict[RouteTargetKind, str] = {
    RouteTargetKind.EXIT: "出口",
    RouteTargetKind.CONNECTION: "つながり",
}


def format_route(route: GridRoute | None) -> str:
    """経路 route を、節の 1 行の「手順: 」の後ろに書く文にする。

    - None (経路なし): NO_ROUTE_TEXT
    - 手順が空 (0 マス): 「移動しない (合計 0 マス)」
    - それ以外: 手順ごとに「<向き>に <マス数> マス」(向きは DIRECTION_LABELS) を「 → 」でつなぎ、
      後ろに「 (合計 <合計のマス数> マス)」を付ける。例: 「上に 3 マス → 左に 2 マス (合計 5 マス)」
    """
    if route is None:
        return NO_ROUTE_TEXT
    if not route.steps:
        return "移動しない (合計 0 マス)"
    steps = " → ".join(
        f"{DIRECTION_LABELS[step.direction]}に {step.tiles} マス"
        for step in route.steps
    )
    return f"{steps} (合計 {route.total_tiles} マス)"


def describe_route_section(
    document: MapDocument | GridMapDocument | None, state: MiddleState | None
) -> str:
    """節「現在地からの経路 (コードの計算)」の中身 (モジュールの docstring の形) を返す。

    document   入力のマップ (MiddleInput.map_document)。None はマップなし
    state      前回の middle の出力 (MiddleInput.previous_state)。None は前回の出力が無い
    場所は notes.grid_route.find_grid_place(document, state.place_name) で探し、目標は
    notes.grid_route.list_route_targets(document, 場所)、経路は notes.grid_route.find_grid_route(場所,
    state.current_tile, 目標の position) で求める。開始のマスが範囲の外かは notes.grid_route.tile_at で確かめる。
    """
    if document is None:
        return ROUTE_NO_MAP_TEXT
    if not isinstance(document, GridMapDocument):
        return ROUTE_FREE_LAYOUT_TEXT
    if state is None:
        return ROUTE_NO_PREVIOUS_OUTPUT_TEXT
    if state.place_name is None or state.current_tile is None:
        return ROUTE_NO_CURRENT_TILE_TEXT
    place = find_grid_place(document, state.place_name)
    if place is None:
        return (
            f"前回の middle の出力の現在地「{state.place_name}」がマップの場所 (name と aliases) に無いため、"
            "経路を計算しない"
        )
    if not place.tiles:
        return f"マップの場所「{place.name}」のマスの図 (tiles) が空のため、経路を計算しない"
    start = state.current_tile
    if tile_at(place, start) is None:
        return (
            f"現在地のマス {_position_text(start)} がマップの場所「{place.name}」のマスの図 (tiles) の"
            "範囲の外のため、経路を計算しない"
        )
    targets = list_route_targets(document, place)
    if not targets:
        return (
            f"マップの場所「{place.name}」に、マスの図 (tiles) の範囲の中の出口とつながりが無いため、"
            "経路を計算しない"
        )
    return "\n".join(
        [
            _header_line(state, place, start),
            *(_target_line(place, start, target) for target in targets),
        ]
    )


def _position_text(position: TilePosition) -> str:
    """座標 position を「(x=<x>, y=<y>)」の文にする。"""
    return f"(x={position.x}, y={position.y})"


def _header_line(state: MiddleState, place: GridMapPlace, start: TilePosition) -> str:
    """節の 1 行目 (前回の出力の現在地の場所・マスと、その出力の入力の時刻) を返す (モジュールの docstring)。"""
    place_name = state.place_name or ""
    if place.name.strip() != place_name:
        place_name += f" (マップの場所「{place.name}」)"
    return (
        f"前回の middle の出力 (入力の時刻 {format_frame_label(state.based_on_t)}) の現在地: "
        f"{place_name} のマス {_position_text(start)}"
    )


def _target_line(place: GridMapPlace, start: TilePosition, target: RouteTarget) -> str:
    """目標 target の 1 行 (種類・説明・座標・行き先・手順) を返す (モジュールの docstring)。"""
    destinations = (
        f"。行き先: {'、'.join(target.destinations)}" if target.destinations else ""
    )
    route = find_grid_route(place, start, target.position)
    return (
        f"- {_TARGET_KIND_LABELS[target.kind]}「{target.description}」"
        f"{_position_text(target.position)}{destinations}。手順: {format_route(route)}"
    )
