import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from harness_fakes import FAKE_FEATURES

from switch2_harness.harness_config import (
    DEFAULT_GOAL,
    HarnessConfig,
    config_from_json_text,
    config_to_json_text,
    format_created_at,
    read_agent,
    read_config,
    write_config,
)

JST = timezone(timedelta(hours=9))

CONFIG = HarnessConfig(
    game="星のカービィ",
    goal="ステージ 1 をクリアする",
    features=("action-history",),
    created_at="2026-11-16T21:00:05+09:00",
)


def test_default_goal():
    assert DEFAULT_GOAL == "できるだけ先へ進める"


def test_format_created_at_keeps_seconds_and_timezone():
    moment = datetime(2026, 11, 16, 21, 0, 5, 123456, tzinfo=JST)
    assert format_created_at(moment) == "2026-11-16T21:00:05+09:00"


def test_format_created_at_in_utc():
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert format_created_at(moment) == "2026-01-02T03:04:05+00:00"


def test_config_to_json_text_has_the_harness_json_keys():
    text = config_to_json_text(CONFIG)
    assert json.loads(text) == {
        "game": "星のカービィ",
        "goal": "ステージ 1 をクリアする",
        "features": ["action-history"],
        "created_at": "2026-11-16T21:00:05+09:00",
    }
    assert text.endswith("}\n")
    assert "星のカービィ" in text  # ensure_ascii=False


def test_config_from_json_text_reads_the_fields():
    text = json.dumps(
        {
            "game": "G",
            "goal": "X",
            "features": ["memo-files", "action-history"],
            "created_at": "2026-11-16T21:00:05+09:00",
        }
    )
    assert config_from_json_text(text, FAKE_FEATURES) == HarnessConfig(
        game="G",
        goal="X",
        features=("action-history", "memo-files"),
        created_at="2026-11-16T21:00:05+09:00",
    )


def test_config_from_json_text_rejects_unknown_feature():
    text = json.dumps(
        {
            "game": "G",
            "goal": "X",
            "features": ["zoom"],
            "created_at": "2026-11-16T21:00:05+09:00",
        }
    )
    with pytest.raises(ValueError) as error:
        config_from_json_text(text, FAKE_FEATURES)
    assert "zoom" in str(error.value)


def test_config_from_json_text_rejects_a_feature_not_in_features_by_default():
    text = json.dumps(
        {
            "game": "G",
            "goal": "X",
            "features": ["memo-files"],
            "created_at": "2026-11-16T21:00:05+09:00",
        }
    )
    with pytest.raises(ValueError):
        config_from_json_text(text)


def test_write_then_read_config(tmp_path):
    path = tmp_path / "harness.json"
    write_config(path, CONFIG)
    assert json.loads(path.read_text(encoding="utf-8"))["game"] == "星のカービィ"
    assert read_config(path, FAKE_FEATURES) == CONFIG


def test_write_config_overwrites(tmp_path):
    path = tmp_path / "harness.json"
    path.write_text("old", encoding="utf-8")
    write_config(path, CONFIG)
    assert read_config(path, FAKE_FEATURES) == CONFIG


def test_read_config_rejects_unknown_feature(tmp_path):
    path = tmp_path / "harness.json"
    path.write_text(
        json.dumps(
            {
                "game": "G",
                "goal": "X",
                "features": ["zoom"],
                "created_at": "2026-11-16T21:00:05+09:00",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_config(path, FAKE_FEATURES)


# --- エージェントの種類 ---


def test_config_defaults_to_claude():
    assert CONFIG.agent == "claude"


def test_config_to_json_text_writes_the_agent_only_for_codex():
    assert "agent" not in json.loads(config_to_json_text(CONFIG))
    codex = replace(CONFIG, agent="codex")
    assert json.loads(config_to_json_text(codex)) == {
        "game": "星のカービィ",
        "goal": "ステージ 1 をクリアする",
        "features": ["action-history"],
        "created_at": "2026-11-16T21:00:05+09:00",
        "agent": "codex",
    }


def test_config_from_json_text_reads_the_agent():
    data = {
        "game": "G",
        "goal": "X",
        "features": [],
        "created_at": "2026-11-16T21:00:05+09:00",
    }
    assert config_from_json_text(json.dumps(data), FAKE_FEATURES).agent == "claude"
    codex = config_from_json_text(json.dumps({**data, "agent": "codex"}), FAKE_FEATURES)
    assert codex.agent == "codex"
    with pytest.raises(ValueError) as error:
        config_from_json_text(json.dumps({**data, "agent": "gemini"}), FAKE_FEATURES)
    assert str(error.value) == (
        "harness.json: unknown agent: gemini (available: claude, codex)"
    )


def test_write_then_read_a_codex_config(tmp_path):
    path = tmp_path / "harness.json"
    codex = replace(CONFIG, agent="codex")
    write_config(path, codex)
    assert read_config(path, FAKE_FEATURES) == codex


def test_read_agent_reads_only_the_agent(tmp_path):
    path = tmp_path / "harness.json"
    # 要素の名前は検証しない (report は要素の一覧を持たずに読むため)
    path.write_text('{"features": ["zoom"], "agent": "codex"}', encoding="utf-8")
    assert read_agent(path) == "codex"
    path.write_text('{"game": "G"}', encoding="utf-8")
    assert read_agent(path) == "claude"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_agent(path)
    with pytest.raises(FileNotFoundError):
        read_agent(tmp_path / "missing.json")
