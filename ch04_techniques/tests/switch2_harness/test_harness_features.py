import pytest
from harness_fakes import FAKE_FEATURES

from switch2_harness.features import (
    BASE_AGENT_COMMANDS,
    FEATURES,
    agent_commands,
    available_names_text,
    validate_feature_names,
)


def test_features_is_empty_in_the_base_condition():
    assert FEATURES == ()


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
    with pytest.raises(ValueError) as error:
        validate_feature_names(["action-history"])
    assert "(available: none)" in str(error.value)


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
