import json
import math

import pytest

from switch2_layers.surface.middle_state_reader import (
    DEFAULT_SURFACE_WAIT_S,
    EMPTY_SURFACE_CONTEXT,
    SURFACE_WAIT_MAX_S,
    SURFACE_WAIT_MIN_S,
    SurfaceContext,
    clamp_surface_wait,
    read_surface_context,
)

STATE = {
    "generated_t": 12.5,
    "based_on_t": 11.0,
    "scene_id": "scene-001",
    "scene_name": "町",
    "is_stuck": False,
    "stuck_description": "",
    "surface_wait_s": 0.8,
    "timeline": "## 直近\n町を歩いている",
}


def test_range_follows_the_issue():
    assert (SURFACE_WAIT_MIN_S, SURFACE_WAIT_MAX_S, DEFAULT_SURFACE_WAIT_S) == (
        0.2,
        2.0,
        0.2,
    )
    assert EMPTY_SURFACE_CONTEXT == SurfaceContext(
        scene_id=None, timeline="", generated_t=None, wait_s=0.2
    )


# --- surface_wait_s を範囲に収める処理 ---


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.5, 0.5),
        (1, 1.0),
        (0.2, 0.2),
        (2.0, 2.0),
        # 範囲外は端に収める
        (0.1, 0.2),
        (0.0, 0.2),
        (-3.0, 0.2),
        (2.5, 2.0),
        (100, 2.0),
        (-math.inf, 0.2),
        (math.inf, 2.0),
        # 数でないものは既定値
        (math.nan, 0.2),
        ("1.0", 0.2),
        (None, 0.2),
        (True, 0.2),
        ([1.0], 0.2),
    ],
)
def test_clamp_surface_wait(value, expected):
    assert clamp_surface_wait(value) == expected


# --- middle/state.json の読み込み ---


def test_missing_file_gives_empty_context(tmp_path):
    assert read_surface_context(tmp_path / "state.json") == EMPTY_SURFACE_CONTEXT


@pytest.mark.parametrize("text", ["{broken", "", "[1, 2]", '"text"', "null"])
def test_broken_json_gives_empty_context(tmp_path, text):
    path = tmp_path / "state.json"
    path.write_text(text, encoding="utf-8")
    assert read_surface_context(path) == EMPTY_SURFACE_CONTEXT


def test_unreadable_bytes_give_empty_context(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"\xff\xfe\xfa")
    assert read_surface_context(path) == EMPTY_SURFACE_CONTEXT


def test_valid_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(STATE), encoding="utf-8")
    assert read_surface_context(path) == SurfaceContext(
        scene_id="scene-001",
        timeline="## 直近\n町を歩いている",
        generated_t=12.5,
        wait_s=0.8,
    )


@pytest.mark.parametrize("wait, expected", [(5.0, 2.0), (0.05, 0.2), (-1, 0.2)])
def test_out_of_range_wait_is_clamped(tmp_path, wait, expected):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({**STATE, "surface_wait_s": wait}), encoding="utf-8")
    assert read_surface_context(path).wait_s == expected


def test_nan_wait_in_file_gives_default(tmp_path):
    path = tmp_path / "state.json"
    # Python の json は NaN を書けるため、読む側も既定値に戻す
    path.write_text(json.dumps({**STATE, "surface_wait_s": math.nan}), encoding="utf-8")
    assert read_surface_context(path).wait_s == DEFAULT_SURFACE_WAIT_S


def test_fields_with_wrong_types_are_treated_as_missing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "scene_id": "../common",
                "timeline": 3,
                "generated_t": "12.5",
                "surface_wait_s": "fast",
            }
        ),
        encoding="utf-8",
    )
    assert read_surface_context(path) == EMPTY_SURFACE_CONTEXT


def test_missing_fields_are_treated_as_missing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"scene_id": "scene-002"}), encoding="utf-8")
    assert read_surface_context(path) == SurfaceContext(
        scene_id="scene-002", timeline="", generated_t=None, wait_s=0.2
    )


def test_null_scene_means_no_scene(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({**STATE, "scene_id": None}), encoding="utf-8")
    assert read_surface_context(path).scene_id is None
