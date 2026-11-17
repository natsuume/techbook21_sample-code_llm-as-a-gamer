"""middle の出力の new_tasks の行き先を、その回の入力のマップと照らして直す (fix_new_tasks)。I/O を扱わない。

規則 (new_tasks の順に、行き先が null でないタスクだけを直す。行き先が null のタスクはそのまま):
    1. マップが無い (None) → 行き先を null にする (no_map)
    2. place の前後の空白を除いた文字列を、マップの場所の並びで、まず全場所の name、次に全場所の aliases の順に
       (name・aliases も前後の空白を除いて) 比べ、最初に当たった場所を行き先の場所にする
       - どれにも当たらない → 行き先を null にする (unknown_place)
       - aliases で当たった → place をその場所の name に置き換える (alias_place)。name で当たれば place は変えない
    3. tile が null でなければ:
       - マス目型 (GridMapDocument) のマップ: その場所の exits の座標・landmarks の座標・その場所から出るつながり
         (from_place の前後の空白を除いた文字列が、その場所の name か aliases に当たる MapConnection) の from_tile の
         どれかと同じ座標なら残し、それ以外は null にする (tile_not_listed)。tiles の範囲の外の座標でも、条件に
         当たれば残す (notes.grid_route.connection_from_tiles を使ってよい)
       - 自由型 (MapDocument) のマップ: null にする (free_map_tile)
       どちらも place は残す
直したものごとに DestinationFix を 1 つ返す。1 つの行き先で place の置き換えと tile の除去が両方起きたら、
DestinationFix を 2 つ、alias_place → tile_not_listed (または free_map_tile) の順に返す (2 つ目の before は、1 つ目の
after)。直した内容は middle/calls.jsonl の destination_fixes に destination_fix_to_json の形で書く。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from switch2_layers.notes.map_document import GridMapDocument, MapDocument
from switch2_layers.tasks.task_queue import NewTask, TaskDestination

# 行き先を直した理由:
#   no_map            マップが無いため、行き先を null にした
#   unknown_place     place がマップの場所の name にも aliases にも無いため、行き先を null にした
#   alias_place       place がマップの場所の aliases に当たったため、place をその場所の name に置き換えた
#   tile_not_listed   tile がその場所の exits・landmarks・出るつながりの from_tile のどれでもないため、tile を null にした
#   free_map_tile     マップが自由型 (座標が無い) のため、tile を null にした
DestinationFixReason = Literal[
    "no_map", "unknown_place", "alias_place", "tile_not_listed", "free_map_tile"
]


@dataclass(frozen=True)
class DestinationFix:
    """行き先を 1 回直した内容。

    index    直したタスクの、new_tasks の中の位置 (0 始まり)
    before   直す前の行き先
    after    直した後の行き先。行き先を null にしたら None
    reason   直した理由
    """

    index: int
    before: TaskDestination
    after: TaskDestination | None
    reason: DestinationFixReason


def fix_new_tasks(
    new_tasks: Sequence[NewTask], map_document: MapDocument | GridMapDocument | None
) -> tuple[tuple[NewTask, ...], tuple[DestinationFix, ...]]:
    """new_tasks の行き先を map_document と照らして直す (モジュールの docstring の規則)。

    (直した後の new_tasks (new_tasks と同じ順・同じ数。文はそのまま), 直した内容 (new_tasks の順。1 つの行き先の中は
    alias_place → tile_not_listed / free_map_tile の順)) を返す。直したものが無ければ、直した内容は空のタプル。
    """
    raise NotImplementedError


def destination_fix_to_json(fix: DestinationFix) -> dict[str, object]:
    """直した内容 fix を {"index", "before", "after", "reason"} (キーはこの順) にする。

    before と after は tasks.task_files.destination_to_json の形 (after が None なら null)。
    """
    raise NotImplementedError
