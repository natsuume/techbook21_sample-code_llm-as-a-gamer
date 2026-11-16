import dataclasses

import pytest

from switch2_layers.middle.middle_types import MiddleState
from switch2_layers.middle.route_section import (
    DIRECTION_LABELS,
    NO_ROUTE_TEXT,
    ROUTE_FREE_LAYOUT_TEXT,
    ROUTE_NO_CURRENT_TILE_TEXT,
    ROUTE_NO_MAP_TEXT,
    ROUTE_NO_PREVIOUS_OUTPUT_TEXT,
    ROUTE_SECTION_TITLE,
    describe_route_section,
    format_route,
)
from switch2_layers.notes.grid_route import GridRoute, MoveDirection, RouteStep
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
    "- 出口「地下への穴」(x=3, y=4)。手順: 経路なし (通れるマスだけではたどり着けない)",
    "- つながり「右上の階段を上に」(x=7, y=1)。行き先: 自分の部屋 (2 階)。手順: 移動しない (合計 0 マス)",
]


def state(**changes: object) -> MiddleState:
    return dataclasses.replace(STATE, **changes)


def assert_routes_are_described() -> None:
    """GRID_DOCUMENT と STATE で経路が書かれることを先に確かめる (理由の行のテストが、別の理由で通らないように)。"""
    assert describe_route_section(GRID_DOCUMENT, STATE) == lines_of(
        [HEADER, *TARGET_LINES]
    )


def test_section_title():
    assert ROUTE_SECTION_TITLE == "現在地からの経路 (コードの計算)"


# --- 経路を書く ---


def test_routes_from_the_current_tile():
    # 1 行目に前回の出力の場所・マス・入力の時刻、続けて目標ごとに 1 行 (出口 → つながりの順)
    assert_routes_are_described()


def test_place_found_by_alias_shows_the_map_name():
    text = describe_route_section(GRID_DOCUMENT, state(place_name="1 階の部屋"))
    assert text.split("\n")[0] == (
        "前回の middle の出力 (入力の時刻 t=10.000) の現在地: 1 階の部屋 (マップの場所「家の 1 階」) "
        "のマス (x=7, y=1)"
    )
    assert text.split("\n")[1:] == TARGET_LINES


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
    wide = dataclasses.replace(
        HOUSE,
        tiles=("." * 12,),
        exits=tuple(TileMark(TilePosition(x, 0), f"出口 {x}") for x in range(12)),
    )
    document = GridMapDocument(places=(wide,), connections=())
    lines = describe_route_section(
        document, state(current_tile=TilePosition(0, 0))
    ).split("\n")
    assert len(lines) == 1 + 10
    assert lines[1] == "- 出口「出口 0」(x=0, y=0)。手順: 移動しない (合計 0 マス)"
    assert lines[10] == "- 出口「出口 9」(x=9, y=0)。手順: 右に 9 マス (合計 9 マス)"


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
        ]
    )


# --- 経路を書かずに理由を書く ---


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
    [
        {"place_name": None},
        {"current_tile": None},
        {"place_name": None, "current_tile": None},
    ],
)
def test_previous_output_without_place_or_tile(changes):
    assert_routes_are_described()
    assert describe_route_section(GRID_DOCUMENT, state(**changes)) == (
        ROUTE_NO_CURRENT_TILE_TEXT
    )
    assert ROUTE_NO_CURRENT_TILE_TEXT == (
        "前回の middle の出力に、現在地の場所 (place_name) かマス (current_tile) が無いため、"
        "経路を計算しない"
    )


def test_place_that_is_not_in_the_map():
    assert_routes_are_described()
    assert describe_route_section(GRID_DOCUMENT, state(place_name="町")) == (
        "前回の middle の出力の現在地「町」がマップの場所 (name と aliases) に無いため、経路を計算しない"
    )


def test_place_with_empty_tiles():
    assert_routes_are_described()
    text = describe_route_section(GRID_DOCUMENT, state(place_name="自分の部屋 (2 階)"))
    assert text == (
        "マップの場所「自分の部屋 (2 階)」のマスの図 (tiles) が空のため、経路を計算しない"
    )


@pytest.mark.parametrize(
    "tile", [TilePosition(9, 1), TilePosition(-1, 1), TilePosition(7, 5)]
)
def test_current_tile_outside_the_tiles(tile):
    assert_routes_are_described()
    text = describe_route_section(GRID_DOCUMENT, state(current_tile=tile))
    assert text == (
        f"現在地のマス (x={tile.x}, y={tile.y}) がマップの場所「家の 1 階」のマスの図 (tiles) の範囲の外のため、"
        "経路を計算しない"
    )


def test_current_tile_on_a_wall_is_inside_the_tiles():
    # 開始のマスの文字は問わない
    text = describe_route_section(GRID_DOCUMENT, state(current_tile=TilePosition(0, 1)))
    assert text.split("\n")[0].endswith("のマス (x=0, y=1)")
    assert len(text.split("\n")) == 1 + len(TARGET_LINES)


def test_place_without_targets():
    assert_routes_are_described()
    no_targets = dataclasses.replace(
        HOUSE, exits=(TileMark(TilePosition(30, 30), "画面の外の通り道"),)
    )
    document = GridMapDocument(
        places=(no_targets,),
        connections=(
            # from_tile が無いつながりと、ほかの場所からのつながりは目標にならない
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
    assert describe_route_section(document, STATE) == (
        "マップの場所「家の 1 階」に、マスの図 (tiles) の範囲の中の出口とつながりが無いため、経路を計算しない"
    )


def test_reasons_are_checked_in_order():
    # マップなし → free → 前回の出力が無い → place_name か current_tile が無い → 場所が無い → tiles が空 →
    # 開始が範囲の外 → 目標が無い の順に確かめ、最初に当たったものだけを書く
    assert describe_route_section(None, None) == ROUTE_NO_MAP_TEXT
    assert describe_route_section(FREE_DOCUMENT, None) == ROUTE_FREE_LAYOUT_TEXT
    assert (
        describe_route_section(GRID_DOCUMENT, state(place_name="町", current_tile=None))
        == ROUTE_NO_CURRENT_TILE_TEXT
    )
    assert describe_route_section(
        GRID_DOCUMENT,
        state(place_name="自分の部屋 (2 階)", current_tile=TilePosition(99, 99)),
    ) == (
        "マップの場所「自分の部屋 (2 階)」のマスの図 (tiles) が空のため、経路を計算しない"
    )
    no_targets = GridMapDocument(
        places=(dataclasses.replace(HOUSE, exits=()),), connections=()
    )
    assert describe_route_section(
        no_targets, state(current_tile=TilePosition(99, 99))
    ) == (
        "現在地のマス (x=99, y=99) がマップの場所「家の 1 階」のマスの図 (tiles) の範囲の外のため、"
        "経路を計算しない"
    )


@pytest.mark.parametrize(
    ("document", "previous"),
    [
        (None, STATE),
        (FREE_DOCUMENT, STATE),
        (GRID_DOCUMENT, None),
        (GRID_DOCUMENT, dataclasses.replace(STATE, place_name=None)),
        (GRID_DOCUMENT, dataclasses.replace(STATE, place_name="町")),
        (GRID_DOCUMENT, dataclasses.replace(STATE, place_name="自分の部屋 (2 階)")),
        (GRID_DOCUMENT, dataclasses.replace(STATE, current_tile=TilePosition(9, 9))),
    ],
)
def test_reason_is_one_line(document, previous):
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
