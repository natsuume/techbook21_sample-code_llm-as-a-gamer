from harness_fakes import FAKE_FEATURES, FAKE_STAGNATION_ALERT, read_fake_template

from switch2_harness.features import REFLECTION_FEATURE, STAGNATION_ALERT_FEATURE
from switch2_harness.harness_config import HarnessConfig
from switch2_harness.instructions import (
    BASE_TEMPLATE_FILE,
    read_package_template,
    render_instructions,
)
from switch2_harness.reflection import NOTICE_LINE, REFLECTION_SECTION_HEADING


def make_config(features: tuple[str, ...]) -> HarnessConfig:
    return HarnessConfig(
        game="ゲーム A",
        goal="ボスを倒す",
        features=features,
        created_at="2026-11-16T21:00:05+09:00",
    )


def test_base_template_file_name():
    assert BASE_TEMPLATE_FILE == "base.md"


def test_render_instructions_with_no_feature_substitutes_base_only():
    text = render_instructions(make_config(()), read_fake_template, FAKE_FEATURES)
    assert text == "# 基本 ゲーム A\n\n目標: ボスを倒す\n"


def test_render_instructions_appends_enabled_sections_in_available_order():
    config = make_config(("memo-files", "action-history"))
    text = render_instructions(config, read_fake_template, FAKE_FEATURES)
    assert text == (
        "# 基本 ゲーム A\n"
        "\n"
        "目標: ボスを倒す\n"
        "\n"
        "## 行動履歴 (ゲーム A)\n"
        "\n"
        "## メモ\n"
        "\n"
        "ボスを倒す を目指す\n"
    )


def test_render_instructions_reads_only_enabled_templates():
    requested: list[str] = []

    def read_template(file_name: str) -> str:
        requested.append(file_name)
        return read_fake_template(file_name)

    render_instructions(make_config(("memo-files",)), read_template, FAKE_FEATURES)
    assert requested == ["base.md", "memo-files.md"]


def test_render_instructions_adds_no_section_for_feature_without_instruction_file():
    available = (*FAKE_FEATURES, FAKE_STAGNATION_ALERT)
    requested: list[str] = []

    def read_template(file_name: str) -> str:
        requested.append(file_name)
        return read_fake_template(file_name)

    with_alert = render_instructions(
        make_config((STAGNATION_ALERT_FEATURE,)), read_template, available
    )
    without_features = render_instructions(make_config(()), read_template, available)
    assert with_alert == without_features
    # instruction_file が None の要素は、テンプレートを読みに行かない
    assert requested == ["base.md", "base.md"]


def test_render_instructions_ends_with_exactly_one_newline():
    def read_template(file_name: str) -> str:
        return "# $game\n\n\n"

    text = render_instructions(make_config(()), read_template, FAKE_FEATURES)
    assert text == "# ゲーム A\n"


def test_read_package_template_reads_base_md():
    text = read_package_template("base.md")
    assert text.startswith("# Switch 2 の操作ルール\n")
    assert "- ゲーム名: $game\n" in text
    assert "- 目標: $goal\n" in text


def test_render_instructions_with_package_base_template():
    text = render_instructions(make_config(()), read_package_template)
    assert "- ゲーム名: ゲーム A\n" in text
    assert "- 目標: ボスを倒す\n" in text
    assert "$game" not in text
    assert "$goal" not in text
    assert text.endswith("確かめてください。\n")
    assert not text.endswith("\n\n")


def test_read_package_template_reads_action_history_md():
    text = read_package_template("action-history.md")
    assert text.startswith("## 行動履歴\n")


def test_render_instructions_with_action_history_adds_its_section():
    text = render_instructions(make_config(("action-history",)), read_package_template)
    assert "\n\n## 行動履歴\n\n" in text
    assert text.index("## 守ること") < text.index("## 行動履歴")
    assert text.endswith("エラーの応答を表示します\n")


def test_read_package_template_reads_notes_md():
    text = read_package_template("notes.md")
    assert text.startswith("## メモファイル\n")


def test_render_instructions_with_notes_adds_its_section():
    text = render_instructions(make_config(("notes",)), read_package_template)
    assert "\n\n## メモファイル\n\n" in text
    assert text.index("## 守ること") < text.index("## メモファイル")
    # 節のテンプレートには置換する $game・$goal が無いため、本文はテンプレートのまま最後に続く
    section = read_package_template("notes.md").rstrip("\n")
    assert text.endswith("\n\n" + section + "\n")


def test_read_package_template_reads_stagnation_alert_md():
    text = read_package_template("stagnation-alert.md")
    assert text.startswith("## 停滞の知らせ\n")


def test_render_instructions_with_stagnation_alert_adds_its_section():
    config = make_config((STAGNATION_ALERT_FEATURE,))
    text = render_instructions(config, read_package_template)
    assert "\n\n## 停滞の知らせ\n\n" in text
    assert text.index("## 守ること") < text.index("## 停滞の知らせ")
    # 節のテンプレートには置換する $game・$goal が無いため、本文はテンプレートのまま最後に続く
    section = read_package_template("stagnation-alert.md").rstrip("\n")
    assert text.endswith("\n\n" + section + "\n")


def test_read_package_template_reads_reflection_md():
    text = read_package_template("reflection.md")
    assert text.startswith("## 振り返り\n")
    # look が出す知らせの行を、そのまま示す
    assert f"\n{NOTICE_LINE}\n" in text


def test_render_instructions_with_notes_and_reflection_adds_both_sections():
    config = make_config(("notes", REFLECTION_FEATURE))
    text = render_instructions(config, read_package_template)
    assert "\n\n## メモファイル\n\n" in text
    assert "\n\n## 振り返り\n\n" in text
    assert (
        text.index("## 守ること")
        < text.index("## メモファイル")
        < text.index("\n## 振り返り\n")
    )
    # 節のテンプレートには置換する $game・$goal が無いため、本文はテンプレートのまま最後に続く
    section = read_package_template("reflection.md").rstrip("\n")
    assert text.endswith("\n\n" + section + "\n")


def test_read_package_template_reads_reflection_notes_section_md():
    # init で notes.md の末尾に足す内容は、振り返りの節の見出しだけ
    text = read_package_template("reflection-notes-section.md")
    assert text == REFLECTION_SECTION_HEADING + "\n"


def test_read_package_template_reads_reflection_critic_md():
    text = read_package_template("reflection-critic.md")
    assert "- ゲーム名: $game\n" in text
    assert "- 目標: $goal\n" in text


def test_read_package_template_reads_notes_initial_md():
    assert read_package_template("notes-initial.md") == (
        "# メモ\n"
        "\n"
        "## 操作方法\n"
        "\n"
        "## ゲームの仕組み\n"
        "\n"
        "## 試して効かなかったこと\n"
        "\n"
        "## 今の状況と次にやること\n"
    )
