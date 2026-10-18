import pytest

from switch2_remote.controller_state import (
    NEUTRAL_REPORT,
    PadState,
    add_to_held,
    build_report,
    dpad_to_hat,
    format_holds,
    format_state,
    join_chords,
    merge,
    merge_all,
    parse_chord,
    parse_stick_spec,
    parse_up_targets,
    remove_targets,
    stick_axis_to_byte,
)


def pad(*buttons: str, **sticks: tuple[float, float]) -> PadState:
    """ボタン名とスティック (LS=(x, y) など) から PadState を作る。"""
    return PadState(frozenset(buttons), dict(sticks))


def report(b1=0, b2=0, hat=15, lx=128, ly=128, rx=128, ry=128) -> bytes:
    return bytes([b1, b2, hat, lx, ly, rx, ry, 0])


# --- CHORD の解釈 ---


def test_parse_chord_uppercases_specs():
    assert parse_chord("a+Right") == pad("A", "RIGHT")


def test_parse_chord_accepts_single_button():
    assert parse_chord("home") == pad("HOME")


def test_parse_chord_allows_duplicate_button():
    assert parse_chord("A+a") == pad("A")


def test_parse_chord_parses_sticks():
    assert parse_chord("b+ls:0.5,-1+RS:0,-0.25") == pad(
        "B", LS=(0.5, -1.0), RS=(0.0, -0.25)
    )


@pytest.mark.parametrize(
    "text",
    [
        "",  # 空の CHORD
        "A+",  # 末尾が空の要素
        "+A",  # 先頭が空の要素
        "A++B",  # 途中が空の要素
        "FOO",  # 未知の SPEC
        "LR",  # 未知の SPEC (L と R の同時押しは L+R と書く)
        "A+FOO",
        "LS:0,1+LS:1,0",  # 同じスティックを 2 回含む
        "UP+DOWN",  # 十字キーの相反する方向
        "LEFT+RIGHT",
        "UP+LEFT+RIGHT",
    ],
)
def test_parse_chord_rejects_invalid_chord(text):
    with pytest.raises(ValueError):
        parse_chord(text)


def test_parse_chord_allows_diagonal_dpad():
    assert parse_chord("UP+RIGHT") == pad("UP", "RIGHT")


# --- スティックの解釈 ---


def test_parse_stick_spec_returns_name_and_values():
    assert parse_stick_spec("LS:0.5,-1") == ("LS", 0.5, -1.0)
    assert parse_stick_spec("RS:-1.0,1.0") == ("RS", -1.0, 1.0)


@pytest.mark.parametrize(
    "spec",
    [
        "LS:1.5,0",  # 範囲外
        "LS:0,-1.01",
        "RS:2,2",
        "LS:+1,0",  # 先頭の +
        "LS:1e0,0",  # 指数表記
        "LS:nan,0",
        "LS:inf,0",
        "LS:.5,0",  # 整数部の無い小数
        "LS:1.,0",  # 小数部の無い小数
        "LS:1",  # 値が 1 つ
        "LS:1,0,0",  # 値が 3 つ
        "LS:1, 0",
        "XS:0,0",  # 未知のスティック
    ],
)
def test_parse_stick_spec_rejects_invalid_format_or_range(spec):
    with pytest.raises(ValueError):
        parse_stick_spec(spec)


@pytest.mark.parametrize("text", ["LS:1.5,0", "LS:+1,0", "LS:1e0,0", "ls:nan,0"])
def test_parse_chord_rejects_invalid_stick(text):
    with pytest.raises(ValueError):
        parse_chord(text)


# --- レポート ---


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.0, 128),
        (1.0, 255),
        (-1.0, 0),
        (0.5, 192),  # 128 + 63.5 = 191.5
        (-0.5, 64),
        (-0.01171875, 126),  # 128 - 1.5 = 126.5 は偶数丸めで 126
    ],
)
def test_stick_axis_to_byte(value, expected):
    assert stick_axis_to_byte(value) == expected


@pytest.mark.parametrize(
    "directions, hat",
    [
        ([], 15),
        (["UP"], 0),
        (["UP", "RIGHT"], 1),
        (["RIGHT"], 2),
        (["DOWN", "RIGHT"], 3),
        (["DOWN"], 4),
        (["DOWN", "LEFT"], 5),
        (["LEFT"], 6),
        (["UP", "LEFT"], 7),
    ],
)
def test_dpad_to_hat(directions, hat):
    assert dpad_to_hat(frozenset(directions)) == hat


@pytest.mark.parametrize("directions", [["UP", "DOWN"], ["LEFT", "RIGHT"]])
def test_dpad_to_hat_rejects_conflicting_directions(directions):
    with pytest.raises(ValueError):
        dpad_to_hat(frozenset(directions))


@pytest.mark.parametrize(
    "state, expected",
    [
        (pad(), report()),
        (pad("A"), report(b1=0x04)),
        (pad("L", "R"), report(b1=0x30)),
        (pad("Y", "B", "X", "ZL", "ZR"), report(b1=0xCB)),
        (pad("MINUS", "PLUS", "HOME"), report(b2=0x13)),
        (pad("UP", "RIGHT"), report(hat=1)),
        (pad("DOWN"), report(hat=4)),
        (pad(LS=(1.0, 0.0)), report(lx=255, ly=128)),
        (pad(LS=(0.0, 1.0)), report(ly=0)),
        (pad(LS=(-1.0, -1.0)), report(lx=0, ly=255)),
        (pad(RS=(0.5, 0.5)), report(rx=192, ry=64)),
        (
            pad("A", "LEFT", LS=(0.0, 1.0), RS=(-1.0, 0.0)),
            report(b1=0x04, hat=6, ly=0, rx=0),
        ),
    ],
)
def test_build_report(state, expected):
    assert build_report(state) == expected


def test_neutral_report_bytes():
    assert NEUTRAL_REPORT == bytes.fromhex("00000f8080808000")
    assert build_report(pad()) == NEUTRAL_REPORT


# --- 状態の合成と衝突 ---


def test_merge_adds_chord_to_base():
    assert merge(pad("L", LS=(0.0, 1.0)), pad("A", RS=(1.0, 0.0))) == pad(
        "L", "A", LS=(0.0, 1.0), RS=(1.0, 0.0)
    )


def test_merge_allows_diagonal_dpad():
    assert merge(pad("UP"), pad("RIGHT")) == pad("UP", "RIGHT")


@pytest.mark.parametrize(
    "base, chord",
    [
        (pad("A"), pad("A")),  # 既に押されているボタン
        (pad("A", "B"), pad("X", "B")),
        (pad(LS=(0.0, 1.0)), pad(LS=(1.0, 0.0))),  # 既に倒されているスティック
        (pad("UP"), pad("DOWN")),  # 合成後の十字キーが相反する
        (pad("RIGHT"), pad("LEFT")),
    ],
)
def test_merge_rejects_collision(base, chord):
    with pytest.raises(ValueError):
        merge(base, chord)


def test_merge_all_combines_layers():
    layers = [pad("L"), pad("B", "RIGHT"), pad(LS=(0.5, -1.0))]
    assert merge_all(layers) == pad("L", "B", "RIGHT", LS=(0.5, -1.0))


def test_merge_all_of_nothing_is_empty():
    assert merge_all([]) == pad()


def test_join_chords_allows_duplicate_buttons():
    assert join_chords([pad("A"), pad("A", LS=(0.0, 1.0))]) == pad("A", LS=(0.0, 1.0))


@pytest.mark.parametrize(
    "chords",
    [
        [pad(LS=(0.0, 1.0)), pad(LS=(1.0, 0.0))],  # 同じスティック
        [pad("UP"), pad("DOWN")],  # 十字キーの相反する方向
    ],
)
def test_join_chords_rejects_invalid_combination(chords):
    with pytest.raises(ValueError):
        join_chords(chords)


# --- down の規則 ---


def test_add_to_held_is_idempotent_for_buttons():
    assert add_to_held(pad("A"), pad(), pad("A")) == pad("A")


def test_add_to_held_adds_new_buttons():
    assert add_to_held(pad("A"), pad("B"), pad("X")) == pad("A", "X")


def test_add_to_held_replaces_stick_value():
    held = pad("A", LS=(1.0, 0.0))
    assert add_to_held(held, pad(), pad(LS=(0.0, 1.0))) == pad("A", LS=(0.0, 1.0))


@pytest.mark.parametrize(
    "held, holds, chord",
    [
        (pad(), pad("B"), pad("B")),  # holds にあるボタン
        (pad(), pad("B", "RIGHT"), pad("A", "RIGHT")),
        (pad(), pad(LS=(0.0, 1.0)), pad(LS=(1.0, 0.0))),  # holds にあるスティック
        (pad("UP"), pad(), pad("DOWN")),  # held と十字キーが相反する
        (pad(), pad("LEFT"), pad("RIGHT")),  # holds と十字キーが相反する
    ],
)
def test_add_to_held_rejects_conflict(held, holds, chord):
    with pytest.raises(ValueError):
        add_to_held(held, holds, chord)


# --- up の TARGET ---


def test_parse_up_targets_uppercases_and_splits_plus():
    assert parse_up_targets(["a+ls", "B", "rs"]) == frozenset({"A", "LS", "B", "RS"})


@pytest.mark.parametrize(
    "texts",
    [
        [""],
        ["A+"],
        ["+A"],
        ["FOO"],
        ["LR"],
        ["LS:0,1"],  # スティックは名前だけで指定する
        ["ALL"],  # up ALL は commands が振り分ける
        ["A", "ALL"],
    ],
)
def test_parse_up_targets_rejects_unknown_or_empty(texts):
    with pytest.raises(ValueError):
        parse_up_targets(texts)


def test_remove_targets_removes_buttons_and_sticks():
    state = pad("A", "B", LS=(0.0, 1.0), RS=(1.0, 0.0))
    assert remove_targets(state, frozenset({"A", "LS"})) == pad("B", RS=(1.0, 0.0))


def test_remove_targets_ignores_names_not_in_state():
    assert remove_targets(pad("A"), frozenset({"B", "RS"})) == pad("A")


def test_pad_state_names_and_is_empty():
    assert pad("A", LS=(0.0, 1.0)).names() == frozenset({"A", "LS"})
    assert pad().is_empty()
    assert not pad(RS=(0.0, 0.0)).is_empty()


# --- 正規化表記 ---


def test_format_state_of_empty_state_is_none():
    assert format_state(pad()) == "none"


def test_format_state_orders_buttons_then_sticks():
    state = pad("LEFT", "HOME", "ZR", "A", RS=(0.0, 0.25), LS=(0.5, -1.0))
    assert format_state(state) == "A+ZR+HOME+LEFT+LS:0.5,-1+RS:0,0.25"


def test_format_state_orders_dpad_by_fixed_order():
    assert format_state(pad("RIGHT", "B", "UP")) == "B+UP+RIGHT"


@pytest.mark.parametrize(
    "value, expected",
    [(1.0, "1"), (-1.0, "-1"), (0.0, "0"), (-0.0, "0"), (0.00001, "0.00001")],
)
def test_format_state_writes_stick_values_in_decimal(value, expected):
    assert format_state(pad(LS=(value, 0.5))) == f"LS:{expected},0.5"


def test_format_state_of_stick_can_be_parsed_again():
    state = pad(LS=(0.00001, -0.0000001))
    assert parse_chord(format_state(state)) == pad(LS=(0.00001, 0.0))


def test_format_holds():
    assert format_holds([]) == "none"
    assert format_holds([(pad("RIGHT", "B"), 1200), (pad("A"), 5)]) == (
        "B+RIGHT:1200ms,A:5ms"
    )
