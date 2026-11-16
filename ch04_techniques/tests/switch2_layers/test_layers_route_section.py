import dataclasses

import pytest

from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.middle.route_section import (
    CROSS_PLACE_ROUTES_HEADING,
    DIRECTION_LABELS,
    MAX_CROSS_PLACE_DESTINATIONS,
    NO_CROSS_PLACE_DESTINATION_TEXT,
    NO_ROUTE_TEXT,
    NO_ROUTE_THROUGH_UNKNOWN_TEXT,
    NO_TILE_STEPS_TEXT,
    ROUTE_FREE_LAYOUT_TEXT,
    ROUTE_NO_CURRENT_TILE_TEXT,
    ROUTE_NO_MAP_TEXT,
    ROUTE_NO_PREVIOUS_OUTPUT_TEXT,
    ROUTE_SECTION_TITLE,
    describe_cross_place_routes,
    describe_route_in_place,
    describe_route_section,
    format_route,
    format_unknown_tile_route,
)
from switch2_layers.notes.grid_route import (
    GridRoute,
    MoveDirection,
    RouteStep,
    UnknownTileRoute,
    connection_from_tiles,
    list_route_targets,
)
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    MapDocument,
    MapPlace,
    TileMark,
    TilePosition,
)


def lines_of(lines: list[str]) -> str:
    """行を改行 1 つでつないだ文 (末尾に改行を付けない) を返す。"""
    return "\n".join(lines)


# 家の 1 階: 左下に出口 (1, 2)。(3, 4) の穴は、まだ確かめていないマス (3, 3) の先にあり、通れるマスだけでは届かない
HOUSE = GridMapPlace(
    name="家の 1 階",
    aliases=("1 階の部屋",),
    description="自分の部屋から階段で下りた先の部屋",
    state="訪問済み",
    changes_on_entry=False,
    origin=TilePosition(0, 0),
    tiles=("#########", "#.......#", "#E......#", "###?#####", "###.#####"),
    exits=(
        TileMark(TilePosition(1, 2), "左下の赤いマット"),
        TileMark(TilePosition(3, 4), "地下への穴"),
    ),
    blocked=(TileMark(TilePosition(4, 1), "机"),),
    landmarks=(TileMark(TilePosition(6, 1), "テレビ"),),
)
# まだ図を描けない場所
ROOM = GridMapPlace(
    name="自分の部屋 (2 階)",
    aliases=(),
    description="",
    state="訪問済み",
    changes_on_entry=False,
    origin=TilePosition(0, 0),
    tiles=(),
    exits=(),
    blocked=(),
    landmarks=(),
)
GRID_DOCUMENT = GridMapDocument(
    places=(HOUSE, ROOM),
    connections=(
        # 町はマップの場所に無いため、場所をまたぐ経路には使わない
        MapConnection(
            from_place="家の 1 階",
            to_place="町",
            how="左下のマットから下へ",
            from_tile=TilePosition(1, 2),
            to_tile=TilePosition(5, 0),
        ),
        MapConnection(
            from_place="家の 1 階",
            to_place="自分の部屋 (2 階)",
            how="右上の階段を上に",
            from_tile=TilePosition(7, 1),
            to_tile=None,
        ),
    ),
)
STATE = MiddleState(
    generated_t=10.6,
    based_on_t=10.0,
    scene_id="scene-001",
    scene_name="フィールド",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.4,
    timeline="",
    surface_rows_offset=0,
    operations_rows_offset=0,
    place_name="家の 1 階",
    current_tile=TilePosition(7, 1),
)
HEADER = (
    "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 家の 1 階 のマス (x=7, y=1)"
)
TARGET_LINES = [
    "- 出口「左下の赤いマット」(x=1, y=2)。行き先: 町。手順: 下に 1 マス → 左に 6 マス (合計 7 マス)",
    # (3, 3) の ? を通れるとみなすと、下 1 → 左 4 → 下 2 で届く (机 (4, 1) があるため、上の行は通れない)
    (
        "- 出口「地下への穴」(x=3, y=4)。手順: 経路なし (通れるマスだけではたどり着けない)。"
        "未確認のマス (?) も通れるとすると: 下に 1 マス → 左に 4 マス → 下に 2 マス (合計 7 マス、うち未確認 1 マス)"
    ),
    "- つながり「右上の階段を上に」(x=7, y=1)。行き先: 自分の部屋 (2 階)。手順: 移動しない (合計 0 マス)",
]
# 自分の部屋 (2 階) は、つながり 1 件で行けて、目標の行の行き先に出ているため、場所をまたぐ経路に書かない
# (町はマップの場所に無い)
NO_CROSS_PLACE = "場所をまたぐ経路: 行ける場所は無い"
# 目標の行が無いとき (B が理由の行) は、自分の部屋 (2 階) を書く。開始のマスが無いか範囲の外なら、マスの手順は無い
ROOM_CROSS_PLACE_LINES = [
    "場所をまたぐ経路 (つながりをたどる):",
    (
        "- 「自分の部屋 (2 階)」まで (つながり 1 件): 「家の 1 階」の「右上の階段を上に」(x=7, y=1)へ (マスの手順なし)"
        " → 「自分の部屋 (2 階)」"
    ),
]


def state(**changes: object) -> MiddleState:
    return dataclasses.replace(STATE, **changes)


def route_lines(
    document: MapDocument | GridMapDocument | None, previous: MiddleState | None
) -> list[str]:
    return describe_route_section(document, previous).split("\n")


def assert_routes_are_described() -> None:
    """GRID_DOCUMENT と STATE で、A・B の目標の行・C が書かれることを確かめる。"""
    assert describe_route_section(GRID_DOCUMENT, STATE) == lines_of(
        [HEADER, *TARGET_LINES, NO_CROSS_PLACE]
    )


def assert_base_has_target_lines() -> None:
    """GRID_DOCUMENT と STATE で、A と B の最初の目標の行が書かれることを先に確かめる (理由の行のテストが、別の理由で
    通らないように)。"""
    assert route_lines(GRID_DOCUMENT, STATE)[:2] == [HEADER, TARGET_LINES[0]]


def test_section_title():
    assert ROUTE_SECTION_TITLE == "現在地からの経路 (コードの計算)"


def test_cross_place_texts():
    assert CROSS_PLACE_ROUTES_HEADING == "場所をまたぐ経路 (つながりをたどる):"
    assert NO_CROSS_PLACE_DESTINATION_TEXT == NO_CROSS_PLACE
    assert NO_TILE_STEPS_TEXT == "(マスの手順なし)"
    assert MAX_CROSS_PLACE_DESTINATIONS == 10


# --- 経路を書く (A: 前回の現在地の行、B: 目標の行、C: 場所をまたぐ経路) ---


def test_routes_from_the_current_tile():
    # A に前回の出力の場所・マス・入力の時刻、B に目標ごとに 1 行 (出口 → つながりの順)、C に場所をまたぐ経路
    assert_routes_are_described()


def test_place_found_by_alias_shows_the_map_name():
    lines = route_lines(GRID_DOCUMENT, state(place_name="1 階の部屋"))
    assert lines[0] == (
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 1 階の部屋 (マップの場所「家の 1 階」) "
        "のマス (x=7, y=1)"
    )
    assert lines[1:] == [*TARGET_LINES, NO_CROSS_PLACE]


def test_exit_with_several_destinations():
    document = dataclasses.replace(
        GRID_DOCUMENT,
        connections=(
            *GRID_DOCUMENT.connections,
            MapConnection(
                from_place="家の 1 階",
                to_place="町の広場",
                how="マットから下を長押し",
                from_tile=TilePosition(1, 2),
                to_tile=None,
            ),
        ),
    )
    lines = describe_route_section(document, STATE).split("\n")
    assert lines[1] == (
        "- 出口「左下の赤いマット」(x=1, y=2)。行き先: 町、町の広場。"
        "手順: 下に 1 マス → 左に 6 マス (合計 7 マス)"
    )


def test_targets_are_at_most_ten():
    # 上の行に 12 個の出口を並べ、下の行から向かう (下の行には出口が無いので、途中で出口を踏まない)
    wide = dataclasses.replace(
        HOUSE,
        tiles=("." * 12, "." * 12),
        exits=tuple(TileMark(TilePosition(x, 0), f"出口 {x}") for x in range(12)),
        blocked=(),
    )
    document = GridMapDocument(places=(wide,), connections=())
    lines = describe_route_section(
        document, state(current_tile=TilePosition(0, 1))
    ).split("\n")
    assert len(lines) == 1 + 10 + 1
    assert lines[1] == "- 出口「出口 0」(x=0, y=0)。手順: 上に 1 マス (合計 1 マス)"
    assert lines[10] == (
        "- 出口「出口 9」(x=9, y=0)。手順: 右に 9 マス → 上に 1 マス (合計 10 マス)"
    )
    assert lines[11] == NO_CROSS_PLACE


def test_route_section_with_negative_origin():
    shifted = dataclasses.replace(
        HOUSE,
        origin=TilePosition(-3, -2),
        tiles=("#E#", "...", "..."),
        exits=(TileMark(TilePosition(-2, -2), "上の扉"),),
    )
    document = GridMapDocument(places=(shifted,), connections=())
    text = describe_route_section(document, state(current_tile=TilePosition(-1, 0)))
    assert text == lines_of(
        [
            "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 家の 1 階 のマス (x=-1, y=0)",
            "- 出口「上の扉」(x=-2, y=-2)。手順: 左に 1 マス → 上に 2 マス (合計 3 マス)",
            NO_CROSS_PLACE,
        ]
    )


# --- 経路を計算しない理由 (B) と、A・C を書くか ---


FREE_DOCUMENT = MapDocument(
    places=(
        MapPlace(
            name="家の 1 階",
            aliases=(),
            description="",
            exits=("左下の赤いマット",),
            blocked=(),
            state="",
            changes_on_entry=False,
        ),
    ),
    connections=(),
)


def test_without_map():
    assert describe_route_section(None, STATE) == ROUTE_NO_MAP_TEXT
    assert ROUTE_NO_MAP_TEXT == "マップが無いため、経路を計算しない"


def test_free_layout():
    # 自由型のマップでは、A と C を書かず、理由の 1 行だけ
    assert describe_route_section(FREE_DOCUMENT, STATE) == ROUTE_FREE_LAYOUT_TEXT
    assert (
        ROUTE_FREE_LAYOUT_TEXT
        == "マップが自由型 (layout が free) のため、経路を計算しない"
    )


def test_without_previous_output():
    assert describe_route_section(GRID_DOCUMENT, None) == ROUTE_NO_PREVIOUS_OUTPUT_TEXT
    assert (
        ROUTE_NO_PREVIOUS_OUTPUT_TEXT
        == "前回の middle の出力が無いため、経路を計算しない"
    )


@pytest.mark.parametrize(
    "changes",
    [{"place_name": None}, {"place_name": None, "current_tile": None}],
)
def test_previous_output_without_place_name(changes):
    # place_name が無ければ、A も C も書かない
    assert_base_has_target_lines()
    assert describe_route_section(GRID_DOCUMENT, state(**changes)) == (
        ROUTE_NO_CURRENT_TILE_TEXT
    )
    assert ROUTE_NO_CURRENT_TILE_TEXT == (
        "前回の middle の出力に、現在地の場所 (place_name) かマス (current_tile) が無いため、"
        "経路を計算しない"
    )


def test_previous_output_without_current_tile_has_cross_place_routes():
    # place_name はあるが current_tile が無い: A を書かず、B は理由の行、C は書く (最初の区間の開始のマスが無いため、
    # マスの手順なし)。B に目標の行が無いため、つながり 1 件で行ける自分の部屋 (2 階) も書く
    assert_base_has_target_lines()
    assert route_lines(GRID_DOCUMENT, state(current_tile=None)) == [
        ROUTE_NO_CURRENT_TILE_TEXT,
        *ROOM_CROSS_PLACE_LINES,
    ]


def test_previous_output_without_current_tile_in_a_place_not_in_the_map():
    # 場所がマップに無ければ C も書かない
    assert describe_route_section(
        GRID_DOCUMENT, state(place_name="町", current_tile=None)
    ) == (ROUTE_NO_CURRENT_TILE_TEXT)


def test_previous_output_without_current_tile_in_a_place_that_changes_on_entry():
    # 入るたびに構造が変わる場所では C を書かない
    document = GridMapDocument(
        places=(dataclasses.replace(HOUSE, changes_on_entry=True), ROOM),
        connections=GRID_DOCUMENT.connections,
    )
    assert describe_route_section(document, state(current_tile=None)) == (
        ROUTE_NO_CURRENT_TILE_TEXT
    )


def test_place_that_is_not_in_the_map():
    # A は書く (「 (マップの場所「<name>」)」は付けない)。C は書かない
    assert_base_has_target_lines()
    assert route_lines(GRID_DOCUMENT, state(place_name="町")) == [
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 町 のマス (x=7, y=1)",
        "前回の middle の出力の現在地「町」がマップの場所 (name と aliases) に無いため、経路を計算しない",
    ]


def test_place_with_empty_tiles():
    # A と理由の行に続けて C を書く (自分の部屋 (2 階) から出るつながりは無い)
    assert_base_has_target_lines()
    assert route_lines(GRID_DOCUMENT, state(place_name="自分の部屋 (2 階)")) == [
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 自分の部屋 (2 階) のマス (x=7, y=1)",
        "マップの場所「自分の部屋 (2 階)」のマスの図 (tiles) が空のため、経路を計算しない",
        NO_CROSS_PLACE,
    ]


@pytest.mark.parametrize(
    "tile", [TilePosition(9, 1), TilePosition(-1, 1), TilePosition(7, 5)]
)
def test_current_tile_outside_the_tiles(tile):
    # A と理由の行に続けて C を書く。B に目標の行が無いため、自分の部屋 (2 階) も書く
    assert_base_has_target_lines()
    assert route_lines(GRID_DOCUMENT, state(current_tile=tile)) == [
        f"前回の middle の出力 (入力の時刻 t=10.000) の現在地: 家の 1 階 のマス (x={tile.x}, y={tile.y})",
        (
            f"現在地のマス (x={tile.x}, y={tile.y}) がマップの場所「家の 1 階」のマスの図 (tiles) の範囲の外のため、"
            "経路を計算しない"
        ),
        *ROOM_CROSS_PLACE_LINES,
    ]


def test_current_tile_on_a_wall_is_inside_the_tiles():
    # 開始のマスの文字は問わない
    lines = route_lines(GRID_DOCUMENT, state(current_tile=TilePosition(0, 1)))
    assert lines[0].endswith("のマス (x=0, y=1)")
    assert len(lines) == 1 + len(TARGET_LINES) + 1


def test_place_without_targets():
    assert_base_has_target_lines()
    no_targets = dataclasses.replace(
        HOUSE, exits=(TileMark(TilePosition(30, 30), "画面の外の通り道"),)
    )
    document = GridMapDocument(
        places=(no_targets,),
        connections=(
            # from_tile が無いつながりと、ほかの場所からのつながりは目標にならない (町はマップの場所に無い)
            MapConnection(
                from_place="家の 1 階", to_place="町", how="外へ", from_tile=None
            ),
            MapConnection(
                from_place="町",
                to_place="家の 1 階",
                how="扉",
                from_tile=TilePosition(1, 1),
            ),
        ),
    )
    assert route_lines(document, STATE) == [
        HEADER,
        "マップの場所「家の 1 階」に、マスの図 (tiles) の範囲の中の出口とつながりが無いため、経路を計算しない",
        NO_CROSS_PLACE,
    ]


def test_reasons_are_checked_in_order():
    # B の理由は、マップなし → free → 前回の出力が無い → place_name か current_tile が無い → 場所が無い →
    # 入るたびに構造が変わる → tiles が空 → 開始が範囲の外 → 目標が無い の順に確かめ、最初に当たったものだけを書く
    assert route_lines(None, None) == [ROUTE_NO_MAP_TEXT]
    assert route_lines(FREE_DOCUMENT, None) == [ROUTE_FREE_LAYOUT_TEXT]
    assert route_lines(GRID_DOCUMENT, state(place_name="町", current_tile=None)) == [
        ROUTE_NO_CURRENT_TILE_TEXT
    ]
    # A の次の行 (B) を比べる
    assert route_lines(
        GRID_DOCUMENT,
        state(place_name="自分の部屋 (2 階)", current_tile=TilePosition(99, 99)),
    )[1:2] == [
        "マップの場所「自分の部屋 (2 階)」のマスの図 (tiles) が空のため、経路を計算しない"
    ]
    changing_room = GridMapDocument(
        places=(dataclasses.replace(ROOM, changes_on_entry=True),), connections=()
    )
    assert route_lines(
        changing_room,
        state(place_name="自分の部屋 (2 階)", current_tile=TilePosition(99, 99)),
    )[1:] == [
        (
            "マップの場所「自分の部屋 (2 階)」は入るたびに構造が変わる (changes_on_entry が true) ため、"
            "経路を計算しない"
        )
    ]
    assert route_lines(changing_room, state(place_name="町"))[1:] == [
        "前回の middle の出力の現在地「町」がマップの場所 (name と aliases) に無いため、経路を計算しない"
    ]
    no_targets = GridMapDocument(
        places=(dataclasses.replace(HOUSE, exits=()),), connections=()
    )
    assert route_lines(no_targets, state(current_tile=TilePosition(99, 99)))[1:2] == [
        (
            "現在地のマス (x=99, y=99) がマップの場所「家の 1 階」のマスの図 (tiles) の範囲の外のため、"
            "経路を計算しない"
        )
    ]


@pytest.mark.parametrize(
    ("document", "previous"),
    [
        (None, STATE),
        (FREE_DOCUMENT, STATE),
        (GRID_DOCUMENT, None),
        (GRID_DOCUMENT, dataclasses.replace(STATE, place_name=None)),
        (
            GRID_DOCUMENT,
            dataclasses.replace(STATE, place_name="町", current_tile=None),
        ),
    ],
)
def test_reason_is_one_line(document, previous):
    # マップなし・free・前回の出力が無い・place_name が無い・場所がマップに無くて current_tile も無いときは、1 行だけ
    assert "\n" not in describe_route_section(document, previous)


# --- 手順の文 ---


def test_format_route():
    assert DIRECTION_LABELS == {
        MoveDirection.UP: "上",
        MoveDirection.DOWN: "下",
        MoveDirection.LEFT: "左",
        MoveDirection.RIGHT: "右",
    }
    assert (
        format_route(
            GridRoute(
                steps=(
                    RouteStep(MoveDirection.UP, 3),
                    RouteStep(MoveDirection.LEFT, 2),
                )
            )
        )
        == "上に 3 マス → 左に 2 マス (合計 5 マス)"
    )
    assert (
        format_route(GridRoute(steps=(RouteStep(MoveDirection.RIGHT, 1),)))
        == "右に 1 マス (合計 1 マス)"
    )


def test_format_route_of_zero_tiles_and_no_route():
    assert format_route(GridRoute(steps=())) == "移動しない (合計 0 マス)"
    assert format_route(None) == NO_ROUTE_TEXT
    assert NO_ROUTE_TEXT == "経路なし (通れるマスだけではたどり着けない)"


def test_format_unknown_tile_route():
    route = GridRoute(
        steps=(RouteStep(MoveDirection.UP, 3), RouteStep(MoveDirection.LEFT, 2))
    )
    assert format_unknown_tile_route(
        UnknownTileRoute(route=route, unknown_tiles=1)
    ) == (
        "経路なし (通れるマスだけではたどり着けない)。未確認のマス (?) も通れるとすると: "
        "上に 3 マス → 左に 2 マス (合計 5 マス、うち未確認 1 マス)"
    )
    assert format_unknown_tile_route(
        UnknownTileRoute(
            route=GridRoute(steps=(RouteStep(MoveDirection.DOWN, 4),)),
            unknown_tiles=3,
        )
    ) == (
        "経路なし (通れるマスだけではたどり着けない)。未確認のマス (?) も通れるとすると: "
        "下に 4 マス (合計 4 マス、うち未確認 3 マス)"
    )


def test_format_unknown_tile_route_of_no_route():
    assert format_unknown_tile_route(None) == NO_ROUTE_THROUGH_UNKNOWN_TEXT
    assert (
        NO_ROUTE_THROUGH_UNKNOWN_TEXT
        == "経路なし (未確認のマス (?) を通ってもたどり着けない)"
    )


def test_describe_route_in_place():
    avoided = connection_from_tiles(GRID_DOCUMENT, HOUSE)
    # 通れるマスだけで行ける目標は、format_route の文
    assert describe_route_in_place(
        HOUSE, TilePosition(7, 1), TilePosition(1, 2), avoided
    ) == ("下に 1 マス → 左に 6 マス (合計 7 マス)")
    # 通れるマスだけでは届かない目標は、? も通れるとした経路の文
    assert describe_route_in_place(
        HOUSE, TilePosition(7, 1), TilePosition(3, 4), avoided
    ) == (
        "経路なし (通れるマスだけではたどり着けない)。未確認のマス (?) も通れるとすると: "
        "下に 1 マス → 左に 4 マス → 下に 2 マス (合計 7 マス、うち未確認 1 マス)"
    )
    # ? を通っても届かない目標
    walled = dataclasses.replace(
        HOUSE,
        tiles=("#########", "#.......#", "#E......#", "#########", "###.#####"),
    )
    assert (
        describe_route_in_place(walled, TilePosition(7, 1), TilePosition(3, 4), avoided)
        == NO_ROUTE_THROUGH_UNKNOWN_TEXT
    )


# --- 未確認のマス (?) を通る経路 (B の目標の行) ---


def test_target_unreachable_by_passable_tiles_shows_the_route_through_unknown_tiles():
    assert route_lines(GRID_DOCUMENT, STATE)[2] == TARGET_LINES[1]


def test_target_unreachable_even_through_unknown_tiles():
    # (3, 3) が # なら、? を通っても地下への穴に届かない
    walled = dataclasses.replace(
        HOUSE,
        tiles=("#########", "#.......#", "#E......#", "#########", "###.#####"),
    )
    document = GridMapDocument(
        places=(walled, ROOM), connections=GRID_DOCUMENT.connections
    )
    assert route_lines(document, STATE)[2] == (
        "- 出口「地下への穴」(x=3, y=4)。手順: 経路なし (未確認のマス (?) を通ってもたどり着けない)"
    )


def test_passable_route_text_is_unchanged_even_if_unknown_tiles_are_shorter():
    # 右の扉 (3, 1) へは、(2, 1) の ? を通れば右に 2 マスだが、通れるマスだけの経路 (下 1 → 右 2 → 上 1) があるので、
    # その文のまま書く
    room = dataclasses.replace(
        HOUSE,
        tiles=("#####", "#.?E#", "#...#", "#####"),
        exits=(TileMark(TilePosition(3, 1), "右の扉"),),
        blocked=(),
    )
    document = GridMapDocument(places=(room,), connections=())
    assert route_lines(document, state(current_tile=TilePosition(1, 1)))[1] == (
        "- 出口「右の扉」(x=3, y=1)。手順: 下に 1 マス → 右に 2 マス → 上に 1 マス (合計 4 マス)"
    )


# --- 入るたびに構造が変わる場所 ---


def test_place_that_changes_on_entry():
    # 入るたびに構造が変わる場所では、古い構造の出口までの経路と場所をまたぐ経路を書かずに、A と理由を書く
    assert_base_has_target_lines()
    document = GridMapDocument(
        places=(dataclasses.replace(HOUSE, changes_on_entry=True), ROOM),
        connections=GRID_DOCUMENT.connections,
    )
    assert route_lines(document, STATE) == [
        HEADER,
        "マップの場所「家の 1 階」は入るたびに構造が変わる (changes_on_entry が true) ため、経路を計算しない",
    ]
    # aliases で見つけた場所なら、A にマップの場所の name を足す
    assert route_lines(document, state(place_name="1 階の部屋"))[0] == (
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 1 階の部屋 (マップの場所「家の 1 階」) "
        "のマス (x=7, y=1)"
    )


def test_place_that_does_not_change_on_entry_has_routes():
    assert HOUSE.changes_on_entry is False
    assert describe_route_section(GRID_DOCUMENT, STATE) == lines_of(
        [HEADER, *TARGET_LINES, NO_CROSS_PLACE]
    )


# --- つながりの出たマスを途中で通らない ---


def test_route_avoids_from_tiles_of_connections():
    # 下の行の床の階段 (4, 2) はつながりの出たマス (tiles では .)。左下の出口へは、踏まずに上の行を回る。
    # 階段そのものが目標なら入る
    stairs_house = dataclasses.replace(
        HOUSE,
        tiles=("#########", "#.......#", "#E......#"),
        exits=(TileMark(TilePosition(1, 2), "左下の赤いマット"),),
        blocked=(),
    )
    document = GridMapDocument(
        places=(stairs_house,),
        connections=(
            MapConnection(
                from_place="家の 1 階",
                to_place="地下",
                how="床の階段",
                from_tile=TilePosition(4, 2),
                to_tile=None,
            ),
        ),
    )
    lines = route_lines(document, state(current_tile=TilePosition(7, 2)))
    assert lines[1:] == [
        "- 出口「左下の赤いマット」(x=1, y=2)。手順: 上に 1 マス → 左に 6 マス → 下に 1 マス (合計 8 マス)",
        "- つながり「床の階段」(x=4, y=2)。行き先: 地下。手順: 左に 3 マス (合計 3 マス)",
        NO_CROSS_PLACE,
    ]


# --- 場所をまたぐ経路 (C) ---


def grid_place(
    name: str,
    tiles: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
    exits: tuple[TileMark, ...] = (),
    changes_on_entry: bool = False,
) -> GridMapPlace:
    return GridMapPlace(
        name=name,
        aliases=aliases,
        description="",
        state="",
        changes_on_entry=changes_on_entry,
        origin=TilePosition(0, 0),
        tiles=tiles,
        exits=exits,
        blocked=(),
        landmarks=(),
    )


def link(
    from_place: str,
    to_place: str,
    how: str,
    from_tile: TilePosition | None = None,
    to_tile: TilePosition | None = None,
) -> MapConnection:
    return MapConnection(
        from_place=from_place,
        to_place=to_place,
        how=how,
        from_tile=from_tile,
        to_tile=to_tile,
    )


# 始まりの町: (10, 7) から、北の道へのつながりのマス (10, 0) まで上に 7 マス
START_TOWN = grid_place(
    "始まりの町", tiles=("##########.", *("#.........." for _ in range(7)))
)
# 北の道と港町は、まだマスの図が無い
NORTH_ROAD = grid_place("北の道")
PORT_TOWN = grid_place("港町")
TO_NORTH_ROAD = link(
    "始まりの町", "北の道", "北の道へ進む", TilePosition(10, 0), TilePosition(8, 2)
)
TO_PORT_TOWN = link("北の道", "港町", "北の端", TilePosition(8, 0))
WORLD = GridMapDocument(
    places=(START_TOWN, NORTH_ROAD, PORT_TOWN),
    connections=(TO_NORTH_ROAD, TO_PORT_TOWN),
)
WORLD_STATE = state(place_name="始まりの町", current_tile=TilePosition(10, 7))
WORLD_FIRST_LEG = (
    "「始まりの町」の「北の道へ進む」(x=10, y=0)へ 上に 7 マス (合計 7 マス)"
)


def test_cross_place_route_of_the_issue_example():
    # 北の道はつながり 1 件で行けて、目標の行の行き先に出ているため書かない。港町までは、始まりの町の中の経路と、
    # 北の道の中の手順なし (マスの図が無い) の区間を書く
    assert describe_route_section(WORLD, WORLD_STATE) == lines_of(
        [
            "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 始まりの町 のマス (x=10, y=7)",
            "- つながり「北の道へ進む」(x=10, y=0)。行き先: 北の道。手順: 上に 7 マス (合計 7 マス)",
            "場所をまたぐ経路 (つながりをたどる):",
            (
                "- 「港町」まで (つながり 2 件): 「始まりの町」の「北の道へ進む」(x=10, y=0)へ 上に 7 マス (合計 7 マス) → "
                "「北の道」の「北の端」(x=8, y=0)へ (マスの手順なし) → 「港町」"
            ),
        ]
    )


@pytest.mark.parametrize(
    "road_tiles, steps",
    [
        (("#.#", "#.#", "#.#"), "上に 2 マス (合計 2 マス)"),
        (
            ("#.#", "#?#", "#.#"),
            (
                "経路なし (通れるマスだけではたどり着けない)。未確認のマス (?) も通れるとすると: "
                "上に 2 マス (合計 2 マス、うち未確認 1 マス)"
            ),
        ),
        (("#.#", "###", "#.#"), "経路なし (未確認のマス (?) を通ってもたどり着けない)"),
    ],
    ids=["passable", "through-unknown", "unreachable"],
)
def test_second_leg_starts_from_the_previous_to_tile(road_tiles, steps):
    # 2 つ目の区間は、1 つ目のつながりの着いたマス (8, 2) から、北の道から出るつながりのマス (8, 0) まで
    # (場所の中の経路と同じ規則で、? を通る経路も書く)
    road = dataclasses.replace(NORTH_ROAD, origin=TilePosition(7, 0), tiles=road_tiles)
    document = dataclasses.replace(WORLD, places=(START_TOWN, road, PORT_TOWN))
    assert route_lines(document, WORLD_STATE)[-1] == (
        f"- 「港町」まで (つながり 2 件): {WORLD_FIRST_LEG} → 「北の道」の「北の端」(x=8, y=0)へ {steps} → 「港町」"
    )


@pytest.mark.parametrize(
    "to_tile, port_from_tile, second_leg",
    [
        # 1 つ目のつながりの着いたマスが無い
        (
            None,
            TilePosition(8, 0),
            "「北の道」の「北の端」(x=8, y=0)へ (マスの手順なし)",
        ),
        # 着いたマスが北の道の tiles の範囲の外
        (
            TilePosition(20, 20),
            TilePosition(8, 0),
            "「北の道」の「北の端」(x=8, y=0)へ (マスの手順なし)",
        ),
        # 北の道から出るつながりのマスが tiles の範囲の外
        (
            TilePosition(8, 2),
            TilePosition(30, 0),
            "「北の道」の「北の端」(x=30, y=0)へ (マスの手順なし)",
        ),
        # 北の道から出るつながりのマスが無い: 座標を書かない
        (TilePosition(8, 2), None, "「北の道」の「北の端」へ (マスの手順なし)"),
    ],
    ids=["no-to-tile", "to-tile-outside", "from-tile-outside", "no-from-tile"],
)
def test_leg_without_tiles_to_route_has_no_steps(to_tile, port_from_tile, second_leg):
    road = dataclasses.replace(
        NORTH_ROAD, origin=TilePosition(7, 0), tiles=("#.#", "#.#", "#.#")
    )
    document = GridMapDocument(
        places=(START_TOWN, road, PORT_TOWN),
        connections=(
            dataclasses.replace(TO_NORTH_ROAD, to_tile=to_tile),
            dataclasses.replace(TO_PORT_TOWN, from_tile=port_from_tile),
        ),
    )
    assert route_lines(document, WORLD_STATE)[-1] == (
        f"- 「港町」まで (つながり 2 件): {WORLD_FIRST_LEG} → {second_leg} → 「港町」"
    )


def test_legs_without_from_tile_and_destinations_without_target_lines():
    # 始まりの町から北の道へのつながりに出たマスが無い: 目標の行は無く (B は理由の行)、北の道もつながり 1 件の行き先に
    # なる。区間の座標を書かず、マスの手順も書かない
    document = dataclasses.replace(
        WORLD,
        connections=(
            dataclasses.replace(TO_NORTH_ROAD, from_tile=None),
            TO_PORT_TOWN,
        ),
    )
    assert route_lines(document, WORLD_STATE) == [
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 始まりの町 のマス (x=10, y=7)",
        "マップの場所「始まりの町」に、マスの図 (tiles) の範囲の中の出口とつながりが無いため、経路を計算しない",
        "場所をまたぐ経路 (つながりをたどる):",
        "- 「北の道」まで (つながり 1 件): 「始まりの町」の「北の道へ進む」へ (マスの手順なし) → 「北の道」",
        (
            "- 「港町」まで (つながり 2 件): 「始まりの町」の「北の道へ進む」へ (マスの手順なし) → "
            "「北の道」の「北の端」(x=8, y=0)へ (マスの手順なし) → 「港町」"
        ),
    ]


def test_leg_route_avoids_other_connection_tiles_of_the_place():
    # 北の道の (8, 1) は、北の道から出る別のつながりのマス。(7, 1) から (9, 1) へは、踏まずに上を回る
    road = dataclasses.replace(
        NORTH_ROAD, origin=TilePosition(7, 0), tiles=("...", "...", "...")
    )
    document = GridMapDocument(
        places=(START_TOWN, road, PORT_TOWN),
        connections=(
            dataclasses.replace(TO_NORTH_ROAD, to_tile=TilePosition(7, 1)),
            dataclasses.replace(TO_PORT_TOWN, from_tile=TilePosition(9, 1)),
            link("北の道", "まだ無い場所", "真ん中の穴", TilePosition(8, 1)),
        ),
    )
    assert route_lines(document, WORLD_STATE)[-1] == (
        f"- 「港町」まで (つながり 2 件): {WORLD_FIRST_LEG} → 「北の道」の「北の端」(x=9, y=1)へ "
        "上に 1 マス → 右に 2 マス → 下に 1 マス (合計 4 マス) → 「港町」"
    )


# 広場: 西の門 (0, 0) から西の町へ、(4, 0) から東の町 (別名で書いたつながり) へ、出たマスの無いつながりで南の村へ。
# 西の町から港町へ
SQUARE = grid_place(
    "広場", tiles=(".....",), exits=(TileMark(TilePosition(0, 0), "西の門"),)
)
SQUARE_DOCUMENT = GridMapDocument(
    places=(
        SQUARE,
        grid_place("西の町"),
        grid_place("東の町", aliases=("東の町の別名",)),
        grid_place("南の村"),
        PORT_TOWN,
    ),
    connections=(
        link("広場", "西の町", "西へ", TilePosition(0, 0)),
        link("広場", "東の町の別名", "東へ", TilePosition(4, 0)),
        link("広場", "南の村", "南へ"),
        link("西の町", "港町", "港へ"),
    ),
)


def test_one_connection_destinations_on_target_lines_are_left_out():
    # つながり 1 件で行ける場所のうち、目標の行の行き先 (出口の行き先と、別名で書いた行き先を含む) に出る西の町と
    # 東の町は書かない。南の村 (出たマスが無く、目標の行が無い) と、つながり 2 件の港町は書く
    assert route_lines(
        SQUARE_DOCUMENT, state(place_name="広場", current_tile=TilePosition(2, 0))
    ) == [
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 広場 のマス (x=2, y=0)",
        "- 出口「西の門」(x=0, y=0)。行き先: 西の町。手順: 左に 2 マス (合計 2 マス)",
        "- つながり「東へ」(x=4, y=0)。行き先: 東の町の別名。手順: 右に 2 マス (合計 2 マス)",
        "場所をまたぐ経路 (つながりをたどる):",
        "- 「南の村」まで (つながり 1 件): 「広場」の「南へ」へ (マスの手順なし) → 「南の村」",
        (
            "- 「港町」まで (つながり 2 件): 「広場」の「西へ」(x=0, y=0)へ 左に 2 マス (合計 2 マス) → "
            "「西の町」の「港へ」へ (マスの手順なし) → 「港町」"
        ),
    ]


def test_one_connection_destinations_are_written_without_target_lines():
    # 現在地のマスが範囲の外で、目標の行が無いときは、つながり 1 件で行ける場所も書く (行き先の名前は、マップの
    # 場所の name)
    assert route_lines(
        SQUARE_DOCUMENT, state(place_name="広場", current_tile=TilePosition(9, 9))
    )[2:] == [
        "場所をまたぐ経路 (つながりをたどる):",
        "- 「西の町」まで (つながり 1 件): 「広場」の「西へ」(x=0, y=0)へ (マスの手順なし) → 「西の町」",
        "- 「東の町」まで (つながり 1 件): 「広場」の「東へ」(x=4, y=0)へ (マスの手順なし) → 「東の町」",
        "- 「南の村」まで (つながり 1 件): 「広場」の「南へ」へ (マスの手順なし) → 「南の村」",
        (
            "- 「港町」まで (つながり 2 件): 「広場」の「西へ」(x=0, y=0)へ (マスの手順なし) → "
            "「西の町」の「港へ」へ (マスの手順なし) → 「港町」"
        ),
    ]


def test_describe_cross_place_routes_with_and_without_targets():
    targets = list_route_targets(SQUARE_DOCUMENT, SQUARE)
    with_targets = describe_cross_place_routes(
        SQUARE_DOCUMENT, SQUARE, TilePosition(2, 0), targets
    ).split("\n")
    without_targets = describe_cross_place_routes(
        SQUARE_DOCUMENT, SQUARE, TilePosition(2, 0), ()
    ).split("\n")
    assert [line.split("」まで")[0] for line in with_targets[1:]] == [
        "- 「南の村",
        "- 「港町",
    ]
    assert [line.split("」まで")[0] for line in without_targets[1:]] == [
        "- 「西の町",
        "- 「東の町",
        "- 「南の村",
        "- 「港町",
    ]
    # 目標が無くても、区間のマスの手順は開始のマスから求める
    assert without_targets[1] == (
        "- 「西の町」まで (つながり 1 件): 「広場」の「西へ」(x=0, y=0)へ 左に 2 マス (合計 2 マス) → 「西の町」"
    )


def test_cross_place_destinations_are_ordered_and_at_most_ten():
    # 駅からつながり 1 件で町 0〜8 へ、2 件で遠い町 0 と遠い町 1 へ行ける。places の順では遠い町が先だが、
    # つながりの数の少ない順に並べ、同じ数なら places の順にして、先頭の 10 件を書く
    station = grid_place("駅")
    near = tuple(grid_place(f"町 {number}") for number in range(9))
    far = tuple(grid_place(f"遠い町 {number}") for number in range(2))
    document = GridMapDocument(
        places=(*far, station, *near),
        connections=(
            *(link("駅", f"町 {number}", f"町 {number}へ") for number in range(9)),
            link("町 0", "遠い町 1", "遠い町 1へ"),
            link("町 1", "遠い町 0", "遠い町 0へ"),
        ),
    )
    lines = route_lines(
        document, state(place_name="駅", current_tile=TilePosition(0, 0))
    )
    # A・B (tiles が空の理由)・見出しの行・10 件
    assert len(lines) == 3 + MAX_CROSS_PLACE_DESTINATIONS
    assert lines[2] == CROSS_PLACE_ROUTES_HEADING
    assert [line.split("」まで")[0] for line in lines[3:]] == [
        *(f"- 「町 {number}" for number in range(9)),
        "- 「遠い町 0",
    ]
    assert lines[-1] == (
        "- 「遠い町 0」まで (つながり 2 件): 「駅」の「町 1へ」へ (マスの手順なし) → "
        "「町 1」の「遠い町 0へ」へ (マスの手順なし) → 「遠い町 0」"
    )


def test_place_that_changes_on_entry_is_a_destination_but_not_followed():
    station = grid_place("駅")
    document = GridMapDocument(
        places=(
            station,
            grid_place("迷いの森", changes_on_entry=True),
            grid_place("洞窟"),
        ),
        connections=(
            link("駅", "迷いの森", "北の線路"),
            link("迷いの森", "洞窟", "奥へ"),
        ),
    )
    assert route_lines(
        document, state(place_name="駅", current_tile=TilePosition(0, 0))
    )[2:] == [
        "場所をまたぐ経路 (つながりをたどる):",
        "- 「迷いの森」まで (つながり 1 件): 「駅」の「北の線路」へ (マスの手順なし) → 「迷いの森」",
    ]


def test_unusable_connections_lead_nowhere():
    # 逆向きのつながり、マップに無い場所へのつながり、同じ場所へのつながりは使わない
    station = grid_place("駅", aliases=("中央駅",))
    document = GridMapDocument(
        places=(station, grid_place("港町")),
        connections=(
            link("港町", "駅", "船着き場から"),
            link("駅", "まだ無い場所", "地下道"),
            link("駅", "中央駅", "ホームを移る"),
        ),
    )
    assert route_lines(
        document, state(place_name="駅", current_tile=TilePosition(0, 0))
    )[2:] == [NO_CROSS_PLACE]
