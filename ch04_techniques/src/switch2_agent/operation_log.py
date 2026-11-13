"""Decision Model の応答と、その応答で送った操作の記録。コントローラーを中立に戻したことも記録する。
advisor に渡す操作の記録の文を作り、Decision Model の要求に入れる直近の記録を返す。

Pipeline のスレッドが add し、同じスレッドで format_since と recent を呼ぶが、ほかのスレッドから読めるようにロックを持つ。
"""

import threading
from collections import deque
from dataclasses import dataclass

from switch2_remote.controller_state import PadState, format_state

from switch2_agent.action_decision import PadAction

DEFAULT_OPERATION_LOG_CAPACITY = 500  # 持つ記録の数の既定値


@dataclass(frozen=True)
class OperationRecord:
    """1 回の応答と、送った操作の記録。

    sent_at       要求を送った時刻 (clock の値)
    received_at   応答を受け取った時刻 (clock の値)。操作はこの直後に送る
    action        応答から決めた操作
    """

    sent_at: float
    received_at: float
    action: PadAction


@dataclass(frozen=True)
class NeutralRecord:
    """判断のループを続けたまま、コントローラーを中立に戻した (何も押していない状態にした) 記録。

    neutralized_at   中立に戻した時刻 (clock の値)
    reason           中立に戻した理由 (例: `Decision Model の失敗`)
    """

    neutralized_at: float
    reason: str


# OperationLog が持つ記録 (応答と送った操作の記録か、中立に戻した記録)
OperationLogRecord = OperationRecord | NeutralRecord


def format_action(action: PadAction) -> str:
    """操作を `押下 <held> / 短押し <taps>` の形 (例: `押下 A+LS:0.8,0 / 短押し B`) の文にする。

    held と taps はどちらも controller_state.format_state の正規化表記で書く (taps はボタンだけの PadState にする)。
    何も無ければ none (例: `押下 none / 短押し none`)。Pipeline の log の action にも使う。
    """
    taps = format_state(PadState(buttons=action.taps))
    return f"押下 {format_state(action.held)} / 短押し {taps}"


def record_time(record: OperationLogRecord) -> float:
    """記録の時刻を返す。OperationRecord は received_at (操作を送る直前)、NeutralRecord は neutralized_at。"""
    if isinstance(record, NeutralRecord):
        return record.neutralized_at
    return record.received_at


def format_record(record: OperationLogRecord) -> str:
    """記録の内容の文を返す。

    OperationRecord は format_action(record.action) (例: `押下 A / 短押し none`)、
    NeutralRecord は `中立に戻した (<reason>)` (例: `中立に戻した (Decision Model の失敗)`)。
    """
    if isinstance(record, NeutralRecord):
        return f"中立に戻した ({record.reason})"
    return format_action(record.action)


class OperationLog:
    """新しいものから capacity 件までの記録 (OperationRecord と NeutralRecord) を、足した順に持つ。

    記録の時刻 (record_time) は add の順に増えていく前提とする。
    """

    def __init__(self, capacity: int = DEFAULT_OPERATION_LOG_CAPACITY):
        """capacity 件まで持つ、空の記録を作る。"""
        self.capacity = capacity
        self.lock = threading.Lock()
        self.records: deque[OperationLogRecord] = deque(maxlen=capacity)

    def add(self, record: OperationLogRecord) -> None:
        """record を末尾に足す。capacity を超えたら最も古い記録を捨てる。"""
        with self.lock:
            self.records.append(record)

    def since(self, time: float) -> list[OperationLogRecord]:
        """record_time(記録) > time (time より後) の記録を古い順に返す。"""
        with self.lock:
            return [record for record in self.records if record_time(record) > time]

    def recent(self, count: int) -> list[OperationLogRecord]:
        """新しいものから count 件の記録を、古い順に返す。

        記録が count 件より少なければ、すべての記録を古い順に返す。count が 0 以下なら空のリストを返す。
        """
        if count <= 0:
            # records[-0:] はすべての記録になるため、先に除く
            return []
        with self.lock:
            return list(self.records)[-count:]

    def format_since(self, time: float, now: float) -> str:
        """since(time) の記録を 1 行 1 件で並べた文を返す。記録が無ければ空文字を返す。

        各行は `<record_time(記録) - now を小数第 2 位まで> 秒: <format_record(記録)>` で、OperationRecord の行には
        続けて ` / advisor <advisor_probability を小数第 2 位まで>` を書く。行は \\n でつなぐ。例:
            -3.21 秒: 押下 A+LS:0.8,0 / 短押し B / advisor 0.12
            -1.05 秒: 中立に戻した (Decision Model の失敗)
        """
        lines = []
        for record in self.since(time):
            line = f"{record_time(record) - now:.2f} 秒: {format_record(record)}"
            if isinstance(record, OperationRecord):
                line += f" / advisor {record.action.advisor_probability:.2f}"
            lines.append(line)
        return "\n".join(lines)
