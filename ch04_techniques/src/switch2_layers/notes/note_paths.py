"""notes/ の中の文書のパス (notes/ からの相対パス、/ 区切り) と、文書の種類・場面との対応。

    strategy.md                    大方針
    advice.md                      詰まったときの助言
    common/controls.md             共通の操作説明
    common/tips.md                 共通の攻略情報
    scenes/<場面ID>/controls.md     場面の操作説明
    scenes/<場面ID>/tips.md         場面の攻略情報
    scenes/<場面ID>/tasks.md        場面の短期タスクリスト (共通の文書は無い)
    scenes/<場面ID>/box_targets.md  場面の「box で取るもの」(`- 名前: 説明` の行。場面の攻略情報の deep のジョブが書く)
    scene_labels.json              場面ラベルの一覧 (notes.scene_labels)
    map/                           マップの文書を置くディレクトリー
    .publish.lock                  publish-note のファイルロック

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
from switch2_layers.file_io import is_temporary_file_name

SCENE_ID_PATTERN = re.compile(r"scene-[0-9]{3,}")
STRATEGY_NOTE = "strategy.md"
ADVICE_NOTE = "advice.md"
COMMON_DIR = "common"
SCENES_DIR = "scenes"
MAP_DIR = "map"
SCENE_LABELS_FILE = "scene_labels.json"
PUBLISH_LOCK_FILE = ".publish.lock"
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
    raise NotImplementedError


def document_key(kind: DocumentKind, scene_id: str | None) -> DocumentKey:
    """種類 kind で場面 scene_id のときに対象になる文書のキーを返す。

    - controls・tips: DocumentKey(kind, scene_id) (scene_id が None なら共通の文書)
    - strategy・advice・map: DocumentKey(kind, None) (scene_id は無視する)
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
    - strategy: [strategy.md]、advice: [advice.md]
    - map: notes_dir/map/ の下の通常のファイルの相対パス (map/... の形、文字列の順)。無ければ空のリスト。
      次のものは除く:
        - パスの要素のどれかが `.` で始まるもの (隠しファイルと隠しディレクトリーの下)
        - 名前が置き換えの一時ファイルの規則 (file_io.is_temporary_file_name) に当たるもの
          (置き換えの書き込みの途中や、kill されたジョブが残したファイルを文書として扱わないため)
        - notes.note_reader.resolve_note_file が None のもの (シンボリックリンクを解決した先が notes/ の中の
          通常のファイルでないもの。notes/ の外のファイルを文書として列挙しない)
    map 以外は、ファイルがまだ無くてもパスを返す (deep が Write で作る)。
    """
    if key.kind in SCENE_DOCUMENT_KINDS:
        notes = [common_note(key.kind)]
        if key.scene_id is not None:
            name: SceneNoteName = (
                "controls" if key.kind is DocumentKind.CONTROLS else "tips"
            )
            notes.insert(0, scene_note(key.scene_id, name))
        return notes
    if key.kind is DocumentKind.STRATEGY:
        return [STRATEGY_NOTE]
    if key.kind is DocumentKind.ADVICE:
        return [ADVICE_NOTE]
    return _map_documents(notes_dir)


def _map_documents(notes_dir: Path) -> list[str]:
    """notes_dir/map/ の下の通常のファイルの相対パス (map/... の形、文字列の順) を返す。

    パスの要素のどれかが `.` で始まるものと、置き換えの一時ファイルの名前のものは除く。map/ が無ければ空のリスト。
    """
    map_dir = notes_dir / MAP_DIR
    if not map_dir.is_dir():
        return []
    notes: list[str] = []
    for path in map_dir.rglob("*"):
        relative = path.relative_to(map_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if is_temporary_file_name(path.name) or not path.is_file():
            continue
        notes.append(f"{MAP_DIR}/{relative.as_posix()}")
    return sorted(notes)
