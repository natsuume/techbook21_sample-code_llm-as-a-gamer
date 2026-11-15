"""surface が 1 回の要求ごとに読む文書の選び方。

- 操作説明 (controls) と攻略情報 (tips): 場面が決まっていて、場面の文書があって空でなければ場面の文書。
  場面が決まっていない、または場面の文書が無いか空 (空白だけを含む) なら、notes/common/ の同じ種類の文書
- 短期タスクリスト (tasks): 場面の文書 scenes/<場面ID>/tasks.md。共通の文書は無く、場面が無いか文書が無ければ空文字列
- 助言 (advice): notes/advice.md
読めない文書 (無い・OSError・UnicodeDecodeError) は空文字列として扱う。
"""

from pathlib import Path

from switch2_layers.surface.surface_types import SurfaceDocuments


def is_blank(text: str) -> bool:
    """text が空か空白だけなら True を返す。"""
    raise NotImplementedError


def read_surface_documents(notes_dir: Path, scene_id: str | None) -> SurfaceDocuments:
    """notes_dir の文書を、現在の場面 scene_id に合わせて読む (モジュールの docstring の規則)。

    scene_id が None、または場面 ID の形でない (notes.note_paths.is_scene_id が False) なら、場面が決まっていないものとして
    扱う (SurfaceDocuments.scene_id も None にする)。uses_common_controls と uses_common_tips は、それぞれ共通の文書を
    使ったら True (共通の文書が無くて空文字列になった場合も True)。
    """
    raise NotImplementedError
