"""判断のループ。フレームを選んで Decision Model に送り、応答から決めた操作を送ることを止まるまで繰り返す。

応答待ちの要求は常に 1 つ。応答を受け取ったら操作を送り、次の要求に進む。min_request_interval_s が 0 より大きければ、
直前の応答 (失敗を含む) が返ってからその時間が過ぎるまで、次の要求のフレームを選ばずに待つ (Jev では 0.2 秒)。
コントローラーのブリッジが復旧中 (recovering) の間は要求を送らず、復旧が終わるのを待つ
(送った操作は届かず、要求のたびに Decision Model の料金がかかるため)。
Decision Model の応答は同期で待つ。その間にキャプチャーが止まったら、別のスレッドの見張り
(capture_watcher.CaptureWatcher) が押下状態を中立に戻し、Pipeline は capture_lost でその判定を読んで止まる。
1 回の要求 (run_once) の流れ:
    1. 直前の応答から min_request_interval_s が過ぎていなければ、残りの時間だけ待つ
    2. キャプチャーが終わっていないか (見張りが止まったと判定していないか)、ブリッジが復旧中になっていないか、
       新しいフレームが届いているかを確かめる
    3. 要求に載せるフレームとして、フレームの履歴の最新の 1 枚を選ぶ
    4. ゲームの情報の 4 ファイルの最新の内容を読み、直近の応答と送った操作の履歴とともに要求を組み立てる。
       系列情報 (parts.timeline) があれば、読んだゲームの情報を渡してから系列情報の文を作り、要求に足す
    5. Decision Model に送る。応答を待つ間にキャプチャーが止まっていたら、応答を捨てて止まる。
       そうでなければ、問いごとの答え (選択肢ごとの確率と、真である確率) から操作を決めて PadOutput に渡す (すぐ戻る)
    6. advisor を呼ぶ答えの役割 (advisor_roles.ADVISOR_ROLES) と、強制の呼び出し (parts.advisor_trigger) の役割を
       合わせ、そのうち advisor が実行中でないものがあれば、
       advisor に渡す画像を要求とは別に 1 回だけ選び (意味のあるフレーム + フレームの履歴から等間隔に選んだもの)、
       その役割ごとに Advisor.request_update を呼ぶ (すぐ戻る)
    7. 要求のログ (request_log) があれば、要求ごとの入出力を書く
時間はすべて clock の差で測る。画像のラベルと操作の履歴の経過秒は、Pipeline の開始時刻 (started_at) を起点にする。
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from switch2_remote.bridge_supervisor import BridgeReconnectingError
from switch2_remote.error_message import describe_error

from switch2_agent.action_decision import PadAction, Thresholds, decide_action
from switch2_agent.advisor_images import build_advisor_images
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.advisor_triggers.base import AdvisorCallState, AdvisorTrigger
from switch2_agent.advisors.base import Advisor, AdvisorContext
from switch2_agent.decision_models.base import (
    Decision,
    DecisionModel,
    DecisionModelError,
)
from switch2_agent.decision_request import (
    DEFAULT_HISTORY_COUNT,
    DecisionRequest,
    LabeledImage,
    build_decision_request,
)
from switch2_agent.frame_history import FrameHistory, TimedFrame
from switch2_agent.frame_selection.advisor_frames import select_advisor_frames
from switch2_agent.frame_selection.base import RecentFrameSelector
from switch2_agent.frame_selection.meaningful_frames import MeaningfulFrameStore
from switch2_agent.operation_log import (
    NeutralRecord,
    OperationLog,
    OperationRecord,
    format_action,
)
from switch2_agent.pad_output import OutputStoppedError
from switch2_agent.questions import Question
from switch2_agent.request_log import RequestLogEntry

# Decision Model の失敗がこの回数続いたら止まる
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5
DEFAULT_ERROR_BACKOFF_S = 1.0  # Decision Model の失敗の後の待ち (秒)
DEFAULT_NO_FRAME_WAIT_S = 0.05  # 新しいフレームが無いときの待ち (秒)
# 新しいフレームが届かない状態がこの秒数続いたら、キャプチャーが止まったとみなす
DEFAULT_STALE_FRAME_TIMEOUT_S = 2.0
# まだ 1 回も要求を送っていない間に、最初のフレームを待つ上限 (秒)。ffmpeg の起動に時間がかかるため長めにとる
DEFAULT_FIRST_FRAME_TIMEOUT_S = 10.0
DEFAULT_BRIDGE_WAIT_S = 0.1  # ブリッジの復旧を待つ間の確認の間隔 (秒)
# 判断のループを続けたまま中立に戻したときに、operation_log の NeutralRecord に書く理由
DECISION_ERROR_NEUTRAL_REASON = "Decision Model の失敗"
BRIDGE_WAIT_NEUTRAL_REASON = "コントローラーの接続の復旧待ち"


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
        """操作を送る。送信の失敗は例外 (RuntimeError / OSError) で出す。

        出力が止められた後 (PadOutput.stop_output の後) は、送らずに pad_output.OutputStoppedError を出す。
        """
        ...

    def neutral(self) -> None:
        """何も押していない状態にする。送信の失敗は外に出さない。"""
        ...


class TimelineLike(Protocol):
    """Pipeline が系列情報の文を作る部品。run では timeline.builder.TimelineBuilder を使う。"""

    def observe_notes(self, notes: Mapping[str, str], now: float) -> None:
        """要求の時刻 now に読んだゲームの情報 notes (種別 → 中身) を受け取る。"""
        ...

    def record_advisor_call(self, role_name: str, now: float) -> None:
        """役割 role_name の advisor が、時刻 now に要求を受け付けたことを受け取る。"""
        ...

    def build(self, now: float, origin: float) -> str:
        """要求の時刻 now の系列情報の文を返す。origin は経過秒の起点 (Pipeline.started_at)。"""
        ...


class RequestLogLike(Protocol):
    """Pipeline が要求ごとの入出力を書く先。run では request_log.RequestLog を使う。"""

    def write(self, entry: RequestLogEntry) -> None:
        """要求 1 回分の記録を書く。書き込みの失敗は OSError か ValueError (文字の符号化の失敗など) で出す。"""
        ...


@dataclass
class PipelineParts:
    """Pipeline が使う部品。

    history          フレームの履歴
    store            意味のあるフレームの保管
    selector         advisor に渡す画像を、フレームの履歴から選ぶ選び方
    model            Decision Model
    output           操作を送る先
    advisors         advisor の役割の name → その役割の advisor (advisor_roles.ADVISOR_ROLES のすべての役割を含む)
    read_notes      ゲームの情報の 4 ファイルを読む関数 (種別 → 中身)
    questions        問いの一覧 (questions.build_questions)
    thresholds       advisor の問いのしきい値
    encode           フレームを JPEG にする関数 (run では decision_request.JpegCache を渡す)
    operation_log    応答と送った操作の記録と、判断のループを続けたまま中立に戻した記録
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
    request_log      要求ごとの入出力を書く先 (run では --save-requests が付いていれば request_log.RequestLog)。
                     None なら書かない
    timeline         系列情報の文を作る部品 (run では --timeline の節が 1 つ以上あれば timeline.builder.TimelineBuilder)。
                     None なら系列情報を足さない (要求の timeline_text は空文字)
    advisor_trigger  advisor の強制の呼び出しの方式 (run では --advisor-idle-seconds が 0 より大きければ
                     advisor_triggers.idle_timer.IdleTimerTrigger)。None なら強制では呼ばない
    """

    history: FrameHistory
    store: MeaningfulFrameStore
    selector: RecentFrameSelector
    model: DecisionModel
    output: PadOutputLike
    advisors: Mapping[str, Advisor]
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
    request_log: RequestLogLike | None = None
    timeline: TimelineLike | None = None
    advisor_trigger: AdvisorTrigger | None = None


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
        min_request_interval_s   直前の応答が返ってから (model.decide が戻るか DecisionModelError を出してから)、
                                 次の要求のフレームを選ぶまでに空ける時間 (秒)。0.0 なら空けない
        started_at               パイプラインの開始時刻 (__init__ での clock())。最初の advisor の操作の記録の起点で、
                                 要求と advisor に渡す画像のラベルと、操作の履歴に書く経過秒 (t) の起点
        request_sent             1 回でも model.decide を呼んだら (要求を送ったら) True
        request_count            送った要求の数 (0 始まり)。model.decide を呼ぶ前に 1 増やし、増やした後の値を
                                 その要求の通し番号 (requests.jsonl と run.jsonl の number) にする
        last_sent_frame_time     最後に成功した要求 (run_once の 10 まで進んだ要求) に載せたフレームの time。
                                 まだ無ければ None
        last_response_at         直前の model.decide が戻った (応答を受け取ったか DecisionModelError を出した) 時刻
                                 (clock() の値)。まだ model.decide を呼んでいなければ None
        last_advisor_at          advisor の役割の name → その役割の操作の記録の起点 (その役割の advisor が
                                 受け付けた要求の received_at。最初はどの役割も started_at)
        last_accepted_at         advisor の役割の name → その役割の advisor が最後に受け付けた要求の received_at。
                                 まだ受け付けていない役割は含まない (強制の呼び出しの AdvisorCallState に渡す)
        no_new_frame_since       「新しいフレームが無い」と最初に分かった時刻。新しいフレームがあると分かったら、
                                 要求を送る前に None に戻す (要求の成否によらない)
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
        min_request_interval_s: float = 0.0,
    ):
        """属性を入れる。

        started_at は parts.clock() の値、last_advisor_at は advisor_roles.ADVISOR_ROLES のどの役割も started_at に
        し、last_accepted_at は空の辞書、request_count は 0、last_response_at は None にする。
        """
        self.parts = parts
        self.max_consecutive_errors = max_consecutive_errors
        self.error_backoff_s = error_backoff_s
        self.no_frame_wait_s = no_frame_wait_s
        self.stale_frame_timeout_s = stale_frame_timeout_s
        self.first_frame_timeout_s = first_frame_timeout_s
        self.bridge_wait_s = bridge_wait_s
        self.min_request_interval_s = min_request_interval_s
        self.last_response_at: float | None = None
        self.started_at = parts.clock()
        self.request_sent = False
        self.request_count = 0
        self.last_sent_frame_time: float | None = None
        self.last_advisor_at: dict[str, float] = {
            role.name: self.started_at for role in ADVISOR_ROLES
        }
        self.last_accepted_at: dict[str, float] = {}
        self.no_new_frame_since: float | None = None
        self.consecutive_errors = 0

    def run_once(self) -> None:
        """1 回の要求と応答を行う。

        1. last_response_at が None でなければ、remaining = last_response_at + min_request_interval_s - clock() とし、
           remaining > 0 なら sleep(remaining) する。待った秒数 interval_wait_s は remaining (待たなければ 0.0)。
           待つのは 2 の確認と 3 のフレームの選択より前 (選ぶ 1 枚が、待つ前の古い画面にならないようにするため)
        2. capture_ended() が真なら、output.neutral() して CaptureStoppedError("capture ended") を出す。
           続けて reason = capture_lost() が None でなければ、output.neutral() して CaptureStoppedError(reason) を出す。
           続けて bridge_recovering() が真なら、要求を送らずに戻る (decision の log は渡さない)。run は始めに
           ブリッジが復旧中でないことを確かめてから run_once を呼ぶが、1 の待ちの間に復旧中に変わることがあり、
           そのまま送ると、届かない操作のために料金のかかる要求を 1 回送ってしまうため。中立に戻すことと
           NeutralRecord の記録は、次の回の run の始めの _wait_for_bridge_recovery に任せる
        3. frame = history.latest() とする。要求に載せるフレームは、この 1 枚だけ (意味のあるフレームは載せない)。
           frame が None か、その time が last_sent_frame_time 以下なら、新しいフレームが無い。
           このときは要求を送らず、次の「新しいフレームが無いときの扱い」をして戻る。
           新しいフレームなら now = clock() とし、no_new_frame_since = None にする (この後の要求が失敗しても None のまま。
           それまでフレームを待った時間を、失敗の後の上限の判定に持ち越さないため)
        4. notes = read_notes()。timeline があれば、timeline.observe_notes(notes, now) の後に
           timeline_text = timeline.build(now, started_at) とする (無ければ空文字)。
           build_decision_request(frame, now, notes, questions, encode,
           origin=started_at, recent_operations=operation_log.recent(DEFAULT_HISTORY_COUNT), timeline_text=timeline_text)。
           画像のラベルの前回の操作の前後は、operation_log の最新の記録の時刻 (operation_log.record_time) で決まる。
           組み立て終えたら encoded_at = clock() (model.decide を呼ぶ直前の時刻)
        5. request_sent = True、request_count を 1 増やす (増やした後の値 number がこの要求の通し番号)。
           sent_at = now、decision = model.decide(request)。戻ったら received_at = clock() とし、
           last_response_at = received_at にする。
           model.decide が DecisionModelError を出したら、すぐ last_response_at = clock() にする。続けて
           output.neutral() し (要求のログの書き込みで、中立に戻すのを遅らせないため)、
           operation_log.add(NeutralRecord(clock(), DECISION_ERROR_NEUTRAL_REASON))
           で中立に戻したことを記録する (次の要求の履歴に出し、画像のラベルの前回の操作の時刻にするため)。
           その後に要求のログに result="error"、error=例外の文字列 (str)、received_at・decision・action は None の
           記録を書いてから、その例外をそのまま出す (run の DecisionModelError の扱いでも、もう一度
           output.neutral() を呼ぶが、記録は足さない)。
           成功したら consecutive_errors = 0。続けて、応答を待つ間にキャプチャーが止まっていないかを確かめる:
           reason = capture_lost() とし、None で capture_ended() が真なら reason = "capture ended"。
           reason があれば応答を捨て (操作を反映せず、operation_log にも log の decision にも残さない)、
           まず output.neutral() し (要求のログの書き込みで、中立に戻すのを遅らせないため)、その後に要求のログに
           result="discarded"、error=reason、decision は応答、action は None の記録を書いてから、
           CaptureStoppedError(reason) を出す
        6. action = decide_action(decision.probabilities, decision.choices, questions, thresholds)、output.apply(action)。
           apply が pad_output.OutputStoppedError を出したら (5 の確認の後に、見張りがキャプチャーの停止を判定して
           出力を止めた)、5 と同じく応答を捨て、要求のログに result="discarded"、error=止まった理由、decision は応答、
           action は決めた操作の記録を書いてから、CaptureStoppedError(止まった理由。capture_lost() の理由で、無ければ
           "pad output was stopped") を出す。5 と違って中立に戻すのを待たずにログを書いてよいのは、見張りが出力を
           止めたときに、PadOutput.stop_output が既に中立を送っているため。
           apply が送信の例外 (RuntimeError / OSError) を出したら、要求のログに result="error"、
           error=describe_error(例外)、decision は応答、action は決めた操作の記録を書き、OutputSendError に包んで出す
        7. operation_log.add(OperationRecord(sent_at, received_at, action))
        8. 強制の呼び出し forced = _forced_calls(action, received_at) (役割の name → 理由の文) を求める:
           advisor_trigger があれば、advisor_trigger.due(AdvisorCallState(started_at, last_accepted_at,
           action.advisor_probabilities), received_at) の役割のうち、action.advisor_requested に無いものを返す
           (advisor の問いで求めた役割は、問いで呼んだものとして扱う)。advisor_trigger が無ければ空。
           action.advisor_requested か forced が空でなければ、_request_advisor_updates(action, forced, notes, received_at)
           で役割ごとの advisor に求め、(受け付けた役割の name の集合, 強制で求めた役割の name の集合) を
           (advisor_accepted, advisor_forced) にする (どちらも空なら呼ばず、どちらも空の集合)。
           _request_advisor_updates は次を行う:
           - advisor_roles.ADVISOR_ROLES の順に、action.advisor_requested か forced に入っていて、
             advisors[name].is_busy() が偽の役割だけを対象にする。is_busy() が真の役割には request_update を呼ばない
           - 対象が 1 つ以上あれば、advisor に渡す画像をその回に 1 回だけ選んで符号化し (_select_advisor_images)、
             対象のすべての役割で使い回す (advisor の実行中や、複数の役割を呼ぶときに、何度も最大 10 枚を符号化して
             速い層を遅らせないため)。対象が無ければ画像を選ばない。
             画像は、advisor_now = clock() として、frame_selection.advisor_frames.select_advisor_frames(history, store,
             selector, advisor_now) で選んだフレームを、advisor_images.build_advisor_images(選んだフレーム, advisor_now,
             origin=started_at, encode) で JPEG にしたもの (意味のあるフレーム → フレームの履歴から選んだもの、どちらも古い順)
           - 対象の役割ごとに (ADVISOR_ROLES の順に) AdvisorContext を作って advisors[name].request_update を呼ぶ。
             AdvisorContext は game、その画像、notes の strategy (無ければ空文字)、
             operation_log.format_since(last_advisor_at[name], received_at, name)、action.advisor_probabilities[name]、
             time.strftime("%H:%M:%S") の壁時計の時刻、強制で求めた役割なら forced[name] の理由 (trigger_reason。
             問いで求めた役割なら None) からなる
           - 受け付けられた (True) 役割の last_advisor_at[name] と last_accepted_at[name] を received_at にし
             (その役割の次の操作の記録の起点と、強制の呼び出しの数え始め)、timeline があれば
             timeline.record_advisor_call(name, received_at) を呼ぶ
           - (受け付けた役割の name の frozenset, 対象のうち forced に入っていた役割の name の frozenset) を返す
        9. 要求のログに result="ok"、error=None、decision は応答、action は送った操作、
           advisor_accepted=受け付けた役割の name の frozenset (呼ばなければ空の集合)、
           advisor_forced=強制で求めた役割の name の frozenset (呼ばなければ空の集合) の記録を書く
        10. last_sent_frame_time = frame.time。5・6 の例外で終わった要求では進めない (そのため、DecisionModelError や
           送信の失敗の後は、新しいフレームが届いていなくても、同じフレームで次の要求を送る)。
           log に次の辞書を渡す:
             {"event": "decision", "number": この要求の通し番号 (requests.jsonl の行と突き合わせるため),
              "interval_wait_s": 1 で待った秒数 (待たなければ 0.0),
              "select_s": 3 で now を取ってから 4 を始めるまでの時間, "encode_s": 4 の時間, "decide_s": model.decide の時間
              (HTTP を含む), "apply_s": output.apply の時間, "images": 要求の画像の枚数 (1),
              "payload_bytes": 要求の画像の JPEG の合計バイト数, "action": operation_log.format_action(action),
              "advisor_probabilities": 役割の name → その役割の問いの確率の辞書 (action.advisor_probabilities),
              "advisor_requested": action.advisor_requested の役割の name のリスト,
              "advisor_accepted": 受け付けた役割の name のリスト,
              "advisor_forced": 強制で求めた役割の name のリスト, "refusals": decision.refusals}
             (役割の name のリストは advisor_roles.ADVISOR_ROLES の順)
           ブリッジが復旧中で戻るときと、新しいフレームが無くて戻るときと、例外で終わるときは、この辞書を渡さない。

        要求のログ: parts.request_log が None でなければ、1 回の要求につき 1 件の request_log.RequestLogEntry
        (number、origin=started_at、sent_at、encoded_at、received_at、request、decision、action、advisor_accepted
        (受け付けた役割の name の frozenset。ok 以外は空の集合)、advisor_forced (強制で求めた役割の name の frozenset。
        ok 以外は空の集合)、result、error) を request_log.write に渡す。write が OSError か ValueError (文字の符号化の
        失敗など) を出したら、判断のループを止めないよう捕まえ、log に {"event": "request_log_error",
        "error": describe_error(例外)} を渡して続ける
        (write が失敗しても、5・6 で出す例外は 5・6 のとおりに出す)。

        新しいフレームが無いときの扱い: no_new_frame_since が None なら clock() の値を入れる。
        clock() - no_new_frame_since が上限以上なら、output.neutral() して CaptureStoppedError を出す
        (古い画面のまま入力を出し続けない)。そうでなければ sleep(no_frame_wait_s) する。上限は、まだ 1 回も
        要求を送っていなければ first_frame_timeout_s (起動直後は最初のフレームを待つ)、送った後は stale_frame_timeout_s。

        例外: model.decide の DecisionModelError はそのまま出す。output.apply の送信の例外 (RuntimeError / OSError) は
        OutputSendError に包んで出す (扱いは run が決める)。
        """
        parts = self.parts
        interval_wait_s = self._wait_for_request_interval()
        if parts.capture_ended():
            parts.output.neutral()
            raise CaptureStoppedError("capture ended")
        lost_reason = parts.capture_lost()
        if lost_reason is not None:
            parts.output.neutral()
            raise CaptureStoppedError(lost_reason)
        if parts.bridge_recovering():
            # 待つ間にブリッジの復旧が始まった。中立に戻すことは、次の回の run の始めの復旧の待ちに任せる
            return
        frame = parts.history.latest()
        if frame is None or (
            self.last_sent_frame_time is not None
            and frame.time <= self.last_sent_frame_time
        ):
            self._wait_for_new_frame()
            return

        now = parts.clock()
        self.no_new_frame_since = None
        selected_at = parts.clock()

        notes = parts.read_notes()
        timeline_text = ""
        if parts.timeline is not None:
            parts.timeline.observe_notes(notes, now)
            timeline_text = parts.timeline.build(now, self.started_at)
        request = build_decision_request(
            frame,
            now,
            notes,
            parts.questions,
            parts.encode,
            origin=self.started_at,
            recent_operations=parts.operation_log.recent(DEFAULT_HISTORY_COUNT),
            timeline_text=timeline_text,
        )
        encoded_at = parts.clock()

        self.request_sent = True
        self.request_count += 1
        number = self.request_count
        sent_at = now
        try:
            decision = parts.model.decide(request)
        except DecisionModelError as e:
            # 失敗の応答も、次の要求までの間隔の起点にする
            self.last_response_at = parts.clock()
            # 中立に戻し、そのことを操作の記録に足してから要求のログを書く。例外は run に任せる
            parts.output.neutral()
            parts.operation_log.add(
                NeutralRecord(parts.clock(), DECISION_ERROR_NEUTRAL_REASON)
            )
            self._write_request_log(
                number, sent_at, encoded_at, request, result="error", error=str(e)
            )
            raise
        received_at = parts.clock()
        self.last_response_at = received_at
        self.consecutive_errors = 0
        # 応答を待つ間にキャプチャーが止まっていたら、古い画面への応答なので操作を反映しない
        lost_reason = parts.capture_lost()
        if lost_reason is None and parts.capture_ended():
            lost_reason = "capture ended"
        if lost_reason is not None:
            parts.output.neutral()
            self._write_request_log(
                number,
                sent_at,
                encoded_at,
                request,
                result="discarded",
                error=lost_reason,
                received_at=received_at,
                decision=decision,
            )
            raise CaptureStoppedError(lost_reason)

        action = decide_action(
            decision.probabilities, decision.choices, parts.questions, parts.thresholds
        )
        apply_started = parts.clock()
        try:
            parts.output.apply(action)
        except OutputStoppedError:
            # 上の確認の後に見張りが出力を止めた (中立は stop_output が送っている)。操作は送られていないので、
            # operation_log には記録せずに止まる
            stopped_reason = parts.capture_lost() or "pad output was stopped"
            self._write_request_log(
                number,
                sent_at,
                encoded_at,
                request,
                result="discarded",
                error=stopped_reason,
                received_at=received_at,
                decision=decision,
                action=action,
            )
            raise CaptureStoppedError(stopped_reason) from None
        except (RuntimeError, OSError) as e:
            self._write_request_log(
                number,
                sent_at,
                encoded_at,
                request,
                result="error",
                error=describe_error(e),
                received_at=received_at,
                decision=decision,
                action=action,
            )
            raise OutputSendError(e) from e
        applied_at = parts.clock()

        parts.operation_log.add(OperationRecord(sent_at, received_at, action))
        forced = self._forced_calls(action, received_at)
        advisor_accepted: frozenset[str] = frozenset()
        advisor_forced: frozenset[str] = frozenset()
        if action.advisor_requested or forced:
            advisor_accepted, advisor_forced = self._request_advisor_updates(
                action, forced, notes, received_at
            )
        self._write_request_log(
            number,
            sent_at,
            encoded_at,
            request,
            result="ok",
            error=None,
            received_at=received_at,
            decision=decision,
            action=action,
            advisor_accepted=advisor_accepted,
            advisor_forced=advisor_forced,
        )

        self.last_sent_frame_time = frame.time
        parts.log(
            {
                "event": "decision",
                "number": number,
                "interval_wait_s": interval_wait_s,
                "select_s": selected_at - now,
                "encode_s": encoded_at - selected_at,
                "decide_s": received_at - encoded_at,
                "apply_s": applied_at - apply_started,
                "images": len(request.images),
                "payload_bytes": sum(len(image.jpeg) for image in request.images),
                "action": format_action(action),
                "advisor_probabilities": dict(action.advisor_probabilities),
                "advisor_requested": [
                    role.name
                    for role in ADVISOR_ROLES
                    if role.name in action.advisor_requested
                ],
                "advisor_accepted": [
                    role.name for role in ADVISOR_ROLES if role.name in advisor_accepted
                ],
                "advisor_forced": [
                    role.name for role in ADVISOR_ROLES if role.name in advisor_forced
                ],
                "refusals": decision.refusals,
            }
        )

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで run_once を繰り返す。

        各回の始めに、bridge_recovering() が真で capture_lost() が None なら、run_once を呼ばずに
        _wait_for_bridge_recovery(stop) で復旧が終わるのを待ち、その後は while の条件の確認に戻る (復旧中は要求を
        送らない)。送信の失敗で復旧が始まった場合と、run_once の 1 の待ちの間に復旧が始まった場合 (run_once は
        要求を送らずに戻る) も、次の回の始めの確認で待つ。capture_lost() が理由を返すときは、
        復旧中でも待たずに run_once を呼び、その始めの確認で止まる。run_once の例外は次のように扱う:
        - DecisionModelError: output.neutral() し (中立に戻したことは run_once が operation_log に記録済みのため、
          記録は足さない)、log に {"event": "decision_error", "error": <説明>} を渡し、
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

        1. output.neutral() を 1 回呼び、operation_log.add(NeutralRecord(<neutral を呼ぶ直前の clock の値>,
           BRIDGE_WAIT_NEUTRAL_REASON)) で中立に戻したことを記録して (復旧後の要求の履歴に出し、画像のラベルの
           前回の操作の時刻にするため)、log に {"event": "bridge_wait"} を渡す
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
        parts.operation_log.add(NeutralRecord(started, BRIDGE_WAIT_NEUTRAL_REASON))
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

    def _write_request_log(
        self,
        number: int,
        sent_at: float,
        encoded_at: float,
        request: DecisionRequest,
        result: Literal["ok", "error", "discarded"],
        error: str | None,
        received_at: float | None = None,
        decision: Decision | None = None,
        action: PadAction | None = None,
        advisor_accepted: frozenset[str] = frozenset(),
        advisor_forced: frozenset[str] = frozenset(),
    ) -> None:
        """parts.request_log があれば、要求 1 回分の RequestLogEntry (origin は started_at) を書く。

        write の OSError と ValueError (文字の符号化の失敗など) は判断のループを止めないよう捕まえ、
        log に {"event": "request_log_error", "error": <説明>} を渡して戻る (run_once の docstring の「要求のログ」)。
        """
        request_log = self.parts.request_log
        if request_log is None:
            return
        entry = RequestLogEntry(
            number=number,
            origin=self.started_at,
            sent_at=sent_at,
            encoded_at=encoded_at,
            received_at=received_at,
            request=request,
            decision=decision,
            action=action,
            advisor_accepted=advisor_accepted,
            advisor_forced=advisor_forced,
            result=result,
            error=error,
        )
        try:
            request_log.write(entry)
        except (OSError, ValueError) as e:
            self.parts.log({"event": "request_log_error", "error": describe_error(e)})

    def _wait_for_request_interval(self) -> float:
        """直前の応答から min_request_interval_s が過ぎるまで待ち、待った秒数を返す (run_once の 1)。

        last_response_at が None か、残りの時間が 0 以下なら、待たずに 0.0 を返す。
        """
        if self.last_response_at is None:
            return 0.0
        remaining = (
            self.last_response_at + self.min_request_interval_s - self.parts.clock()
        )
        if remaining <= 0:
            return 0.0
        self.parts.sleep(remaining)
        return remaining

    def _forced_calls(self, action: PadAction, now: float) -> dict[str, str]:
        """強制で呼ぶ役割の name → 理由の文を返す (run_once の 8)。

        advisor_trigger が無ければ空の辞書。あれば、advisor_trigger.due の役割のうち、action.advisor_requested に
        無いもの (advisor の問いで求めていない役割) だけを返す。
        """
        trigger = self.parts.advisor_trigger
        if trigger is None:
            return {}
        state = AdvisorCallState(
            started_at=self.started_at,
            last_accepted=dict(self.last_accepted_at),
            advisor_probabilities=dict(action.advisor_probabilities),
        )
        return {
            call.role_name: call.reason
            for call in trigger.due(state, now)
            if call.role_name not in action.advisor_requested
        }

    def _request_advisor_updates(
        self,
        action: PadAction,
        forced: Mapping[str, str],
        notes: Mapping[str, str],
        received_at: float,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """action.advisor_requested と forced の役割の advisor に求める (run_once の 8)。

        (受け付けた役割の name の集合, 強制で求めた役割の name の集合) を返す。
        forced は _forced_calls の結果、notes はその要求で読んだゲームの情報 (種別 → 中身)、
        received_at はその要求の応答を受け取った時刻。
        """
        parts = self.parts
        targets = [
            role.name
            for role in ADVISOR_ROLES
            if (role.name in action.advisor_requested or role.name in forced)
            and not parts.advisors[role.name].is_busy()
        ]
        if not targets:
            return frozenset(), frozenset()
        # 呼ぶ役割が複数でも、画像は 1 回だけ選んで符号化し、使い回す
        images = self._select_advisor_images()
        trigger_time_text = time.strftime("%H:%M:%S")
        accepted: set[str] = set()
        for name in targets:
            context = AdvisorContext(
                game=parts.game,
                images=images,
                current_strategy=notes.get("strategy", ""),
                operation_log_text=parts.operation_log.format_since(
                    self.last_advisor_at[name], received_at, name
                ),
                trigger_probability=action.advisor_probabilities[name],
                trigger_time_text=trigger_time_text,
                trigger_reason=forced.get(name),
            )
            if parts.advisors[name].request_update(context):
                self.last_advisor_at[name] = received_at
                self.last_accepted_at[name] = received_at
                if parts.timeline is not None:
                    parts.timeline.record_advisor_call(name, received_at)
                accepted.add(name)
        return frozenset(accepted), frozenset(
            name for name in targets if name in forced
        )

    def _select_advisor_images(self) -> list[LabeledImage]:
        """advisor に渡す画像を、今の時刻 (clock()) で要求とは別に選んで JPEG にする (run_once の 8)。"""
        parts = self.parts
        now = parts.clock()
        frames = select_advisor_frames(parts.history, parts.store, parts.selector, now)
        return build_advisor_images(frames, now, self.started_at, parts.encode)

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
