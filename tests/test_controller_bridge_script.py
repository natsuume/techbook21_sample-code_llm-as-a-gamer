from pathlib import Path

from switch2_remote.controller_bridge import BRIDGE_SCRIPT


def test_lines_with_non_ascii_characters_end_with_ascii_character():
    """ASCII 以外の文字を含む行は、行末 (改行を除く) の文字を ASCII にする。

    Windows PowerShell 5.1 は BOM なし UTF-8 のスクリプトを CP932 として読む。
    日本語の直後に改行があると、改行がマルチバイト文字の 2 バイト目として読まれ、次の行がコメントに取り込まれるおそれがある。
    """
    text = Path(BRIDGE_SCRIPT).read_text(encoding="utf-8")
    offending = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if not line.isascii() and not line[-1].isascii()
    ]
    assert offending == []
