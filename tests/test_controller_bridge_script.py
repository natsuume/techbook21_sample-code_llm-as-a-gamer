from pathlib import Path

import pytest

from switch2_remote.bluetooth_radio import RADIO_SCRIPT
from switch2_remote.controller_bridge import BRIDGE_SCRIPT

UTF8_WITHOUT_BOM = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)"
TAKE_CONSOLE_OUT = "$out = [Console]::Out"


def read_script(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", [BRIDGE_SCRIPT, RADIO_SCRIPT])
def test_lines_with_non_ascii_characters_end_with_ascii_character(script):
    """ASCII 以外の文字を含む行は、行末 (改行を除く) の文字を ASCII にする。

    Windows PowerShell 5.1 は BOM なし UTF-8 のスクリプトを CP932 として読む。
    日本語の直後に改行があると、改行がマルチバイト文字の 2 バイト目として読まれ、次の行がコメントに取り込まれるおそれがある。
    """
    text = read_script(script)
    offending = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if not line.isascii() and not line[-1].isascii()
    ]
    assert offending == []


@pytest.mark.parametrize("script", [BRIDGE_SCRIPT, RADIO_SCRIPT])
def test_script_writes_utf8_without_bom_before_taking_console_out(script):
    """標準出力を BOM なし UTF-8 にしてから [Console]::Out を取り出す。

    OutputEncoding を設定すると [Console]::Out が作り直されるため、先に取り出した Out は元の文字コードのまま残る。
    BOM が付くと、Python 側で最初の行 (ready / ok) の判定が崩れる。
    """
    text = read_script(script)
    assert UTF8_WITHOUT_BOM in text
    assert TAKE_CONSOLE_OUT in text
    assert text.index(UTF8_WITHOUT_BOM) < text.index(TAKE_CONSOLE_OUT)


@pytest.mark.parametrize("script", [BRIDGE_SCRIPT, RADIO_SCRIPT])
def test_script_continues_when_output_encoding_cannot_be_set(script):
    """OutputEncoding の設定は try / catch で囲み、失敗しても既定の文字コードのまま続ける。

    コンソールの無い環境では設定が例外になることがあり、$ErrorActionPreference = "Stop" のままでは
    ready / ok を出力する前に終わってしまうため。
    """
    text = read_script(script)
    assert f"try {{ {UTF8_WITHOUT_BOM} }} catch {{ }}" in text


def test_radio_script_waits_for_winrt_operations_with_time_limit():
    """WinRT の非同期の待ちに上限を設け、無期限には待たない。

    bluetooth_radio.py は RADIO_RESET_TIMEOUT でプロセスを止めるため、止められる前に
    無線をオンに戻す処理 (finally) まで進めるようにする。
    """
    text = read_script(RADIO_SCRIPT)
    assert "$task.Wait(-1)" not in text
    assert "$task.Wait($AsyncTimeoutSeconds * 1000)" in text


def test_radio_script_turns_radio_on_even_if_off_request_fails():
    """オフの要求は try の中で行い、期限切れや例外の後もオンに戻す処理に進む。

    オフの要求が期限切れになっても、無線がオフになっている可能性があるため。
    オンを試さないのは、オフの要求が拒否された ($turnOn = $false) ときだけ。
    """
    text = read_script(RADIO_SCRIPT)
    off_request = text.index("SetStateAsync([Windows.Devices.Radios.RadioState]::Off)")
    guard = text.rindex("try {", 0, off_request)
    turn_on = text.index("if ($turnOn) {")
    assert text.index("catch {", off_request) < turn_on
    assert guard < off_request < turn_on
    assert text.count("$turnOn = $false") == 1


def test_bridge_script_takes_keep_sending_neutral_switch():
    text = read_script(BRIDGE_SCRIPT)
    assert 'param([string]$PortName = "COM3", [switch]$KeepSendingNeutral)' in text
    assert "$sender.KeepSendingNeutral = $KeepSendingNeutral.IsPresent" in text
    # 起動引数で切り替えるため、スクリプト内で値を決め打ちしない
    assert "$KeepSendingNeutral = $false" not in text


def test_radio_script_takes_off_seconds_with_default_five():
    text = read_script(RADIO_SCRIPT)
    assert "param([int]$OffSeconds = 5)" in text
