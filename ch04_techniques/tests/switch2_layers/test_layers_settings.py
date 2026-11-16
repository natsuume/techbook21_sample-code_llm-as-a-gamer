import dataclasses
import json
import math
from pathlib import Path

import pytest

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.progress.screen_progress import DEFAULT_PROGRESS_WINDOW_S
from switch2_layers.progress.stuck_advice_trigger import DEFAULT_STUCK_ADVICE_SECONDS
from switch2_layers.settings import (
    DEFAULT_BOX_REQUERY_EVERY,
    DEFAULT_DEEP_RUNNER,
    DEFAULT_MIDDLE_KIND,
    DEFAULT_SURFACE,
    DEFAULT_THRESHOLD_ADVISOR,
    OPENAI_API_KEY_ENV,
    TYPESAFE_API_KEY_ENV,
    DeepOnceSettings,
    DeepRunnerChoice,
    MiddleKind,
    MiddleOnceSettings,
    RunSettings,
    SurfaceKind,
    deep_once_settings_errors,
    middle_once_settings_errors,
    openai_api_key_errors,
    run_settings_errors,
    run_settings_to_json,
    typesafe_api_key_errors,
)


def test_default_run_settings_are_valid():
    assert run_settings_errors(RunSettings()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_fps": 10.0},
        {"interval_fps": 0.5},
        {"interval_width": 64},
        {"interval_width": 1280},
        {"max_surface_errors": 1},
        {"middle_interval_s": 1.0},
        {"middle_interval_s": 10.0},
        {"middle_window_s": 0.5},
        {"max_middle_errors": 1},
        {"deep_min_start_interval_s": 0.0},
        # deep_auto_seconds は deep_min_start_interval_s 以上に限るため、最小間隔も小さくして境界を確かめる
        {"deep_auto_seconds": 0.5, "deep_min_start_interval_s": 0.0},
        {"deep_auto_seconds": 15.0, "deep_min_start_interval_s": 15.0},
        {"deep_timeout_s": 0.5},
        {"allow_home": True},
    ],
)
def test_boundary_values_are_valid(changes):
    assert run_settings_errors(dataclasses.replace(RunSettings(), **changes)) == []


@pytest.mark.parametrize(
    "changes, option",
    [
        ({"interval_fps": 0.0}, "--interval-fps"),
        ({"interval_fps": -1.0}, "--interval-fps"),
        ({"interval_fps": 10.5}, "--interval-fps"),
        ({"interval_fps": math.nan}, "--interval-fps"),
        ({"interval_width": 63}, "--interval-width"),
        ({"interval_width": 1281}, "--interval-width"),
        ({"max_surface_errors": 0}, "--max-surface-errors"),
        ({"middle_interval_s": 0.99}, "--middle-interval"),
        ({"middle_interval_s": 10.01}, "--middle-interval"),
        ({"middle_interval_s": math.nan}, "--middle-interval"),
        ({"middle_window_s": 0.0}, "--middle-window"),
        ({"middle_window_s": math.inf}, "--middle-window"),
        ({"middle_window_s": math.nan}, "--middle-window"),
        ({"max_middle_errors": 0}, "--max-middle-errors"),
        ({"deep_min_start_interval_s": -1.0}, "--deep-min-start-interval"),
        ({"deep_min_start_interval_s": math.inf}, "--deep-min-start-interval"),
        ({"deep_auto_seconds": 0.0}, "--deep-auto-seconds"),
        ({"deep_auto_seconds": math.inf}, "--deep-auto-seconds"),
        ({"deep_timeout_s": 0.0}, "--deep-timeout"),
        ({"deep_timeout_s": math.inf}, "--deep-timeout"),
    ],
)
def test_out_of_range_values_are_reported_with_option_name(changes, option):
    errors = run_settings_errors(dataclasses.replace(RunSettings(), **changes))
    assert len(errors) == 1
    assert option in errors[0]


@pytest.mark.parametrize(
    "auto_seconds, min_start_interval",
    [(14.9, 15.0), (5.0, 15.0), (60.0, 61.0)],
)
def test_auto_seconds_shorter_than_min_start_interval_is_an_error(
    auto_seconds, min_start_interval
):
    # 自動の呼び出しが最小間隔より短いと、判定の周期ごとに要求が捨てられ続けるため、引数の誤りにする
    errors = run_settings_errors(
        RunSettings(
            deep_auto_seconds=auto_seconds,
            deep_min_start_interval_s=min_start_interval,
        )
    )
    assert len(errors) == 1
    assert "--deep-auto-seconds" in errors[0]
    assert "--deep-min-start-interval" in errors[0]


def test_surface_settings_defaults_follow_the_issue():
    settings = RunSettings()
    assert settings.surface is SurfaceKind.JEV
    assert DEFAULT_SURFACE is SurfaceKind.JEV
    assert settings.threshold_advisor == DEFAULT_THRESHOLD_ADVISOR == 0.5
    assert settings.box_requery_every == DEFAULT_BOX_REQUERY_EVERY == 5
    assert [kind.value for kind in SurfaceKind] == ["jev", "fake"]


@pytest.mark.parametrize(
    "changes",
    [
        {"threshold_advisor": 0.0},
        {"threshold_advisor": 1.0},
        {"box_requery_every": 1},
        {"surface": SurfaceKind.FAKE},
    ],
)
def test_surface_boundary_values_are_valid(changes):
    assert run_settings_errors(dataclasses.replace(RunSettings(), **changes)) == []


@pytest.mark.parametrize(
    "changes, option",
    [
        ({"threshold_advisor": -0.01}, "--threshold-advisor"),
        ({"threshold_advisor": 1.01}, "--threshold-advisor"),
        ({"threshold_advisor": math.nan}, "--threshold-advisor"),
        ({"box_requery_every": 0}, "--box-requery-every"),
        ({"box_requery_every": -3}, "--box-requery-every"),
    ],
)
def test_surface_out_of_range_values_are_reported_with_option_name(changes, option):
    errors = run_settings_errors(dataclasses.replace(RunSettings(), **changes))
    assert len(errors) == 1
    assert option in errors[0]


def test_run_settings_to_json_writes_surface_as_string():
    value = run_settings_to_json(RunSettings(surface=SurfaceKind.FAKE))
    assert value["surface"] == "fake"
    assert type(value["surface"]) is str
    assert value["threshold_advisor"] == 0.5
    assert value["box_requery_every"] == 5


def test_multiple_errors_are_all_reported():
    errors = run_settings_errors(
        RunSettings(middle_interval_s=20.0, max_middle_errors=0)
    )
    assert len(errors) == 2


def test_middle_once_settings_errors():
    assert middle_once_settings_errors(MiddleOnceSettings(Path("runs/x"))) == []
    errors = middle_once_settings_errors(
        MiddleOnceSettings(Path("runs/x"), middle_window_s=0.0)
    )
    assert len(errors) == 1
    assert "--middle-window" in errors[0]


@pytest.mark.parametrize(
    "kind, scene_id",
    [
        (DocumentKind.CONTROLS, None),
        (DocumentKind.CONTROLS, "scene-001"),
        (DocumentKind.TIPS, "scene-012"),
        (DocumentKind.STRATEGY, None),
        (DocumentKind.MAP, None),
    ],
)
def test_valid_deep_once_settings(kind, scene_id):
    assert (
        deep_once_settings_errors(DeepOnceSettings(Path("runs/x"), kind, scene_id))
        == []
    )


@pytest.mark.parametrize(
    "kind, scene_id",
    [
        (DocumentKind.CONTROLS, "../x"),
        (DocumentKind.TIPS, "scene-1"),
        (DocumentKind.STRATEGY, "scene-001"),
        (DocumentKind.ADVICE, "scene-001"),
        (DocumentKind.MAP, "scene-001"),
    ],
)
def test_invalid_deep_once_scene(kind, scene_id):
    errors = deep_once_settings_errors(DeepOnceSettings(Path("runs/x"), kind, scene_id))
    assert len(errors) == 1
    assert "--scene" in errors[0]


def test_run_settings_to_json_contains_all_arguments():
    settings = RunSettings(runs_dir=Path("elsewhere"), allow_home=True)
    value = run_settings_to_json(settings)
    assert set(value) == {field.name for field in dataclasses.fields(RunSettings)}
    assert value["runs_dir"] == "elsewhere"
    assert value["allow_home"] is True
    assert value["middle_interval_s"] == 5.0
    # そのまま JSON にできる
    assert json.loads(json.dumps(value)) == value


def test_deep_runner_defaults_to_claude_code():
    assert [choice.value for choice in DeepRunnerChoice] == ["claude", "fake"]
    assert DEFAULT_DEEP_RUNNER is DeepRunnerChoice.CLAUDE


def test_run_settings_keep_the_deep_runner_choice():
    assert RunSettings().deep is DeepRunnerChoice.CLAUDE
    # run.jsonl の start の args に、値の文字列で書く (Claude Code とフェイクのどちらで動かしたかを残す)
    assert run_settings_to_json(RunSettings())["deep"] == "claude"
    fake = run_settings_to_json(RunSettings(deep=DeepRunnerChoice.FAKE))
    assert fake["deep"] == "fake"
    assert type(fake["deep"]) is str
    assert json.loads(json.dumps(fake))["deep"] == "fake"


# --- --middle と API キー ---


def test_middle_kinds_and_default():
    assert [kind.value for kind in MiddleKind] == ["fake", "luna"]
    assert DEFAULT_MIDDLE_KIND is MiddleKind.LUNA
    assert RunSettings().middle is MiddleKind.LUNA
    assert MiddleOnceSettings(Path("runs/x")).middle is MiddleKind.LUNA
    assert OPENAI_API_KEY_ENV == "OPENAI_API_KEY"


@pytest.mark.parametrize("middle", list(MiddleKind))
def test_middle_kind_does_not_affect_range_errors(middle):
    assert run_settings_errors(RunSettings(middle=middle)) == []
    assert (
        middle_once_settings_errors(MiddleOnceSettings(Path("runs/x"), middle=middle))
        == []
    )


def test_run_settings_to_json_writes_middle_as_string():
    value = run_settings_to_json(RunSettings(middle=MiddleKind.FAKE))
    assert value["middle"] == "fake"
    assert json.loads(json.dumps(value))["middle"] == "fake"


@pytest.mark.parametrize(
    "environ",
    [{}, {"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": " \n"}, {"OTHER": "sk-test"}],
)
def test_luna_without_api_key_is_an_error(environ):
    errors = openai_api_key_errors(MiddleKind.LUNA, environ)
    assert len(errors) == 1
    assert "OPENAI_API_KEY" in errors[0]
    # キーなしで動かす方法を示す
    assert "--middle fake" in errors[0]


def test_luna_with_api_key_has_no_error():
    assert openai_api_key_errors(MiddleKind.LUNA, {"OPENAI_API_KEY": "sk-test"}) == []


def test_api_key_is_not_in_the_message():
    errors = openai_api_key_errors(MiddleKind.LUNA, {"OPENAI_API_KEY": "   "})
    assert errors
    errors_with_key = openai_api_key_errors(
        MiddleKind.LUNA, {"OPENAI_API_KEY": "sk-secret"}
    )
    assert all("sk-secret" not in error for error in errors_with_key)


@pytest.mark.parametrize("environ", [{}, {"OPENAI_API_KEY": "sk-test"}])
def test_fake_never_needs_api_key(environ):
    assert openai_api_key_errors(MiddleKind.FAKE, environ) == []


@pytest.mark.parametrize(
    "environ",
    [{}, {"TYPESAFE_API_KEY": ""}, {"TYPESAFE_API_KEY": " \n"}, {"OTHER": "x"}],
)
def test_jev_surface_without_api_key_is_an_error(environ):
    errors = typesafe_api_key_errors(SurfaceKind.JEV, environ)
    assert len(errors) == 1
    assert TYPESAFE_API_KEY_ENV == "TYPESAFE_API_KEY"
    assert "TYPESAFE_API_KEY" in errors[0]
    # キーなしで動かす方法を示す
    assert "--surface fake" in errors[0]


def test_jev_surface_with_api_key_has_no_error():
    assert typesafe_api_key_errors(SurfaceKind.JEV, {"TYPESAFE_API_KEY": "ts-x"}) == []


@pytest.mark.parametrize("environ", [{}, {"TYPESAFE_API_KEY": "ts-x"}])
def test_fake_surface_never_needs_api_key(environ):
    assert typesafe_api_key_errors(SurfaceKind.FAKE, environ) == []


# --- 画面の停滞の計測 (--progress-window・--stuck-advice-seconds) ---


def test_progress_settings_defaults_follow_the_issue():
    settings = RunSettings()
    assert settings.progress_window_s == DEFAULT_PROGRESS_WINDOW_S == 300.0
    assert settings.stuck_advice_seconds == DEFAULT_STUCK_ADVICE_SECONDS == 60.0


@pytest.mark.parametrize(
    "changes",
    [
        {"progress_window_s": 0.001},
        {"progress_window_s": 3600.0},
        {"stuck_advice_seconds": 0.001},
        {"stuck_advice_seconds": 600.0},
    ],
)
def test_progress_boundary_values_are_valid(changes):
    assert run_settings_errors(dataclasses.replace(RunSettings(), **changes)) == []


@pytest.mark.parametrize(
    "changes, option",
    [
        ({"progress_window_s": 0.0}, "--progress-window"),
        ({"progress_window_s": -1.0}, "--progress-window"),
        ({"progress_window_s": math.inf}, "--progress-window"),
        ({"progress_window_s": math.nan}, "--progress-window"),
        ({"stuck_advice_seconds": 0.0}, "--stuck-advice-seconds"),
        ({"stuck_advice_seconds": -60.0}, "--stuck-advice-seconds"),
        ({"stuck_advice_seconds": math.inf}, "--stuck-advice-seconds"),
        ({"stuck_advice_seconds": math.nan}, "--stuck-advice-seconds"),
    ],
)
def test_progress_out_of_range_values_are_reported_with_option_name(changes, option):
    errors = run_settings_errors(dataclasses.replace(RunSettings(), **changes))
    assert len(errors) == 1
    assert option in errors[0]


def test_progress_errors_follow_the_argument_order():
    errors = run_settings_errors(
        RunSettings(
            stuck_advice_seconds=0.0, progress_window_s=0.0, max_middle_errors=0
        )
    )
    assert len(errors) == 3
    assert "--max-middle-errors" in errors[0]
    assert "--progress-window" in errors[1]
    assert "--stuck-advice-seconds" in errors[2]


def test_run_settings_to_json_has_the_progress_arguments():
    value = run_settings_to_json(
        RunSettings(progress_window_s=120.0, stuck_advice_seconds=30.0)
    )
    assert value["progress_window_s"] == 120.0
    assert value["stuck_advice_seconds"] == 30.0
    assert json.loads(json.dumps(value)) == value
