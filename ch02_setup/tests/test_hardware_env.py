import pytest

from switch2_remote.hardware_env import (
    COM_PORT_ENV,
    DEVICE_FPS_ENV,
    com_port_from_env,
    device_fps_from_env,
)


def test_environment_variable_names():
    # .env.sample と README に書く名前と同じ
    assert COM_PORT_ENV == "SWITCH2_COM_PORT"
    assert DEVICE_FPS_ENV == "SWITCH2_DEVICE_FPS"


# --- COM ポート (SWITCH2_COM_PORT) ---


@pytest.mark.parametrize(
    "environ",
    [
        {},
        # .env.sample の空の行をそのまま写したときの値
        {"SWITCH2_COM_PORT": ""},
        # ほかの変数の誤りは COM ポートに関係しない
        {"SWITCH2_DEVICE_FPS": "45"},
    ],
)
def test_com_port_without_value_is_com3(environ):
    assert com_port_from_env(environ) == "COM3"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("COM3", "COM3"),
        ("COM4", "COM4"),
        ("com4", "COM4"),
        ("Com4", "COM4"),
        ("COM10", "COM10"),
    ],
)
def test_com_port_from_environ_in_upper_case(value, expected):
    assert com_port_from_env({"SWITCH2_COM_PORT": value}) == expected


# 起動引数 --com-port と同じく、COM<番号> の形だけを受け付ける。空文字列だけを無いのと同じに扱い、空白は除かない
INVALID_COM_PORTS = [
    "COM0",
    "COM04",
    "COM",
    "4",
    "-PortName",
    "COM4;",
    "COM 4",
    " COM4",
    "COM4\n",
    " ",
]


@pytest.mark.parametrize("value", INVALID_COM_PORTS)
def test_invalid_com_port_is_an_error_with_the_variable_name(value):
    with pytest.raises(ValueError) as excinfo:
        com_port_from_env({"SWITCH2_COM_PORT": value})
    assert str(excinfo.value) == f"SWITCH2_COM_PORT must be COM1, COM2, ...: {value!r}"


# --- デバイスから受け取るフレームレート (SWITCH2_DEVICE_FPS) ---


@pytest.mark.parametrize(
    "environ",
    [
        {},
        # .env.sample の空の行をそのまま写したときの値
        {"SWITCH2_DEVICE_FPS": ""},
        # ほかの変数の誤りはフレームレートに関係しない
        {"SWITCH2_COM_PORT": "COM0"},
    ],
)
def test_device_fps_without_value_is_60(environ):
    assert device_fps_from_env(environ) == 60


@pytest.mark.parametrize("value, expected", [("30", 30), ("60", 60)])
def test_device_fps_from_environ_as_integer(value, expected):
    device_fps = device_fps_from_env({"SWITCH2_DEVICE_FPS": value})
    assert device_fps == expected
    assert type(device_fps) is int


# "30" か "60" と同じ文字列だけを受け付ける。int にできる値 (" 30"・"030"・"+30"・"3_0"・全角の "３０") も誤り
INVALID_DEVICE_FPS = [
    "45",
    "120",
    "0",
    " 30",
    "30 ",
    "030",
    "30.0",
    "+30",
    "3_0",
    "３０",
    "abc",
    " ",
]


@pytest.mark.parametrize("value", INVALID_DEVICE_FPS)
def test_invalid_device_fps_is_an_error_with_the_variable_name(value):
    with pytest.raises(ValueError) as excinfo:
        device_fps_from_env({"SWITCH2_DEVICE_FPS": value})
    assert str(excinfo.value) == f"SWITCH2_DEVICE_FPS must be one of 30, 60: {value!r}"
