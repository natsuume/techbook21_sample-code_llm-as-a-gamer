"""orchestrator/delegate.jsonl の 1 行 (委任のループが Jev に送った要求 1 回の記録) と、その書き込み。

行の形 (キーはこの順):
    {"t_sent", "t_received", "frame_ts", "probabilities": {"none": 確率, <行動の名前>: 確率, ..., "return_control": 確率}
     か null, "chosen", "handling", "usage", "error"}
    t_sent          ask を呼んだ時刻 (実行の経過秒)
    t_received      ask が戻った (または例外を出した) 時刻
    frame_ts        送った画像の元のフレームの時刻の配列 (古い順)
    probabilities   選択肢ごとの確率 (その要求で送った選択肢の順。delegate_loop.DelegateRequestResult.option_names)。
                    失敗した要求では null
    chosen          選んだ選択肢の名前 (delegate_questions.choose_option(確率, その要求で送った選択肢の名前)。
                    応答を使わなかった要求でも、確率があれば書く)。失敗した要求では null
    handling        そのとき行った処理 (delegate_loop.DelegateHandling の値: started・continued・returned・ignored)
    usage           Jev の応答の usage (受け取った値のまま)。無いか、失敗した要求では null
    error           失敗の説明 (error_text.describe_exception)。答えがあれば null
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.file_io import append_json_line
from switch2_layers.orchestrator.delegate_loop import DelegateHandling


@dataclass(frozen=True)
class DelegateRecord:
    """委任の要求 1 回の記録。欄の意味はモジュールの docstring の行の形のとおり。"""

    t_sent: float
    t_received: float
    frame_ts: tuple[float, ...]
    probabilities: Mapping[str, float] | None
    chosen: str | None
    handling: DelegateHandling
    usage: object | None
    error: str | None


def delegate_row(record: DelegateRecord) -> dict[str, object]:
    """delegate.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    frame_ts は配列、probabilities は record の順のままの新しい辞書 (None なら null) にする。
    """
    return {
        "t_sent": record.t_sent,
        "t_received": record.t_received,
        "frame_ts": list(record.frame_ts),
        "probabilities": (
            None if record.probabilities is None else dict(record.probabilities)
        ),
        "chosen": record.chosen,
        "handling": record.handling,
        "usage": record.usage,
        "error": record.error,
    }


class DelegateLog:
    """delegate.jsonl に 1 行ずつ追記する。

    path   delegate.jsonl のパス (run_directory.RunPaths.orchestrator_delegate_log)
    委任のループ (OrchestratorController の run のループ) のスレッドからだけ書く。
    """

    def __init__(self, path: Path) -> None:
        """パスを持つ。ファイルとディレクトリーはまだ作らない。"""
        self.path = path

    def write(self, record: DelegateRecord) -> None:
        """delegate_row(record) を 1 行追記する (file_io.append_json_line)。

        親のディレクトリー (orchestrator/) が無ければ、先に作る。追記の例外はそのまま出す。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_json_line(self.path, delegate_row(record))
