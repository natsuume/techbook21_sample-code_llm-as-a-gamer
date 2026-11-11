"""advisor の型。Pipeline はこの型だけに依存する。"""

from dataclasses import dataclass
from typing import Protocol

from switch2_agent.decision_request import LabeledImage


@dataclass(frozen=True)
class AdvisorContext:
    """advisor に渡す文脈。

    game                  ゲーム名 (game_notes.GAME_NAME_PATTERN に一致する)
    images                直近の要求の画像 (要求と同じ順)
    current_strategy      今の戦略 (ゲームの情報の strategy の中身)
    operation_log_text    前回 advisor を呼んでからの応答と送った操作 (OperationLog.format_since)
    trigger_probability   呼んだきっかけの advisor の問いの確率
    trigger_time_text     呼んだきっかけの要求の時刻 (time.strftime で書いた壁時計の時刻)
    """

    game: str
    images: list[LabeledImage]
    current_strategy: str
    operation_log_text: str
    trigger_probability: float
    trigger_time_text: str


class Advisor(Protocol):
    """戦略の更新を裏で行う advisor。どのメソッドもすぐ戻る。"""

    def request_update(self, context: AdvisorContext) -> bool:
        """戦略の更新を求める。受け付けたら True を返す。実行中なら何もせず False を返す。すぐ戻る。"""
        ...

    def is_busy(self) -> bool:
        """更新の実行中なら True を返す。"""
        ...

    def close(self) -> None:
        """終了する。実行中の更新は待たない。"""
        ...
