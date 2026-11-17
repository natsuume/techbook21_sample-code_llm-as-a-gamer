import dataclasses

import pytest

from switch2_layers.middle.middle_types import (
    MapObservation,
    MiddleOutput,
    MiddleState,
    ObservationKind,
    middle_state_from_json,
    middle_state_to_json,
)
from switch2_layers.notes.map_document import TilePosition

STATE = MiddleState(
    generated_t=5.5,
    based_on_t=5.0,
    scene_id="scene-001",
    scene_name="町",
    is_stuck=False,
    stuck_description="",
    surface_wait_s=0.2,
    timeline="## 直近",
    surface_rows_offset=120,
    operations_rows_offset=40,
    tasks_rows_offset=60,
)
# 省けるキー (tasks_rows_offset・place_name・current_tile・press_home) が無い middle/state.json
STATE_JSON_WITHOUT_OPTIONAL_KEYS = {
    "generated_t": 5.5,
    "based_on_t": 5.0,
    "scene_id": "scene-001",
    "scene_name": "町",
    "is_stuck": False,
    "stuck_description": "",
    "surface_wait_s": 0.2,
    "timeline": "## 直近",
    "surface_rows_offset": 120,
    "operations_rows_offset": 40,
}


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
        "surface_rows_offset",
        "operations_rows_offset",
        "tasks_rows_offset",
        "place_name",
        "current_tile",
        "press_home",
    ]
    assert value["surface_rows_offset"] == 120
    assert value["operations_rows_offset"] == 40
    assert value["tasks_rows_offset"] == 60
    assert middle_state_from_json(value) == STATE


def test_null_scene_is_allowed():
    value = {**middle_state_to_json(STATE), "scene_id": None, "scene_name": None}
    state = middle_state_from_json(value)
    assert state.scene_id is None
    assert state.scene_name is None


def test_zero_offsets_are_allowed():
    value = {
        **middle_state_to_json(STATE),
        "surface_rows_offset": 0,
        "operations_rows_offset": 0,
        "tasks_rows_offset": 0,
    }
    state = middle_state_from_json(value)
    assert state.surface_rows_offset == 0
    assert state.operations_rows_offset == 0
    assert state.tasks_rows_offset == 0


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
        {"surface_rows_offset": None},
        {"surface_rows_offset": -1},
        {"surface_rows_offset": 1.5},
        {"surface_rows_offset": True},
        {"operations_rows_offset": "40"},
        {"tasks_rows_offset": None},
        {"tasks_rows_offset": -1},
        {"tasks_rows_offset": "60"},
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
    "key", ["timeline", "surface_rows_offset", "operations_rows_offset"]
)
def test_missing_key_is_rejected(key):
    value = middle_state_to_json(STATE)
    del value[key]
    with pytest.raises(ValueError):
        middle_state_from_json(value)


# --- tasks.jsonl を読み終えた位置と、使わなくなったキー ---


def test_missing_tasks_rows_offset_is_read_as_zero():
    state = middle_state_from_json(STATE_JSON_WITHOUT_OPTIONAL_KEYS)
    assert state.tasks_rows_offset == 0
    assert state == dataclasses.replace(STATE, tasks_rows_offset=0)


@pytest.mark.parametrize(
    "changes",
    [
        {"first_task": "宿屋に入る", "first_task_since_t": 3.25},
        {"first_task": "", "first_task_since_t": None},
        # 型も確かめない
        {"first_task": None, "first_task_since_t": "3.25"},
        {"first_task": ["宿屋に入る"]},
        {"first_task_since_t": True},
    ],
)
def test_first_task_keys_are_ignored(changes):
    value = {**middle_state_to_json(STATE), **changes}
    assert middle_state_from_json(value) == STATE


def test_first_task_keys_are_not_written():
    value = middle_state_to_json(STATE)
    assert "first_task" not in value
    assert "first_task_since_t" not in value


# --- 現在地の場所とマス ---

STATE_WITH_PLACE = dataclasses.replace(
    STATE, place_name="家の 1 階", current_tile=TilePosition(7, -1)
)


def test_state_without_place_has_defaults():
    assert STATE.place_name is None
    assert STATE.current_tile is None


def test_place_keys_are_written():
    value = middle_state_to_json(STATE_WITH_PLACE)
    assert value["place_name"] == "家の 1 階"
    assert value["current_tile"] == {"x": 7, "y": -1}
    assert list(value["current_tile"]) == ["x", "y"]
    # 決まっていない場所とマスは null
    value = middle_state_to_json(STATE)
    assert value["place_name"] is None
    assert value["current_tile"] is None


@pytest.mark.parametrize(
    "state",
    [
        STATE_WITH_PLACE,
        dataclasses.replace(STATE_WITH_PLACE, current_tile=None),
        dataclasses.replace(STATE_WITH_PLACE, place_name=None, current_tile=None),
    ],
)
def test_place_round_trip(state):
    assert middle_state_from_json(middle_state_to_json(state)) == state


def test_missing_place_keys_are_read_as_null():
    # place_name と current_tile のキーが無い middle/state.json は、どちらも null として読む
    state = middle_state_from_json(STATE_JSON_WITHOUT_OPTIONAL_KEYS)
    assert state.place_name is None
    assert state.current_tile is None
    value = {**middle_state_to_json(STATE_WITH_PLACE)}
    del value["place_name"]
    del value["current_tile"]
    assert middle_state_from_json(value) == STATE


def test_place_keys_are_read():
    value = {
        **STATE_JSON_WITHOUT_OPTIONAL_KEYS,
        "place_name": "家の 1 階",
        "current_tile": {"x": -3, "y": 4, "floor": 1},
    }
    state = middle_state_from_json(value)
    assert state.place_name == "家の 1 階"
    # 座標の object のほかのキーは無視する
    assert state.current_tile == TilePosition(-3, 4)


@pytest.mark.parametrize(
    "changes",
    [
        {"place_name": 1},
        {"place_name": ["家の 1 階"]},
        {"current_tile": {"x": 1.0, "y": 2}},
        {"current_tile": {"x": 1, "y": True}},
        {"current_tile": {"x": "1", "y": 2}},
        {"current_tile": {"x": 1}},
        {"current_tile": [1, 2]},
        {"current_tile": "1,2"},
        {"current_tile": 1},
    ],
)
def test_wrong_place_types_are_rejected(changes):
    value = {
        **STATE_JSON_WITHOUT_OPTIONAL_KEYS,
        "place_name": "家の 1 階",
        "current_tile": {"x": 1, "y": 2},
    }
    assert middle_state_from_json(value).current_tile == TilePosition(1, 2)
    with pytest.raises(ValueError):
        middle_state_from_json({**value, **changes})


def test_output_and_observation_tiles_have_defaults():
    # フェイクやテストのように、座標を書かない出力と観察の既定値は None
    output = MiddleOutput(
        scene_name=None,
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        new_tasks=(),
        cancel_task_ids=(),
    )
    assert output.current_tile is None
    observation = MapObservation(
        kind=ObservationKind.EXIT, place="家の 1 階", description="扉"
    )
    assert observation.tile is None


# --- HOME を押すか (press_home) ---


def test_press_home_defaults_to_false():
    assert STATE.press_home is False
    output = MiddleOutput(
        scene_name=None,
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.5,
        timeline="",
        new_tasks=(),
        cancel_task_ids=(),
    )
    assert output.press_home is False


@pytest.mark.parametrize("press_home", [True, False])
def test_press_home_is_written_and_read(press_home):
    state = dataclasses.replace(STATE, press_home=press_home)
    value = middle_state_to_json(state)
    assert value["press_home"] is press_home
    assert middle_state_from_json(value) == state


def test_missing_press_home_is_read_as_false():
    value = middle_state_to_json(dataclasses.replace(STATE, press_home=True))
    del value["press_home"]
    assert middle_state_from_json(value).press_home is False
    assert middle_state_from_json(STATE_JSON_WITHOUT_OPTIONAL_KEYS).press_home is False


@pytest.mark.parametrize("press_home", ["true", 1, None])
def test_wrong_press_home_type_is_rejected(press_home):
    value = {**STATE_JSON_WITHOUT_OPTIONAL_KEYS, "press_home": press_home}
    with pytest.raises(ValueError):
        middle_state_from_json(value)
