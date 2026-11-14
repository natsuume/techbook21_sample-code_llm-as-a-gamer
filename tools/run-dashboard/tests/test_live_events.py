import copy
import json
import math
from typing import get_args

from run_dashboard.live_events import (
    LIVE_EVENT_KINDS,
    LiveEventKind,
    encode_live_event_data,
    json_compatible,
)


def test_live_event_kinds():
    assert LIVE_EVENT_KINDS == (
        "request",
        "frame",
        "pad",
        "run",
        "note_version",
        "advisor_run",
        "advisor_step",
        "stats",
        "end",
    )
    assert get_args(LiveEventKind) == LIVE_EVENT_KINDS


def test_encoded_data_has_no_spaces_and_escapes_non_ascii():
    # 区切りの空白なし、非 ASCII は \uXXXX のエスケープ。読むと元の値
    data = {"action": "押下 A", "p": [0.5, 1], "nested": {"ok": True, "e": None}}
    text = encode_live_event_data(data)
    assert text == (
        '{"action":"\\u62bc\\u4e0b A","p":[0.5,1],"nested":{"ok":true,"e":null}}'
    )
    assert "\n" not in text
    assert json.loads(text) == data


def test_unpaired_surrogate_is_escaped_and_read_back():
    # 対になっていないサロゲートは \ud800 のエスケープにする (UTF-8 に符号化できる)
    data = {"summary": "a\ud800b"}
    text = encode_live_event_data(data)
    assert "\\ud800" in text
    text.encode("ascii")
    assert json.loads(text) == data


def test_non_finite_numbers_become_null_in_nested_values():
    # NaN・inf・-inf は、入れ子の dict・list・tuple の中も null にする
    data = {
        "nan": math.nan,
        "list": [math.inf, {"deep": -math.inf}, 1.5],
        "tuple": (math.nan, 2.0),
    }
    expected = {
        "nan": None,
        "list": [None, {"deep": None}, 1.5],
        "tuple": [None, 2.0],
    }
    assert json_compatible(data) == expected
    assert json.loads(encode_live_event_data(data)) == expected


def test_other_values_are_kept_and_input_is_not_changed():
    # bool・int・None・文字列はそのまま (True を 1 にしない)。渡した値は変えない
    data = {"flag": True, "count": 1, "none": None, "text": "x", "values": [False, 0]}
    original = copy.deepcopy(data)
    converted = json_compatible(data)
    assert converted == data
    assert converted["flag"] is True
    assert converted["values"][0] is False
    assert isinstance(converted["count"], int)
    assert data == original
    assert encode_live_event_data(data) == (
        '{"flag":true,"count":1,"none":null,"text":"x","values":[false,0]}'
    )
    nested = {"x": [math.nan, {"y": math.inf}]}
    json_compatible(nested)
    assert math.isnan(nested["x"][0])
    assert nested["x"][1]["y"] == math.inf
