"""codex_instructions のテスト。置き換えは本物のテンプレート (instructions/*.md) で確かめる。"""

from pathlib import Path

import pytest

from switch2_harness.codex_instructions import (
    CLAUDE_HARNESS_COMMAND,
    CODEX_REPLACEMENTS,
    codex_read_template,
    codex_template,
    replace_exactly_once,
)
from switch2_harness.features import FEATURES
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.instructions import read_package_template, render_instructions

ALL_FEATURES = tuple(feature.name for feature in FEATURES)

# Claude Code のツールの名前の書き方とサブコマンドの書き方 (Codex 版の指示に残ってはいけないもの)
CLAUDE_TOOL_PHRASES = ("Read ツール", "Read で", "Write か Edit", "Edit が", "uv run")

# Codex 版の指示に書く、ハーネスの実行ファイルの絶対パス
EXECUTABLE = Path("/repo/ch04_techniques/.venv/bin/switch2-harness")


def make_config(features: tuple[str, ...]) -> HarnessConfig:
    return HarnessConfig(
        game="ゲーム A",
        goal="ボスを倒す",
        features=features,
        created_at="2026-11-21T21:00:05+09:00",
    )


def undo(text: str, replacements) -> str:
    """Codex 版の文の置き換えを元に戻す (サブコマンドの書き方を戻してから、ツールの名前を戻す)。"""
    text = text.replace(str(EXECUTABLE), CLAUDE_HARNESS_COMMAND)
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
    codex_text = codex_template(file_name, claude_text, EXECUTABLE)
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
    assert codex_template(file_name, text, EXECUTABLE) == text


def test_codex_agents_md_uses_codex_tools():
    text = render_instructions(
        make_config(ALL_FEATURES),
        codex_read_template(read_package_template, EXECUTABLE),
    )
    for phrase in CLAUDE_TOOL_PHRASES:
        assert phrase not in text
    assert "- `look` が表示した PNG は、view_image ツールで開いて読みます\n" in text
    assert "その場で apply_patch ツールで `notes.md` に書いてください" in text
    assert "- 並べた画像を view_image ツールで開いて読みます。" in text


def test_claude_md_is_rendered_from_the_templates_as_they_are():
    claude_text = render_instructions(make_config(ALL_FEATURES), read_package_template)
    codex_text = render_instructions(
        make_config(ALL_FEATURES),
        codex_read_template(read_package_template, EXECUTABLE),
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
        codex_template("base.md", "# 別のテンプレート\n", EXECUTABLE)


def test_codex_base_md_writes_the_commands_with_the_absolute_executable():
    text = codex_template("base.md", read_package_template("base.md"), EXECUTABLE)
    # Codex の rules がサンドボックスの外で動かすのは、絶対パスの形だけ
    assert f"{EXECUTABLE} look " in text
    assert f"{EXECUTABLE} press tap RIGHT RIGHT A " in text
    assert f"{EXECUTABLE} status " in text
    assert f"{EXECUTABLE} finish " in text
    assert text.count(str(EXECUTABLE)) == read_package_template("base.md").count(
        CLAUDE_HARNESS_COMMAND
    )
    assert CLAUDE_HARNESS_COMMAND not in text


def test_codex_read_template_reads_through_the_given_function():
    requested: list[str] = []

    def read_template(file_name: str) -> str:
        requested.append(file_name)
        return "本文"

    read = codex_read_template(read_template, EXECUTABLE)
    assert read("stagnation-alert.md") == "本文"
    assert requested == ["stagnation-alert.md"]
