from switch2_layers.middle.place_names import collect_place_names
from switch2_layers.notes.map_document import MapConnection, MapDocument, MapPlace


def place(name: str, *aliases: str) -> MapPlace:
    return MapPlace(
        name=name,
        aliases=aliases,
        description="",
        exits=(),
        blocked=(),
        state="",
        changes_on_entry=False,
    )


def document(*places: MapPlace) -> MapDocument:
    return MapDocument(
        places=places,
        connections=(MapConnection(from_place="町", to_place="草原", how="北の出口"),),
    )


def test_names_and_aliases_of_the_map_then_recorded_names():
    # map.json の場所ごとに name、続けて aliases。その後に記録の place_name を書かれた順に
    map_document = document(place("家の 1 階", "1 階の部屋", "居間"), place("町"))
    assert collect_place_names(map_document, ["草原", "洞窟"]) == [
        "家の 1 階",
        "1 階の部屋",
        "居間",
        "町",
        "草原",
        "洞窟",
    ]


def test_connections_are_not_place_names():
    # つながりの from・to は一覧に入れない (場所の名前は places と記録から作る)
    assert collect_place_names(document(place("町")), []) == ["町"]


def test_duplicates_keep_the_first_appearance():
    map_document = document(
        place("家の 1 階", "1 階の部屋", "家の 1 階"),
        place("外", "1 階の部屋"),
    )
    assert collect_place_names(map_document, ["外", "庭", "家の 1 階", "庭"]) == [
        "家の 1 階",
        "1 階の部屋",
        "外",
        "庭",
    ]


def test_without_map_only_recorded_names_are_used():
    assert collect_place_names(None, ["草原", "町", "草原"]) == ["草原", "町"]


def test_names_are_stripped_and_blank_names_are_skipped():
    map_document = document(place(" 家 ", "", "  ", " 部屋 "))
    assert collect_place_names(map_document, [" 部屋", "   ", "庭 ", "家"]) == [
        "家",
        "部屋",
        "庭",
    ]


def test_nothing_gives_an_empty_list():
    assert collect_place_names(None, []) == []
    assert collect_place_names(MapDocument(places=(), connections=()), []) == []
