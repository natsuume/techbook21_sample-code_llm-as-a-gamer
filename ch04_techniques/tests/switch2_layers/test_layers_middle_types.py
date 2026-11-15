import pytest

from switch2_layers.middle.middle_types import (
    MiddleState,
    middle_state_from_json,
    middle_state_to_json,
)

STATE = MiddleState(
    generated_t=5.5,
    based_on_t=5.0,
    scene_id="scene-001",
    scene_name="町",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.2,
    timeline="## 直近",
    surface_rows_until_t=4.8,
    operations_rows_until_t=4.9,
)


def test_round_trip():
    value = middle_state_to_json(STATE)
    assert list(value) == [
        "generated_t",
        "based_on_t",
        "scene_id",
        "scene_name",
        "is_stuck",
        "stuck_description",
        "surface_wait_s",
        "timeline",
        "surface_rows_until_t",
        "operations_rows_until_t",
    ]
    assert value["surface_rows_until_t"] == 4.8
    assert value["operations_rows_until_t"] == 4.9
    assert middle_state_from_json(value) == STATE


def test_null_scene_is_allowed():
    value = {**middle_state_to_json(STATE), "scene_id": None, "scene_name": None}
    state = middle_state_from_json(value)
    assert state.scene_id is None
    assert state.scene_name is None


def test_null_row_bounds_are_allowed():
    value = {
        **middle_state_to_json(STATE),
        "surface_rows_until_t": None,
        "operations_rows_until_t": None,
    }
    state = middle_state_from_json(value)
    assert state.surface_rows_until_t is None
    assert state.operations_rows_until_t is None


@pytest.mark.parametrize(
    "changes",
    [
        {"generated_t": "5.5"},
        {"based_on_t": True},
        {"surface_wait_s": None},
        {"scene_id": "../x"},
        {"scene_id": 1},
        {"scene_name": 3},
        {"is_stuck": "no"},
        {"stuck_description": None},
        {"timeline": ["a"]},
        {"surface_rows_until_t": "4.8"},
        {"surface_rows_until_t": True},
        {"operations_rows_until_t": [4.9]},
    ],
)
def test_wrong_types_are_rejected(changes):
    with pytest.raises(ValueError):
        middle_state_from_json({**middle_state_to_json(STATE), **changes})


@pytest.mark.parametrize("value", [None, [], "state", 1])
def test_non_object_is_rejected(value):
    with pytest.raises(ValueError):
        middle_state_from_json(value)


@pytest.mark.parametrize(
    "key", ["timeline", "surface_rows_until_t", "operations_rows_until_t"]
)
def test_missing_key_is_rejected(key):
    value = middle_state_to_json(STATE)
    del value[key]
    with pytest.raises(ValueError):
        middle_state_from_json(value)
