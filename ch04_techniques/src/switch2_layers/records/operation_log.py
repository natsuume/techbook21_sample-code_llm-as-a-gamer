"""コントローラーに送った操作の記録 operations.jsonl。

1 件を {"t", "source": "apply" | "neutral" | "stop" | "orchestrator" | "monitor", "held", "hold_seconds", "taps",
"sticks", "ok", "error"} の 1 行で追記する。
hardware.pad_output.PadOutput が、送ろうとするたびに OperationEvent を on_operation に渡し、OperationLog.write が書く。
source は PadOutput.apply の引数で決まり (既定 apply)、Orchestrator 型の操作 (orchestrator.orchestrator_controller) は
orchestrator か monitor を渡す。
surface のスレッド (apply) と終了処理 (neutral・stop) から書くため、追記は 1 つのロックの中で行う。
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from switch2_layers.file_io import append_json_line
from switch2_layers.pad_action import PadAction, action_to_json
from switch2_layers.run_clock import RunClock

# 操作を送った処理: apply (surface の操作)、neutral (中立に戻した)、stop (全体を止めるときに中立にして出力を止めた)、
# orchestrator (Orchestrator 型の主ループの操作)、monitor (Orchestrator 型の監視の操作)
OperationSource = Literal["apply", "neutral", "stop", "orchestrator", "monitor"]


@dataclass(frozen=True)
class OperationEvent:
    """コントローラーに送ろうとした 1 回分の操作。

    source   送った処理
    action   送ろうとした操作 (apply・orchestrator・monitor ではボタンの制限を通した後の操作。neutral と stop では
             pad_action.NEUTRAL_ACTION)
    ok       送信に成功したら True
    error    送信の失敗の説明。成功なら None
    """

    source: OperationSource
    action: PadAction
    ok: bool
    error: str | None = None


def operation_row(t: float, event: OperationEvent) -> dict[str, object]:
    """operations.jsonl の 1 行の辞書を返す。

    {"t": t, "source": event.source, "held": ..., "hold_seconds": ..., "taps": ..., "sticks": ..., "ok": event.ok,
     "error": event.error}。held・hold_seconds・taps・sticks は pad_action.action_to_json(event.action) の値。
    """
    action = action_to_json(event.action)
    return {
        "t": t,
        "source": event.source,
        "held": action["held"],
        "hold_seconds": action["hold_seconds"],
        "taps": action["taps"],
        "sticks": action["sticks"],
        "ok": event.ok,
        "error": event.error,
    }


class OperationLog:
    """operations.jsonl に 1 件ずつ追記する。

    path    operations.jsonl のパス (親のディレクトリーは呼び出し側が作る)
    clock   t を付ける時計
    """

    def __init__(self, path: Path, clock: RunClock) -> None:
        """属性を持つ。ファイルはまだ作らない。"""
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()

    def write(self, event: OperationEvent) -> None:
        """operation_row(clock.now(), event) を 1 行追記する。複数のスレッドから呼んでよい。

        clock.now() を取ることと追記を 1 つのロックの中で行い、行が t の順 (同じ値を含む) に並ぶようにする。
        """
        with self._lock:
            append_json_line(self.path, operation_row(self.clock.now(), event))
