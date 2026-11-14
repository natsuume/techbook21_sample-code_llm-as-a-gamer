"""advisor の型。Pipeline はこの型だけに依存する。

advisor は役割 (advisor_roles.ADVISOR_ROLES) ごとに 1 つずつあり、Pipeline は役割の name → Advisor の辞書を持つ。
"""

from dataclasses import dataclass
from typing import Protocol

from switch2_agent.decision_request import LabeledImage


@dataclass(frozen=True)
class AdvisorContext:
    """1 つの役割の advisor に渡す文脈。

    game                  ゲーム名 (game_notes.GAME_NAME_PATTERN に一致する)
    images                advisor 用に選んだ画像 (意味のあるフレーム → フレームの履歴から等間隔に選んだもの、
                          どちらも古い順。advisor_images.build_advisor_images)。同じ要求で呼ぶ役割どうしで同じもの
    current_strategy      今の戦略 (ゲームの情報の strategy の中身)
    operation_log_text    その役割の advisor が前回受け付けてからの応答と送った操作
                          (OperationLog.format_since。行の advisor の確率は、その役割の問いの確率)
    trigger_probability   呼んだきっかけの要求の、その役割の問いの確率
    trigger_time_text     呼んだきっかけの要求の時刻 (time.strftime で書いた壁時計の時刻)
    trigger_reason        強制の呼び出し (advisor_triggers) で呼んだときの理由の文 (ForcedCall.reason)。
                          advisor の問いが真になって呼んだときは None
    """

    game: str
    images: list[LabeledImage]
    current_strategy: str
    operation_log_text: str
    trigger_probability: float
    trigger_time_text: str
    trigger_reason: str | None = None


class Advisor(Protocol):
    """1 つの役割の仕事 (受け持ちのゲームの情報のファイルの更新) を裏で行う advisor。どのメソッドもすぐ戻る。"""

    def request_update(self, context: AdvisorContext) -> bool:
        """役割の仕事を求める。受け付けたら True を返す。実行中なら何もせず False を返す。すぐ戻る。"""
        ...

    def is_busy(self) -> bool:
        """更新の要求を今は受け付けない (更新の実行中か、失敗した実行の後の待ちの間) なら True を返す。"""
        ...

    def close(self) -> None:
        """終了する。実行中の更新は待たない。"""
        ...
