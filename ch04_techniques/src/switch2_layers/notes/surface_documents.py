"""surface が 1 回の要求ごとに読む文書の選び方。

- 操作説明 (controls) と攻略情報 (tips): 場面が決まっていて、場面の文書があって空でなければ場面の文書。
  場面が決まっていない、または場面の文書が無いか空 (空白だけを含む) なら、notes/common/ の同じ種類の文書
- 今実行中のタスク (tasks と task_id): 場面のキューの状態 scenes/<場面ID>/tasks.json の tasks の最初の項目
  (キューの先頭のタスク) の text と id。共通の文書は無く、場面が無いか、tasks.json が読めない (無い・notes/ の外を
  指す・UTF-8 でない) か、tasks が空なら空文字列と None
- 助言 (advice): notes/advice.md
- ゲームの性質 (game): notes/game.md (場面に依らない。場面が決まっていても、いなくても同じ文書)
文書と tasks.json は notes.note_reader.read_note_text で読む。読めない文書 (無い・シンボリックリンクを解決した先が
notes/ の中の通常のファイルでない・OSError・UnicodeDecodeError) は空文字列として扱う (場面の文書なら、無いものとして
共通の文書を読む。tasks.json なら、先頭のタスクが無いものとして扱う)。
"""

import json
from pathlib import Path
from typing import Literal

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.notes.note_paths import (
    ADVICE_NOTE,
    GAME_NOTE,
    common_note,
    is_scene_id,
    scene_note,
    tasks_json_note,
)
from switch2_layers.notes.note_reader import read_note_text
from switch2_layers.surface.surface_types import SurfaceDocuments


def is_blank(text: str) -> bool:
    """text が空か空白だけなら True を返す。"""
    return not text.strip()


def read_surface_documents(notes_dir: Path, scene_id: str | None) -> SurfaceDocuments:
    """notes_dir の文書を、現在の場面 scene_id に合わせて読む (モジュールの docstring の規則)。

    scene_id が None、または場面 ID の形でない (notes.note_paths.is_scene_id が False) なら、場面が決まっていないものとして
    扱う (SurfaceDocuments.scene_id も None にする)。uses_common_controls と uses_common_tips は、それぞれ共通の文書を
    使ったら True (共通の文書が無くて空文字列になった場合も True)。
    tasks と task_id は、場面が決まっていれば、その場面の tasks.json を
    notes.note_reader.read_note_text(notes_dir, notes.note_paths.tasks_json_note(scene_id)) で読み、tasks の最初の項目の
    text と id にする (_read_head_task)。場面が決まっていないか、読んだ文字列が空 (無い・notes/ の外を指す・UTF-8 で
    ない) か、tasks が空なら、空文字列と None。
    advice と game は読んだまま (前後の空白も除かない) にし、読めなければ空文字列にする。
    """
    if scene_id is not None and not is_scene_id(scene_id):
        scene_id = None
    controls, uses_common_controls = _read_scene_or_common(
        notes_dir, scene_id, DocumentKind.CONTROLS
    )
    tips, uses_common_tips = _read_scene_or_common(
        notes_dir, scene_id, DocumentKind.TIPS
    )
    tasks, task_id = "", None
    if scene_id is not None:
        tasks, task_id = _read_head_task(notes_dir, scene_id)
    return SurfaceDocuments(
        tasks=tasks,
        task_id=task_id,
        tips=tips,
        controls=controls,
        advice=read_note_text(notes_dir, ADVICE_NOTE),
        scene_id=scene_id,
        uses_common_controls=uses_common_controls,
        uses_common_tips=uses_common_tips,
        game=read_note_text(notes_dir, GAME_NOTE),
    )


def _read_head_task(notes_dir: Path, scene_id: str) -> tuple[str, str | None]:
    """場面 scene_id の tasks.json の tasks の最初の項目の (text, id) を返す。

    tasks.json は notes.note_reader.read_note_text(notes_dir, notes.note_paths.tasks_json_note(scene_id)) で読む
    (シンボリックリンクを解決した先が notes/ の外なら読まない)。読んだ文字列が空 (無い・notes/ の外を指す・UTF-8 で
    ない) か、tasks が空なら ("", None)。
    """
    text = read_note_text(notes_dir, tasks_json_note(scene_id))
    if not text:
        return "", None
    tasks = json.loads(text)["tasks"]
    if not tasks:
        return "", None
    head = tasks[0]
    return head["text"], head["id"]


def _read_scene_or_common(
    notes_dir: Path,
    scene_id: str | None,
    kind: Literal[DocumentKind.CONTROLS, DocumentKind.TIPS],
) -> tuple[str, bool]:
    """種類 kind の場面の文書を読み、無いか空なら共通の文書を読む。(文書, 共通の文書を使ったら True) を返す。"""
    if scene_id is not None:
        name = "controls" if kind is DocumentKind.CONTROLS else "tips"
        text = read_note_text(notes_dir, scene_note(scene_id, name))
        if not is_blank(text):
            return text, False
    return read_note_text(notes_dir, common_note(kind)), True
