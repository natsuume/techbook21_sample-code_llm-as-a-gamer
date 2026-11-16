import pytest

from switch2_layers.notes.cross_place_route import (
    CrossPlaceLeg,
    CrossPlaceRoute,
    find_cross_place_routes,
)
from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
    TilePosition,
)


def place(
    name: str, aliases: tuple[str, ...] = (), changes_on_entry: bool = False
) -> GridMapPlace:
    return GridMapPlace(
        name=name,
        aliases=aliases,
        description="",
        state="",
        changes_on_entry=changes_on_entry,
        origin=TilePosition(0, 0),
        tiles=(),
        exits=(),
        blocked=(),
        landmarks=(),
    )


def link(from_place: str, to_place: str, how: str = "") -> MapConnection:
    return MapConnection(
        from_place=from_place,
        to_place=to_place,
        how=how or f"{from_place}から{to_place}へ",
        from_tile=None,
        to_tile=None,
    )


def document(
    places: tuple[GridMapPlace, ...], connections: tuple[MapConnection, ...]
) -> GridMapDocument:
    return GridMapDocument(places=places, connections=connections)


def destinations(routes: tuple[CrossPlaceRoute, ...]) -> list[str]:
    return [found.destination.name for found in routes]


TOWN = place("始まりの町", aliases=("町",))
NORTH_ROAD = place("北の道")
PORT = place("港町")
CAVE = place("洞窟")
FOREST = place("森")


# --- 道筋のたどり方 ---


def test_routes_follow_connections_breadth_first():
    to_road = link("始まりの町", "北の道", "北の道へ進む")
    to_port = link("北の道", "港町", "北の端")
    to_cave = link("港町", "洞窟", "港の裏")
    world = document((TOWN, NORTH_ROAD, PORT, CAVE), (to_road, to_port, to_cave))
    assert find_cross_place_routes(world, TOWN) == (
        CrossPlaceRoute(NORTH_ROAD, (CrossPlaceLeg(TOWN, to_road),)),
        CrossPlaceRoute(
            PORT, (CrossPlaceLeg(TOWN, to_road), CrossPlaceLeg(NORTH_ROAD, to_port))
        ),
        CrossPlaceRoute(
            CAVE,
            (
                CrossPlaceLeg(TOWN, to_road),
                CrossPlaceLeg(NORTH_ROAD, to_port),
                CrossPlaceLeg(PORT, to_cave),
            ),
        ),
    )


def test_legs_keep_the_places_and_connections_of_the_map():
    to_road = link("始まりの町", "北の道", "北の道へ進む")
    to_port = link("北の道", "港町", "北の端")
    world = document((TOWN, NORTH_ROAD, PORT), (to_road, to_port))
    found = find_cross_place_routes(world, TOWN)[1]
    # 区間の場所は、つながりの from を当てはめたマップの場所 (同じ要素)
    assert found.destination is PORT
    assert [leg.place for leg in found.legs] == [TOWN, NORTH_ROAD]
    assert found.legs[0].place is TOWN
    assert found.legs[1].place is NORTH_ROAD
    assert found.legs[1].connection is to_port


def test_connections_are_matched_by_name_and_aliases():
    # from と to は、場所の name か aliases に、前後の空白を除いて当てはめる。つながりは書かれたまま区間に残す
    road = place("北の道", aliases=(" 北の街道 ",))
    by_alias = link(" 町 ", "北の街道", "北へ")
    world = document((TOWN, road), (by_alias,))
    assert find_cross_place_routes(world, TOWN) == (
        CrossPlaceRoute(road, (CrossPlaceLeg(TOWN, by_alias),)),
    )


def test_connections_are_matched_to_the_first_place_with_the_name():
    # 「部屋」は、places の順に最初に一致した場所 (aliases に「部屋」を持つ家) に当てはめる
    house = place("家", aliases=("部屋",))
    room = place("部屋")
    world = document((TOWN, house, room), (link("始まりの町", "部屋"),))
    assert destinations(find_cross_place_routes(world, TOWN)) == ["家"]


def test_unmatched_and_same_place_connections_are_not_used():
    world = document(
        (TOWN, NORTH_ROAD, PORT),
        (
            # to がマップの場所に無い
            link("始まりの町", "知らない場所"),
            # from がマップの場所に無い (始まりの町から、この先へは行けない)
            link("知らない場所", "北の道"),
            # from と to が同じ場所 (aliases で書いたものも)
            link("始まりの町", "始まりの町"),
            link("始まりの町", "町"),
            link("始まりの町", "港町", "南の門"),
        ),
    )
    assert destinations(find_cross_place_routes(world, TOWN)) == ["港町"]


def test_connections_are_followed_only_in_their_direction():
    # 北の道から始まりの町へのつながりは、逆向き (始まりの町から北の道) には使わない
    world = document((TOWN, NORTH_ROAD), (link("北の道", "始まりの町"),))
    assert find_cross_place_routes(world, TOWN) == ()
    assert destinations(find_cross_place_routes(world, NORTH_ROAD)) == ["始まりの町"]


def test_places_found_first_are_followed_first():
    # 洞窟へは、北の道を通っても港町を通っても 2 件。connections では港町から洞窟へのつながりが先に出てくるが、
    # 先に見つけた北の道から出るつながりを使う
    to_road = link("始まりの町", "北の道")
    to_port = link("始まりの町", "港町")
    port_to_cave = link("港町", "洞窟")
    road_to_cave = link("北の道", "洞窟")
    world = document(
        (TOWN, NORTH_ROAD, PORT, CAVE), (to_road, to_port, port_to_cave, road_to_cave)
    )
    [cave_route] = [
        found
        for found in find_cross_place_routes(world, TOWN)
        if found.destination is CAVE
    ]
    assert cave_route.legs == (
        CrossPlaceLeg(TOWN, to_road),
        CrossPlaceLeg(NORTH_ROAD, road_to_cave),
    )


def test_first_connection_in_order_is_used_between_the_same_places():
    left = link("始まりの町", "北の道", "左の門")
    right = link("始まりの町", "北の道", "右の門")
    world = document((TOWN, NORTH_ROAD), (left, right))
    assert find_cross_place_routes(world, TOWN) == (
        CrossPlaceRoute(NORTH_ROAD, (CrossPlaceLeg(TOWN, left),)),
    )


def test_place_that_changes_on_entry_is_a_destination_but_not_followed():
    maze = place("迷いの森", changes_on_entry=True)
    world = document(
        (TOWN, maze, CAVE),
        (link("始まりの町", "迷いの森"), link("迷いの森", "洞窟")),
    )
    assert destinations(find_cross_place_routes(world, TOWN)) == ["迷いの森"]


def test_place_that_changes_on_entry_can_be_passed_by_another_way():
    # 入るたびに構造が変わる場所の先でも、ほかの道筋で行ける場所は行き先になる
    maze = place("迷いの森", changes_on_entry=True)
    world = document(
        (TOWN, maze, NORTH_ROAD, CAVE),
        (
            link("始まりの町", "迷いの森"),
            link("迷いの森", "洞窟"),
            link("始まりの町", "北の道"),
            link("北の道", "洞窟"),
        ),
    )
    routes = find_cross_place_routes(world, TOWN)
    assert destinations(routes) == ["迷いの森", "北の道", "洞窟"]
    assert [leg.place.name for leg in routes[2].legs] == ["始まりの町", "北の道"]


def test_start_is_not_a_destination():
    world = document(
        (TOWN, NORTH_ROAD),
        (link("始まりの町", "北の道"), link("北の道", "始まりの町")),
    )
    assert destinations(find_cross_place_routes(world, TOWN)) == ["北の道"]


def test_start_is_followed_even_if_it_changes_on_entry():
    maze = place("迷いの森", changes_on_entry=True)
    world = document((maze, TOWN), (link("迷いの森", "始まりの町"),))
    assert destinations(find_cross_place_routes(world, maze)) == ["始まりの町"]


def test_place_without_usable_connections_has_no_routes():
    assert find_cross_place_routes(document((TOWN, NORTH_ROAD), ()), TOWN) == ()


def test_start_that_is_not_in_the_map_is_an_error():
    with pytest.raises(ValueError):
        find_cross_place_routes(document((TOWN,), ()), place("始まりの町"))


# --- 並べ方 ---


def test_routes_are_ordered_by_connections_then_by_places_order():
    # places の順: 港町・洞窟・北の道・始まりの町・森。つながり 1 件の北の道と森を places の順に、続けて
    # つながり 2 件の港町と洞窟を places の順に並べる
    world = document(
        (PORT, CAVE, NORTH_ROAD, TOWN, FOREST),
        (
            link("始まりの町", "森"),
            link("始まりの町", "北の道"),
            link("北の道", "洞窟"),
            link("北の道", "港町"),
        ),
    )
    routes = find_cross_place_routes(world, TOWN)
    assert destinations(routes) == ["北の道", "森", "港町", "洞窟"]
    assert [len(found.legs) for found in routes] == [1, 1, 2, 2]


def test_routes_are_not_limited_in_number():
    many = tuple(place(f"場所 {number}") for number in range(12))
    world = document(
        (TOWN, *many),
        tuple(link("始まりの町", f"場所 {number}") for number in range(12)),
    )
    assert destinations(find_cross_place_routes(world, TOWN)) == [
        f"場所 {number}" for number in range(12)
    ]
