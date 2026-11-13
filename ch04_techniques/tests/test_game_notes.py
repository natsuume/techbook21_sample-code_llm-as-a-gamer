import errno
from pathlib import Path

import pytest

from switch2_agent.default_notes import (
    DEFAULT_CONTROLS_TEXT,
    DEFAULT_NOTES,
    DEFAULT_STRATEGY_TEXT,
)
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
    assert NOTE_KINDS == ("controls", "tips", "strategy", "advice")
    assert NOTE_TITLES == {
        "controls": "ゲームの操作方法",
        "tips": "攻略情報・Tips",
        "strategy": "目標・進行状況・戦略",
        "advice": "問題の解決の助言",
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


def test_ensure_game_notes_creates_four_files_with_default_notes(tmp_path):
    root = tmp_path / "game_notes"
    paths = ensure_game_notes(root, "zelda")
    assert set(paths) == set(NOTE_KINDS)
    for kind in NOTE_KINDS:
        assert paths[kind] == root / "zelda" / f"{kind}.md"
    # controls と strategy には既定の内容を書き、tips と advice は空にする
    assert paths["controls"].read_text(encoding="utf-8") == DEFAULT_CONTROLS_TEXT
    assert paths["strategy"].read_text(encoding="utf-8") == DEFAULT_STRATEGY_TEXT
    assert paths["tips"].read_text(encoding="utf-8") == ""
    assert paths["advice"].read_text(encoding="utf-8") == ""


def test_ensure_game_notes_uses_default_notes_constant_by_default():
    assert ensure_game_notes.__defaults__ == (DEFAULT_NOTES,)


def test_ensure_game_notes_writes_given_defaults(tmp_path):
    paths = ensure_game_notes(tmp_path, "zelda", defaults={"tips": "敵は左から来る"})
    assert paths["tips"].read_text(encoding="utf-8") == "敵は左から来る"
    assert paths["controls"].read_text(encoding="utf-8") == ""
    assert paths["strategy"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("blank", ["", "  \n\n\t"])
def test_ensure_game_notes_writes_defaults_into_blank_existing_files(tmp_path, blank):
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    for kind in NOTE_KINDS:
        (notes_dir / f"{kind}.md").write_text(blank, encoding="utf-8")
    paths = ensure_game_notes(tmp_path, "zelda")
    assert paths["controls"].read_text(encoding="utf-8") == DEFAULT_CONTROLS_TEXT
    assert paths["strategy"].read_text(encoding="utf-8") == DEFAULT_STRATEGY_TEXT
    # 既定の内容が無い種別は、空白だけのファイルも変えない
    assert paths["tips"].read_text(encoding="utf-8") == blank
    assert paths["advice"].read_text(encoding="utf-8") == blank


def test_ensure_game_notes_warns_and_continues_when_blank_file_cannot_be_written(
    tmp_path, capsys
):
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    controls = notes_dir / "controls.md"
    controls.write_text("", encoding="utf-8")
    controls.chmod(0o444)
    try:
        paths = ensure_game_notes(tmp_path, "zelda")
    finally:
        controls.chmod(0o644)
    assert paths["controls"].read_text(encoding="utf-8") == ""
    # ほかの種別は既定の内容を書く
    assert paths["strategy"].read_text(encoding="utf-8") == DEFAULT_STRATEGY_TEXT
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1
    assert "controls.md" in err[0]


def test_ensure_game_notes_empties_new_file_when_default_notes_fail_partway(
    tmp_path, capsys, monkeypatch
):
    original_write_text = Path.write_text

    def write_text_failing_partway(self, data, *args, **kwargs):
        # 既定の内容は途中まで書いてから、ディスクの空きが無いときの OSError にする。空の書き込みはそのまま通す
        if data:
            original_write_text(self, data[:10], *args, **kwargs)
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text_failing_partway)
    paths = ensure_game_notes(tmp_path, "zelda")
    # 新しく作ったファイルも起動を止めず、途中まで書けた中身は、次の起動でもう一度書けるよう空に戻す
    assert paths["controls"].read_text(encoding="utf-8") == ""
    assert paths["strategy"].read_text(encoding="utf-8") == ""
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 2
    assert "controls.md" in err[0]
    assert "strategy.md" in err[1]


def test_ensure_game_notes_keeps_unreadable_existing_file(tmp_path):
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    # UTF-8 として読めない中身 (UnicodeDecodeError)
    (notes_dir / "controls.md").write_bytes(b"\xff\xfe\xfd")
    paths = ensure_game_notes(tmp_path, "zelda")
    assert paths["controls"].read_bytes() == b"\xff\xfe\xfd"


def test_ensure_game_notes_does_not_create_progress_file(tmp_path):
    root = tmp_path / "game_notes"
    paths = ensure_game_notes(root, "zelda")
    assert "progress" not in paths
    assert not (root / "zelda" / "progress.md").exists()
    assert sorted(path.name for path in (root / "zelda").iterdir()) == [
        "advice.md",
        "controls.md",
        "strategy.md",
        "tips.md",
    ]


def test_ensure_game_notes_keeps_progress_file_of_earlier_run(tmp_path):
    # 以前の実行で作られた progress.md は、変えず、返す辞書にも入れない (読まない)
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    (notes_dir / "progress.md").write_text("1 面の途中", encoding="utf-8")
    paths = ensure_game_notes(tmp_path, "zelda")
    assert (notes_dir / "progress.md").read_text(encoding="utf-8") == "1 面の途中"
    assert "progress" not in paths
    assert "1 面の途中" not in read_game_notes(paths).values()


def test_ensure_game_notes_keeps_existing_files(tmp_path):
    notes_dir = tmp_path / "zelda"
    notes_dir.mkdir()
    (notes_dir / "strategy.md").write_text("右へ進む", encoding="utf-8")
    paths = ensure_game_notes(tmp_path, "zelda")
    # 中身のある strategy.md は既定の内容で書き直さない
    assert paths["strategy"].read_text(encoding="utf-8") == "右へ進む"
    assert paths["controls"].read_text(encoding="utf-8") == DEFAULT_CONTROLS_TEXT


def test_read_game_notes_reads_files(tmp_path):
    paths = ensure_game_notes(tmp_path, "zelda", defaults={})
    paths["tips"].write_text("敵は左から来る", encoding="utf-8")
    notes = read_game_notes(paths)
    assert notes == {
        "controls": "",
        "tips": "敵は左から来る",
        "strategy": "",
        "advice": "",
    }


def test_read_game_notes_returns_empty_for_missing_file(tmp_path):
    paths = ensure_game_notes(tmp_path, "zelda")
    paths["advice"].unlink()
    assert read_game_notes(paths)["advice"] == ""


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
        {
            "advice": "扉の前で A",
            "strategy": "右へ進む",
            "tips": "",
            "controls": "A で決定",
        }
    )
    assert text == (
        "## ゲームの操作方法\nA で決定\n\n"
        "## 攻略情報・Tips\n(まだ書かれていない)\n\n"
        "## 目標・進行状況・戦略\n右へ進む\n\n"
        "## 問題の解決の助言\n扉の前で A"
    )


def test_format_game_notes_marks_missing_and_blank_notes_as_unwritten():
    text = format_game_notes({"controls": "  \n", "tips": "注意"})
    assert text.count("(まだ書かれていない)") == 3
    assert "## 攻略情報・Tips\n注意" in text
