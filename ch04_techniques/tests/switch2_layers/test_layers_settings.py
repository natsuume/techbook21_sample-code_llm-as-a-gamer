import dataclasses
import json
import math
from pathlib import Path

import pytest

from switch2_layers.deep.deep_types import DocumentKind
from switch2_layers.settings import (
    DeepOnceSettings,
    MiddleOnceSettings,
    RunSettings,
    deep_once_settings_errors,
    middle_once_settings_errors,
    run_settings_errors,
    run_settings_to_json,
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
