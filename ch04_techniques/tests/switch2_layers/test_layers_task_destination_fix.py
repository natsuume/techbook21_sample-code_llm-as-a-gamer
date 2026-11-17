from typing import get_args

import pytest

from switch2_layers.middle.task_destination_fix import (
    DestinationFix,
    DestinationFixReason,
    destination_fix_to_json,
    fix_new_tasks,
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
from switch2_layers.tasks.task_queue import NewTask, TaskDestination


def grid_place(
    name: str,
    aliases: tuple[str, ...] = (),
    exits: tuple[TilePosition, ...] = (),
    landmarks: tuple[TilePosition, ...] = (),
    blocked: tuple[TilePosition, ...] = (),
) -> GridMapPlace:
    """マス目型の場所を返す (tiles は 4×4 の通れるマス)。"""
    return GridMapPlace(
        name=name,
        aliases=aliases,
        description="",
        state="",
        changes_on_entry=False,
        origin=TilePosition(0, 0),
        tiles=("....",) * 4,
        exits=tuple(TileMark(position, "出口") for position in exits),
        blocked=tuple(TileMark(position, "壁") for position in blocked),
        landmarks=tuple(TileMark(position, "目印") for position in landmarks),
    )


# 町: 出口 (3, 4)・目印 (2, 2)・通れないマス (1, 1)。町から出るつながりの出たマスは (0, 9) (tiles の範囲の外。
# つながりの from は町の別の名前)。草原から町へのつながりの出たマス (7, 7) は、町から出るつながりではない
GRID_MAP = GridMapDocument(
    places=(
        grid_place(
            "町",
            aliases=("町の中", "広場"),
            exits=(TilePosition(3, 4),),
            landmarks=(TilePosition(2, 2),),
            blocked=(TilePosition(1, 1),),
        ),
        grid_place("広場", aliases=("裏通り",)),
        grid_place("草原", aliases=("裏通り",)),
    ),
    connections=(
        MapConnection(
            from_place="町の中",
            to_place="草原",
            how="北の出口から上へ",
            from_tile=TilePosition(0, 9),
            to_tile=None,
        ),
        MapConnection(
            from_place="草原",
            to_place="町",
            how="南の出口から下へ",
            from_tile=TilePosition(7, 7),
            to_tile=None,
        ),
    ),
)
FREE_MAP = MapDocument(
    places=(
        MapPlace(
            name="町",
            aliases=("町の中",),
            description="",
            exits=("北の出口",),
            blocked=(),
            state="",
            changes_on_entry=False,
        ),
    ),
    connections=(),
)


def task(
    place: str, tile: TilePosition | None, text: str = "宿屋に入っている"
) -> NewTask:
    return NewTask(text=text, destination=TaskDestination(place=place, tile=tile))


def test_fix_reasons():
    assert get_args(DestinationFixReason) == (
        "no_map",
        "unknown_place",
        "alias_place",
        "tile_not_listed",
        "free_map_tile",
    )


# --- マップが無いとき ---


def test_destinations_without_map_become_null():
    new_tasks = (
        task("町", TilePosition(3, 4)),
        NewTask(text="会話の枠が閉じている"),
        task("どこか", None, text="店の前にいる"),
    )
    fixed, fixes = fix_new_tasks(new_tasks, None)
    assert fixed == (
        NewTask(text="宿屋に入っている"),
        NewTask(text="会話の枠が閉じている"),
        NewTask(text="店の前にいる"),
    )
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="町", tile=TilePosition(3, 4)),
            after=None,
            reason="no_map",
        ),
        DestinationFix(
            index=2,
            before=TaskDestination(place="どこか", tile=None),
            after=None,
            reason="no_map",
        ),
    )


# --- 場所 ---


@pytest.mark.parametrize("map_document", [GRID_MAP, FREE_MAP])
def test_tasks_without_destination_are_kept(map_document):
    new_tasks = (NewTask(text="会話の枠が閉じている"),)
    assert fix_new_tasks(new_tasks, map_document) == (new_tasks, ())


def test_place_matching_a_name_is_kept():
    new_tasks = (task("町", TilePosition(3, 4)), task("草原", None))
    assert fix_new_tasks(new_tasks, GRID_MAP) == (new_tasks, ())


@pytest.mark.parametrize("map_document", [GRID_MAP, FREE_MAP])
def test_unknown_place_becomes_null(map_document):
    new_tasks = (task("城", TilePosition(3, 4)),)
    fixed, fixes = fix_new_tasks(new_tasks, map_document)
    assert fixed == (NewTask(text="宿屋に入っている"),)
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="城", tile=TilePosition(3, 4)),
            after=None,
            reason="unknown_place",
        ),
    )


@pytest.mark.parametrize("place", ["町の中", "  町の中 "])
def test_alias_is_replaced_with_the_name(place):
    # 前後の空白を除いて照らす
    new_tasks = (task(place, TilePosition(3, 4)),)
    fixed, fixes = fix_new_tasks(new_tasks, GRID_MAP)
    assert fixed == (task("町", TilePosition(3, 4)),)
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place=place, tile=TilePosition(3, 4)),
            after=TaskDestination(place="町", tile=TilePosition(3, 4)),
            reason="alias_place",
        ),
    )


def test_names_of_all_places_are_matched_before_aliases():
    # 「広場」は 1 番目の場所 (町) の別の名前で、2 番目の場所の名前。名前を先に照らすので、2 番目の場所に当たる
    new_tasks = (task("広場", None),)
    assert fix_new_tasks(new_tasks, GRID_MAP) == (new_tasks, ())


def test_first_place_in_the_map_order_wins_among_aliases():
    # 「裏通り」は 2 番目 (広場) と 3 番目 (草原) の場所の別の名前。並びの最初の場所にする
    fixed, fixes = fix_new_tasks((task("裏通り", None),), GRID_MAP)
    assert fixed == (task("広場", None),)
    assert [fix.reason for fix in fixes] == ["alias_place"]


# --- マス ---


@pytest.mark.parametrize(
    "tile",
    [
        # 出口
        TilePosition(3, 4),
        # 目印
        TilePosition(2, 2),
        # その場所から出るつながりの出たマス (tiles の範囲の外でも残す)
        TilePosition(0, 9),
    ],
)
def test_listed_tile_is_kept(tile):
    new_tasks = (task("町", tile),)
    assert fix_new_tasks(new_tasks, GRID_MAP) == (new_tasks, ())


@pytest.mark.parametrize(
    "tile",
    [
        # どこにも載っていないマス
        TilePosition(0, 0),
        # 通れないマス
        TilePosition(1, 1),
        # ほかの場所から出るつながりの出たマス
        TilePosition(7, 7),
    ],
)
def test_tile_not_listed_becomes_null(tile):
    fixed, fixes = fix_new_tasks((task("町", tile),), GRID_MAP)
    # place は残す
    assert fixed == (task("町", None),)
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="町", tile=tile),
            after=TaskDestination(place="町", tile=None),
            reason="tile_not_listed",
        ),
    )


def test_alias_and_tile_fixes_are_two_fixes_in_order():
    fixed, fixes = fix_new_tasks((task("町の中", TilePosition(0, 0)),), GRID_MAP)
    assert fixed == (task("町", None),)
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="町の中", tile=TilePosition(0, 0)),
            after=TaskDestination(place="町", tile=TilePosition(0, 0)),
            reason="alias_place",
        ),
        DestinationFix(
            index=0,
            before=TaskDestination(place="町", tile=TilePosition(0, 0)),
            after=TaskDestination(place="町", tile=None),
            reason="tile_not_listed",
        ),
    )


def test_tile_on_a_free_map_becomes_null():
    fixed, fixes = fix_new_tasks(
        (task("町", TilePosition(3, 4)), task("町の中", TilePosition(1, 2))), FREE_MAP
    )
    assert fixed == (task("町", None), task("町", None))
    assert fixes == (
        DestinationFix(
            index=0,
            before=TaskDestination(place="町", tile=TilePosition(3, 4)),
            after=TaskDestination(place="町", tile=None),
            reason="free_map_tile",
        ),
        DestinationFix(
            index=1,
            before=TaskDestination(place="町の中", tile=TilePosition(1, 2)),
            after=TaskDestination(place="町", tile=TilePosition(1, 2)),
            reason="alias_place",
        ),
        DestinationFix(
            index=1,
            before=TaskDestination(place="町", tile=TilePosition(1, 2)),
            after=TaskDestination(place="町", tile=None),
            reason="free_map_tile",
        ),
    )


def test_place_only_destination_on_a_free_map_is_kept():
    new_tasks = (task("町", None),)
    assert fix_new_tasks(new_tasks, FREE_MAP) == (new_tasks, ())


def test_fixes_follow_the_order_of_the_new_tasks():
    new_tasks = (
        task("町", TilePosition(3, 4), text="A"),
        task("城", None, text="B"),
        NewTask(text="C"),
        task("町", TilePosition(0, 0), text="D"),
    )
    fixed, fixes = fix_new_tasks(new_tasks, GRID_MAP)
    # 文と数と順はそのまま
    assert [new_task.text for new_task in fixed] == ["A", "B", "C", "D"]
    assert [(fix.index, fix.reason) for fix in fixes] == [
        (1, "unknown_place"),
        (3, "tile_not_listed"),
    ]


# --- 記録の形 ---


def test_destination_fix_to_json():
    fix = DestinationFix(
        index=1,
        before=TaskDestination(place="町の中", tile=TilePosition(3, 4)),
        after=TaskDestination(place="町", tile=TilePosition(3, 4)),
        reason="alias_place",
    )
    value = destination_fix_to_json(fix)
    assert value == {
        "index": 1,
        "before": {"place": "町の中", "tile": {"x": 3, "y": 4}},
        "after": {"place": "町", "tile": {"x": 3, "y": 4}},
        "reason": "alias_place",
    }
    assert list(value) == ["index", "before", "after", "reason"]


def test_destination_fix_to_json_with_null_after():
    fix = DestinationFix(
        index=0,
        before=TaskDestination(place="城", tile=None),
        after=None,
        reason="unknown_place",
    )
    assert destination_fix_to_json(fix) == {
        "index": 0,
        "before": {"place": "城", "tile": None},
        "after": None,
        "reason": "unknown_place",
    }
