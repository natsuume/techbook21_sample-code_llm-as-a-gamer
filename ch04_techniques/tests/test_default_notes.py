from switch2_agent.default_notes import (
    DEFAULT_CONTROLS_TEXT,
    DEFAULT_NOTES,
    DEFAULT_STRATEGY_TEXT,
    GENERAL_MARK,
)
from switch2_agent.game_notes import NOTE_KINDS


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


def test_default_notes_have_only_controls_and_strategy():
    assert DEFAULT_NOTES == {
        "controls": DEFAULT_CONTROLS_TEXT,
        "strategy": DEFAULT_STRATEGY_TEXT,
    }
    assert set(DEFAULT_NOTES) <= set(NOTE_KINDS)


def test_default_notes_use_level_three_headings_only():
    # 要求の文では、ファイルの中身が `## <見出し>` の下に入るため
    for text in DEFAULT_NOTES.values():
        assert headings(text)
        assert all(line.startswith("### ") for line in headings(text))


def test_default_controls_has_general_section_then_section_for_this_game():
    assert headings(DEFAULT_CONTROLS_TEXT) == [
        "### 一般的なボタンの対応 (Nintendo Switch 2 のゲームに多い傾向。このゲームではまだ確かめていない)",
        "### このゲームで確かめた操作",
    ]


def test_default_controls_marks_every_general_line():
    general_section = DEFAULT_CONTROLS_TEXT.split("### このゲームで確かめた操作")[0]
    items = [line for line in general_section.splitlines() if line.startswith("- ")]
    assert items
    assert all(line.startswith(f"- {GENERAL_MARK} ") for line in items)


def test_default_controls_has_common_confirm_and_cancel_buttons():
    assert f"- {GENERAL_MARK} A: 決定する" in DEFAULT_CONTROLS_TEXT
    assert f"- {GENERAL_MARK} B: キャンセルする、戻る" in DEFAULT_CONTROLS_TEXT
    assert f"- {GENERAL_MARK} HOME: 本体の HOME メニューに戻る" in DEFAULT_CONTROLS_TEXT


def test_default_strategy_has_three_sections_in_order():
    assert headings(DEFAULT_STRATEGY_TEXT) == ["### 目標", "### 進行状況", "### 戦略"]


def test_default_strategy_has_goal_levels_with_smallest_goal_learning_controls():
    lines = DEFAULT_STRATEGY_TEXT.splitlines()
    big = next(i for i, line in enumerate(lines) if line.startswith("- 大目標: "))
    middle = next(i for i, line in enumerate(lines) if line.startswith("  - 中目標: "))
    small = next(i for i, line in enumerate(lines) if line.startswith("    - 小目標: "))
    assert big < middle < small
    assert "操作方法を把握する" in lines[small]
    for index in (big, middle, small):
        assert lines[index].endswith("(状態: 途中)")


def test_default_strategy_has_safety_limits():
    for phrase in (
        "通信対戦のソフト、オンラインに接続する機能、ショップでの購入を選ばない",
        "HOME メニューでは、試行しているソフト以外を起動しない",
        "セーブデータの削除と、本体の設定の変更をしない",
        "迷ったら何も押さない",
        "HOME ボタンは試さない",
    ):
        assert phrase in DEFAULT_STRATEGY_TEXT
