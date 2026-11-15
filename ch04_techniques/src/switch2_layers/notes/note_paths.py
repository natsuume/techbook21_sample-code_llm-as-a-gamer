"""notes/ の中の文書のパス (notes/ からの相対パス、/ 区切り) と、文書の種類・場面との対応。

    strategy.md                    大方針
    advice.md                      詰まったときの助言
    common/controls.md             共通の操作説明
    common/tips.md                 共通の攻略情報
    scenes/<場面ID>/controls.md     場面の操作説明
    scenes/<場面ID>/tips.md         場面の攻略情報
    scenes/<場面ID>/tasks.md        場面の短期タスクリスト (共通の文書は無い)
    scene_labels.json              場面ラベルの一覧 (notes.scene_labels)
    map/                           マップの文書を置くディレクトリー
    .publish.lock                  publish-note のファイルロック

場面 ID は `scene-` と 3 桁以上の連番 (SCENE_ID_PATTERN)。パスに使う前に必ず確かめ、notes/ の外を指せないようにする。
"""

import re
from pathlib import Path
from typing import Literal

from switch2_layers.deep.deep_types import DocumentKey, DocumentKind

SCENE_ID_PATTERN = re.compile(r"scene-[0-9]{3,}")
STRATEGY_NOTE = "strategy.md"
ADVICE_NOTE = "advice.md"
COMMON_DIR = "common"
SCENES_DIR = "scenes"
MAP_DIR = "map"
SCENE_LABELS_FILE = "scene_labels.json"
PUBLISH_LOCK_FILE = ".publish.lock"

# 場面の文書の名前 (拡張子を除く)
SceneNoteName = Literal["controls", "tips", "tasks"]


def is_scene_id(text: str) -> bool:
    """text が場面 ID の形 (SCENE_ID_PATTERN に fullmatch) なら True を返す。"""
    raise NotImplementedError


def common_note(kind: DocumentKind) -> str:
    """共通の文書の相対パス common/<種類>.md を返す。kind が controls・tips 以外なら ValueError。"""
    raise NotImplementedError


def scene_note(scene_id: str, name: SceneNoteName) -> str:
    """場面の文書の相対パス scenes/<scene_id>/<name>.md を返す。scene_id が場面 ID の形でなければ ValueError。"""
    raise NotImplementedError


def document_key(kind: DocumentKind, scene_id: str | None) -> DocumentKey:
    """種類 kind で場面 scene_id のときに対象になる文書のキーを返す。

    - controls・tips: DocumentKey(kind, scene_id) (scene_id が None なら共通の文書)
    - strategy・advice・map: DocumentKey(kind, None) (scene_id は無視する)
    controls・tips で scene_id が場面 ID の形でなければ ValueError。
    """
    raise NotImplementedError


def deep_document_notes(notes_dir: Path, key: DocumentKey) -> list[str]:
    """deep のジョブの対象の文書の相対パスのリストを返す (job.json の documents と work/ のコピーに使う)。

    - controls・tips で場面あり: [scenes/<場面ID>/<種類>.md, common/<種類>.md] (場面の文書と共通の文書の両方)
    - controls・tips で場面なし: [common/<種類>.md]
    - strategy: [strategy.md]、advice: [advice.md]
    - map: notes_dir/map/ の下の通常のファイルすべての相対パス (map/... の形、文字列の順)。無ければ空のリスト
    map 以外は、ファイルがまだ無くてもパスを返す。
    """
    raise NotImplementedError
