"""主ループの記録 orchestrator/sessions.jsonl・orchestrator/turns.jsonl の行の形と書き込み。

セッションごとの stream-json の出力 (orchestrator/session-<NNN>/stream.jsonl) は、orchestrator.claude_stream_session が
書く。時刻はすべて実行の経過秒 t (run_clock.RunClock.now の値)。

orchestrator/sessions.jsonl の行 (キーはこの順):
    開始       {"t", "event": "start", "session": 番号, "stream": stream.jsonl の実行ディレクトリーからの相対パス}
    終了       {"t", "event": "end", "session", "reason": SessionEndReason の値, "usage_tokens": 最後の使用量,
                "returncode": 終了コード (起動できなかった・終わらなかったときは null), "completed_turns": result を
                受け取ったターンの数 (引き継ぎの依頼を含む), "error": 起動できなかったときの説明 (無ければ null)}
    要約       {"t", "event": "compact", "session", "metadata": compact_boundary の行の compact_metadata (無ければ null)}
orchestrator/turns.jsonl の行 (1 ターンに 1 行。result を受け取ったとき、またはターンを終えずにセッションを終えたときに
書く。キーはこの順):
    {"session", "turn": セッションの中の番号 (1 始まり), "kind": TurnKind の値, "t_sent", "t_result": result を受け取った
     時刻 (受け取らなかったら null), "duration_s": t_result - t_sent (小数第 3 位に丸める。受け取らなかったら null),
     "input_text": 送ったテキストのブロックの文 (画像のラベルは除く), "images": 送った画像の実行ディレクトリーからの
     相対パス (古い順), "response_json": 最後の assistant の文の、最後の ```json のブロックの中身 (無ければ null),
     "errors": parse_decision の誤りの説明のリスト (誤りが無い・読まなかったら null), "outcome": TurnOutcome の値,
     "usage_tokens": result を受け取った時点 (受け取らなかったら、ターンを終えた時点) のセッションの使用量}
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# sessions.jsonl の event
SESSION_EVENT_START = "start"
SESSION_EVENT_END = "end"
SESSION_EVENT_COMPACT = "compact"
# 応答の本文の ```json のブロックの開きの行 (行全体。前後の空白を許す)
JSON_FENCE_OPEN_PATTERN = re.compile(r"[ \t]*```json[ \t]*")
# ブロックの閉じの行 (言語名の付かない、フェンスだけの行。前後の空白を許す)
FENCE_CLOSE_PATTERN = re.compile(r"[ \t]*```[ \t]*")


class SessionEndReason(StrEnum):
    """セッションを終えた理由 (sessions.jsonl の終了の行の reason)。"""

    ROTATE = "rotate"  # 使用量がしきい値を超え、引き継ぎの依頼の後に終えた
    TIMEOUT = "timeout"  # 1 ターンが --turn-timeout 秒を超えた
    EXIT = "exit"  # 入れ替えの途中ではないのに、子プロセスが終わった (起動できなかったときを含む)
    CLOSE = "close"  # 停止の合図、または主ループが実行全体を止めた


class TurnKind(StrEnum):
    """ターンの種類 (turns.jsonl の kind)。"""

    TURN = "turn"  # 画面と出来事を渡すふつうのターン (セッションの最初の入力を含む)
    HANDOFF = "handoff"  # 引き継ぎの依頼


class TurnOutcome(StrEnum):
    """ターンの終わり方 (turns.jsonl の outcome)。"""

    APPLIED = "applied"  # 誤りの無い JSON を読み、apply した
    INVALID = "invalid"  # JSON が誤っていた (errors に説明)
    HANDOFF = "handoff"  # 引き継ぎの依頼の result を受け取った (JSON は読まない)
    TIMEOUT = "timeout"  # --turn-timeout 秒たっても result が届かなかった
    EXITED = "exited"  # result が届く前に子プロセスが終わった
    STOPPED = "stopped"  # result が届く前に停止の合図が立った


@dataclass(frozen=True)
class TurnRecord:
    """turns.jsonl の 1 行の中身 (モジュールの docstring の各キーと同じ名前)。"""

    session: int
    turn: int
    kind: TurnKind
    t_sent: float
    t_result: float | None
    input_text: str
    images: tuple[str, ...]
    response_json: str | None
    errors: tuple[str, ...] | None
    outcome: TurnOutcome
    usage_tokens: int


def last_json_block(text: str | None) -> str | None:
    """text の最後の、閉じた ```json のブロックの中身を返す。text が None か、閉じたブロックが無ければ None。

    text を行に分け (\\n と \\r\\n)、前から次の規則で読む:
        - JSON_FENCE_OPEN_PATTERN に fullmatch する行で、ブロックを開く。開いている間に同じ形の行が現れたら、
          そこから数え直す (前の開きの行と、その後の行は捨てる)
        - 開いている間に FENCE_CLOSE_PATTERN に fullmatch する行が現れたら、そのブロックを閉じる。中身は開きの行と
          閉じの行の間の行を \\n でつないだもの。```text のように言語名の付いた行は閉じの行にしない (中身に含める)
        - 最後まで閉じなかったブロックは使わない
    閉じたブロックのうち最後のものの中身を返す。記録 (turns.jsonl の response_json) のためだけに使い、JSON として
    読めるかは確かめない。
    """
    raise NotImplementedError


def session_start_row(t: float, session: int, stream: str) -> dict[str, object]:
    """sessions.jsonl の開始の行 {"t", "event": "start", "session", "stream"} を返す。"""
    raise NotImplementedError


def session_end_row(
    t: float,
    session: int,
    reason: SessionEndReason,
    usage_tokens: int,
    returncode: int | None,
    completed_turns: int,
    error: str | None,
) -> dict[str, object]:
    """sessions.jsonl の終了の行 {"t", "event": "end", "session", "reason", "usage_tokens", "returncode",
    "completed_turns", "error"} を返す (reason は値の文字列)。"""
    raise NotImplementedError


def compact_row(t: float, session: int, metadata: object | None) -> dict[str, object]:
    """sessions.jsonl の要約の行 {"t", "event": "compact", "session", "metadata"} を返す。"""
    raise NotImplementedError


def turn_row(record: TurnRecord) -> dict[str, object]:
    """turns.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    duration_s は t_result があれば round(t_result - t_sent, 3)、無ければ None。kind と outcome は値の文字列、images と
    errors はリスト (errors が None なら None) にする。
    """
    raise NotImplementedError


class SessionRecordWriter:
    """sessions.jsonl と turns.jsonl に 1 行ずつ追記する (file_io.append_json_line)。

    paths   実行ディレクトリー。書く前に paths.orchestrator_dir を (親を含めて) 作る
    主ループのスレッドからだけ呼ぶ。書き込みの失敗 (OSError・ValueError) は外に出さず、logging で警告を書く
    (記録の失敗で主ループを止めない)。
    """

    def __init__(self, paths: RunPaths) -> None:
        """属性を持つ。ファイルはまだ作らない。"""
        raise NotImplementedError

    def write_session(self, row: dict[str, object]) -> None:
        """sessions.jsonl に row を 1 行追記する。"""
        raise NotImplementedError

    def write_turn(self, record: TurnRecord) -> None:
        """turns.jsonl に turn_row(record) を 1 行追記する。"""
        raise NotImplementedError
