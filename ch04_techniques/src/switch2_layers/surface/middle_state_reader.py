"""surface が middle/state.json から読む値 (現在の場面・系列情報とその作成時刻・待ち時間・現在地)。

- 待ち時間は surface_wait_s を SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S 秒の範囲に収めて使う。
  ファイルが無いか読めない (壊れた JSON を含む) ときは DEFAULT_SURFACE_WAIT_S
- 系列情報は timeline と generated_t を使う。ファイルが無いか読めないときは空 (空文字列と None)
- 現在の場面は scene_id。ファイルが無いか読めないときは None (場面が決まっていない)
- 現在地は place_name・current_tile と、それを出した middle の出力がもとにした画面の時刻 based_on_t。
  ファイルが無いか読めないときは、どれも None
middle はこのファイルを置き換えの書き込みで書くため、書きかけの内容を読むことはない。
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from switch2_layers.notes.map_document import TilePosition
from switch2_layers.notes.note_paths import is_scene_id

SURFACE_WAIT_MIN_S = 0.2
SURFACE_WAIT_MAX_S = 2.0
DEFAULT_SURFACE_WAIT_S = 0.2


@dataclass(frozen=True)
class SurfaceContext:
    """surface が middle/state.json から読んだ値。

    scene_id      現在の場面 ID。決まっていなければ None
    timeline      系列情報。無ければ空文字列
    generated_t   系列情報を作った時刻 (middle/state.json の generated_t)。無ければ None
    wait_s        次の要求までの待ち時間 (秒。SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S)
    place_name    現在地の場所の名前 (middle/state.json の place_name。書かれたまま)。無ければ None
    current_tile  現在地のマス (middle/state.json の current_tile)。無ければ None
    based_on_t    place_name と current_tile を出した middle の出力がもとにした画面の時刻 (middle/state.json の
                  based_on_t)。無ければ None
    place_name・current_tile・based_on_t の既定値は、値の無い SurfaceContext (EMPTY_SURFACE_CONTEXT やテストなど) のため。
    """

    scene_id: str | None
    timeline: str
    generated_t: float | None
    wait_s: float
    place_name: str | None = None
    current_tile: TilePosition | None = None
    based_on_t: float | None = None


# middle/state.json が無いか読めないときの値
EMPTY_SURFACE_CONTEXT = SurfaceContext(
    scene_id=None, timeline="", generated_t=None, wait_s=DEFAULT_SURFACE_WAIT_S
)


def clamp_surface_wait(value: object) -> float:
    """surface_wait_s の値 value を待ち時間にする。

    - 数 (int・float。bool は除く) なら SURFACE_WAIT_MIN_S〜SURFACE_WAIT_MAX_S の範囲に収める
      (小さければ SURFACE_WAIT_MIN_S、大きければ SURFACE_WAIT_MAX_S。-inf と inf も収める)
    - nan、数でない値、None は DEFAULT_SURFACE_WAIT_S
    """
    if not _is_number(value):
        return DEFAULT_SURFACE_WAIT_S
    number = float(value)
    if math.isnan(number):
        return DEFAULT_SURFACE_WAIT_S
    return min(max(number, SURFACE_WAIT_MIN_S), SURFACE_WAIT_MAX_S)


def read_surface_context(path: Path) -> SurfaceContext:
    """middle/state.json の path を読み、SurfaceContext を返す。

    - ファイルが無い・読めない・JSON として壊れている・object でないときは EMPTY_SURFACE_CONTEXT
    - object なら、項目ごとに次のように読む (型が合わない項目は、無いものとして扱う)
        scene_id      場面 ID の形 (notes.note_paths.is_scene_id) の文字列ならその値、それ以外は None
        timeline      文字列ならその値、それ以外は空文字列
        generated_t   数 (bool を除く) ならその値、それ以外は None
        wait_s        clamp_surface_wait(surface_wait_s の値。無ければ None)
        place_name    前後の空白を除いても空でない文字列ならその値 (書かれたまま)、それ以外は None
        current_tile  {"x": 整数, "y": 整数} の object (整数は bool を除く int。1.0 は整数としない) なら TilePosition、
                      それ以外は None
        based_on_t    数 (bool を除く int・float) なら float にした値、それ以外は None
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return EMPTY_SURFACE_CONTEXT
    if not isinstance(value, dict):
        return EMPTY_SURFACE_CONTEXT
    scene_id = value.get("scene_id")
    timeline = value.get("timeline")
    generated_t = value.get("generated_t")
    place_name = value.get("place_name")
    based_on_t = value.get("based_on_t")
    return SurfaceContext(
        scene_id=scene_id
        if isinstance(scene_id, str) and is_scene_id(scene_id)
        else None,
        timeline=timeline if isinstance(timeline, str) else "",
        generated_t=float(generated_t) if _is_number(generated_t) else None,
        wait_s=clamp_surface_wait(value.get("surface_wait_s")),
        place_name=place_name
        if isinstance(place_name, str) and place_name.strip()
        else None,
        current_tile=_tile_position(value.get("current_tile")),
        based_on_t=float(based_on_t) if _is_number(based_on_t) else None,
    )


def _tile_position(value: object) -> TilePosition | None:
    """value が {"x": 整数, "y": 整数} の object (整数は bool を除く int) なら TilePosition、それ以外は None を返す。"""
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    y = value.get("y")
    if _is_integer(x) and _is_integer(y):
        return TilePosition(x=x, y=y)
    return None


def _is_integer(value: object) -> TypeGuard[int]:
    """value が整数 (int。bool を除く) なら True。JSON の 1.0 は float として読まれるため整数としない。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    """value が数 (int・float。bool を除く) なら True。"""
    return isinstance(value, int | float) and not isinstance(value, bool)
