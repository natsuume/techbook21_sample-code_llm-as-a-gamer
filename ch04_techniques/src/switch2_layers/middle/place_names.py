"""middle に渡す場所の名前の一覧を、マップの一覧 (map.json) と middle の記録の場所の名前から作る。I/O を扱わない。

middle は、場面ラベルと同じく、この一覧に当てはまる場所があればその名前をそのまま出力し、無いときだけ新しい名前を作る。
deep が重複をまとめた別の名前 (aliases) も一覧に入れ、middle が前に使った名前でも選べるようにする。
"""

from collections.abc import Sequence

from switch2_layers.notes.map_document import MapDocument


def collect_place_names(
    document: MapDocument | None, recorded_names: Sequence[str]
) -> list[str]:
    """場所の名前の一覧を返す。

    document         notes/map/map.json の中身。None はマップなし
    recorded_names   middle/map_records.jsonl の place_name (書かれた順。middle.map_records.read_recorded_place_names)
    並べる順: document の places の順に、各場所の name、続けてその場所の aliases (aliases の順)。その後に recorded_names
    の順。各名前は前後の空白を除いた形にし、除いて空になるものは入れない。同じ名前 (前後の空白を除いた形で比べる) は、
    最初に現れたものだけを残す。document が None なら recorded_names だけから作る。
    """
    raise NotImplementedError
