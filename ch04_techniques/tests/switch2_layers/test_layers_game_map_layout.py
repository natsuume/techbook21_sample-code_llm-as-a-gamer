from pathlib import Path

import pytest

from switch2_layers.notes.game_map_layout import (
    GAME_GRID_LAYOUT_LINE,
    GAME_MAP_FORMAT_HEADING,
    parse_game_map_layout,
    read_game_map_layout,
)
from switch2_layers.notes.map_document import MapLayout


def lines_of(lines: list[str]) -> str:
    """行を改行 1 つでつないだ文を返す。"""
    return "\n".join(lines)


def game_document(map_format_lines: list[str]) -> str:
    """deep/prompts/game.md の形のゲームの性質の文書を、「マップの形式」の節の本文の行を差し替えて作る。"""
    return "\n".join(
        [
            "# ゲームの性質",
            "",
            "## ジャンル",
            "- RPG (推測)",
            "",
            "## リアルタイムに進む部分",
            "- 未確認",
            "",
            "## ターン制の部分",
            "- 未確認",
            "",
            "## マップの形式",
            *map_format_lines,
            "",
            "## ダメージとゲームオーバーの条件",
            "- 未確認",
            "",
            "## 進めるのに要るそのほかの根本の情報",
            "- 未確認",
            "",
        ]
    )


GRID_GAME = game_document(
    ["- 型: マス目型", "- 2D で真上から見る", "- 1 マスは画面の幅の約 1/15"]
)


def make_notes(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    return notes


def test_heading_and_grid_line():
    assert GAME_MAP_FORMAT_HEADING == "## マップの形式"
    assert GAME_GRID_LAYOUT_LINE == "- 型: マス目型"


# --- 本文の読み方 ---


def test_grid_line_after_the_heading_is_grid():
    assert parse_game_map_layout(GRID_GAME) is MapLayout.GRID


@pytest.mark.parametrize(
    "first_line",
    ["- 型: 自由型", "- 型: 未確認", "- 2D で真上から見る", "型: マス目型"],
)
def test_other_first_lines_are_free(first_line):
    text = game_document([first_line, "- 型: マス目型"])
    assert parse_game_map_layout(text) is MapLayout.FREE


def test_first_non_blank_line_is_used():
    # 見出しの直後の空の行と空白だけの行は飛ばし、最初の空でない行を見る
    assert (
        parse_game_map_layout(game_document(["", "   ", "- 型: マス目型"]))
        is MapLayout.GRID
    )


def test_grid_line_is_compared_without_surrounding_spaces():
    assert (
        parse_game_map_layout(game_document(["  - 型: マス目型  "])) is MapLayout.GRID
    )


def test_grid_line_after_the_next_heading_is_not_read():
    # 節の本文が空なら、次の ## の見出しより後の行は見ない
    text = lines_of(
        [
            "# ゲームの性質",
            "## マップの形式",
            "",
            "## ダメージとゲームオーバーの条件",
            "- 型: マス目型",
        ]
    )
    assert parse_game_map_layout(text) is MapLayout.FREE


def test_grid_line_outside_the_section_is_not_read():
    # 「マップの形式」の節より前に同じ行があっても、節の最初の行でなければ grid にしない
    text = lines_of(
        [
            "# ゲームの性質",
            "## ジャンル",
            "- 型: マス目型",
            "## マップの形式",
            "- 型: 自由型",
        ]
    )
    assert parse_game_map_layout(text) is MapLayout.FREE


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# ゲームの性質\n\n## ジャンル\n- 型: マス目型\n",
        "# ゲームの性質\n\n## マップの形式\n",
        "# ゲームの性質\n\n## マップの形式",
    ],
    ids=["empty", "no-section", "empty-section-at-the-end", "heading-only"],
)
def test_missing_section_or_line_is_free(text):
    assert parse_game_map_layout(text) is MapLayout.FREE


def test_first_heading_is_used():
    text = lines_of(
        [
            "## マップの形式",
            "- 型: 自由型",
            "## マップの形式",
            "- 型: マス目型",
        ]
    )
    assert parse_game_map_layout(text) is MapLayout.FREE


# --- 文書の読み込み ---


def test_read_grid_layout_from_game_md(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game.md").write_text(GRID_GAME, encoding="utf-8")
    assert read_game_map_layout(notes) is MapLayout.GRID


def test_read_free_layout_from_game_md(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game.md").write_text(game_document(["- 型: 自由型"]), encoding="utf-8")
    assert read_game_map_layout(notes) is MapLayout.FREE


def test_missing_game_md_is_free(tmp_path):
    assert read_game_map_layout(make_notes(tmp_path)) is MapLayout.FREE


def test_game_md_that_is_not_utf8_is_free(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game.md").write_bytes(GRID_GAME.encode("utf-8") + b"\xff\xfe")
    assert read_game_map_layout(notes) is MapLayout.FREE


def test_game_md_that_is_a_directory_is_free(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game.md").mkdir()
    assert read_game_map_layout(notes) is MapLayout.FREE


def test_game_md_symlinked_to_outside_of_notes_is_free(tmp_path):
    # notes/ の外のファイルは、マス目型と書いてあっても読まない
    notes = make_notes(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(GRID_GAME, encoding="utf-8")
    (notes / "game.md").symlink_to(outside)
    assert read_game_map_layout(notes) is MapLayout.FREE


def test_game_md_symlinked_inside_notes_is_read(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "game-real.md").write_text(GRID_GAME, encoding="utf-8")
    (notes / "game.md").symlink_to(notes / "game-real.md")
    assert read_game_map_layout(notes) is MapLayout.GRID
