"""box で取る対象の一覧 (box_targets.md) の読み込み。

対象の一覧は、deep が場面ごとに notes/scenes/<場面ID>/box_targets.md に書く。読む順は次のとおり:
    1. 場面が決まっていれば notes/scenes/<場面ID>/box_targets.md
    2. 1 が無いか空なら notes/common/box_targets.md (起動時に DEFAULT_BOX_TARGETS で作る)
    3. 2 も無いか空なら DEFAULT_BOX_TARGETS (固定の 4 つ)
「空」は、書式に合う行が 1 つも無いこと (空のファイル、見出しだけのファイルを含む)。ファイルは
notes.note_reader.read_note_text で読み、読めないファイル (無い・notes/ の外を指すシンボリックリンク・OSError・
UnicodeDecodeError) は空として扱う。

書式は 1 行に 1 つの `- 名前: 説明` (BOX_TARGET_LINE_PATTERN)。行頭は `- `、区切りは半角の `:`。
名前と説明は前後の空白を除き、どちらも空でない行だけを使う。書式に合わない行 (見出し、空行、字下げした行、
区切りの無い行など) は無視する。同じ名前の 2 つ目以降の行も無視する。先頭から最大 MAX_BOX_TARGETS 個を使う。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.notes.note_paths import COMMON_DIR, SCENES_DIR, is_scene_id
from switch2_layers.notes.note_reader import read_note_text

BOX_TARGETS_FILE = "box_targets.md"
MAX_BOX_TARGETS = 8  # 1 回の要求で box を取る対象の最大の数
BOX_TARGETS_HEADING = "# box で取るもの"
# `- 名前: 説明` の行。名前は `:` を含まない
BOX_TARGET_LINE_PATTERN = re.compile(r"- (?P<name>[^:]*):(?P<description>.*)")


@dataclass(frozen=True)
class BoxTarget:
    """box で取る対象 1 つ。

    name          対象の名前 (box のラベル。surface.jsonl の boxes の label と、画像に書き込む名前)
    description   対象の説明 (Jev の問いの文に入れる)
    """

    name: str
    description: str


# box_targets.md が無いか空のときに使う、固定の 4 つ
DEFAULT_BOX_TARGETS: tuple[BoxTarget, ...] = (
    BoxTarget("自機", "プレイヤーが操作しているキャラクター、乗り物、またはカーソル"),
    BoxTarget("敵", "プレイヤーに害を与えるキャラクター。複数いれば自機に最も近いもの"),
    BoxTarget("敵の攻撃", "敵が放った弾や攻撃。複数あれば自機に最も近いもの"),
    BoxTarget("目標地点", "今のタスクで向かう場所、または次に操作する対象"),
)


def common_box_targets_note() -> str:
    """共通の対象の一覧の notes/ からの相対パス common/box_targets.md を返す (notes.note_paths.COMMON_DIR の下)。"""
    return f"{COMMON_DIR}/{BOX_TARGETS_FILE}"


def scene_box_targets_note(scene_id: str) -> str:
    """場面の対象の一覧の notes/ からの相対パス scenes/<scene_id>/box_targets.md を返す (notes.note_paths.SCENES_DIR の下)。

    scene_id が場面 ID の形 (notes.note_paths.is_scene_id) でなければ ValueError。
    """
    if not is_scene_id(scene_id):
        raise ValueError(f"invalid scene id: {scene_id!r}")
    return f"{SCENES_DIR}/{scene_id}/{BOX_TARGETS_FILE}"


def initial_box_targets_text() -> str:
    """起動時の notes/common/box_targets.md の中身を返す。

    1 行目が BOX_TARGETS_HEADING、空行の後に、DEFAULT_BOX_TARGETS を順に 1 行ずつ `- <名前>: <説明>` で並べる。
    末尾は改行。parse_box_targets で読み直すと DEFAULT_BOX_TARGETS になる。
    """
    lines = [BOX_TARGETS_HEADING, ""]
    lines += [
        f"- {target.name}: {target.description}" for target in DEFAULT_BOX_TARGETS
    ]
    return "\n".join(lines) + "\n"


def parse_box_targets(text: str) -> tuple[BoxTarget, ...]:
    """box_targets.md の中身 text から、対象を書かれた順に返す (モジュールの docstring の書式)。

    各行は末尾の改行と空白を除いてから BOX_TARGET_LINE_PATTERN に fullmatch させる。名前と説明は前後の空白を除き、
    どちらかが空の行、同じ名前の 2 つ目以降の行は無視する。MAX_BOX_TARGETS 個に達したら残りの行は読まない。
    書式に合う行が無ければ空のタプルを返す。
    """
    targets: list[BoxTarget] = []
    seen_names: set[str] = set()
    for line in text.splitlines():
        if len(targets) >= MAX_BOX_TARGETS:
            break
        match = BOX_TARGET_LINE_PATTERN.fullmatch(line.rstrip())
        if match is None:
            continue
        name = match.group("name").strip()
        description = match.group("description").strip()
        if not name or not description or name in seen_names:
            continue
        seen_names.add(name)
        targets.append(BoxTarget(name, description))
    return tuple(targets)


def read_box_targets(notes_dir: Path, scene_id: str | None) -> tuple[BoxTarget, ...]:
    """notes_dir から、現在の場面 scene_id の対象の一覧を読む (モジュールの docstring の順)。

    scene_id が None、または場面 ID の形でなければ、場面が決まっていないものとして 1 を飛ばす。
    戻り値は 1〜MAX_BOX_TARGETS 個 (どのファイルも空なら DEFAULT_BOX_TARGETS)。
    """
    if scene_id is not None and is_scene_id(scene_id):
        scene_targets = _read_targets_note(notes_dir, scene_box_targets_note(scene_id))
        if scene_targets:
            return scene_targets
    common_targets = _read_targets_note(notes_dir, common_box_targets_note())
    if common_targets:
        return common_targets
    return DEFAULT_BOX_TARGETS


def _read_targets_note(notes_dir: Path, note: str) -> tuple[BoxTarget, ...]:
    """文書 note (notes/ からの相対パス) の対象の一覧を読む。読めない文書は空のタプルにする (note_reader.read_note_text)。"""
    return parse_box_targets(read_note_text(notes_dir, note))
