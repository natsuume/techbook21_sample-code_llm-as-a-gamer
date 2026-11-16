"""middle の user メッセージの節「現在地からの経路 (コードの計算)」の中身を作る。I/O を扱わない。

前回の middle の出力 (middle/state.json) の現在地の場所 (place_name) とマス (current_tile) と、マップ (map.json) から、
現在地のマスから出口とつながりのマスまでの経路 (経路の計算は notes.grid_route) と、つながりをたどって行ける場所ごとの
道筋 (道筋の計算は notes.cross_place_route) を書く。

節の中身は、次の A・B・C をこの順に、改行 1 つでつないだもの (書かない部分は飛ばす。末尾に改行を付けない):
    部分                    書くとき
    A. 前回の現在地の行     マップが grid で、前回の出力の place_name と current_tile がどちらも None でないとき
    B. 場所の中の経路       いつも。目標ごとの行か、経路を計算しない理由の 1 行
    C. 場所をまたぐ経路     マップが grid で、前回の出力の place_name が None でなく、その場所がマップにあり
                            (name と aliases で探す)、その場所の changes_on_entry が false のとき
そのため、マップなし・layout が free・前回の出力が無いときは、B の理由の 1 行だけになる。

A. 前回の現在地の行:
    前回の middle の出力 (入力の時刻 t=<based_on_t>) の現在地: <place_name> のマス (x=<x>, y=<y>)
    - <place_name> は前回の出力の place_name。マップの場所を aliases で見つけたとき (場所の name を前後の空白を
      除いたものが place_name と違うとき) は、<place_name> の後に「 (マップの場所「<name>」)」を足す。場所がマップに
      無いときは足さない
    - t は frames.surface_frames.format_frame_label の形
B. 場所の中の経路:
    目標 (notes.grid_route.list_route_targets の順) ごとに 1 行:
        - <種類>「<説明>」(x=<x>, y=<y>)。行き先: <行き先 1>、<行き先 2>。手順: <経路の文>
    - <種類> は出口なら「出口」、つながりなら「つながり」。<説明> は出口の description かつながりの how。
      行き先が無ければ「。行き先: ...」の部分を書かない
    - <経路の文> は describe_route_in_place(場所, current_tile, 目標の position, connection_from_tiles(マップ, 場所))
      の文。通れるマスだけで経路があれば format_route の文 (例: 「上に 3 マス → 左に 2 マス (合計 5 マス)」)、
      無ければ、未確認のマス (?) も通れるとした経路の文 (format_unknown_tile_route)
    次のときは、目標の行を書かずに理由を 1 行だけ書く (上から順に確かめ、最初に当たったもの):
        1. マップなし                                      ROUTE_NO_MAP_TEXT
        2. マップの layout が free                         ROUTE_FREE_LAYOUT_TEXT
        3. 前回の出力が無い                                ROUTE_NO_PREVIOUS_OUTPUT_TEXT
        4. 前回の出力の place_name か current_tile が None  ROUTE_NO_CURRENT_TILE_TEXT
        5. 場所がマップに無い (name と aliases で探す)     「前回の middle の出力の現在地「<place_name>」がマップの
                                                           場所 (name と aliases) に無いため、経路を計算しない」
        6. 場所の changes_on_entry が true                 「マップの場所「<name>」は入るたびに構造が変わる
                                                           (changes_on_entry が true) ため、経路を計算しない」
        7. 場所の tiles が空                               「マップの場所「<name>」のマスの図 (tiles) が空のため、
                                                           経路を計算しない」
        8. 開始のマス (current_tile) が tiles の範囲の外   「現在地のマス (x=<x>, y=<y>) がマップの場所「<name>」の
                                                           マスの図 (tiles) の範囲の外のため、経路を計算しない」
        9. 目標が無い                                      「マップの場所「<name>」に、マスの図 (tiles) の範囲の中の
                                                           出口とつながりが無いため、経路を計算しない」
C. 場所をまたぐ経路: describe_cross_place_routes(マップ, 場所, current_tile, B に書いた目標) の行
    (B が理由の行なら、B に書いた目標は無い)。
<name> はマップの場所の name。名前・説明・how は、マップと前回の出力に書かれたまま。
経路は、その場所から出るつながりの from_tile のマスを、目標でなければ途中で通らない
(notes.grid_route.connection_from_tiles を find_grid_route と find_unknown_tile_route の avoided に渡す)。
"""

from collections.abc import Collection, Sequence

from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.notes.cross_place_route import (
    CrossPlaceLeg,
    CrossPlaceRoute,
    find_cross_place_routes,
)
from switch2_layers.notes.grid_route import (
    GridRoute,
    MoveDirection,
    RouteTarget,
    RouteTargetKind,
    UnknownTileRoute,
    connection_from_tiles,
    find_grid_place,
    find_grid_route,
    find_unknown_tile_route,
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
# 経路が無い目標の手順の文 (通れるマスだけではたどり着けない)
NO_ROUTE_TEXT = "経路なし (通れるマスだけではたどり着けない)"
# 未確認のマス (?) も通れるとしても経路が無い目標の手順の文
NO_ROUTE_THROUGH_UNKNOWN_TEXT = "経路なし (未確認のマス (?) を通ってもたどり着けない)"
# 場所をまたぐ経路 (C) の最初の行 (行き先があるとき)
CROSS_PLACE_ROUTES_HEADING = "場所をまたぐ経路 (つながりをたどる):"
# 場所をまたぐ経路 (C) の行き先が無いときの 1 行
NO_CROSS_PLACE_DESTINATION_TEXT = "場所をまたぐ経路: 行ける場所は無い"
# 場所をまたぐ経路の区間で、場所の中の経路を計算しないときの手順の文
NO_TILE_STEPS_TEXT = "(マスの手順なし)"
# 場所をまたぐ経路の行き先の最大の件数
MAX_CROSS_PLACE_DESTINATIONS = 10
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
    return f"{_steps_text(route)} (合計 {route.total_tiles} マス)"


def format_unknown_tile_route(route: UnknownTileRoute | None) -> str:
    """通れるマスだけでは経路が無い目標について、未確認のマス (?) も通れるとした経路 route を文にする。

    - None (? を通っても経路なし): NO_ROUTE_THROUGH_UNKNOWN_TEXT
    - それ以外: 「<NO_ROUTE_TEXT>。未確認のマス (?) も通れるとすると: <手順> (合計 <合計のマス数> マス、うち未確認
      <unknown_tiles> マス)」。<手順> は、手順ごとの「<向き>に <マス数> マス」(向きは DIRECTION_LABELS) を「 → 」で
      つないだもの (format_route と同じ)。手順が空なら <手順> は「移動しない」。
      例: 「経路なし (通れるマスだけではたどり着けない)。未確認のマス (?) も通れるとすると: 上に 3 マス → 左に 2 マス
      (合計 5 マス、うち未確認 1 マス)」
    """
    if route is None:
        return NO_ROUTE_THROUGH_UNKNOWN_TEXT
    return (
        f"{NO_ROUTE_TEXT}。未確認のマス (?) も通れるとすると: {_steps_text(route.route)} "
        f"(合計 {route.route.total_tiles} マス、うち未確認 {route.unknown_tiles} マス)"
    )


def describe_route_in_place(
    place: GridMapPlace,
    start: TilePosition,
    goal: TilePosition,
    avoided: Collection[TilePosition],
) -> str:
    """場所 place の中の、マス start からマス goal までの経路の文を返す (B の目標の行の「手順: 」の後ろと、C の区間の
    手順の文に使う)。

    notes.grid_route.find_grid_route(place, start, goal, avoided) が経路を返せば、format_route の文 (通れるマスだけ
    で行ける目標の文)。None なら、notes.grid_route.find_unknown_tile_route(place, start, goal, avoided) の結果を
    format_unknown_tile_route にした文。
    """
    route = find_grid_route(place, start, goal, avoided)
    if route is not None:
        return format_route(route)
    return format_unknown_tile_route(
        find_unknown_tile_route(place, start, goal, avoided)
    )


def describe_cross_place_routes(
    document: GridMapDocument,
    place: GridMapPlace,
    current_tile: TilePosition | None,
    targets: Sequence[RouteTarget],
) -> str:
    """節の C (場所をまたぐ経路) の行を、改行 1 つでつないで返す (末尾に改行を付けない)。

    document       grid のマップ
    place          前回の現在地の場所 (document.places の要素で、changes_on_entry は false)
    current_tile   前回の出力の current_tile (None は無い)
    targets        B に書いた目標 (B が理由の行なら空)
    行き先: notes.cross_place_route.find_cross_place_routes(document, place) の道筋 (つながりの数の少ない順、同じなら
    document.places の順) から、次のものを除いた先頭の MAX_CROSS_PLACE_DESTINATIONS 件:
        - つながり 1 件の道筋で、行き先の場所が、targets のどれかの destinations の名前を
          notes.grid_route.find_grid_place で当てはめた場所 (同じ要素) と同じもの
    書き方:
        行き先があるとき: 1 行目に CROSS_PLACE_ROUTES_HEADING、続けて行き先ごとに 1 行
            - 「<行き先の name>」まで (つながり <区間の数> 件): <区間 1> → <区間 2> → … → 「<行き先の name>」
        行き先が無いとき: NO_CROSS_PLACE_DESTINATION_TEXT の 1 行だけ
    区間 (道筋の legs ごと): 「<区間の場所の name>」の「<つながりの how>」(x=<x>, y=<y>)へ <手順の文>
        - 座標はつながりの from_tile。None なら「(x=<x>, y=<y>)」の部分を書かない (「…の「<how>」へ <手順の文>」)
        - 区間の開始のマスは、1 つ目の区間では current_tile、2 つ目からは 1 つ前の区間のつながりの to_tile
        - 手順の文: 開始のマスと from_tile がどちらも None でなく、どちらも区間の場所の tiles の範囲の中にある
          (notes.grid_route.tile_at が None でない) ときは、describe_route_in_place(区間の場所, 開始のマス, from_tile,
          notes.grid_route.connection_from_tiles(document, 区間の場所)) の文。それ以外は NO_TILE_STEPS_TEXT
    例: 「- 「港町」まで (つながり 2 件): 「始まりの町」の「北の道へ進む」(x=10, y=0)へ 上に 7 マス (合計 7 マス) →
    「北の道」の「北の端」(x=8, y=0)へ (マスの手順なし) → 「港町」」
    """
    # 目標の行の行き先に出ている場所 (つながり 1 件で行けるなら、場所をまたぐ経路に重ねて書かない)
    on_target_lines = _destination_places(document, targets)
    lines: list[str] = []
    for route in find_cross_place_routes(document, place):
        if len(route.legs) == 1 and any(
            route.destination is found for found in on_target_lines
        ):
            continue
        lines.append(_cross_place_line(document, route, current_tile))
        if len(lines) == MAX_CROSS_PLACE_DESTINATIONS:
            break
    if not lines:
        return NO_CROSS_PLACE_DESTINATION_TEXT
    return "\n".join([CROSS_PLACE_ROUTES_HEADING, *lines])


def describe_route_section(
    document: MapDocument | GridMapDocument | None, state: MiddleState | None
) -> str:
    """節「現在地からの経路 (コードの計算)」の中身 (モジュールの docstring の A・B・C) を返す。

    document   入力のマップ (MiddleInput.map_document)。None はマップなし
    state      前回の middle の出力 (MiddleInput.previous_state)。None は前回の出力が無い
    場所は notes.grid_route.find_grid_place(document, state.place_name) で探し、目標は
    notes.grid_route.list_route_targets(document, 場所)、目標の行の経路の文は describe_route_in_place(場所,
    state.current_tile, 目標の position, notes.grid_route.connection_from_tiles(document, 場所)) で求める。
    開始のマスが範囲の外かは notes.grid_route.tile_at で確かめる。C は describe_cross_place_routes(document, 場所,
    state.current_tile, B に書いた目標) で求める。
    """
    if document is None:
        return ROUTE_NO_MAP_TEXT
    if not isinstance(document, GridMapDocument):
        return ROUTE_FREE_LAYOUT_TEXT
    if state is None:
        return ROUTE_NO_PREVIOUS_OUTPUT_TEXT
    place = (
        None
        if state.place_name is None
        else find_grid_place(document, state.place_name)
    )
    lines: list[str] = []
    if state.place_name is not None and state.current_tile is not None:
        lines.append(
            _header_line(state.place_name, state.based_on_t, place, state.current_tile)
        )
    place_lines, targets = _place_route_lines(document, state, place)
    lines += place_lines
    if place is not None and not place.changes_on_entry:
        lines.append(
            describe_cross_place_routes(document, place, state.current_tile, targets)
        )
    return "\n".join(lines)


def _place_route_lines(
    document: GridMapDocument, state: MiddleState, place: GridMapPlace | None
) -> tuple[list[str], tuple[RouteTarget, ...]]:
    """節の B (場所の中の経路) の行と、その行に書いた目標を返す (モジュールの docstring)。

    place は state.place_name をマップの場所に当てはめたもの (place_name が None か、マップに無ければ None)。
    経路を計算しないときは、理由の 1 行と空の目標を返す (理由はモジュールの docstring の 4. から順に確かめ、最初に
    当たったもの。1.〜3. は呼び出し側が先に確かめる)。
    """
    start = state.current_tile
    if state.place_name is None or start is None:
        return [ROUTE_NO_CURRENT_TILE_TEXT], ()
    if place is None:
        reason = (
            f"前回の middle の出力の現在地「{state.place_name}」がマップの場所 (name と aliases) に無いため、"
            "経路を計算しない"
        )
    elif place.changes_on_entry:
        reason = (
            f"マップの場所「{place.name}」は入るたびに構造が変わる (changes_on_entry が true) ため、"
            "経路を計算しない"
        )
    elif not place.tiles:
        reason = f"マップの場所「{place.name}」のマスの図 (tiles) が空のため、経路を計算しない"
    elif tile_at(place, start) is None:
        reason = (
            f"現在地のマス {_position_text(start)} がマップの場所「{place.name}」のマスの図 (tiles) の"
            "範囲の外のため、経路を計算しない"
        )
    else:
        targets = list_route_targets(document, place)
        if targets:
            avoided = connection_from_tiles(document, place)
            lines = [_target_line(place, start, target, avoided) for target in targets]
            return lines, targets
        reason = (
            f"マップの場所「{place.name}」に、マスの図 (tiles) の範囲の中の出口とつながりが無いため、"
            "経路を計算しない"
        )
    return [reason], ()


def _position_text(position: TilePosition) -> str:
    """座標 position を「(x=<x>, y=<y>)」の文にする。"""
    return f"(x={position.x}, y={position.y})"


def _steps_text(route: GridRoute) -> str:
    """経路 route の手順を、手順ごとの「<向き>に <マス数> マス」を「 → 」でつないだ文にする。手順が空なら「移動しない」。"""
    if not route.steps:
        return "移動しない"
    return " → ".join(
        f"{DIRECTION_LABELS[step.direction]}に {step.tiles} マス"
        for step in route.steps
    )


def _header_line(
    place_name: str,
    based_on_t: float,
    place: GridMapPlace | None,
    start: TilePosition,
) -> str:
    """節の A の行 (前回の出力の現在地の場所・マスと、その出力の入力の時刻) を返す (モジュールの docstring)。

    place は place_name を当てはめたマップの場所 (マップに無ければ None。そのときは「 (マップの場所「<name>」)」を
    足さない)。
    """
    shown_name = place_name
    if place is not None and place.name.strip() != place_name:
        shown_name += f" (マップの場所「{place.name}」)"
    return (
        f"前回の middle の出力 (入力の時刻 {format_frame_label(based_on_t)}) の現在地: "
        f"{shown_name} のマス {_position_text(start)}"
    )


def _target_line(
    place: GridMapPlace,
    start: TilePosition,
    target: RouteTarget,
    avoided: frozenset[TilePosition],
) -> str:
    """目標 target の 1 行 (種類・説明・座標・行き先・手順) を返す (モジュールの docstring)。

    avoided は、経路の途中で通らないマス (その場所から出るつながりの from_tile)。手順は describe_route_in_place の文。
    """
    destinations = (
        f"。行き先: {'、'.join(target.destinations)}" if target.destinations else ""
    )
    steps = describe_route_in_place(place, start, target.position, avoided)
    return (
        f"- {_TARGET_KIND_LABELS[target.kind]}「{target.description}」"
        f"{_position_text(target.position)}{destinations}。手順: {steps}"
    )


def _destination_places(
    document: GridMapDocument, targets: Sequence[RouteTarget]
) -> list[GridMapPlace]:
    """targets の destinations の名前を、find_grid_place でマップの場所に当てはめたもの (当てはまらない名前は除く)
    を返す。"""
    places: list[GridMapPlace] = []
    for target in targets:
        for name in target.destinations:
            found = find_grid_place(document, name)
            if found is not None:
                places.append(found)
    return places


def _cross_place_line(
    document: GridMapDocument,
    route: CrossPlaceRoute,
    current_tile: TilePosition | None,
) -> str:
    """場所をまたぐ道筋 route の 1 行 (describe_cross_place_routes の書き方) を返す。

    区間の開始のマスは、1 つ目の区間では current_tile、2 つ目からは 1 つ前の区間のつながりの to_tile。
    """
    legs: list[str] = []
    start = current_tile
    for leg in route.legs:
        legs.append(_leg_text(document, leg, start))
        start = leg.connection.to_tile
    destination = f"「{route.destination.name}」"
    return (
        f"- {destination}まで (つながり {len(route.legs)} 件): "
        f"{' → '.join(legs)} → {destination}"
    )


def _leg_text(
    document: GridMapDocument, leg: CrossPlaceLeg, start: TilePosition | None
) -> str:
    """区間 leg の文 (「<場所の name>」の「<how>」(x=<x>, y=<y>)へ <手順の文>) を返す。start は区間の開始のマス。

    開始のマスとつながりの from_tile がどちらもあり、どちらも区間の場所の tiles の範囲の中にあるときだけ、
    describe_route_in_place で手順の文を求める。それ以外は NO_TILE_STEPS_TEXT。
    """
    goal = leg.connection.from_tile
    position = "" if goal is None else _position_text(goal)
    steps = NO_TILE_STEPS_TEXT
    if (
        start is not None
        and goal is not None
        and tile_at(leg.place, start) is not None
        and tile_at(leg.place, goal) is not None
    ):
        steps = describe_route_in_place(
            leg.place, start, goal, connection_from_tiles(document, leg.place)
        )
    return f"「{leg.place.name}」の「{leg.connection.how}」{position}へ {steps}"
