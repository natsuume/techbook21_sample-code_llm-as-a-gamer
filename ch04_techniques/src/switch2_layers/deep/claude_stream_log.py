"""claude -p の stream-json の出力の記録 (deep/<種類>-<連番>/stream.jsonl) と、終わった後の結果 (result.json)。

stream-json の出力は、標準出力の 1 行 1 件の JSON。assistant の行 (モデルの文とツールの呼び出し)、user の行
(ツールの結果) などが続き、最後に type が "result" の行 (結果の文 result・費用 total_cost_usd・is_error・
permission_denials など) が届く。SIGTERM で止めると result の行は出ない
(https://code.claude.com/docs/en/headless、2026-11-15 に確認)。

stream.jsonl の 1 行の形 (届いた行 1 つにつき 1 行):
    {"elapsed_s": <起動からその行を読むまでの秒 (小数第 3 位まで)>, "data": <行を JSON として読んだもの>}
    {"elapsed_s": <同じ>, "raw": <JSON として読めなかった行 (末尾の改行を除く)>}

result.json の形 (build_result_record):
    {"returncode", "duration_s", "cost_usd", "last_message", "is_error", "permission_denials", "stopped", "killed",
     "error", "stderr"}
"""

from pathlib import Path

STREAM_FILE = "stream.jsonl"
RESULT_FILE = "result.json"
# stream-json の最後の行 (実行の結果) の type
RESULT_MESSAGE_TYPE = "result"
# モデルの応答の行の type
ASSISTANT_MESSAGE_TYPE = "assistant"


def stream_line_record(line: str, elapsed_s: float) -> dict[str, object]:
    """届いた 1 行 line を、stream.jsonl の 1 行の辞書にする。

    line の末尾の改行 (\\n と \\r) を除いた文字列を JSON として読めたら {"elapsed_s": elapsed_s, "data": 読んだ値}、
    読めなければ {"elapsed_s": elapsed_s, "raw": 末尾の改行を除いた文字列} を返す。elapsed_s は小数第 3 位に丸める。
    読んだ値が object でなくても data に入れる。
    """
    raise NotImplementedError


def is_result_message(data: object) -> bool:
    """data が object で、その type が RESULT_MESSAGE_TYPE ("result") なら True を返す。"""
    raise NotImplementedError


def assistant_text(data: object) -> str | None:
    """data が assistant の行なら、その文を返す。そうでなければ None。

    assistant の行は {"type": "assistant", "message": {"content": [{"type": "text", "text": ...}, ...]}} の形。
    content のうち type が "text" の要素の text を、出てきた順に改行でつないだものを返す。text の要素が無ければ
    (ツールの呼び出しだけの行など) None。形が合わない行も None。
    """
    raise NotImplementedError


class ClaudeStreamLog:
    """stream-json の行を stream.jsonl に 1 行ずつ追記し、最後の result の行と、最後の assistant の文を覚える。

    path   stream.jsonl のパス (親のディレクトリーは呼び出し側が作る)。作るときにファイルを空にして作る
    1 つのスレッド (標準出力を読むスレッド) からだけ呼ぶ。
    """

    def __init__(self, path: Path) -> None:
        """path を空のファイルとして作る (既にあれば中身を消す)。作れなければ OSError をそのまま出す。"""
        raise NotImplementedError

    def add_line(self, line: str, elapsed_s: float) -> None:
        """stream_line_record(line, elapsed_s) を 1 行追記して flush し、result の行と assistant の文を覚える。

        - data が result の行 (is_result_message) なら、result_message をその値にする
        - data が assistant の行で文がある (assistant_text が None でない) なら、last_assistant_text をその文にする
        書き込みが OSError で失敗したら、以後は書かずに覚えるだけにし、write_error に最初の失敗の説明を入れる
        (例外は出さない。記録の失敗で claude の出力を読むのを止めないため)。
        """
        raise NotImplementedError

    @property
    def result_message(self) -> dict[str, object] | None:
        """最後に届いた result の行。まだ届いていなければ None。"""
        raise NotImplementedError

    @property
    def last_assistant_text(self) -> str | None:
        """最後に届いた、文を持つ assistant の行の文。まだ無ければ None。"""
        raise NotImplementedError

    @property
    def write_error(self) -> str | None:
        """stream.jsonl への最初の書き込みの失敗の説明。失敗していなければ None。"""
        raise NotImplementedError

    def close(self) -> None:
        """ファイルを閉じる。2 回目以降の呼び出しと、閉じる失敗 (OSError) は無視する。"""
        raise NotImplementedError


def build_result_record(
    returncode: int | None,
    duration_s: float,
    result_message: dict[str, object] | None,
    last_assistant_text: str | None,
    stopped: bool,
    killed: bool,
    error: str | None,
    stderr: str,
) -> dict[str, object]:
    """result.json に書く辞書を返す。キーは次の順:

    returncode           子プロセスの終了コード。起動できなかったときは None
    duration_s           起動から終わるまでの秒 (小数第 3 位に丸める)
    cost_usd             result_message の total_cost_usd (数でなければ None。result の行が無ければ None)
    last_message         最後のメッセージ。result_message の result が文字列ならそれ、そうでなければ
                         last_assistant_text (無ければ None)
    is_error             result_message の is_error (真偽でなければ None。result の行が無ければ None)
    permission_denials   result_message の permission_denials (リストでなければ [])
    stopped              stop か kill で止めたら True
    killed               kill したら True
    error                起動できなかったなど、claude の出力の外の失敗の説明。無ければ None
    stderr               標準エラー出力 (無ければ空文字列)
    """
    raise NotImplementedError


def permission_denials_of(result_message: dict[str, object] | None) -> list[object]:
    """result_message の permission_denials がリストならそれを、そうでなければ (result の行が無い場合を含む) [] を返す。"""
    raise NotImplementedError
