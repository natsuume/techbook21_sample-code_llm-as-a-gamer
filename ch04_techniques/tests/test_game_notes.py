import pytest

from switch2_agent.game_notes import (
    NOTE_KINDS,
    NOTE_TITLES,
    ensure_game_notes,
    format_game_notes,
    game_notes_dir,
    read_game_notes,
)

INVALID_GAME_NAMES = ["", "..", "../outside", "a/b", "zelda game", "ゼルダ", "/abs"]


def test_note_kinds_and_titles():
    assert NOTE_KINDS == ("controls", "tips", "progress", "strategy")
    assert NOTE_TITLES == {
        "controls": "ゲームの操作方法",
        "tips": "攻略情報・Tips",
        "progress": "進行状況",
        "strategy": "戦略",
    }


def test_game_notes_dir_is_under_root(tmp_path):
    assert game_notes_dir(tmp_path, "zelda_2-test") == tmp_path / "zelda_2-test"


@pytest.mark.parametrize("game", INVALID_GAME_NAMES)
def test_game_notes_dir_rejects_invalid_game_name(tmp_path, game):
    with pytest.raises(ValueError):
        game_notes_dir(tmp_path, game)


@pytest.mark.parametrize("game", INVALID_GAME_NAMES)
def test_ensure_game_notes_rejects_invalid_game_name(tmp_path, game):
    with pytest.raises(ValueError):
        ensure_game_notes(tmp_path, game)


def test_ensure_game_notes_creates_four_empty_files(tmp_path):
    root = tmp_path / "game_notes"
    paths = ensure_game_notes(root, "zelda")
    assert set(paths) == set(NOTE_KINDS)
    for kind in NOTE_KINDS:
        assert paths[kind] == root / "zelda" / f"{kind}.md"
        assert paths[kind].read_text(encoding="utf-8") == ""


def test_ensure_game_notes_keeps_existing_files(tmp_path):
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    (notes_dir / "strategy.md").write_text("右へ進む", encoding="utf-8")
    paths = ensure_game_notes(tmp_path, "zelda")
    assert paths["strategy"].read_text(encoding="utf-8") == "右へ進む"
    assert paths["controls"].read_text(encoding="utf-8") == ""


def test_read_game_notes_reads_files(tmp_path):
    paths = ensure_game_notes(tmp_path, "zelda")
    paths["tips"].write_text("敵は左から来る", encoding="utf-8")
    notes = read_game_notes(paths)
    assert notes == {
        "controls": "",
        "tips": "敵は左から来る",
        "progress": "",
        "strategy": "",
    }


def test_read_game_notes_returns_empty_for_missing_file(tmp_path):
    paths = ensure_game_notes(tmp_path, "zelda")
    paths["progress"].unlink()
    assert read_game_notes(paths)["progress"] == ""


def test_read_game_notes_returns_empty_and_warns_for_unreadable_file(tmp_path, capsys):
    paths = dict(ensure_game_notes(tmp_path, "zelda"))
    # ディレクトリーはファイルとして読めない (IsADirectoryError)
    unreadable = tmp_path / "not-a-file.md"
    unreadable.mkdir()
    paths["controls"] = unreadable
    notes = read_game_notes(paths)
    assert notes["controls"] == ""
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_format_game_notes_lists_headings_in_note_kinds_order():
    text = format_game_notes(
        {"strategy": "右へ進む", "progress": "1 面", "tips": "", "controls": "A で決定"}
    )
    assert text == (
        "## ゲームの操作方法\nA で決定\n\n"
        "## 攻略情報・Tips\n(まだ書かれていない)\n\n"
        "## 進行状況\n1 面\n\n"
        "## 戦略\n右へ進む"
    )


def test_format_game_notes_marks_missing_and_blank_notes_as_unwritten():
    text = format_game_notes({"controls": "  \n", "tips": "注意"})
    assert text.count("(まだ書かれていない)") == 3
    assert "## 攻略情報・Tips\n注意" in text
