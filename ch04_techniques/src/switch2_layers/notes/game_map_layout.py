"""ゲームの性質の文書 (notes/game.md) の「マップの形式」の節から、マップの型 (MapLayout) を読む。

文書の形は deep の game の種類のジョブの指示 (deep/prompts/game.md) のとおりで、「マップの形式」の節の最初の行に
`- 型: マス目型`・`- 型: 自由型`・`- 型: 未確認` のどれかを書く。読み方 (parse_game_map_layout):
    - 見出しの行 GAME_MAP_FORMAT_HEADING (`## マップの形式`) の後で、次の `## ` で始まる見出しの行より前にある、
      最初の空でない (前後の空白を除いても空でない) 行を見る
    - その行が、前後の空白を除いて GAME_GRID_LAYOUT_LINE (`- 型: マス目型`) と一致すれば MapLayout.GRID
    - それ以外 (行が `- 型: 自由型`・`- 型: 未確認` などほかの文、見出しの行や節の中の行が無い) は MapLayout.FREE
deep の map のジョブは、この型と notes/map/map.json の layout を比べて、型を書き換えるかを決める
(deep.deep_job_directory.find_previous_latest_frame_t)。
"""

from pathlib import Path

from switch2_layers.notes.map_document import MapLayout
from switch2_layers.notes.note_paths import GAME_NOTE
from switch2_layers.notes.note_reader import read_note_text

# 「マップの形式」の節の見出しの行
GAME_MAP_FORMAT_HEADING = "## マップの形式"
# 「マップの形式」の節の最初の行のうち、マス目型 (grid) を表す行
GAME_GRID_LAYOUT_LINE = "- 型: マス目型"


def parse_game_map_layout(text: str) -> MapLayout:
    """ゲームの性質の文書の本文 text から、マップの型を読む (読み方はモジュールの docstring)。I/O を扱わない。

    見出しの行が複数あれば、最初のものを使う。空文字列は MapLayout.FREE。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != GAME_MAP_FORMAT_HEADING:
            continue
        for section_line in lines[index + 1 :]:
            if section_line.startswith("## "):
                # 節の中に空でない行が無いまま、次の見出しになった
                return MapLayout.FREE
            if section_line.strip():
                if section_line.strip() == GAME_GRID_LAYOUT_LINE:
                    return MapLayout.GRID
                return MapLayout.FREE
        return MapLayout.FREE
    return MapLayout.FREE


def read_game_map_layout(notes_dir: Path) -> MapLayout:
    """notes_dir のゲームの性質の文書 (note_paths.GAME_NOTE) を読み、parse_game_map_layout の結果を返す。

    文書は notes.note_reader.read_note_text で読む。読めない文書 (無い・シンボリックリンクを解決した先が notes/ の中の
    通常のファイルでない・OSError・UTF-8 として読めない) は空文字列になり、MapLayout.FREE を返す。例外は出さない。
    """
    return parse_game_map_layout(read_note_text(notes_dir, GAME_NOTE))
