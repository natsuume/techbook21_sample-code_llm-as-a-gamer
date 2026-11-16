"""grid (マス目型) のマップで、つながり (connections) をたどって、ある場所から行ける場所ごとの道筋 (通るつながりの順)
を求める。I/O を扱わない。

つながりの当てはめ:
    - つながりの from と to を、それぞれ notes.grid_route.find_grid_place でマップの場所に当てはめる (name と aliases
      を前後の空白を除いて比べ、places の順に最初に一致した場所)
    - from か to のどちらかが当てはまらないつながりと、from と to が同じ場所 (同じ要素) に当てはまるつながりは使わない
    - つながりは from から to への向きにだけたどる (逆向きに戻れるとは仮定しない)
道筋の決め方 (find_cross_place_routes):
    - 開始の場所から、通るつながりの数が少ない道筋を、幅優先でたどる。同じ数の道筋が複数あるときは、先に見つけた
      場所から出るつながりを使い、同じ場所から出るつながりの中では connections の順に先に出てくるものを使う
    - changes_on_entry が true の場所は、行き先にはなるが、その場所から先へはたどらない
    - 開始の場所は行き先にしない
場所は places の中の位置で区別する (同じ中身の場所が 2 つあっても、別の場所として扱う)。
"""

from dataclasses import dataclass

from switch2_layers.notes.map_document import (
    GridMapDocument,
    GridMapPlace,
    MapConnection,
)


@dataclass(frozen=True)
class CrossPlaceLeg:
    """場所をまたぐ道筋の区間 1 つ (ある場所の中を通って、その場所から出るつながりで次の場所へ移る)。

    place        区間の場所 (connection の from を当てはめたマップの場所)
    connection   その場所から出るつながり (マップの connections の要素のまま)
    """

    place: GridMapPlace
    connection: MapConnection


@dataclass(frozen=True)
class CrossPlaceRoute:
    """開始の場所から行き先の場所までの、つながりをたどる道筋。

    destination   行き先の場所 (最後の区間のつながりの to を当てはめたマップの場所)
    legs          区間 (通る順。1 件以上)。1 つ目の区間の place は開始の場所、2 つ目からは 1 つ前の区間の
                  つながりの to を当てはめた場所
    """

    destination: GridMapPlace
    legs: tuple[CrossPlaceLeg, ...]


def find_cross_place_routes(
    document: GridMapDocument, start: GridMapPlace
) -> tuple[CrossPlaceRoute, ...]:
    """document の場所 start (document.places の要素の 1 つ) から、つながりをたどって行ける場所ごとの道筋を返す
    (当てはめと道筋の決め方はモジュールの docstring)。

    たどり着ける場所 (start を除く) ごとに 1 件。並べ方は、legs の数の少ない順、同じなら document.places の順。
    件数は絞らない (行き先の選び方と件数の上限は、呼び出し側が決める)。
    start からは、start の changes_on_entry によらずたどる。start から出るつながりが無い・どのつながりも使えない
    ときは空のタプル。
    start は document.places の中で、同じオブジェクト (is) の要素の位置で探す。document.places に無ければ ValueError。
    """
    raise NotImplementedError
