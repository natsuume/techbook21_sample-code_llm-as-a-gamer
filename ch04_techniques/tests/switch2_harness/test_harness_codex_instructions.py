"""codex_instructions のテスト。置き換えは本物のテンプレート (instructions/*.md) で確かめる。"""

import pytest

from switch2_harness.codex_instructions import (
    CODEX_REPLACEMENTS,
    codex_read_template,
    codex_template,
    replace_exactly_once,
)
from switch2_harness.features import FEATURES
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.instructions import read_package_template, render_instructions

ALL_FEATURES = tuple(feature.name for feature in FEATURES)

# Claude Code のツールの名前の書き方 (Codex 版の指示に残ってはいけないもの)
CLAUDE_TOOL_PHRASES = ("Read ツール", "Read で", "Write か Edit", "Edit が")


def make_config(features: tuple[str, ...]) -> HarnessConfig:
    return HarnessConfig(
        game="ゲーム A",
        goal="ボスを倒す",
        features=features,
        created_at="2026-11-21T21:00:05+09:00",
    )


def undo(text: str, replacements) -> str:
    for old, new in reversed(replacements):
        text = text.replace(new, old)
    return text


def test_replacements_cover_the_templates_with_tool_names():
    assert set(CODEX_REPLACEMENTS) == {
        "base.md",
        "notes.md",
        "reflection.md",
        "multi-frame.md",
    }


@pytest.mark.parametrize("file_name", sorted(CODEX_REPLACEMENTS))
def test_codex_template_changes_only_the_tool_names(file_name):
    claude_text = read_package_template(file_name)
    codex_text = codex_template(file_name, claude_text)
    assert codex_text != claude_text
    # 置き換えた箇所を元に戻すと、Claude Code 版の文と 1 文字も違わない
    assert undo(codex_text, CODEX_REPLACEMENTS[file_name]) == claude_text
    for phrase in CLAUDE_TOOL_PHRASES:
        assert phrase not in codex_text


@pytest.mark.parametrize(
    "file_name",
    [
        "action-history.md",
        "stagnation-alert.md",
        "reflection-critic.md",
        "notes-initial.md",
        "reflection-notes-section.md",
    ],
)
def test_codex_template_keeps_templates_without_tool_names(file_name):
    text = read_package_template(file_name)
    assert codex_template(file_name, text) == text


def test_codex_agents_md_uses_codex_tools():
    text = render_instructions(
        make_config(ALL_FEATURES), codex_read_template(read_package_template)
    )
    for phrase in CLAUDE_TOOL_PHRASES:
        assert phrase not in text
    assert "- `look` が表示した PNG は、view_image ツールで開いて読みます\n" in text
    assert "その場で apply_patch ツールで `notes.md` に書いてください" in text
    assert "- 並べた画像を view_image ツールで開いて読みます。" in text


def test_claude_md_is_rendered_from_the_templates_as_they_are():
    claude_text = render_instructions(make_config(ALL_FEATURES), read_package_template)
    codex_text = render_instructions(
        make_config(ALL_FEATURES), codex_read_template(read_package_template)
    )
    assert "- `look` が表示した PNG は、Read ツールで開いて読みます\n" in claude_text
    assert "その場で Write か Edit ツールで `notes.md` に書いてください" in claude_text
    all_replacements = [pair for pairs in CODEX_REPLACEMENTS.values() for pair in pairs]
    assert undo(codex_text, all_replacements) == claude_text


def test_replace_exactly_once_rejects_a_missing_or_repeated_text():
    assert replace_exactly_once("a b", [("a", "x")]) == "x b"
    with pytest.raises(ValueError):
        replace_exactly_once("b", [("a", "x")])
    with pytest.raises(ValueError):
        replace_exactly_once("a a", [("a", "x")])


def test_codex_template_fails_when_the_claude_text_changed():
    with pytest.raises(ValueError):
        codex_template("base.md", "# 別のテンプレート\n")


def test_codex_read_template_reads_through_the_given_function():
    requested: list[str] = []

    def read_template(file_name: str) -> str:
        requested.append(file_name)
        return "本文"

    read = codex_read_template(read_template)
    assert read("stagnation-alert.md") == "本文"
    assert requested == ["stagnation-alert.md"]
