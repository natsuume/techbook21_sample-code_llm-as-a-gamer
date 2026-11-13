"""claude -p の stream-json の出力を、届いた時刻つきで 1 行ずつ stream.jsonl に書く。

--record-dashboard を付けたとき、advisor は claude -p を `--output-format stream-json --verbose` で動かす。
標準出力には 1 行 1 件の JSON が届く。ツール呼び出しは assistant の tool_use、ツールの結果は user の tool_result で、
最後の行が result になる。SIGTERM で止めると result の行は出ない
(https://code.claude.com/docs/en/headless、2026-11-13 に確認)。

stream.jsonl の 1 行の形 (届いた行 1 つにつき 1 行。UTF-8):
    {"mono": <行を読んだ時刻 (秒。advisor の clock、既定は time.monotonic)>, "data": <行を JSON として読んだもの>}
    {"mono": <同じ>, "raw": <JSON として読めなかった行 (末尾の改行を除く)>}

読み取りのスレッド: 標準出力は 1 行ずつ、標準エラー出力は最後までまとめて、それぞれ専用の daemon スレッドで読む
(ProcessOutputReader)。読み取りスレッドが readline で止まっている間に、別のスレッドからパイプを close すると、
close も止まる (claude の子孫のプロセスがパイプを持ち続ける場合。Python 3.13.5 で確認)。そのため、読み終わらない
読み取りスレッドはパイプを閉じずに手放し、StreamJsonLog.stop で以後に届いた行を書かないようにする。
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol, TextIO

STREAM_FILE_NAME = "stream.jsonl"
# stream-json の最後の行 (実行の結果) の type
RESULT_MESSAGE_TYPE = "result"


class TextReadable(Protocol):
    """最後まで読めるテキストのストリーム (Popen の stderr など)。"""

    def read(self) -> str: ...


def build_stream_line_record(line: str, mono: float) -> dict:
    """届いた 1 行 line を、stream.jsonl の 1 行の辞書にする (純粋な関数)。

    line の末尾の改行 (\\n と \\r) を除いた文字列を JSON として読めたら {"mono": mono, "data": <読んだ値>}、
    読めなければ {"mono": mono, "raw": <末尾の改行を除いた文字列>} を返す。
    読んだ値が object でなくても (配列や数でも) data に入れる。
    """
    raise NotImplementedError


def is_result_message(data: object) -> bool:
    """data が object (dict) で、その type が RESULT_MESSAGE_TYPE ("result") なら True を返す (純粋な関数)。"""
    raise NotImplementedError


def open_for_writing(path: Path) -> TextIO:
    """path を UTF-8 の書き込み用 (上書き) に開く。StreamJsonLog の既定の open_file。

    UTF-8 に符号化できない文字 (行の JSON のエスケープ `\\ud800` などから読んだ、対になっていないサロゲート) は、
    errors="backslashreplace" で `\\ud800` の形のまま書く。JSON の文字列の中では同じエスケープになるため、
    書いた行は JSON として読め、読むと元の文字に戻る。
    """
    return path.open("w", encoding="utf-8", errors="backslashreplace")


class StreamJsonLog:
    """stream.jsonl への書き込みと、最後に届いた result の行の保持。

    path        書き込むファイル (advisor-<連番>/stream.jsonl。親のディレクトリーは呼び出し側が作る)
    clock       行を読んだ時刻を測る時計 (advisor の clock)
    on_error    書き込みの失敗の説明 (switch2_remote.error_message.describe_error) を受け取る関数。1 つの StreamJsonLog に
                つき最大 1 回だけ呼ぶ
    open_file   path を書き込み用に開く関数 (既定 open_for_writing)。テストでは失敗する偽物を渡す

    状態 (lock で守る):
        file          開いているファイル。開けなかったとき、書けなくなったとき、stop の後は None
        last_result   最後に届いた、is_result_message が真の行の data。届いていなければ None
        stopped       stop() を呼んだら True
    """

    def __init__(
        self,
        path: Path,
        clock: Callable[[], float],
        on_error: Callable[[str], None],
        open_file: Callable[[Path], TextIO] = open_for_writing,
    ):
        """属性を入れ、open_file(path) でファイルを開く。

        開けなければ (OSError)、file を None にし、on_error に説明を渡して、記録せずに続ける (例外は出さない)。
        """
        raise NotImplementedError

    def add_line(self, line: str) -> None:
        """届いた 1 行 line (末尾の改行を含んでよい) を記録する。標準出力の読み取りスレッドから呼ぶ。

        clock() の値を mono にして build_stream_line_record で辞書にし、その data が is_result_message なら
        last_result にする (ファイルに書けるかどうかに依らず更新する)。
        stop の前でファイルが開いていれば、辞書を JSON (ensure_ascii=False) の 1 行にして書き、すぐに flush する
        (ほかのプロセスから、書いた行をすぐに読めるようにするため)。
        書けなければ (OSError、ValueError)、ファイルを閉じて file を None にし (閉じる失敗は無視する)、on_error に
        説明を渡す。以後の行は書かない。
        """
        raise NotImplementedError

    @property
    def last_result(self) -> object | None:
        """最後に届いた result の行の data。届いていなければ None。"""
        raise NotImplementedError

    def stop(self) -> None:
        """以後に届いた行を書かないようにし、ファイルを閉じる (閉じる失敗は無視する)。2 回目以降の呼び出しは何もしない。

        stop の後も、add_line は last_result を更新する。
        """
        raise NotImplementedError


class ProcessOutputReader:
    """子プロセスの標準出力と標準エラー出力を、それぞれ裏の daemon スレッドで読む。

    stdout    1 行ずつ読むテキストのストリーム (Popen の stdout)。届いた行 (末尾の改行を含む) を順に on_line に渡す
    stderr    最後まで読むテキストのストリーム (Popen の stderr)
    on_line   標準出力の 1 行を受け取る関数 (StreamJsonLog.add_line)

    読む途中で OSError か ValueError (パイプが閉じられたときなど) が起きたら、そのストリームの読み取りをやめる。
    そこまでに読んだ標準出力の行は on_line に渡し済みで、標準エラー出力はそこまでに読めた分を持たない (None のまま)。
    on_line が例外 (Exception) を出したら、logging で警告を書き、その行を飛ばして標準出力を最後まで読み続ける
    (読むのをやめると、子プロセスがパイプへの書き込みで止まり、実行の上限まで終わらないため)。
    """

    def __init__(
        self,
        stdout: Iterable[str],
        stderr: TextReadable,
        on_line: Callable[[str], None],
    ):
        """属性を入れる。スレッドはまだ起動しない。"""
        raise NotImplementedError

    def start(self) -> None:
        """標準出力と標準エラー出力の読み取りスレッド (daemon) を起動し、終わるのを待たずに戻る。"""
        raise NotImplementedError

    def join(self, timeout: float) -> bool:
        """2 つの読み取りスレッドの終了を、合わせて timeout 秒まで待つ。両方が終わっていれば True を返す。

        timeout が 0 以下なら待たずに、今終わっているかを返す。
        """
        raise NotImplementedError

    @property
    def stderr_text(self) -> str | None:
        """標準エラー出力の全体。読み終わっていないか、読む途中で失敗したら None。"""
        raise NotImplementedError
