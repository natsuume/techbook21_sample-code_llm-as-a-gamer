"""surface が 1 回の要求ごとに読む文書の選び方。

- 操作説明 (controls) と攻略情報 (tips): 場面が決まっていて、場面の文書があって空でなければ場面の文書。
  場面が決まっていない、または場面の文書が無いか空 (空白だけを含む) なら、notes/common/ の同じ種類の文書
- 短期タスクリスト (tasks): 場面の文書 scenes/<場面ID>/tasks.md。共通の文書は無く、場面が無いか文書が無ければ空文字列
- 助言 (advice): notes/advice.md
文書は notes.note_reader.read_note_text で読む。読めない文書 (無い・シンボリックリンクを解決した先が notes/ の中の
通常のファイルでない・OSError・UnicodeDecodeError) は空文字列として扱う (場面の文書なら、無いものとして共通の文書を読む)。
"""

from pathlib import Path
from typing import Literal

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.file_io import read_text_or_empty
from switch2_layers.notes.note_paths import (
    ADVICE_NOTE,
    common_note,
    is_scene_id,
    scene_note,
)
from switch2_layers.surface.surface_types import SurfaceDocuments


def is_blank(text: str) -> bool:
    """text が空か空白だけなら True を返す。"""
    return not text.strip()


def read_surface_documents(notes_dir: Path, scene_id: str | None) -> SurfaceDocuments:
    """notes_dir の文書を、現在の場面 scene_id に合わせて読む (モジュールの docstring の規則)。

    scene_id が None、または場面 ID の形でない (notes.note_paths.is_scene_id が False) なら、場面が決まっていないものとして
    扱う (SurfaceDocuments.scene_id も None にする)。uses_common_controls と uses_common_tips は、それぞれ共通の文書を
    使ったら True (共通の文書が無くて空文字列になった場合も True)。
    """
    if scene_id is not None and not is_scene_id(scene_id):
        scene_id = None
    controls, uses_common_controls = _read_scene_or_common(
        notes_dir, scene_id, DocumentKind.CONTROLS
    )
    tips, uses_common_tips = _read_scene_or_common(
        notes_dir, scene_id, DocumentKind.TIPS
    )
    tasks = ""
    if scene_id is not None:
        tasks = read_text_or_empty(notes_dir / scene_note(scene_id, "tasks"))
    return SurfaceDocuments(
        tasks=tasks,
        tips=tips,
        controls=controls,
        advice=read_text_or_empty(notes_dir / ADVICE_NOTE),
        scene_id=scene_id,
        uses_common_controls=uses_common_controls,
        uses_common_tips=uses_common_tips,
    )


def _read_scene_or_common(
    notes_dir: Path,
    scene_id: str | None,
    kind: Literal[DocumentKind.CONTROLS, DocumentKind.TIPS],
) -> tuple[str, bool]:
    """種類 kind の場面の文書を読み、無いか空なら共通の文書を読む。(文書, 共通の文書を使ったら True) を返す。"""
    if scene_id is not None:
        name = "controls" if kind is DocumentKind.CONTROLS else "tips"
        text = read_text_or_empty(notes_dir / scene_note(scene_id, name))
        if not is_blank(text):
            return text, False
    return read_text_or_empty(notes_dir / common_note(kind)), True
