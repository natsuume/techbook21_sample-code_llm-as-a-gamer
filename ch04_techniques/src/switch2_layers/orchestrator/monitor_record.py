"""orchestrator/monitor.jsonl の 1 行 (監視が Jev に送った要求 1 回の記録) と、その書き込み。

行の形 (キーはこの順):
    {"t_sent", "t_received", "monitor", "frame_t",
     "probabilities": {"NONE": 確率, "C0": 確率, ...} か null, "hit": "C<i>" か null, "usage", "error"}
    t_sent       ask を呼んだ時刻 (実行の経過秒)
    t_received   ask が戻った (または例外を出した) 時刻
    monitor      要求を送った監視の名前
    frame_t      送ったフレームの時刻
    probabilities
                 選択肢ごとの確率 (monitor_questions.monitor_option_names の順)。失敗した要求では null
    hit          当たって操作を始めた条件の選択肢の名前。当たらなかった、または応答を操作に使わなかった (要求を送った後に
                 主ループの actions を受け付けた・監視を止めたか置き換えた) ときは null
    usage        Jev の応答の usage (受け取った値のまま)。無いか、失敗した要求では null
    error        失敗の説明 (error_text.describe_exception)。答えがあれば null
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MonitorRecord:
    """監視の要求 1 回の記録。欄の意味はモジュールの docstring の行の形のとおり。

    hit は当たって操作を始めた条件の位置 (0 始まり)。無ければ None。
    """

    t_sent: float
    t_received: float
    monitor: str
    frame_t: float
    probabilities: Mapping[str, float] | None
    hit: int | None
    usage: object | None
    error: str | None


def monitor_row(record: MonitorRecord) -> dict[str, object]:
    """monitor.jsonl の 1 行の辞書を、モジュールの docstring のキーの順で返す。

    probabilities は record の順のままの新しい辞書 (None なら null)、hit は monitor_questions.case_option_name(位置)
    (None なら null)。
    """
    raise NotImplementedError


class MonitorLog:
    """monitor.jsonl に 1 行ずつ追記する。

    path   monitor.jsonl のパス (run_directory.RunPaths.orchestrator_monitor_log)
    run のループのスレッドからだけ書く。
    """

    def __init__(self, path: Path) -> None:
        """パスを持つ。ファイルとディレクトリーはまだ作らない。"""
        raise NotImplementedError

    def write(self, record: MonitorRecord) -> None:
        """monitor_row(record) を 1 行追記する (file_io.append_json_line)。

        親のディレクトリー (orchestrator/) が無ければ、先に作る。追記の例外はそのまま出す。
        """
        raise NotImplementedError
