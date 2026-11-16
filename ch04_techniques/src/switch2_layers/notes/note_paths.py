"""notes/ の中の文書のパス (notes/ からの相対パス、/ 区切り) と、文書の種類・場面との対応。

    strategy.md                    大方針
    advice.md                      詰まったときの助言
    game.md                        ゲームの性質 (場面に依らない。game の deep のジョブが書く)
    common/controls.md             共通の操作説明
    common/tips.md                 共通の攻略情報
    scenes/<場面ID>/controls.md     場面の操作説明
    scenes/<場面ID>/tips.md         場面の攻略情報
    scenes/<場面ID>/tasks.md        場面の短期タスクリスト (共通の文書は無い)
    scenes/<場面ID>/box_targets.md  場面の「box で取るもの」(`- 名前: 説明` の行。場面の攻略情報の deep のジョブが書く)
    scene_labels.json              場面ラベルの一覧 (notes.scene_labels)
    map/                           マップの文書を置くディレクトリー
    map/map.json                   マップの場所とつながりの一覧 (notes.map_document。map の deep のジョブが書く)
    map/places.md                  マップの場所ごとの文章 (map の deep のジョブが書く)

場面 ID は `scene-` と 3 桁以上の連番 (SCENE_ID_PATTERN)。パスに使う前に必ず確かめ、notes/ の外を指せないようにする。
"""

import re
from pathlib import Path
from typing import Literal

from switch2_layers.deep.deep_types import (
    SCENE_DOCUMENT_KINDS,
    DocumentKey,
    DocumentKind,
)

SCENE_ID_PATTERN = re.compile(r"scene-[0-9]{3,}")
STRATEGY_NOTE = "strategy.md"
ADVICE_NOTE = "advice.md"
GAME_NOTE = "game.md"
COMMON_DIR = "common"
SCENES_DIR = "scenes"
MAP_DIR = "map"
# マップの文書。場所とつながりの一覧 (形は notes.map_document) と、場所ごとの文章
MAP_JSON_NOTE = f"{MAP_DIR}/map.json"
MAP_PLACES_NOTE = f"{MAP_DIR}/places.md"
SCENE_LABELS_FILE = "scene_labels.json"
BOX_TARGETS_FILE = "box_targets.md"

# 場面の文書の名前 (拡張子を除く)
SceneNoteName = Literal["controls", "tips", "tasks"]


def is_scene_id(text: str) -> bool:
    """text が場面 ID の形 (SCENE_ID_PATTERN に fullmatch) なら True を返す。"""
    return SCENE_ID_PATTERN.fullmatch(text) is not None


def common_note(kind: DocumentKind) -> str:
    """共通の文書の相対パス common/<種類>.md を返す。kind が controls・tips 以外なら ValueError。"""
    if kind not in SCENE_DOCUMENT_KINDS:
        raise ValueError(f"only controls and tips have common documents: {kind}")
    return f"{COMMON_DIR}/{kind.value}.md"


def scene_note(scene_id: str, name: SceneNoteName) -> str:
    """場面の文書の相対パス scenes/<scene_id>/<name>.md を返す。scene_id が場面 ID の形でなければ ValueError。"""
    if not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id: {scene_id!r}")
    return f"{SCENES_DIR}/{scene_id}/{name}.md"


def box_targets_note(scene_id: str) -> str:
    """場面の「box で取るもの」の文書の相対パス scenes/<scene_id>/box_targets.md を返す。

    scene_id が場面 ID の形でなければ ValueError。
    """
    if not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id: {scene_id!r}")
    return f"{SCENES_DIR}/{scene_id}/{BOX_TARGETS_FILE}"


def document_key(kind: DocumentKind, scene_id: str | None) -> DocumentKey:
    """種類 kind で場面 scene_id のときに対象になる文書のキーを返す。

    - controls・tips: DocumentKey(kind, scene_id) (scene_id が None なら共通の文書)
    - strategy・advice・map・game: DocumentKey(kind, None) (scene_id は無視する)
    controls・tips で scene_id が場面 ID の形でなければ ValueError。
    """
    if kind not in SCENE_DOCUMENT_KINDS:
        return DocumentKey(kind, None)
    if scene_id is not None and not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id: {scene_id!r}")
    return DocumentKey(kind, scene_id)


def deep_document_notes(notes_dir: Path, key: DocumentKey) -> list[str]:
    """deep のジョブの対象の文書の相対パスのリストを返す。

    結果は job.json の documents になり、deep が Edit・Write で書いてよい文書の範囲 (権限のルール) になる。

    - controls で場面あり: [scenes/<場面ID>/controls.md, common/controls.md] (場面の文書と共通の文書の両方)
    - tips で場面あり: [scenes/<場面ID>/tips.md, scenes/<場面ID>/box_targets.md, common/tips.md]
      (場面の攻略情報のジョブは、場面の「box で取るもの」も書く)
    - controls・tips で場面なし: [common/<種類>.md]
    - strategy: [strategy.md]、advice: [advice.md]、game: [GAME_NOTE] (game.md だけ。マップの文書は対象にしない)
    - map: [MAP_JSON_NOTE, MAP_PLACES_NOTE] (map/map.json と map/places.md。notes_dir/map/ の下のほかのファイルは
      対象にしない)
    ファイルがまだ無くてもパスを返す (deep が Write で作る)。notes_dir の中は調べない。
    """
    if key.kind in SCENE_DOCUMENT_KINDS:
        if key.scene_id is None:
            return [common_note(key.kind)]
        if key.kind is DocumentKind.CONTROLS:
            return [scene_note(key.scene_id, "controls"), common_note(key.kind)]
        return [
            scene_note(key.scene_id, "tips"),
            box_targets_note(key.scene_id),
            common_note(key.kind),
        ]
    if key.kind is DocumentKind.STRATEGY:
        return [STRATEGY_NOTE]
    if key.kind is DocumentKind.ADVICE:
        return [ADVICE_NOTE]
    if key.kind is DocumentKind.GAME:
        return [GAME_NOTE]
    return [MAP_JSON_NOTE, MAP_PLACES_NOTE]
