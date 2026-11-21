"""PC ごとに違う機材の値 (ブリッジが開く COM ポートと、デバイスから受け取るフレームレート) を環境変数から読む。

switch2-remote-daemon と、第4章の switch2-agent・switch2-layers run は、起動引数 --com-port・--device-fps を
省いたときの既定値を、この環境変数から決める。起動引数を指定したときは、環境変数を読まない。
    SWITCH2_COM_PORT     ブリッジが開く COM ポート (COM1、COM2 のような COM と 1 以上の整数。大文字小文字を区別しない)
    SWITCH2_DEVICE_FPS   デバイスから受け取るフレームレート (30 か 60)
変数が無いか空文字列なら、それぞれの既定値 (COM3・60) にする。空文字列を無いのと同じに扱うのは、
.env.sample の SWITCH2_COM_PORT= のような空の行をそのまま写しても既定値になるようにするため。
値の誤りは、変数名を含むメッセージの ValueError にする (呼び出し側は起動引数の誤りと同じく扱う)。
"""

from collections.abc import Mapping

from switch2_remote.capture import DEFAULT_DEVICE_FPS, DEVICE_FPS_CHOICES
from switch2_remote.controller_bridge import DEFAULT_COM_PORT, is_valid_com_port

COM_PORT_ENV = "SWITCH2_COM_PORT"
DEVICE_FPS_ENV = "SWITCH2_DEVICE_FPS"


def com_port_from_env(environ: Mapping[str, str]) -> str:
    """environ の SWITCH2_COM_PORT から、ブリッジが開く COM ポートの名前を返す。

    変数が無いか空文字列なら DEFAULT_COM_PORT (COM3) を返す。値が controller_bridge.is_valid_com_port の形なら
    大文字にして返す (com4 → COM4)。前後の空白は除かない。
    そうでなければ ValueError(f"{COM_PORT_ENV} must be COM1, COM2, ...: {value!r}") を送出する。
    """
    value = environ.get(COM_PORT_ENV, "")
    if value == "":
        return DEFAULT_COM_PORT
    if not is_valid_com_port(value):
        raise ValueError(f"{COM_PORT_ENV} must be COM1, COM2, ...: {value!r}")
    return value.upper()


def device_fps_from_env(environ: Mapping[str, str]) -> int:
    """environ の SWITCH2_DEVICE_FPS から、デバイスから受け取るフレームレートを返す。

    変数が無いか空文字列なら DEFAULT_DEVICE_FPS (60) を返す。値の文字列が DEVICE_FPS_CHOICES の
    どれかの 10 進の表記 ("30" か "60") と同じなら int にして返す。前後の空白を含む値や "030"・"30.0" などは、
    int にできても誤りにする。誤りは ValueError(f"{DEVICE_FPS_ENV} must be one of 30, 60: {value!r}") を
    送出する (選択肢の "30, 60" は DEVICE_FPS_CHOICES から作る)。
    """
    value = environ.get(DEVICE_FPS_ENV, "")
    if value == "":
        return DEFAULT_DEVICE_FPS
    # int() は " 30"・"+30"・"3_0"・全角の数字も受け付けるため、選択肢の表記と文字列のまま比べる
    for device_fps in DEVICE_FPS_CHOICES:
        if value == str(device_fps):
            return device_fps
    choices = ", ".join(str(fps) for fps in DEVICE_FPS_CHOICES)
    raise ValueError(f"{DEVICE_FPS_ENV} must be one of {choices}: {value!r}")
