from pathlib import Path

import pytest

from switch2_layers.notes.system_screen_advice import (
    SYSTEM_SCREEN_ADVICE_HEADING,
    advice_for_phase,
    parse_system_screen_advice,
    read_system_screen_advice,
)

SYSTEM_LINES = [
    "- 段階: prepare (t=123.400)",
    "- 今の画面: HOME メニュー。カーソルは上の段にある",
    "### 次に試す操作",
    "- 上の段に指定のソフトが無いので、「もっとみる」を開く",
]
SYSTEM_BODY = "\n".join(SYSTEM_LINES)


def advice_document(system_lines: list[str] | None, last: bool = False) -> str:
    """deep/prompts/advice.md の形の助言の文書を作る。system_lines が None なら「本体の画面」の節を置かない。

    last が True なら「本体の画面」の節を文書の最後に置き、False なら別の節の間に置く。
    """
    before = [
        "# 詰まったときの助言",
        "",
        "## 詰まりの原因",
        "- 壁に向かって進み続けている",
        "",
    ]
    after = ["## 試すこと", "- 右の出口を確かめる", ""]
    section = [] if system_lines is None else ["## 本体の画面", *system_lines, ""]
    if last:
        return "\n".join([*before, *after, *section])
    return "\n".join([*before, *section, *after])


def make_notes(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    return notes


# --- 節の本文の取り出し (I/O を扱わない) ---


def test_parse_returns_the_body_up_to_the_next_heading():
    assert SYSTEM_SCREEN_ADVICE_HEADING == "## 本体の画面"
    # 見出しの次の行から、次の「## 」の見出しの前まで。「### 」の小見出しは本文に含める
    text = advice_document(["", *SYSTEM_LINES, "", ""])
    assert parse_system_screen_advice(text) == SYSTEM_BODY


def test_parse_returns_the_body_up_to_the_end_of_the_document():
    text = advice_document(SYSTEM_LINES, last=True)
    assert parse_system_screen_advice(text) == SYSTEM_BODY


@pytest.mark.parametrize(
    "text",
    [
        "",
        advice_document(None),
        # 節が空 (空白だけの行を含む)
        advice_document(["", "   ", ""]),
        advice_document([], last=True),
    ],
    ids=["empty", "no-section", "blank-section", "empty-last"],
)
def test_parse_without_the_section_or_body_returns_empty(text):
    assert parse_system_screen_advice(text) == ""


# --- 文書の読み込み ---


def test_read_returns_the_section_of_the_advice_note(tmp_path):
    notes = make_notes(tmp_path)
    (notes / "advice.md").write_text(advice_document(SYSTEM_LINES), encoding="utf-8")
    assert read_system_screen_advice(notes) == SYSTEM_BODY


def test_read_without_the_advice_note_returns_empty(tmp_path):
    notes = make_notes(tmp_path)
    assert read_system_screen_advice(notes) == ""
    # 文書があれば読む (無い文書と、読める文書を見分ける)
    (notes / "advice.md").write_text(advice_document(SYSTEM_LINES), encoding="utf-8")
    assert read_system_screen_advice(notes) == SYSTEM_BODY


def test_read_treats_a_symlink_outside_notes_as_missing(tmp_path):
    notes = make_notes(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(advice_document(["- 外の助言"]), encoding="utf-8")
    (notes / "advice.md").symlink_to(outside)
    assert read_system_screen_advice(notes) == ""
    # notes/ の中を指すリンクは、その先の文書として読む
    inside = notes / "inside.md"
    inside.write_text(advice_document(SYSTEM_LINES), encoding="utf-8")
    (notes / "advice.md").unlink()
    (notes / "advice.md").symlink_to(inside)
    assert read_system_screen_advice(notes) == SYSTEM_BODY


@pytest.mark.parametrize(
    ("section", "phase_name", "expected_kept"),
    [
        ("- 段階: prepare (t=5.000)\n- 一覧を開く", "prepare", True),
        ("\n- 段階: recover (t=8.000)\n- 再開する", "recover", True),
        ("- 段階: prepare (t=5.000)\n- 一覧を開く", "recover", False),
        ("- 段階: prepare (t=5.000)\n- 一覧を開く", "clean_up", False),
        # 書式のずれ (全角のコロン・強調の記号・段階の行の前の小見出し) は許す
        ("- **段階**：prepare (t=5.000)\n- 一覧を開く", "prepare", True),
        ("### 今の状況\n- 段階: `recover` (t=8.000)\n- 再開する", "recover", True),
        # 段階の行が無い助言は、どの段階のものか分からないので渡さない
        ("- 一覧を開く", "prepare", False),
        ("", "prepare", False),
    ],
)
def test_advice_for_phase_keeps_only_the_advice_written_for_the_phase(
    section, phase_name, expected_kept
):
    expected = section if expected_kept else ""
    assert advice_for_phase(section, phase_name) == expected
