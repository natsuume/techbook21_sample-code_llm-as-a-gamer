"""押下状態 (held) と短押し (tapping) を合わせたレポートを、ブリッジ (BridgeSupervisor) に送る。

第2章の Controller の tap / hold の層は使わず、判断のたびに押下状態をまるごと置き換える。
短押しは押してから TAP_PRESS_S 秒後に、タイマースレッドが離す。

押下状態の 2 層:
    held      apply で置き換える、押したままの状態 (ボタンとスティック)
    tapping   短押しの最中のボタンと、離す時刻 (clock の値)
送るレポートは 2 層を合成したもの。十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) が
held と tapping の合成で生じたら、短押しを優先し、送るレポートからだけ held の反対の方向を外す
(held 自体は書き換えないため、短押しが離れたら held の方向がレポートに戻る)。
tapping どうしでは相反する方向が生じないよう、反対の方向を短押ししている最中は新しい短押しを始めない。
controller_state.build_report は相反する方向で ValueError を出すため、合成の段階で必ず解消する。

apply は Pipeline のスレッドから、release_due_taps はタイマースレッドから呼ぶため、
状態の変更と送信は 1 つのロックの中で行う。
"""

import threading
import time
from collections.abc import Callable
from typing import Protocol

from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction

TAP_PRESS_S = 0.12  # 短押しで押している時間 (秒)。第2章の tap と同じ
# 同じボタンを離してから次に押すまでの最小の間隔 (秒)。第2章の tap と同じ
TAP_RELEASE_GAP_S = 0.1
TIMER_INTERVAL_S = 0.01  # タイマースレッドが離す時刻を過ぎた短押しを確かめる間隔 (秒)


class ReportSender(Protocol):
    """PadOutput がレポートを送る先。run では switch2_remote.bridge_supervisor.BridgeSupervisor を使う。"""

    def set_report(self, report: bytes) -> str:
        """8 バイトのレポートを送り、ブリッジの ack の行を返す。"""
        ...


def compose_report_state(held: PadState, tapping: frozenset[str]) -> PadState:
    """held と短押しの最中のボタン tapping を合成した、送る押下状態を返す (純粋な関数)。

    ボタンは held のボタンと tapping の和、スティックは held のもの。tapping に十字キーの方向があれば、
    その反対の方向を held のボタンから外す (短押しを優先する)。tapping どうしは相反する方向を含まない前提とする。
    """
    raise NotImplementedError


class PadOutput:
    """押下状態と短押しを持ち、変化のたびに合成したレポートを sender.set_report で送る。

    属性:
        sender            レポートを送る先
        clock             現在時刻 (秒) を返す関数。短押しの離す時刻と間隔の判定に使う
        lock              状態の変更と送信を守るロック
        held              押したままの状態 (apply で置き換える)
        tapping           短押しの最中のボタン → 離す時刻 (clock の値)
        last_released     ボタン → 前回の短押しを離した時刻 (clock の値)
        last_error        release_due_taps と neutral の送信の最後の失敗の説明
                          (switch2_remote.error_message.describe_error)。無ければ None
        stop_timer        close() で立て、タイマースレッドを止める
        timer_thread      タイマースレッド。start_timer が False なら None
    """

    sender: ReportSender
    clock: Callable[[], float]
    lock: threading.Lock
    held: PadState
    tapping: dict[str, float]
    last_released: dict[str, float]
    last_error: str | None
    stop_timer: threading.Event
    timer_thread: threading.Thread | None

    def __init__(
        self,
        sender: ReportSender,
        clock: Callable[[], float] = time.monotonic,
        start_timer: bool = True,
    ):
        """何も押していない状態で作る (作るときにはレポートを送らない)。

        start_timer が True なら、TIMER_INTERVAL_S ごとに release_due_taps を呼ぶタイマースレッド
        (daemon スレッド) を起動する。False なら起動しない (テストでは release_due_taps を直接呼ぶ)。
        """
        raise NotImplementedError

    def apply(self, action: PadAction) -> None:
        """held を action.held に置き換え、action.taps の短押しを始めて、合成したレポートを送る。

        action.held は十字キーの相反する方向を含まない前提とする (action_decision.decide_action が保証する)。
        action.taps のうち、次のすべてを満たすボタンだけを短押しとして始める (離す時刻 = clock() + TAP_PRESS_S):
            - 新しい held のボタンに含まれない
            - 短押しの最中ではない (tapping に無い)
            - 十字キーの方向なら、その反対の方向を短押ししている最中ではない
            - 前回の短押しを離してから TAP_RELEASE_GAP_S 秒以上たった (一度も短押ししていなければ満たす)
        短押しの最中のボタンの離す時刻は変えない。送るのは compose_report_state(held, tapping のボタン) の
        build_report。変化が無くても送る。送信の例外はそのまま呼び出し側に出す (状態は更新したまま)。
        """
        raise NotImplementedError

    def release_due_taps(self) -> None:
        """離す時刻を過ぎた (離す時刻 <= clock()) 短押しを tapping から外し、外したときだけ合成したレポートを送る。

        外したボタンの last_released に clock() の値を入れる。外すものが無ければ何も送らない。
        送信の例外 (RuntimeError / OSError) は外に出さず、説明を last_error に記録する (タイマースレッドから呼ぶため)。
        タイマースレッドから TIMER_INTERVAL_S ごとに呼ぶ。テストから直接呼べるよう公開している。
        """
        raise NotImplementedError

    def neutral(self) -> None:
        """held と tapping を空にし、中立のレポート (controller_state.NEUTRAL_REPORT) を送る。

        送信の例外 (RuntimeError / OSError) は外に出さず、説明を last_error に記録する。
        """
        raise NotImplementedError

    def close(self) -> None:
        """タイマースレッドを止め、終わるのを待つ。タイマースレッドが無ければ何もしない。"""
        raise NotImplementedError
