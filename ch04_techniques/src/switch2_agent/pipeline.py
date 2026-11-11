"""判断のループ。フレームを選んで Decision Model に送り、応答から決めた操作を送ることを止まるまで繰り返す。

応答待ちの要求は常に 1 つ。応答を受け取ったら操作を送り、すぐ次の要求に進む。
1 回の要求 (run_once) の流れ:
    1. キャプチャーが終わっていないか、新しいフレームが届いているかを確かめる
    2. 要求に載せるフレームを選ぶ (前回の要求の送信から今までの直近の区間 + それより前の意味のあるフレーム)
    3. ゲームの情報の 4 ファイルの最新の内容を読み、要求を組み立てる
    4. Decision Model に送り、問いごとの確率から操作を決めて PadOutput に渡す (すぐ戻る)
    5. advisor を呼ぶ答えなら、Advisor.request_update を呼ぶ (すぐ戻る)
時間はすべて clock の差で測る。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from switch2_agent.action_decision import PadAction, Thresholds
from switch2_agent.advisors.base import Advisor
from switch2_agent.decision_models.base import DecisionModel
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.base import RecentFrameSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.operation_log import OperationLog
from switch2_agent.questions import Question

# Decision Model の失敗がこの回数続いたら止まる
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5
DEFAULT_ERROR_BACKOFF_S = 1.0  # Decision Model の失敗の後の待ち (秒)
DEFAULT_NO_FRAME_WAIT_S = 0.05  # 新しいフレームが無いときの待ち (秒)
# 新しいフレームが届かない状態がこの秒数続いたら、キャプチャーが止まったとみなす
DEFAULT_STALE_FRAME_TIMEOUT_S = 2.0


class CaptureStoppedError(RuntimeError):
    """キャプチャーが終わったか、新しいフレームが届かなくなったため、判断を続けられない。"""


class PadOutputLike(Protocol):
    """Pipeline が操作を送る先。run では pad_output.PadOutput を使う。"""

    def apply(self, action: PadAction) -> None:
        """操作を送る。送信の失敗は例外 (RuntimeError / OSError) で出す。"""
        ...

    def neutral(self) -> None:
        """何も押していない状態にする。送信の失敗は外に出さない。"""
        ...


@dataclass
class PipelineParts:
    """Pipeline が使う部品。

    history          フレームの履歴
    store            意味のあるフレームの保管
    selector         直近の区間のフレームの選び方
    model            Decision Model
    output           操作を送る先
    advisor          advisor
    read_notes       ゲームの情報の 4 ファイルを読む関数 (種別 → 中身)
    questions        問いの一覧 (questions.build_questions)
    thresholds       問いの種類ごとのしきい値
    encode           フレームを JPEG にする関数 (run では decision_request.JpegCache を渡す)
    operation_log    応答と送った操作の記録
    log              ログの辞書を 1 件ずつ受け取る関数 (run では RunLog.write)
    clock            現在時刻 (秒) を返す関数 (フレームの time と同じ時計)
    sleep            秒数を受け取って待つ関数
    game             ゲーム名
    capture_ended    キャプチャーが終わったら True を返す関数
    """

    history: FrameHistory
    store: MeaningfulFrameStore
    selector: RecentFrameSelector
    model: DecisionModel
    output: PadOutputLike
    advisor: Advisor
    read_notes: Callable[[], dict[str, str]]
    questions: list[Question]
    thresholds: Thresholds
    encode: Callable[[TimedFrame], bytes]
    operation_log: OperationLog
    log: Callable[[dict], None]
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    game: str
    capture_ended: Callable[[], bool]


class Pipeline:
    """判断のループ。

    属性:
        parts                    部品
        max_consecutive_errors   Decision Model の失敗がこの回数続いたら止まる
        error_backoff_s          Decision Model の失敗の後の待ち (秒)
        no_frame_wait_s          新しいフレームが無いときの待ち (秒)
        stale_frame_timeout_s    新しいフレームが無い状態がこの秒数以上続いたら止まる
        started_at               パイプラインの開始時刻 (__init__ での clock())。最初の advisor の操作の記録の起点
        previous_sent_at         前回の要求の送信時刻。まだ送っていなければ None
        last_sent_frame_time     前回の要求に載せた最新のフレームの time。まだ送っていなければ None
        last_advisor_at          操作の記録の起点 (advisor が受け付けた要求の received_at。最初は started_at)
        no_new_frame_since       「新しいフレームが無い」と最初に分かった時刻。新しいフレームで要求を送れたら None に戻す
        consecutive_errors       Decision Model の連続の失敗の回数。model.decide が成功したら 0 に戻す
    """

    parts: PipelineParts
    max_consecutive_errors: int
    error_backoff_s: float
    no_frame_wait_s: float
    stale_frame_timeout_s: float
    started_at: float
    previous_sent_at: float | None
    last_sent_frame_time: float | None
    last_advisor_at: float
    no_new_frame_since: float | None
    consecutive_errors: int

    def __init__(
        self,
        parts: PipelineParts,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
        error_backoff_s: float = DEFAULT_ERROR_BACKOFF_S,
        no_frame_wait_s: float = DEFAULT_NO_FRAME_WAIT_S,
        stale_frame_timeout_s: float = DEFAULT_STALE_FRAME_TIMEOUT_S,
    ):
        """属性を入れる。started_at と last_advisor_at は parts.clock() の値にする。"""
        raise NotImplementedError

    def run_once(self) -> None:
        """1 回の要求と応答を行う。

        1. capture_ended() が真なら、output.neutral() して CaptureStoppedError を出す
        2. history.latest() が None か、その time が last_sent_frame_time 以下なら、新しいフレームが無い。
           このときは要求を送らず、次の「新しいフレームが無いときの扱い」をして戻る
        3. now = clock()。select_request_frames(history, store, selector, previous_sent_at, now) で選ぶ。
           直近の区間 (recent) が空なら、2 と同じく要求を送らず「新しいフレームが無いときの扱い」をして戻る
        4. notes = read_notes()、build_decision_request(frames, now, notes, questions, encode)
        5. sent_at = now、decision = model.decide(request)、received_at = clock()。成功したら consecutive_errors = 0
        6. action = decide_action(decision.probabilities, questions, thresholds)、output.apply(action)
        7. operation_log.add(OperationRecord(sent_at, received_at, action))
        8. action.advisor_requested なら AdvisorContext を作って advisor.request_update を呼ぶ。
           AdvisorContext は game、request.images、notes の strategy (無ければ空文字)、
           operation_log.format_since(last_advisor_at, received_at)、action.advisor_probability、
           time.strftime("%H:%M:%S") の壁時計の時刻からなる。
           受け付けられたら (True)、last_advisor_at = received_at (次の操作の記録の起点)
        9. previous_sent_at = sent_at、last_sent_frame_time = 載せた直近の区間の最新のフレームの time、
           no_new_frame_since = None。log に次の辞書を渡す:
             {"event": "decision", "select_s": 3 の時間, "encode_s": 4 の時間, "decide_s": model.decide の時間
              (HTTP を含む), "apply_s": output.apply の時間, "images": 画像の枚数,
              "payload_bytes": 画像の JPEG の合計バイト数, "action": operation_log.format_action(action),
              "advisor_probability", "advisor_requested", "advisor_accepted": request_update の結果
              (呼ばなければ False), "refusals": decision.refusals}

        新しいフレームが無いときの扱い: no_new_frame_since が None なら clock() の値を入れる。
        clock() - no_new_frame_since が stale_frame_timeout_s 以上なら、output.neutral() して
        CaptureStoppedError を出す (古い画面のまま入力を出し続けない)。そうでなければ sleep(no_frame_wait_s) する。

        例外: model.decide の DecisionModelError、output.apply の送信の例外 (RuntimeError / OSError) は
        そのまま出す (扱いは run が決める)。
        """
        raise NotImplementedError

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで run_once を繰り返す。

        - DecisionModelError: output.neutral() し、log に {"event": "decision_error", "error": <説明>} を渡し、
          consecutive_errors を 1 増やし、sleep(error_backoff_s) する。consecutive_errors が
          max_consecutive_errors に達したら RuntimeError (DecisionModelError ではない) で止まる
        - switch2_remote.bridge_supervisor.BridgeReconnectingError (apply の送信): log に
          {"event": "bridge_reconnecting", "error": <説明>} を渡して続ける
        - CaptureStoppedError: log に {"event": "capture_stopped", "error": <説明>} を渡し、例外をそのまま出して止まる
        - そのほかの RuntimeError / OSError (apply の送信。ブリッジが failed など): log に
          {"event": "pipeline_error", "error": <説明>} を渡し、例外をそのまま出して止まる
        説明は switch2_remote.error_message.describe_error で作る。
        """
        raise NotImplementedError
