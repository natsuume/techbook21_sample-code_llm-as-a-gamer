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


def test_bridge_script_takes_keep_sending_neutral_switch():
    text = read_script(BRIDGE_SCRIPT)
    assert 'param([string]$PortName = "COM3", [switch]$KeepSendingNeutral)' in text
    assert "$sender.KeepSendingNeutral = $KeepSendingNeutral.IsPresent" in text
    # 起動引数で切り替えるため、スクリプト内で値を決め打ちしない
    assert "$KeepSendingNeutral = $false" not in text


def test_radio_script_takes_off_seconds_with_default_five():
    text = read_script(RADIO_SCRIPT)
    assert "param([int]$OffSeconds = 5)" in text
