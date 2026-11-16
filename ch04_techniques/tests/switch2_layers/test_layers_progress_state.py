import json
import math

import pytest

from switch2_layers.progress.progress_state import (
    ProgressState,
    progress_state_from_json,
    progress_state_to_json,
    read_progress_state,
    write_progress_state,
)

STATE = ProgressState(t=85.2, static_s=12.4, no_novel_s=85.0, window_s=300.0)
ROW = {"t": 85.2, "static_s": 12.4, "no_novel_s": 85.0, "window_s": 300.0}


def test_to_json_has_the_keys_in_order():
    value = progress_state_to_json(STATE)
    assert list(value) == ["t", "static_s", "no_novel_s", "window_s"]
    assert value == ROW


def test_to_json_rounds_to_milliseconds():
    value = progress_state_to_json(
        ProgressState(t=0.1 + 0.2, static_s=1.23456, no_novel_s=2.00049, window_s=300.0)
    )
    assert value == {"t": 0.3, "static_s": 1.235, "no_novel_s": 2.0, "window_s": 300.0}


def test_from_json_reads_the_four_values():
    assert progress_state_from_json(ROW) == STATE
    # 整数も数として読み、ほかのキーは無視する
    assert progress_state_from_json(
        {"t": 3, "static_s": 0, "no_novel_s": 1, "window_s": 300, "other": "x"}
    ) == ProgressState(3.0, 0.0, 1.0, 300.0)


@pytest.mark.parametrize(
    "value",
    [
        [],
        "state",
        {"t": 1.0, "static_s": 0.0, "no_novel_s": 0.0},
        {**ROW, "t": None},
        {**ROW, "static_s": "12.4"},
        {**ROW, "no_novel_s": True},
        {**ROW, "window_s": math.inf},
        {**ROW, "t": math.nan},
    ],
)
def test_from_json_rejects_broken_values(value):
    with pytest.raises(ValueError):
        progress_state_from_json(value)


def test_write_and_read(tmp_path):
    path = tmp_path / "state.json"
    write_progress_state(path, STATE)
    assert json.loads(path.read_text(encoding="utf-8")) == ROW
    assert read_progress_state(path) == STATE
    # 置き換えの書き込みの一時ファイルを残さない
    assert [child.name for child in tmp_path.iterdir()] == ["state.json"]


def test_write_does_not_create_the_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_progress_state(tmp_path / "progress" / "state.json", STATE)


def test_read_returns_none_without_values(tmp_path):
    path = tmp_path / "state.json"
    # 無い
    assert read_progress_state(path) is None
    # JSON として壊れている
    path.write_text("{broken", encoding="utf-8")
    assert read_progress_state(path) is None
    # 形に合わない
    path.write_text(json.dumps({"t": 1.0}), encoding="utf-8")
    assert read_progress_state(path) is None
    # UTF-8 として読めない
    path.write_bytes(b"\xff\xfe")
    assert read_progress_state(path) is None
    # ディレクトリー
    directory = tmp_path / "directory"
    directory.mkdir()
    assert read_progress_state(directory) is None
