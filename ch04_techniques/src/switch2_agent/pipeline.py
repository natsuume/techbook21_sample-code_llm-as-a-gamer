"""判断のループ。フレームを選んで Decision Model に送り、応答から決めた操作を送ることを止まるまで繰り返す。

応答待ちの要求は常に 1 つ。応答を受け取ったら操作を送り、すぐ次の要求に進む。
コントローラーのブリッジが復旧中 (recovering) の間は要求を送らず、復旧が終わるのを待つ
(送った操作は届かず、要求のたびに Decision Model の料金がかかるため)。
Decision Model の応答は同期で待つ。その間にキャプチャーが止まったら、別のスレッドの見張り
(capture_watcher.CaptureWatcher) が押下状態を中立に戻し、Pipeline は capture_lost でその判定を読んで止まる。
1 回の要求 (run_once) の流れ:
    1. キャプチャーが終わっていないか (見張りが止まったと判定していないか)、新しいフレームが届いているかを確かめる
    2. 要求に載せるフレームを選ぶ (前回の要求の送信から今までの直近の区間 + それより前の意味のあるフレーム)
    3. ゲームの情報の 4 ファイルの最新の内容を読み、要求を組み立てる
    4. Decision Model に送る。応答を待つ間にキャプチャーが止まっていたら、応答を捨てて止まる。
       そうでなければ、問いごとの確率から操作を決めて PadOutput に渡す (すぐ戻る)
    5. advisor を呼ぶ答えなら、Advisor.request_update を呼ぶ (すぐ戻る)
時間はすべて clock の差で測る。
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from switch2_remote.bridge_supervisor import BridgeReconnectingError
from switch2_remote.error_message import describe_error

from switch2_agent.action_decision import PadAction, Thresholds, decide_action
from switch2_agent.advisors.base import Advisor, AdvisorContext
from switch2_agent.decision_models.base import DecisionModel, DecisionModelError
from switch2_agent.decision_request import build_decision_request
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.base import RecentFrameSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.frame_selection.request_frames import select_request_frames
from switch2_agent.operation_log import OperationLog, OperationRecord, format_action
from switch2_agent.questions import Question

# Decision Model の失敗がこの回数続いたら止まる
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5
DEFAULT_ERROR_BACKOFF_S = 1.0  # Decision Model の失敗の後の待ち (秒)
DEFAULT_NO_FRAME_WAIT_S = 0.05  # 新しいフレームが無いときの待ち (秒)
# 新しいフレームが届かない状態がこの秒数続いたら、キャプチャーが止まったとみなす
DEFAULT_STALE_FRAME_TIMEOUT_S = 2.0
# まだ 1 回も要求を送っていない間に、最初のフレームを待つ上限 (秒)。ffmpeg の起動に時間がかかるため長めにとる
DEFAULT_FIRST_FRAME_TIMEOUT_S = 10.0
DEFAULT_BRIDGE_WAIT_S = 0.1  # ブリッジの復旧を待つ間の確認の間隔 (秒)


class CaptureStoppedError(RuntimeError):
    """キャプチャーが終わったか、新しいフレームが届かなくなったため、判断を続けられない。"""


class OutputSendError(Exception):
    """output.apply の送信の失敗 (RuntimeError / OSError) を、ほかの場所の例外と区別して run に伝える。

    error に元の例外を持つ。run_once を直接呼んだときも、apply の送信の失敗はこの例外で出る。
    """

    def __init__(self, error: RuntimeError | OSError):
        super().__init__(describe_error(error))
        self.error = error


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
    bridge_failed    コントローラーのブリッジが復旧できない状態 (failed) なら True を返す関数
                     (run では lambda: supervisor.state == "failed")
    bridge_recovering  コントローラーのブリッジが復旧中 (recovering) なら True を返す関数
                       (run では lambda: supervisor.state == "recovering")
    capture_lost     キャプチャーが止まったと見張り (capture_watcher.CaptureWatcher) が判定していれば、その理由を
                     返す関数。まだなら None (run では watcher.lost)
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
    bridge_failed: Callable[[], bool]
    bridge_recovering: Callable[[], bool]
    capture_lost: Callable[[], str | None]


class Pipeline:
    """判断のループ。

    属性:
        parts                    部品
        max_consecutive_errors   Decision Model の失敗がこの回数続いたら止まる
        error_backoff_s          Decision Model の失敗の後の待ち (秒)
        no_frame_wait_s          新しいフレームが無いときの待ち (秒)
        stale_frame_timeout_s    最初の要求を送った後、新しいフレームが無い状態がこの秒数以上続いたら止まる
        first_frame_timeout_s    まだ 1 回も要求を送っていない間、新しいフレームが無い状態がこの秒数以上続いたら止まる
        bridge_wait_s            ブリッジの復旧を待つ間の確認の間隔 (秒)
        started_at               パイプラインの開始時刻 (__init__ での clock())。最初の advisor の操作の記録の起点
        request_sent             1 回でも model.decide を呼んだら (要求を送ったら) True
        previous_sent_at         前回の要求の送信時刻。まだ送っていなければ None
        last_sent_frame_time     前回の要求に載せた最新のフレームの time。まだ送っていなければ None
        last_advisor_at          操作の記録の起点 (advisor が受け付けた要求の received_at。最初は started_at)
        no_new_frame_since       「新しいフレームが無い」と最初に分かった時刻。直近の区間に新しいフレームがあると
                                 分かったら、要求を送る前に None に戻す (要求の成否によらない)
        consecutive_errors       Decision Model の連続の失敗の回数。model.decide が成功したら 0 に戻す
    """

    def __init__(
        self,
        parts: PipelineParts,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
        error_backoff_s: float = DEFAULT_ERROR_BACKOFF_S,
        no_frame_wait_s: float = DEFAULT_NO_FRAME_WAIT_S,
        stale_frame_timeout_s: float = DEFAULT_STALE_FRAME_TIMEOUT_S,
        first_frame_timeout_s: float = DEFAULT_FIRST_FRAME_TIMEOUT_S,
        bridge_wait_s: float = DEFAULT_BRIDGE_WAIT_S,
    ):
        """属性を入れる。started_at と last_advisor_at は parts.clock() の値にする。"""
        self.parts = parts
        self.max_consecutive_errors = max_consecutive_errors
        self.error_backoff_s = error_backoff_s
        self.no_frame_wait_s = no_frame_wait_s
        self.stale_frame_timeout_s = stale_frame_timeout_s
        self.first_frame_timeout_s = first_frame_timeout_s
        self.bridge_wait_s = bridge_wait_s
        self.started_at = parts.clock()
        self.request_sent = False
        self.previous_sent_at: float | None = None
        self.last_sent_frame_time: float | None = None
        self.last_advisor_at = self.started_at
        self.no_new_frame_since: float | None = None
        self.consecutive_errors = 0

    def run_once(self) -> None:
        """1 回の要求と応答を行う。

        1. capture_ended() が真なら、output.neutral() して CaptureStoppedError("capture ended") を出す。
           続けて reason = capture_lost() が None でなければ、output.neutral() して CaptureStoppedError(reason) を出す
        2. history.latest() が None か、その time が last_sent_frame_time 以下なら、新しいフレームが無い。
           このときは要求を送らず、次の「新しいフレームが無いときの扱い」をして戻る
        3. now = clock()。select_request_frames(history, store, selector, previous_sent_at, now) で選ぶ。
           直近の区間 (recent) が空なら、2 と同じく要求を送らず「新しいフレームが無いときの扱い」をして戻る。
           空でなければ、no_new_frame_since = None にする (この後の要求が失敗しても None のまま。
           それまでフレームを待った時間を、失敗の後の上限の判定に持ち越さないため)
        4. notes = read_notes()、build_decision_request(frames, now, notes, questions, encode)
        5. request_sent = True、sent_at = now、decision = model.decide(request)、received_at = clock()。
           成功したら consecutive_errors = 0。続けて、応答を待つ間にキャプチャーが止まっていないかを確かめる:
           reason = capture_lost() とし、None で capture_ended() が真なら reason = "capture ended"。
           reason があれば応答を捨て (操作を反映せず、operation_log にも log の decision にも残さない)、
           output.neutral() して CaptureStoppedError(reason) を出す
        6. action = decide_action(decision.probabilities, questions, thresholds)、output.apply(action)
        7. operation_log.add(OperationRecord(sent_at, received_at, action))
        8. action.advisor_requested なら AdvisorContext を作って advisor.request_update を呼ぶ。
           AdvisorContext は game、request.images、notes の strategy (無ければ空文字)、
           operation_log.format_since(last_advisor_at, received_at)、action.advisor_probability、
           time.strftime("%H:%M:%S") の壁時計の時刻からなる。
           受け付けられたら (True)、last_advisor_at = received_at (次の操作の記録の起点)
        9. previous_sent_at = sent_at、last_sent_frame_time = 載せた直近の区間の最新のフレームの time。
           log に次の辞書を渡す:
             {"event": "decision", "select_s": 3 の時間, "encode_s": 4 の時間, "decide_s": model.decide の時間
              (HTTP を含む), "apply_s": output.apply の時間, "images": 画像の枚数,
              "payload_bytes": 画像の JPEG の合計バイト数, "action": operation_log.format_action(action),
              "advisor_probability", "advisor_requested", "advisor_accepted": request_update の結果
              (呼ばなければ False), "refusals": decision.refusals}

        新しいフレームが無いときの扱い: no_new_frame_since が None なら clock() の値を入れる。
        clock() - no_new_frame_since が上限以上なら、output.neutral() して CaptureStoppedError を出す
        (古い画面のまま入力を出し続けない)。そうでなければ sleep(no_frame_wait_s) する。上限は、まだ 1 回も
        要求を送っていなければ first_frame_timeout_s (起動直後は最初のフレームを待つ)、送った後は stale_frame_timeout_s。

        例外: model.decide の DecisionModelError はそのまま出す。output.apply の送信の例外 (RuntimeError / OSError) は
        OutputSendError に包んで出す (扱いは run が決める)。
        """
        parts = self.parts
        if parts.capture_ended():
            parts.output.neutral()
            raise CaptureStoppedError("capture ended")
        lost_reason = parts.capture_lost()
        if lost_reason is not None:
            parts.output.neutral()
            raise CaptureStoppedError(lost_reason)
        latest = parts.history.latest()
        if latest is None or (
            self.last_sent_frame_time is not None
            and latest.time <= self.last_sent_frame_time
        ):
            self._wait_for_new_frame()
            return

        now = parts.clock()
        frames = select_request_frames(
            parts.history, parts.store, parts.selector, self.previous_sent_at, now
        )
        if not frames.recent:
            self._wait_for_new_frame()
            return
        self.no_new_frame_since = None
        selected_at = parts.clock()

        notes = parts.read_notes()
        request = build_decision_request(
            frames, now, notes, parts.questions, parts.encode
        )
        encoded_at = parts.clock()

        self.request_sent = True
        sent_at = now
        decision = parts.model.decide(request)
        received_at = parts.clock()
        self.consecutive_errors = 0
        # 応答を待つ間にキャプチャーが止まっていたら、古い画面への応答なので操作を反映しない
        lost_reason = parts.capture_lost()
        if lost_reason is None and parts.capture_ended():
            lost_reason = "capture ended"
        if lost_reason is not None:
            parts.output.neutral()
            raise CaptureStoppedError(lost_reason)

        action = decide_action(
            decision.probabilities, parts.questions, parts.thresholds
        )
        apply_started = parts.clock()
        try:
            parts.output.apply(action)
        except (RuntimeError, OSError) as e:
            raise OutputSendError(e) from e
        applied_at = parts.clock()

        parts.operation_log.add(OperationRecord(sent_at, received_at, action))
        advisor_accepted = False
        if action.advisor_requested:
            advisor_accepted = parts.advisor.request_update(
                AdvisorContext(
                    game=parts.game,
                    images=request.images,
                    current_strategy=notes.get("strategy", ""),
                    operation_log_text=parts.operation_log.format_since(
                        self.last_advisor_at, received_at
                    ),
                    trigger_probability=action.advisor_probability,
                    trigger_time_text=time.strftime("%H:%M:%S"),
                )
            )
            if advisor_accepted:
                self.last_advisor_at = received_at

        self.previous_sent_at = sent_at
        self.last_sent_frame_time = max(frame.time for frame in frames.recent)
        parts.log(
            {
                "event": "decision",
                "select_s": selected_at - now,
                "encode_s": encoded_at - selected_at,
                "decide_s": received_at - encoded_at,
                "apply_s": applied_at - apply_started,
                "images": len(request.images),
                "payload_bytes": sum(len(image.jpeg) for image in request.images),
                "action": format_action(action),
                "advisor_probability": action.advisor_probability,
                "advisor_requested": action.advisor_requested,
                "advisor_accepted": advisor_accepted,
                "refusals": decision.refusals,
            }
        )

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで run_once を繰り返す。

        各回の始めに、bridge_recovering() が真で capture_lost() が None なら、run_once を呼ばずに
        _wait_for_bridge_recovery(stop) で復旧が終わるのを待ち、その後は while の条件の確認に戻る (復旧中は要求を
        送らない)。送信の失敗で復旧が始まった場合も、次の回の始めの確認で待つ。capture_lost() が理由を返すときは、
        復旧中でも待たずに run_once を呼び、その始めの確認で止まる。run_once の例外は次のように扱う:
        - DecisionModelError: output.neutral() し、log に {"event": "decision_error", "error": <説明>} を渡し、
          consecutive_errors を 1 増やし、sleep(error_backoff_s) する。consecutive_errors が
          max_consecutive_errors に達したら RuntimeError (DecisionModelError ではない) で止まる
        - OutputSendError (apply の送信の失敗) は、元の例外で次のように扱う:
            - switch2_remote.bridge_supervisor.BridgeReconnectingError: log に
              {"event": "bridge_reconnecting", "error": <説明>} を渡して続ける
            - そのほかの RuntimeError / OSError で、bridge_failed() が偽: log に {"event": "send_error", "error": <説明>}
              を渡して続ける (BridgeSupervisor は、送信中にブリッジが壊れると復旧を始めたうえで元の例外を出すため)
            - bridge_failed() が真: log に {"event": "pipeline_error", "error": <説明>} を渡し、元の例外を
              そのまま出して止まる
        - CaptureStoppedError: log に {"event": "capture_stopped", "error": <説明>} を渡し、例外をそのまま出して止まる
        それ以外の例外 (_wait_for_bridge_recovery の RuntimeError を含む) は、そのまま出して止まる。
        説明は switch2_remote.error_message.describe_error で作る。
        """
        parts = self.parts
        while not stop.is_set():
            try:
                if parts.bridge_recovering() and parts.capture_lost() is None:
                    self._wait_for_bridge_recovery(stop)
                    continue
                self.run_once()
            except DecisionModelError as e:
                parts.output.neutral()
                parts.log({"event": "decision_error", "error": describe_error(e)})
                self.consecutive_errors += 1
                parts.sleep(self.error_backoff_s)
                if self.consecutive_errors >= self.max_consecutive_errors:
                    raise RuntimeError(
                        f"decision model failed {self.consecutive_errors} times in a row: "
                        f"{describe_error(e)}"
                    ) from e
            except OutputSendError as e:
                error = e.error
                if isinstance(error, BridgeReconnectingError):
                    parts.log(
                        {"event": "bridge_reconnecting", "error": describe_error(error)}
                    )
                    continue
                if parts.bridge_failed():
                    parts.log(
                        {"event": "pipeline_error", "error": describe_error(error)}
                    )
                    raise error from None
                parts.log({"event": "send_error", "error": describe_error(error)})
            except CaptureStoppedError as e:
                parts.log({"event": "capture_stopped", "error": describe_error(e)})
                raise

    def _wait_for_bridge_recovery(self, stop: threading.Event) -> None:
        """コントローラーのブリッジの復旧が終わるのを、要求を送らずに待つ。

        1. output.neutral() を 1 回呼び、log に {"event": "bridge_wait"} を渡す
        2. stop が立つか、bridge_recovering() が偽になるか、capture_lost() が理由を返すまで、
           sleep(bridge_wait_s) を繰り返す
        3. log に {"event": "bridge_wait_end", "waited_s": <1 の時点からの clock の差>} を渡す
        4. bridge_failed() が真なら、log に {"event": "pipeline_error", "error": <説明>} を渡し、
           RuntimeError("controller bridge failed while waiting for recovery") を出す
        5. そうでなければ戻る (stop が立って抜けたときは、run の while の条件で run が終わる。
           キャプチャーが止まって抜けたときは、次の回の run_once の始めの確認で止まる)
        """
        parts = self.parts
        started = parts.clock()
        parts.output.neutral()
        parts.log({"event": "bridge_wait"})
        while (
            not stop.is_set()
            and parts.bridge_recovering()
            and parts.capture_lost() is None
        ):
            parts.sleep(self.bridge_wait_s)
        parts.log({"event": "bridge_wait_end", "waited_s": parts.clock() - started})
        if parts.bridge_failed():
            error = RuntimeError("controller bridge failed while waiting for recovery")
            parts.log({"event": "pipeline_error", "error": describe_error(error)})
            raise error

    def _wait_for_new_frame(self) -> None:
        """新しいフレームが無いときの扱い (run_once の docstring のとおり)。"""
        now = self.parts.clock()
        if self.no_new_frame_since is None:
            self.no_new_frame_since = now
        timeout = (
            self.stale_frame_timeout_s
            if self.request_sent
            else self.first_frame_timeout_s
        )
        if now - self.no_new_frame_since >= timeout:
            self.parts.output.neutral()
            raise CaptureStoppedError(f"no new frame for {timeout} seconds")
        self.parts.sleep(self.no_frame_wait_s)
