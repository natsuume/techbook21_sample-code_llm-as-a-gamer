"""surface の要求の state の map に入れる、現在地の場所のマップの文字列 (notes.surface_map_text)。"""

import dataclasses

import pytest

from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    MapDocument,
    MapPlace,
    TileMark,
    TilePosition,
)
from switch2_layers.notes.surface_map_text import (
    CHANGES_ON_ENTRY_LINE,
    GOAL_TILE,
    GRID_LEGEND_LINE,
    NO_GOAL_LINE,
    NO_TILES_LINE,
    PLAYER_TILE,
    UNKNOWN_PLAYER_LINE,
    build_surface_map_text,
    find_map_place,
)
from switch2_layers.tasks.task_queue import TaskDestination


def grid_place(
    name: str,
    aliases: tuple[str, ...] = (),
    tiles: tuple[str, ...] = (),
    origin: tuple[int, int] = (0, 0),
    exits: tuple[TileMark, ...] = (),
    blocked: tuple[TileMark, ...] = (),
    landmarks: tuple[TileMark, ...] = (),
    changes_on_entry: bool = False,
) -> GridMapPlace:
    return GridMapPlace(
        name=name,
        aliases=aliases,
        description=f"{name}の説明",
        state=f"{name}の状態",
        changes_on_entry=changes_on_entry,
        origin=TilePosition(*origin),
        tiles=tiles,
        exits=exits,
        blocked=blocked,
        landmarks=landmarks,
    )


def mark(x: int, y: int, description: str) -> TileMark:
    return TileMark(TilePosition(x, y), description)


def link(
    from_place: str,
    to_place: str,
    how: str,
    from_tile: tuple[int, int] | None = None,
) -> MapConnection:
    return MapConnection(
        from_place=from_place,
        to_place=to_place,
        how=how,
        from_tile=None if from_tile is None else TilePosition(*from_tile),
        to_tile=None,
    )


# 代表の grid のマップ。現在地は「家の 1 階」(図の左上は x=10, y=20)。机 (13, 21) は blocked にあるが、文字列には書かない
HOUSE = grid_place(
    "家の 1 階",
    aliases=("家",),
    tiles=("#####", "#...#", "#.?.#", "##E##"),
    origin=(10, 20),
    exits=(mark(12, 23, "下の扉"),),
    blocked=(mark(13, 21, "机"),),
    landmarks=(mark(11, 21, "テレビ"),),
)
UPSTAIRS = grid_place("家の 2 階")
TOWN = grid_place("始まりの町", aliases=("町",))
PORT = grid_place("港町", aliases=("港",))
YARD = grid_place("裏庭")
CAVE = grid_place("洞窟")  # どのつながりでも行けない場所
# 家の 1 階から出るつながり (from_tile の無い裏庭へのつながりは、つながりの行に書かない)
TO_TOWN = link("家の 1 階", "町", "下の扉から外へ出る", (12, 23))
TO_UPSTAIRS = link("家の 1 階", "家の 2 階", "階段を上る", (13, 22))
TO_YARD = link("家の 1 階", "裏庭", "窓から出る")
# ほかの場所から出るつながり (家の 1 階のつながりの行には書かない)
INTO_HOUSE = link("始まりの町", "家の 1 階", "家に入る", (5, 4))
TO_PORT = link("始まりの町", "港町", "北へ進む", (9, 0))
WORLD = GridMapDocument(
    places=(HOUSE, UPSTAIRS, TOWN, PORT, YARD, CAVE),
    connections=(TO_TOWN, TO_UPSTAIRS, TO_YARD, INTO_HOUSE, TO_PORT),
)

PLAYER = TilePosition(11, 22)
BASED_ON_T = 12.5
# 同じ場所の行き先 (階段のマス。出口でも目印でもなく、つながりの from_tile)
STAIRS_DESTINATION = TaskDestination("家の 1 階", TilePosition(13, 22))
GRID_LINES = [
    "場所: 家の 1 階",
    "主人公 (P): x=11, y=22 (t=12.500 の画面での位置。その後に動いていることがある)",
    "目標 (G): x=13, y=22 階段を上る",
    "凡例: # 通れない / . 通れる / E 出口 / ? 未確認 / P 主人公 / G 目標",
    "図の左上のマスは x=10, y=20。x は右へ、y は下へ増える",
    "#####",
    "#...#",
    "#P?G#",
    "##E##",
    "出口: x=12, y=23 下の扉",
    "つながり: x=12, y=23 下の扉から外へ出る (行き先の場所: 町)",
    "つながり: x=13, y=22 階段を上る (行き先の場所: 家の 2 階)",
    "目印: x=11, y=21 テレビ",
]
# GRID_LINES の中の位置
PLAYER_LINE = 1
GOAL_LINE = 2
TILE_ROWS = slice(5, 9)


def house_lines(
    destination: TaskDestination | None = STAIRS_DESTINATION,
    current_tile: TilePosition | None = PLAYER,
    based_on_t: float | None = BASED_ON_T,
    document: GridMapDocument = WORLD,
    place_name: str = "家の 1 階",
) -> list[str]:
    """家の 1 階を現在地にした文字列を、行に分けて返す。"""
    return build_surface_map_text(
        document, place_name, current_tile, based_on_t, destination
    ).split("\n")


def test_constants_follow_the_spec():
    assert PLAYER_TILE == "P"
    assert GOAL_TILE == "G"
    assert UNKNOWN_PLAYER_LINE == "主人公の位置: 分からない"
    assert NO_GOAL_LINE == "目標: 無い"
    assert CHANGES_ON_ENTRY_LINE == "この場所は、入るたびに構造が変わる"
    assert GRID_LEGEND_LINE == (
        "凡例: # 通れない / . 通れる / E 出口 / ? 未確認 / P 主人公 / G 目標"
    )
    assert NO_TILES_LINE == "マスの図: まだ無い"


# --- 場所の探し方 (find_map_place) ---


def test_find_map_place_by_name_or_aliases():
    assert find_map_place(WORLD, "家の 1 階") is HOUSE
    assert find_map_place(WORLD, "家") is HOUSE
    assert find_map_place(WORLD, "港") is PORT


def test_find_map_place_compares_without_surrounding_spaces():
    road = grid_place("北の道", aliases=(" 北の街道 ",))
    document = GridMapDocument(places=(HOUSE, road), connections=())
    assert find_map_place(document, " 家 ") is HOUSE
    assert find_map_place(document, "北の街道") is road
    assert find_map_place(document, "\t北の道\n") is road


def test_find_map_place_returns_the_first_place_in_the_order_of_places():
    # 「部屋」は、places の順に最初に一致した場所 (aliases に「部屋」を持つ家) にする
    house = grid_place("家", aliases=("部屋",))
    room = grid_place("部屋")
    document = GridMapDocument(places=(house, room), connections=())
    assert find_map_place(document, "部屋") is house


@pytest.mark.parametrize("place_name", ["", "   ", "城"])
def test_find_map_place_returns_none_for_blank_or_unknown_names(place_name):
    assert find_map_place(WORLD, place_name) is None


# 代表の free のマップ。現在地は「始まりの町」
FREE_TOWN = MapPlace(
    name="始まりの町",
    aliases=("町",),
    description="小さな町",
    exits=("北の門", "東の橋"),
    blocked=("川", "崖"),
    state="訪問済み",
    changes_on_entry=False,
)
FREE_PORT = MapPlace(
    name="港町",
    aliases=("港",),
    description="海沿いの町",
    exits=(),
    blocked=(),
    state="未訪問",
    changes_on_entry=False,
)
FREE_WORLD = MapDocument(
    places=(FREE_TOWN, FREE_PORT),
    connections=(MapConnection("始まりの町", "港町", "北の門から出る"),),
)


def test_find_map_place_works_for_free_maps():
    assert find_map_place(FREE_WORLD, "町") is FREE_TOWN
    assert find_map_place(FREE_WORLD, " 港町 ") is FREE_PORT
    assert find_map_place(FREE_WORLD, "城") is None


# --- マップを渡さないとき (空文字列) ---


@pytest.mark.parametrize(
    "document, place_name",
    [
        (None, "家の 1 階"),
        (WORLD, None),
        (WORLD, "城"),
        (WORLD, "  "),
        (FREE_WORLD, None),
        (FREE_WORLD, "城"),
    ],
    ids=[
        "no_map",
        "no_place_name",
        "unknown_place",
        "blank_place_name",
        "free_no_place_name",
        "free_unknown_place",
    ],
)
def test_no_map_text_without_map_or_known_place(document, place_name):
    text = build_surface_map_text(
        document, place_name, PLAYER, BASED_ON_T, STAIRS_DESTINATION
    )
    assert text == ""


# --- grid の行 ---


def test_grid_text_has_all_lines_in_order():
    text = build_surface_map_text(
        WORLD, "家の 1 階", PLAYER, BASED_ON_T, STAIRS_DESTINATION
    )
    assert text == "\n".join(GRID_LINES)
    assert not text.endswith("\n")


@pytest.mark.parametrize("place_name", ["家", " 家 ", " 家の 1 階 "])
def test_grid_text_of_a_place_found_by_alias_uses_the_name(place_name):
    assert house_lines(place_name=place_name) == GRID_LINES


def test_player_time_is_written_as_a_frame_label():
    lines = house_lines(based_on_t=7.125)
    assert lines[PLAYER_LINE] == (
        "主人公 (P): x=11, y=22 (t=7.125 の画面での位置。その後に動いていることがある)"
    )
    assert "t=t=" not in "\n".join(lines)


@pytest.mark.parametrize(
    "current_tile, based_on_t",
    [(None, BASED_ON_T), (PLAYER, None), (None, None)],
    ids=["no_tile", "no_time", "neither"],
)
def test_unknown_player_is_not_drawn(current_tile, based_on_t):
    lines = house_lines(current_tile=current_tile, based_on_t=based_on_t)
    assert lines[PLAYER_LINE] == "主人公の位置: 分からない"
    # 主人公のマスは置き換えず、目標のマスだけを置き換える
    assert lines[TILE_ROWS] == ["#####", "#...#", "#.?G#", "##E##"]
    assert lines[GOAL_LINE] == "目標 (G): x=13, y=22 階段を上る"


# --- grid の目標 (同じ場所) ---


def test_no_goal_without_destination():
    lines = house_lines(destination=None)
    assert lines[GOAL_LINE] == "目標: 無い"
    assert lines[TILE_ROWS] == ["#####", "#...#", "#P?.#", "##E##"]


@pytest.mark.parametrize("place", ["家の 1 階", "家"])
def test_no_goal_for_the_current_place_without_tile(place):
    lines = house_lines(destination=TaskDestination(place, None))
    # 行き先の場所が現在地の場所なので、行き先の場所を添えない
    assert lines[GOAL_LINE] == "目標: 無い"
    assert lines[TILE_ROWS] == ["#####", "#...#", "#P?.#", "##E##"]


@pytest.mark.parametrize("place", ["家", " 家 ", " 家の 1 階 "])
def test_goal_in_the_current_place_matched_by_name_or_alias(place):
    lines = house_lines(destination=TaskDestination(place, TilePosition(13, 22)))
    assert lines[GOAL_LINE] == "目標 (G): x=13, y=22 階段を上る"
    assert lines[TILE_ROWS][2] == "#P?G#"


# 目標の説明の優先順を確かめる場所 (図は 5 × 3。左上は x=0, y=0)
PLAZA = grid_place(
    "広場",
    tiles=(".....", ".....", "....."),
    exits=(mark(0, 0, "西の門"), mark(0, 0, "もう一つの門")),
    landmarks=(
        mark(0, 0, "門の看板"),
        mark(1, 1, "噴水"),
        mark(2, 2, "井戸"),
        mark(2, 2, "桶"),
    ),
)
FOREST = grid_place("森")
PLAZA_WORLD = GridMapDocument(
    places=(PLAZA, FOREST),
    connections=(
        link("広場", "森", "門をくぐる", (0, 0)),
        link("広場", "森", "上の道"),
        link("広場", "森", "噴水に飛び込む", (1, 1)),
        # ほかの場所から出るつながりの from_tile は、目標の説明に使わない
        link("森", "広場", "広場へ戻る", (2, 2)),
    ),
)


@pytest.mark.parametrize(
    "tile, goal_line",
    [
        # 出口 (同じ座標の最初の出口) → つながり → 目印 の順
        ((0, 0), "目標 (G): x=0, y=0 西の門"),
        ((1, 1), "目標 (G): x=1, y=1 噴水に飛び込む"),
        ((2, 2), "目標 (G): x=2, y=2 井戸"),
        # どれにも当たらなければ説明なし (末尾に空白を付けない)
        ((3, 0), "目標 (G): x=3, y=0"),
    ],
    ids=["exit", "connection", "landmark", "nothing"],
)
def test_goal_description_follows_the_priority(tile, goal_line):
    destination = TaskDestination("広場", TilePosition(*tile))
    lines = build_surface_map_text(PLAZA_WORLD, "広場", None, None, destination).split(
        "\n"
    )
    assert lines[GOAL_LINE] == goal_line


# --- grid のマスの図の置き換え ---


def test_player_wins_when_player_and_goal_are_on_the_same_tile():
    lines = house_lines(current_tile=TilePosition(13, 22))
    assert lines[PLAYER_LINE].startswith("主人公 (P): x=13, y=22 (")
    assert lines[GOAL_LINE] == "目標 (G): x=13, y=22 階段を上る"
    assert lines[TILE_ROWS] == ["#####", "#...#", "#.?P#", "##E##"]


def test_player_outside_the_tiles_is_written_without_being_drawn():
    lines = house_lines(current_tile=TilePosition(30, 30))
    assert lines[PLAYER_LINE] == (
        "主人公 (P): x=30, y=30 (t=12.500 の画面での位置。その後に動いていることがある)"
    )
    assert lines[TILE_ROWS] == ["#####", "#...#", "#.?G#", "##E##"]


@pytest.mark.parametrize("tile", [(9, 22), (12, 24), (15, 20), (10, 19)])
def test_goal_outside_the_tiles_is_written_without_being_drawn(tile):
    x, y = tile
    lines = house_lines(destination=TaskDestination("家", TilePosition(x, y)))
    assert lines[GOAL_LINE] == f"目標 (G): x={x}, y={y}"
    assert lines[TILE_ROWS] == ["#####", "#...#", "#P?.#", "##E##"]


def test_place_without_tiles_says_there_are_no_tiles_yet():
    cellar = grid_place(
        "地下室",
        exits=(mark(0, 0, "はしご"),),
        landmarks=(mark(1, 0, "樽"),),
    )
    document = GridMapDocument(
        places=(cellar, HOUSE),
        connections=(link("地下室", "家の 1 階", "はしごを上る", (0, 0)),),
    )
    text = build_surface_map_text(
        document,
        "地下室",
        TilePosition(0, 0),
        2.0,
        TaskDestination("地下室", TilePosition(1, 0)),
    )
    # 凡例・図の左上のマスの行・図の行の代わりに 1 行だけ書く
    assert text.split("\n") == [
        "場所: 地下室",
        "主人公 (P): x=0, y=0 (t=2.000 の画面での位置。その後に動いていることがある)",
        "目標 (G): x=1, y=0 樽",
        "マスの図: まだ無い",
        "出口: x=0, y=0 はしご",
        "つながり: x=0, y=0 はしごを上る (行き先の場所: 家の 1 階)",
        "目印: x=1, y=0 樽",
    ]


# 入るたびに構造が変わる家の 1 階 (ほかは WORLD と同じ)
MAZE_HOUSE = dataclasses.replace(HOUSE, changes_on_entry=True)
MAZE_WORLD = dataclasses.replace(WORLD, places=(MAZE_HOUSE, *WORLD.places[1:]))


def test_changes_on_entry_line_follows_the_goal_line():
    lines = house_lines(document=MAZE_WORLD)
    assert lines == [
        *GRID_LINES[:3],
        "この場所は、入るたびに構造が変わる",
        *GRID_LINES[3:],
    ]


# --- grid の目標 (別の場所) ---


@pytest.mark.parametrize(
    "destination",
    [
        TaskDestination("始まりの町", None),
        TaskDestination("町", None),
        TaskDestination(" 町 ", None),
        # 別の場所のマスは使わない
        TaskDestination("始まりの町", TilePosition(1, 1)),
    ],
    ids=["name", "alias", "alias_with_spaces", "with_tile"],
)
def test_goal_toward_another_place_is_the_first_connection(destination):
    lines = house_lines(destination=destination)
    assert lines[GOAL_LINE] == (
        "目標 (G): x=12, y=23 下の扉から外へ出る (行き先の場所: 始まりの町)"
    )
    assert lines[TILE_ROWS] == ["#####", "#...#", "#P?.#", "##G##"]


@pytest.mark.parametrize("place", ["港町", "港"])
def test_goal_toward_a_place_two_connections_away_names_the_final_place(place):
    # 最初のつながりの to は「町」だが、説明には最終の行き先の場所の name を書く
    lines = house_lines(destination=TaskDestination(place, None))
    assert lines[GOAL_LINE] == (
        "目標 (G): x=12, y=23 下の扉から外へ出る (行き先の場所: 港町)"
    )
    assert lines[TILE_ROWS][3] == "##G##"


@pytest.mark.parametrize(
    "place, goal_line",
    [
        # つながりをたどっても行けない
        ("洞窟", "目標: 無い (行き先の場所: 洞窟)"),
        # 最初のつながりの from_tile が null
        ("裏庭", "目標: 無い (行き先の場所: 裏庭)"),
        # マップに無い場所は、書かれたままの名前を書く
        ("城", "目標: 無い (行き先の場所: 城)"),
    ],
    ids=["no_route", "no_from_tile", "not_in_map"],
)
def test_no_goal_toward_another_place(place, goal_line):
    lines = house_lines(destination=TaskDestination(place, None))
    assert lines[GOAL_LINE] == goal_line
    assert lines[TILE_ROWS] == ["#####", "#...#", "#P?.#", "##E##"]


def test_no_goal_toward_another_place_when_the_current_place_changes_on_entry():
    lines = house_lines(document=MAZE_WORLD, destination=TaskDestination("町", None))
    assert lines[GOAL_LINE] == "目標: 無い (行き先の場所: 始まりの町)"
    assert lines[GOAL_LINE + 1] == "この場所は、入るたびに構造が変わる"
    # 図の行は、構造が変わる行の分だけ後ろにずれる。目標のマスは置き換えない
    assert lines[GOAL_LINE + 4 : GOAL_LINE + 8] == [
        "#####",
        "#...#",
        "#P?.#",
        "##E##",
    ]


# --- grid のつながりの行 ---


def test_connection_lines_are_from_the_current_place_with_from_tile():
    document = GridMapDocument(
        places=WORLD.places,
        connections=(
            # from は前後の空白を除いて name か aliases と比べる。to は書かれたまま
            link(" 家 ", "港", "裏口から出る", (14, 21)),
            link("家の 1 階", "裏庭", "窓から出る"),
            link("始まりの町", "家の 1 階", "家に入る", (5, 4)),
            link("家の 1 階", " 家の 2 階 ", "梯子を上る", (10, 20)),
        ),
    )
    lines = house_lines(document=document, destination=None)
    assert [line for line in lines if line.startswith("つながり: ")] == [
        "つながり: x=14, y=21 裏口から出る (行き先の場所: 港)",
        "つながり: x=10, y=20 梯子を上る (行き先の場所:  家の 2 階 )",
    ]


# --- free の行 ---

FREE_LINES = [
    "場所: 始まりの町",
    "説明: 小さな町",
    "出口: 北の門",
    "出口: 東の橋",
    "通れない位置: 川",
    "通れない位置: 崖",
    "状態: 訪問済み",
]


def test_free_text_has_all_lines_in_order_without_player_or_goal():
    # 主人公のマスと時刻は使わない
    text = build_surface_map_text(FREE_WORLD, "町", TilePosition(1, 1), 5.0, None)
    assert text == "\n".join(FREE_LINES)


@pytest.mark.parametrize(
    "destination, extra_lines",
    [
        (TaskDestination("港", None), ["行き先の場所: 港町"]),
        (TaskDestination("港町", TilePosition(2, 3)), ["行き先の場所: 港町"]),
        (TaskDestination("城", None), ["行き先の場所: 城"]),
        # 行き先の場所が現在地の場所なら書かない
        (TaskDestination(" 町 ", TilePosition(0, 0)), []),
        (TaskDestination("始まりの町", None), []),
        (None, []),
    ],
    ids=[
        "alias",
        "name_with_tile",
        "not_in_map",
        "current_by_alias",
        "current_by_name",
        "none",
    ],
)
def test_free_text_writes_the_destination_place_only_for_another_place(
    destination, extra_lines
):
    text = build_surface_map_text(FREE_WORLD, "始まりの町", None, None, destination)
    assert text.split("\n") == [*FREE_LINES, *extra_lines]


def test_free_text_of_a_place_that_changes_on_entry():
    document = MapDocument(
        places=(dataclasses.replace(FREE_TOWN, changes_on_entry=True), FREE_PORT),
        connections=FREE_WORLD.connections,
    )
    text = build_surface_map_text(
        document, "始まりの町", None, None, TaskDestination("港", None)
    )
    assert text.split("\n") == [
        *FREE_LINES,
        "この場所は、入るたびに構造が変わる",
        "行き先の場所: 港町",
    ]


def test_free_text_without_exits_or_blocked():
    text = build_surface_map_text(FREE_WORLD, "港", None, None, None)
    # 出口と通れない位置の行は、要素が無ければ書かない
    assert text.split("\n") == ["場所: 港町", "説明: 海沿いの町", "状態: 未訪問"]
