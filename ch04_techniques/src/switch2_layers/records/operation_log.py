"""コントローラーに送った操作の記録 operations.jsonl。

1 件を {"t", "source": "apply" | "neutral" | "stop", "held", "taps", "sticks", "ok", "error"} の 1 行で追記する。
hardware.pad_output.PadOutput が、送ろうとするたびに OperationEvent を on_operation に渡し、OperationLog.write が書く。
surface のスレッド (apply) と終了処理 (neutral・stop) から書くため、追記は 1 つのロックの中で行う。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from switch2_layers.pad_action import PadAction
from switch2_layers.run_clock import RunClock

# 操作を送った処理: apply (surface の操作)、neutral (中立に戻した)、stop (全体を止めるときに中立にして出力を止めた)
OperationSource = Literal["apply", "neutral", "stop"]


@dataclass(frozen=True)
class OperationEvent:
    """コントローラーに送ろうとした 1 回分の操作。

    source   送った処理
    action   送ろうとした操作 (apply ではボタンの制限を通した後の操作。neutral と stop では pad_action.NEUTRAL_ACTION)
    ok       送信に成功したら True
    error    送信の失敗の説明。成功なら None
    """

    source: OperationSource
    action: PadAction
    ok: bool
    error: str | None = None


def operation_row(t: float, event: OperationEvent) -> dict[str, object]:
    """operations.jsonl の 1 行の辞書を返す。

    {"t": t, "source": event.source, "held": ..., "taps": ..., "sticks": ..., "ok": event.ok, "error": event.error}。
    held・taps・sticks は pad_action.action_to_json(event.action) の値。
    """
    raise NotImplementedError


class OperationLog:
    """operations.jsonl に 1 件ずつ追記する。

    path    operations.jsonl のパス (親のディレクトリーは呼び出し側が作る)
    clock   t を付ける時計
    """

    def __init__(self, path: Path, clock: RunClock) -> None:
        """属性を持つ。ファイルはまだ作らない。"""
        raise NotImplementedError

    def write(self, event: OperationEvent) -> None:
        """operation_row(clock.now(), event) を 1 行追記する。複数のスレッドから呼んでよい。

        clock.now() を取ることと追記を 1 つのロックの中で行い、行が t の順 (同じ値を含む) に並ぶようにする。
        """
        raise NotImplementedError
