import pytest

from switch2_layers.notes.grid_route import (
    MAX_ROUTE_TARGETS,
    PASSABLE_TILES,
    GridRoute,
    MoveDirection,
    RouteStep,
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
    MapConnection,
    TileMark,
    TilePosition,
)

UP = MoveDirection.UP
DOWN = MoveDirection.DOWN
LEFT = MoveDirection.LEFT
RIGHT = MoveDirection.RIGHT


def place(
    tiles: list[str],
    origin: tuple[int, int] = (0, 0),
    name: str = "家の 1 階",
    aliases: tuple[str, ...] = (),
    exits: tuple[TileMark, ...] = (),
    blocked: tuple[TileMark, ...] = (),
    landmarks: tuple[TileMark, ...] = (),
) -> GridMapPlace:
    return GridMapPlace(
        name=name,
        aliases=aliases,
        description="",
        state="",
        changes_on_entry=False,
        origin=TilePosition(*origin),
        tiles=tuple(tiles),
        exits=exits,
        blocked=blocked,
        landmarks=landmarks,
    )


def route(*steps: tuple[MoveDirection, int]) -> GridRoute:
    return GridRoute(
        steps=tuple(RouteStep(direction, tiles) for direction, tiles in steps)
    )


# issue の I/O 契約の例の場所 (机 (4, 1) は、tiles では . だが blocked にある)
HOUSE_TILES = ["#########", "#.......#", "#E......#"]
HOUSE = place(
    HOUSE_TILES,
    exits=(TileMark(TilePosition(1, 2), "左下の赤いマット"),),
    blocked=(TileMark(TilePosition(4, 1), "机"),),
    landmarks=(TileMark(TilePosition(6, 1), "テレビ"),),
)
OPEN_3X3 = ["...", "...", "..."]


# --- マスの文字 ---


def test_tile_at_reads_tiles_with_origin():
    house = place(HOUSE_TILES)
    assert tile_at(house, TilePosition(0, 0)) == "#"
    assert tile_at(house, TilePosition(1, 2)) == "E"
    assert tile_at(house, TilePosition(7, 1)) == "."
    # 範囲の外
    assert tile_at(house, TilePosition(9, 1)) is None
    assert tile_at(house, TilePosition(1, 3)) is None
    assert tile_at(house, TilePosition(-1, 0)) is None
    assert tile_at(house, TilePosition(0, -1)) is None


def test_tile_at_with_negative_origin():
    # マス (x, y) は tiles[y - origin.y] の x - origin.x 文字目
    shifted = place(["#E#", "..?"], origin=(-3, -2))
    assert tile_at(shifted, TilePosition(-3, -2)) == "#"
    assert tile_at(shifted, TilePosition(-2, -2)) == "E"
    assert tile_at(shifted, TilePosition(-1, -1)) == "?"
    assert tile_at(shifted, TilePosition(0, -1)) is None
    assert tile_at(shifted, TilePosition(-1, 0)) is None


def test_tile_at_of_empty_tiles_is_always_outside():
    assert tile_at(place([]), TilePosition(0, 0)) is None


def test_passable_tiles():
    assert PASSABLE_TILES == frozenset({".", "E"})


# --- 経路 ---


def test_route_from_the_issue_example():
    # (7, 1) から左下の赤いマット (1, 2) へ。最短 7 マスで向きを 1 回変える経路は、下 → 左 と、左 → 下 の 2 つ。
    # 左 → 下 は机 (4, 1) を通るので通れず、下 → 左 になる
    found = find_grid_route(HOUSE, TilePosition(7, 1), TilePosition(1, 2))
    assert found == route((DOWN, 1), (LEFT, 6))
    assert found.total_tiles == 7


# --- 通れないマス (blocked) ---


def test_blocked_tile_is_not_passable_even_on_a_passable_character():
    # 机 (4, 1) は tiles では . だが、blocked にあるので通らない。左に 6 マスの経路の代わりに下の行を回る。
    # 下の行の左端 (1, 2) は出口なので、その手前で上の行へ戻る
    assert find_grid_route(
        place(HOUSE_TILES), TilePosition(7, 1), TilePosition(1, 1)
    ) == route((LEFT, 6))
    found = find_grid_route(HOUSE, TilePosition(7, 1), TilePosition(1, 1))
    assert found == route((DOWN, 1), (LEFT, 4), (UP, 1), (LEFT, 2))
    assert found.total_tiles == 8


def test_blocked_tile_can_close_the_only_way():
    corridor = place(["....."], blocked=(TileMark(TilePosition(2, 0), "岩"),))
    assert find_grid_route(corridor, TilePosition(0, 0), TilePosition(4, 0)) is None


def test_blocked_goal_can_be_entered():
    # 目標のマスは、blocked にあっても入ってよい
    found = find_grid_route(HOUSE, TilePosition(7, 1), TilePosition(4, 1))
    assert found == route((LEFT, 3))


def test_start_on_a_blocked_tile_does_not_matter():
    # 開始のマスは、blocked にあっても問わない
    found = find_grid_route(HOUSE, TilePosition(4, 1), TilePosition(1, 1))
    assert found == route((LEFT, 3))


def test_blocked_outside_the_tiles_and_landmarks_do_not_change_the_route():
    # tiles の範囲の外の blocked と、landmarks の座標は経路に関わらない
    marked = place(
        ["....."],
        blocked=(TileMark(TilePosition(9, 9), "画面の外の壁"),),
        landmarks=(TileMark(TilePosition(2, 0), "看板"),),
    )
    found = find_grid_route(marked, TilePosition(0, 0), TilePosition(4, 0))
    assert found == route((RIGHT, 4))


def test_same_tile_is_a_route_of_zero_tiles():
    found = find_grid_route(place(HOUSE_TILES), TilePosition(7, 1), TilePosition(7, 1))
    assert found == GridRoute(steps=())
    assert found.total_tiles == 0


def test_same_tile_on_a_wall_is_a_route_of_zero_tiles():
    # 開始のマスの文字は問わない (範囲の中なら、壁の上でも 0 マスの経路)
    found = find_grid_route(place(HOUSE_TILES), TilePosition(0, 0), TilePosition(0, 0))
    assert found == GridRoute(steps=())


def test_route_goes_around_walls():
    found = find_grid_route(
        place([".#.", ".#.", "..."]), TilePosition(0, 0), TilePosition(2, 0)
    )
    assert found == route((DOWN, 2), (RIGHT, 2), (UP, 2))
    assert found.total_tiles == 6


def test_moves_in_the_same_direction_are_merged():
    found = find_grid_route(place(["....."]), TilePosition(0, 0), TilePosition(4, 0))
    assert found == route((RIGHT, 4))
    assert found.total_tiles == 4


def test_route_with_several_turns():
    found = find_grid_route(
        place(["..#", "#..", "#.."]), TilePosition(0, 0), TilePosition(2, 2)
    )
    assert found == route((RIGHT, 1), (DOWN, 2), (RIGHT, 1))


def test_exit_tile_that_is_not_the_goal_is_avoided():
    # 目標でない出口のマス (E) は、途中で踏まずに回り込む (乗ると場所を移るため)
    found = find_grid_route(
        place(["...", ".E.", "..."]), TilePosition(0, 1), TilePosition(2, 1)
    )
    assert found == route((UP, 1), (RIGHT, 2), (DOWN, 1))
    assert found.total_tiles == 4


def test_exit_coordinates_on_a_passable_tile_are_avoided():
    # exits の座標のマスは、tiles の文字が . でも出口として避ける
    with_exit = place(OPEN_3X3, exits=(TileMark(TilePosition(1, 1), "床のマット"),))
    assert find_grid_route(
        place(OPEN_3X3), TilePosition(0, 1), TilePosition(2, 1)
    ) == route((RIGHT, 2))
    found = find_grid_route(with_exit, TilePosition(0, 1), TilePosition(2, 1))
    assert found == route((UP, 1), (RIGHT, 2), (DOWN, 1))


@pytest.mark.parametrize(
    "corridor",
    [place([".E."]), place(["..."], exits=(TileMark(TilePosition(1, 0), "扉"),))],
    ids=["exit-tile", "exit-coordinates"],
)
def test_route_is_none_when_the_only_way_is_through_another_exit(corridor):
    assert find_grid_route(corridor, TilePosition(0, 0), TilePosition(2, 0)) is None


def test_exit_goal_and_exit_start_are_allowed():
    # 目標の出口には入ってよい。開始のマスが出口でも問わない
    assert find_grid_route(
        place(["..E"]), TilePosition(0, 0), TilePosition(2, 0)
    ) == route((RIGHT, 2))
    assert find_grid_route(
        place([".E."]), TilePosition(1, 0), TilePosition(2, 0)
    ) == route((RIGHT, 1))


@pytest.mark.parametrize("blocking", ["#", "?"])
def test_wall_and_unknown_tiles_are_not_passable(blocking):
    found = find_grid_route(
        place([f".{blocking}."]), TilePosition(0, 0), TilePosition(2, 0)
    )
    assert found is None


@pytest.mark.parametrize("goal_tile", ["#", "?", "E", "."])
def test_goal_tile_can_be_entered_whatever_its_character(goal_tile):
    found = find_grid_route(
        place([f"..{goal_tile}"]), TilePosition(0, 0), TilePosition(2, 0)
    )
    assert found == route((RIGHT, 2))


def test_start_tile_character_does_not_matter():
    found = find_grid_route(place(["#.."]), TilePosition(0, 0), TilePosition(2, 0))
    assert found == route((RIGHT, 2))
    found = find_grid_route(place(["?.."]), TilePosition(0, 0), TilePosition(2, 0))
    assert found == route((RIGHT, 2))


def test_unreachable_goal_has_no_route():
    assert (
        find_grid_route(place(["..#.."]), TilePosition(0, 0), TilePosition(4, 0))
        is None
    )
    # 壁に囲まれたマス
    enclosed = place(["#####", "#...#", "##.##", "#.#.#", "#####"])
    assert find_grid_route(enclosed, TilePosition(1, 1), TilePosition(1, 3)) is None


@pytest.mark.parametrize(
    "start",
    [TilePosition(3, 0), TilePosition(-1, 0), TilePosition(0, 1), TilePosition(0, -1)],
)
def test_start_outside_the_tiles_has_no_route(start):
    assert find_grid_route(place(["..."]), start, TilePosition(0, 0)) is None


def test_goal_outside_the_tiles_has_no_route():
    assert (
        find_grid_route(place(["..."]), TilePosition(0, 0), TilePosition(3, 0)) is None
    )


def test_empty_tiles_have_no_route():
    assert find_grid_route(place([]), TilePosition(0, 0), TilePosition(0, 0)) is None


def test_route_does_not_leave_the_tiles():
    # tiles の範囲の外を回り込まない
    found = find_grid_route(
        place([".#.", ".#."]), TilePosition(0, 0), TilePosition(2, 0)
    )
    assert found is None


def test_route_with_negative_origin():
    shifted = place(["#E#", "...", "..."], origin=(-3, -2))
    found = find_grid_route(shifted, TilePosition(-1, 0), TilePosition(-2, -2))
    assert found == route((LEFT, 1), (UP, 2))
    assert found.total_tiles == 3


# --- 同じ長さの経路の選び方 ---


def test_tie_with_same_turns_prefers_down_before_right():
    # 下 2 → 右 2 と、右 2 → 下 2 は、どちらも 4 マスで向きを 1 回変える。向きの並び (上・下・左・右) で下を先にする
    found = find_grid_route(place(OPEN_3X3), TilePosition(0, 0), TilePosition(2, 2))
    assert found == route((DOWN, 2), (RIGHT, 2))


def test_tie_with_same_turns_prefers_up_before_left():
    found = find_grid_route(place(OPEN_3X3), TilePosition(2, 2), TilePosition(0, 0))
    assert found == route((UP, 2), (LEFT, 2))


def test_tie_with_same_turns_compares_from_the_first_move():
    # 下 1 → 右 2 と右 2 → 下 1 は向きを 1 回変える。右 → 下 → 右 (2 回) は選ばない
    found = find_grid_route(place(OPEN_3X3), TilePosition(0, 0), TilePosition(2, 1))
    assert found == route((DOWN, 1), (RIGHT, 2))


def test_fewer_turns_win_over_the_direction_order():
    # (0, 2) が壁なので下 2 → 右 2 は通れない。最短 4 マスのうち、下で始まる経路は向きを 2 回以上変えるため、
    # 向きを 1 回だけ変える右 2 → 下 2 を選ぶ
    found = find_grid_route(
        place(["...", "...", "#.."]), TilePosition(0, 0), TilePosition(2, 2)
    )
    assert found == route((RIGHT, 2), (DOWN, 2))


def test_same_input_gives_the_same_route():
    first = find_grid_route(place(OPEN_3X3), TilePosition(0, 0), TilePosition(2, 2))
    for _ in range(5):
        assert (
            find_grid_route(place(OPEN_3X3), TilePosition(0, 0), TilePosition(2, 2))
            == first
        )


def test_direction_order():
    assert list(MoveDirection) == [UP, DOWN, LEFT, RIGHT]
    assert [direction.value for direction in MoveDirection] == [
        "up",
        "down",
        "left",
        "right",
    ]


# --- 場所の探し方 ---


def grid_document(
    *places: GridMapPlace, connections: tuple[MapConnection, ...] = ()
) -> GridMapDocument:
    return GridMapDocument(places=places, connections=connections)


def test_find_grid_place_by_name_and_aliases():
    house = place(HOUSE_TILES, name="家の 1 階", aliases=("1 階の部屋", "居間"))
    room = place([], name="自分の部屋 (2 階)")
    document = grid_document(house, room)
    assert find_grid_place(document, "家の 1 階") is house
    assert find_grid_place(document, "居間") is house
    assert find_grid_place(document, "自分の部屋 (2 階)") is room
    assert find_grid_place(document, "町") is None


def test_find_grid_place_compares_stripped_names():
    house = place(HOUSE_TILES, name=" 家の 1 階 ", aliases=(" 居間",))
    document = grid_document(house)
    assert find_grid_place(document, "家の 1 階") is house
    assert find_grid_place(document, " 居間  ") is house
    assert find_grid_place(document, "") is None
    assert find_grid_place(document, "   ") is None


def test_find_grid_place_returns_the_first_match():
    first = place(HOUSE_TILES, name="家", aliases=("部屋",))
    second = place([], name="部屋")
    assert find_grid_place(grid_document(first, second), "部屋") is first


# --- 経路を求める目標 ---


def mark(x: int, y: int, description: str) -> TileMark:
    return TileMark(TilePosition(x, y), description)


def connection(
    from_place: str,
    to_place: str,
    how: str,
    from_tile: tuple[int, int] | None,
) -> MapConnection:
    return MapConnection(
        from_place=from_place,
        to_place=to_place,
        how=how,
        from_tile=None if from_tile is None else TilePosition(*from_tile),
        to_tile=None,
    )


def exit_target(x: int, y: int, description: str, *destinations: str) -> RouteTarget:
    return RouteTarget(
        kind=RouteTargetKind.EXIT,
        position=TilePosition(x, y),
        description=description,
        destinations=destinations,
    )


def connection_target(x: int, y: int, how: str, to_place: str) -> RouteTarget:
    return RouteTarget(
        kind=RouteTargetKind.CONNECTION,
        position=TilePosition(x, y),
        description=how,
        destinations=(to_place,),
    )


def test_targets_are_exits_then_connections_from_the_place():
    house = place(
        HOUSE_TILES,
        name="家の 1 階",
        aliases=("1 階の部屋",),
        exits=(
            mark(1, 2, "左下の赤いマット"),
            # tiles の範囲の外の出口は目標にしない
            mark(20, 2, "画面の外の通り道"),
            mark(4, 0, "上の扉"),
        ),
    )
    document = grid_document(
        house,
        connections=(
            # from_tile が出口と同じマス: 目標にせず、その出口の行き先にする
            connection("家の 1 階", "町", "左下のマットから下へ", (1, 2)),
            # 別の場所から: 目標にしない
            connection("自分の部屋 (2 階)", "家の 1 階", "階段を下に", (7, 1)),
            connection("家の 1 階", "自分の部屋 (2 階)", "右上の階段を上に", (7, 1)),
            # from_tile が無い・範囲の外: 目標にしない
            connection("家の 1 階", "庭", "窓から", None),
            connection("家の 1 階", "裏庭", "壁の外", (9, 1)),
            # from は aliases でもよい (前後の空白を除いて比べる)
            connection(" 1 階の部屋 ", "台所", "左の通路", (1, 1)),
            # 同じ出口から 2 つ目の行き先
            connection("家の 1 階", "町の広場", "マットから下を長押し", (1, 2)),
        ),
    )
    assert list_route_targets(document, house) == (
        exit_target(1, 2, "左下の赤いマット", "町", "町の広場"),
        exit_target(4, 0, "上の扉"),
        connection_target(7, 1, "右上の階段を上に", "自分の部屋 (2 階)"),
        connection_target(1, 1, "左の通路", "台所"),
    )


def test_targets_without_exits_or_connections_are_empty():
    house = place(HOUSE_TILES, exits=(mark(20, 2, "画面の外"),))
    document = grid_document(
        house, connections=(connection("家の 1 階", "庭", "窓から", None),)
    )
    assert list_route_targets(document, house) == ()


def test_targets_of_empty_tiles_are_empty():
    room = place([], exits=(mark(0, 0, "扉"),))
    document = grid_document(
        room, connections=(connection("家の 1 階", "庭", "窓から", (0, 0)),)
    )
    assert list_route_targets(document, room) == ()


def test_targets_are_limited_to_ten_in_order():
    assert MAX_ROUTE_TARGETS == 10
    wide = place(
        ["." * 12, "." * 12], exits=tuple(mark(x, 0, f"出口 {x}") for x in range(7))
    )
    document = grid_document(
        wide,
        connections=tuple(
            connection("家の 1 階", f"場所 {x}", f"つながり {x}", (x, 1))
            for x in range(5)
        ),
    )
    targets = list_route_targets(document, wide)
    assert len(targets) == 10
    # 出口の 7 件を先に、続けてつながりの先頭から 3 件
    assert [target.description for target in targets] == [
        *(f"出口 {x}" for x in range(7)),
        "つながり 0",
        "つながり 1",
        "つながり 2",
    ]


def test_exits_beyond_ten_are_cut():
    wide = place(["." * 12], exits=tuple(mark(x, 0, f"出口 {x}") for x in range(12)))
    targets = list_route_targets(grid_document(wide), wide)
    assert [target.description for target in targets] == [
        f"出口 {x}" for x in range(10)
    ]
