import pytest
from harness_fakes import FAKE_FEATURES, FAKE_REFLECTION

from switch2_harness.features import (
    ACTION_HISTORY_FEATURE,
    BASE_AGENT_COMMANDS,
    FEATURES,
    NOTES_FEATURE,
    NOTES_FILE_NAME,
    REFLECTION_FEATURE,
    STAGNATION_ALERT_FEATURE,
    Feature,
    FileAppend,
    WritableFile,
    agent_commands,
    agent_file_appends,
    agent_writable_files,
    available_names_text,
    validate_feature_names,
)


def test_features_has_action_history_and_notes():
    assert ACTION_HISTORY_FEATURE == "action-history"
    assert NOTES_FEATURE == "notes"
    assert NOTES_FILE_NAME == "notes.md"
    assert FEATURES[:2] == (
        Feature(
            name="action-history",
            title="行動履歴",
            instruction_file="action-history.md",
            commands=(),
        ),
        Feature(
            name="notes",
            title="メモファイル",
            instruction_file="notes.md",
            commands=(),
            writable_files=(
                WritableFile(name="notes.md", template_file="notes-initial.md"),
            ),
        ),
    )


def test_features_lists_every_feature_in_order():
    assert tuple(feature.name for feature in FEATURES) == (
        "action-history",
        "notes",
        "stagnation-alert",
        "reflection",
    )


def test_features_has_stagnation_alert_third():
    assert STAGNATION_ALERT_FEATURE == "stagnation-alert"
    # CLAUDE.md に節を足し、サブコマンドも書き換えられるファイルも増やさない
    assert FEATURES[2] == Feature(
        name="stagnation-alert",
        title="停滞の知らせ",
        instruction_file="stagnation-alert.md",
        commands=(),
    )
    assert FEATURES[2].writable_files == ()


def test_features_has_reflection_last_requiring_notes():
    assert REFLECTION_FEATURE == "reflection"
    # CLAUDE.md に節を足し、notes.md の末尾に「振り返り」の見出しを足す。サブコマンドも書き換えられるファイルも増やさない
    assert FEATURES[3] == Feature(
        name="reflection",
        title="振り返り",
        instruction_file="reflection.md",
        commands=(),
        requires=("notes",),
        file_appends=(
            FileAppend(name="notes.md", template_file="reflection-notes-section.md"),
        ),
    )


def test_feature_has_no_writable_files_by_default():
    feature = Feature(name="x", title="X", instruction_file="x.md", commands=())
    assert feature.writable_files == ()


def test_action_history_adds_no_agent_command():
    assert agent_commands(("action-history",)) == BASE_AGENT_COMMANDS


def test_notes_adds_no_agent_command():
    assert agent_commands(("notes",)) == BASE_AGENT_COMMANDS


def test_base_agent_commands():
    assert BASE_AGENT_COMMANDS == ("look", "press", "status", "finish")


def test_available_names_text_joins_names():
    assert available_names_text(FAKE_FEATURES) == "action-history, memo-files"


def test_available_names_text_is_none_when_empty():
    assert available_names_text(()) == "none"


def test_validate_feature_names_orders_by_available_and_removes_duplicates():
    names = ["memo-files", "action-history", "memo-files"]
    assert validate_feature_names(names, FAKE_FEATURES) == (
        "action-history",
        "memo-files",
    )


def test_validate_feature_names_accepts_no_names():
    assert validate_feature_names([], ()) == ()


def test_validate_feature_names_rejects_unknown_name_with_available_list():
    with pytest.raises(ValueError) as error:
        validate_feature_names(["memo-files", "zoom"], FAKE_FEATURES)
    assert str(error.value) == (
        "unknown feature: zoom (available: action-history, memo-files)"
    )


def test_validate_feature_names_says_none_when_no_feature_is_available():
    with pytest.raises(ValueError) as error:
        validate_feature_names(["action-history"], ())
    assert str(error.value) == "unknown feature: action-history (available: none)"


def test_validate_feature_names_lists_all_unknown_names():
    with pytest.raises(ValueError) as error:
        validate_feature_names(["x", "action-history", "y"], FAKE_FEATURES)
    assert str(error.value).startswith("unknown feature: x, y (available: ")


def test_validate_feature_names_uses_features_by_default():
    assert validate_feature_names(["action-history"]) == ("action-history",)
    with pytest.raises(ValueError) as error:
        validate_feature_names(["memo-files"])
    assert str(error.value) == (
        f"unknown feature: memo-files (available: {available_names_text(FEATURES)})"
    )


# 併用する要素 (requires) のある要素を含む、テスト用の要素
FEATURES_WITH_REFLECTION = (*FAKE_FEATURES, FAKE_REFLECTION)


def test_validate_feature_names_rejects_a_feature_without_its_required_feature():
    with pytest.raises(ValueError) as error:
        validate_feature_names(
            ["reflection", "action-history"], FEATURES_WITH_REFLECTION
        )
    assert str(error.value) == "feature reflection requires memo-files"


def test_validate_feature_names_accepts_a_feature_with_its_required_feature():
    names = ["reflection", "memo-files"]
    assert validate_feature_names(names, FEATURES_WITH_REFLECTION) == (
        "memo-files",
        "reflection",
    )


def test_validate_feature_names_checks_unknown_names_before_requires():
    with pytest.raises(ValueError) as error:
        validate_feature_names(["reflection", "zoom"], FEATURES_WITH_REFLECTION)
    assert str(error.value) == (
        "unknown feature: zoom (available: action-history, memo-files, reflection)"
    )


def test_validate_feature_names_requires_notes_for_reflection_by_default():
    with pytest.raises(ValueError) as error:
        validate_feature_names(["reflection"])
    assert str(error.value) == "feature reflection requires notes"
    assert validate_feature_names(["reflection", "notes"]) == ("notes", "reflection")


def test_agent_commands_without_enabled_features():
    assert agent_commands((), FAKE_FEATURES) == ("look", "press", "status", "finish")


def test_agent_commands_adds_enabled_commands_in_available_order():
    enabled = ("memo-files", "action-history")
    assert agent_commands(enabled, FAKE_FEATURES) == (
        "look",
        "press",
        "status",
        "finish",
        "history",
        "note",
        "notes",
    )


def test_agent_commands_skips_disabled_features():
    assert agent_commands(("memo-files",), FAKE_FEATURES) == (
        "look",
        "press",
        "status",
        "finish",
        "note",
        "notes",
    )


def test_agent_writable_files_without_enabled_features():
    assert agent_writable_files((), FAKE_FEATURES) == ()


def test_agent_writable_files_lists_files_of_enabled_features():
    enabled = ("memo-files", "action-history")
    assert agent_writable_files(enabled, FAKE_FEATURES) == (
        WritableFile("memo.md", "memo-initial.md"),
    )


def test_agent_writable_files_uses_features_by_default():
    assert agent_writable_files(("notes",)) == (
        WritableFile("notes.md", "notes-initial.md"),
    )


def test_agent_file_appends_lists_appends_of_enabled_features():
    enabled = ("memo-files", "reflection")
    assert agent_file_appends(enabled, FEATURES_WITH_REFLECTION) == (
        FileAppend("memo.md", "memo-reflection.md"),
    )


def test_agent_file_appends_skips_disabled_features():
    assert agent_file_appends(("memo-files",), FEATURES_WITH_REFLECTION) == ()
    assert agent_file_appends((), FEATURES_WITH_REFLECTION) == ()


def test_agent_file_appends_follows_the_available_order():
    first = Feature(
        name="first",
        title="1",
        instruction_file=None,
        commands=(),
        file_appends=(FileAppend("memo.md", "first.md"),),
    )
    second = Feature(
        name="second",
        title="2",
        instruction_file=None,
        commands=(),
        file_appends=(
            FileAppend("memo.md", "second-a.md"),
            FileAppend("todo.md", "second-b.md"),
        ),
    )
    assert agent_file_appends(("second", "first"), (first, second)) == (
        FileAppend("memo.md", "first.md"),
        FileAppend("memo.md", "second-a.md"),
        FileAppend("todo.md", "second-b.md"),
    )


def test_agent_file_appends_uses_features_by_default():
    assert agent_file_appends(("notes", "reflection")) == (
        FileAppend("notes.md", "reflection-notes-section.md"),
    )
