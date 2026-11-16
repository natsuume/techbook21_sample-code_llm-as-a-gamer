"""Orchestrator 型の操作の実行と監視 (OrchestratorController)。主ループ (Claude Code) の Decision を当て、操作の列と
「画面の条件 → 操作」の Jev の監視を 1 つのループ (run) で動かす。

主ループのスレッドは、parse_decision の結果を apply で渡し、次の入力を作るときに take_events で出来事を取り出す。
apply・take_events・macro_names・set_started はどのスレッドから呼んでもよい (状態は 1 つのロックの中で変える)。
PadOutput への送信と orchestrator/monitor.jsonl への書き込みは、run のループのスレッドだけが行う。Jev への要求は、
ループが別のスレッドで送る (要求の応答を待つ間も、主ループの操作をすぐ始めるため)。

apply (decision_parser.parse_decision を通った Decision を受け取る):
    1. マクロの呼び出しを確かめる: decision.actions と、monitor が MonitorCreate ならその条件の actions にある
       MacroAction の名前が、MacroRegistry.names_after(decision.macros_set, decision.macros_delete) に無ければ、
       何も変えずに ValueError を出す (parse_decision に今の登録 (macro_names) を渡していれば起きない)
    2. マクロ: MacroRegistry.apply_changes(macros_set, macros_delete)
    3. 監視: MonitorCreate なら今の監視を止めて、新しい監視を作る (条件の操作の列は、2 の後のマクロで今展開する)。
       新しい監視には、1 から始まる連番の世代を振る (同じ内容の監視を作り直したときも新しい番号。monitor_loop の
       「監視の世代」)。MonitorStop なら今の監視を止める。None なら変えない。止めた監視の操作の列は捨てる (次の回りで
       中立に戻す)。止めた監視の MonitorEnd は知らせに入れない
    4. 操作: decision.actions が空なら何もしない (実行中の操作の列も止めない)。空でなければ、主ループの操作の残りの数
       (実行中の操作と、まだ始めていない操作を含む) を捨てた操作の数に足し、actions を expand_actions で展開した列を
       次の回りで始める主ループの列にする (残りの操作の数は、この時点で新しい列の数になる)
    PadOutput には送らない。
run のループの 1 回の回り (stop が立つまで繰り返す。1〜4 を済ませてから 5 で待つ):
    1. set_started の後なら、apply の変更を送信に当てる: 主ループの新しい列があれば、実行中の列 (主ループか監視) を
       捨てて、新しい列を始める。監視を止めたか置き換えたなら、その監視の操作の列を捨てる。列を捨てるときは、
       実行中の列があったときだけ PadOutput.neutral で中立に戻す (ActionExecutor.cancel。列が無ければ何も送らない)。
       set_started の前は何も送らない (受け付けた列は持っておき、set_started の後の回りで始める)
    2. 返った Jev の応答を処理する (下の「監視」)
    3. 実行中の列を、clock の今の時刻まで進める (ActionExecutor.advance)
    4. 監視の要求を出せるなら、別のスレッド (daemon) で monitor_loop.ask_monitor を 1 回呼ぶ。そのスレッドの中で
       例外 (Exception。画像の作成の失敗など、jev_client.JevError 以外を含む) が出たら、error を
       error_text.describe_exception(例外) にした失敗の結果 (MonitorRequestResult) に変えてループに返す (スレッドの外に
       例外を出さない)。結果を返したら、例外のときも含めて必ず「返っていない要求」の状態を消す
    5. parts.wait(wake, timeout) を 1 回呼んで待つ。timeout は settings.poll_interval_s 以下。wake は apply・
       set_started・Jev の応答が返ったときに立て、待ち終えたら下ろす
    stop が立ったら、PadOutput.neutral で中立に戻して戻る。返っていない Jev の要求は待たない (後で返った応答は、
    記録も判定もしない)。
監視:
    要求を出せるのは、次のすべてに当たるとき:
        - set_started の後で、監視があり、主ループの操作の残りが無く、監視の操作の列を実行していない
        - 出力が止まっていない (ActionExecutor.output_stopped が False。PadOutput.stop_output の後は、操作を
          送ろうとする前でも、要求を送らない)
        - 返っていない要求が無い (同時に送る要求は 1 つまで。監視を置き換えた後も、前の監視の要求が返るまで次を送らない)
        - latest_frame() がフレームを返し、そのフレームの時刻 (CapturedFrame.t) が、最後に操作を離した時刻
          (ActionExecutor.last_release_t。主ループの操作と監視の操作の両方) に MONITOR_FRAME_SETTLE_S を足した時刻
          (ミリ秒に丸めた値) より後である。まだ何も離していなければ、時刻は問わない。後のフレームが無ければ、届くまで要求を送らない
          (キャプチャーとゲームの反応の遅れの分、操作の前の画面が映ったフレームで問い、every の条件を二重に実行
          しないため)
    前の応答を処理した回りのうちに、出せるなら次の要求を送る。要求には、その時点の監視の世代を付ける。
    応答の処理:
        - orchestrator/monitor.jsonl に 1 行書く (monitor_record。hit は下で操作を始めた条件)
        - 要求を送った監視の世代 (MonitorRequestResult.generation) が今の監視の世代と違えば (止めたか置き換えた。
          同じ内容の監視を作り直した場合を含む)、ほかに何もしない (当たりにも失敗の回数にも入れない)
        - 失敗 (MonitorRequestResult.error がある。要求のスレッドの例外を変えた結果を含む): ActiveMonitor.record_failure。
          MonitorEnd が返ったら監視を止め、知らせに入れる (実行全体は止めない)
        - 答えがあった: ActiveMonitor.record_answer (失敗の回数を 0 に戻す)。要求を送った後に、空でない actions を
          apply で受け付けていれば、ほかに何もしない (操作の前の画面への答えで操作しない)。当たった条件があれば、
          条件の操作の列 (ActiveMonitor.operations_of。展開した後の列) を監視の列 (source が monitor) として始め、
          その場で 1 回進める (ActionExecutor.advance)。押す操作を 1 つも送らないうちに出力の停止が分かったら
          (ActionExecutor.stopped_before_sending。列を始める時点で止まっていたときと、最初の押す操作が
          OutputStoppedError で拒まれたとき)、当たりに数えず、monitor.jsonl の hit も null にする。始められたら、
          その条件の CaseHit の回数を 1 足す。展開した後の列が空なら (条件の actions が空のときと、展開すると空に
          なるマクロのとき)、列を始めない。その時点で出力が止まっていれば (ActionExecutor.output_stopped)、
          空でない列と同じく当たりに数えず、hit も null にする。止まっていなければ、CaseHit の回数を 1 足し、
          その場で次の「実行し終えた」の扱いをする
        - 監視の列を最後まで実行し終えたら、ActiveMonitor.case_finished。MonitorEnd が返ったら (once の条件) 監視を
          止め、知らせに入れる。主ループの列か監視の停止・置き換えで途中で捨てた列は、実行し終えたことにしない
take_events:
    前回の呼び出し以降の case_hits・monitor_ends・discarded_action_count・blocked_buttons (主ループと監視の両方の操作で
    外したボタン) を返して空にし、running・remaining_action_count・macros・monitor は呼んだ時点の値を返す
    (orchestrator_events.OrchestratorEvents の欄の説明)。
    case_hits は、監視の世代と条件の位置の組ごとに 1 つにまとめる。同じ名前の監視でも、世代が違えば別の CaseHit に
    する。CaseHit の condition と actions は、当たった世代の監視の条件のもの。
操作の記録:
    PadOutput.apply の source に、主ループの列なら orchestrator、監視の列なら monitor を渡す (action_executor の
    「操作の記録」)。operations.jsonl の行は、PadOutput の on_operation (run では OperationLog.write) が 1 回の送信に
    つき 1 行書く。このクラスは記録の関数を受け取らず、operations.jsonl に行を書かない。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from switch2_layers.error_text import describe_exception
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.orchestrator.action_executor import (
    ActionExecutor,
    PrimitiveAction,
    expand_actions,
)
from switch2_layers.orchestrator.decision_types import (
    Action,
    Decision,
    MacroAction,
    MonitorCreate,
)
from switch2_layers.orchestrator.macro_registry import MacroRegistry
from switch2_layers.orchestrator.monitor_loop import (
    ActiveMonitor,
    MonitorAsker,
    MonitorRequestResult,
    ask_monitor,
)
from switch2_layers.orchestrator.monitor_record import MonitorLog, MonitorRecord
from switch2_layers.orchestrator.orchestrator_events import (
    CaseHit,
    MonitorEnd,
    OrchestratorEvents,
)
from switch2_layers.pad_action import sort_buttons
from switch2_layers.run_clock import RunClock, round_to_milliseconds
from switch2_layers.run_directory import RunPaths
from switch2_layers.safety.safety_monitor import SAFETY_IMAGE_WIDTH
from switch2_layers.surface.jev_client import new_asset_reference

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# 監視の当たりのしきい値の既定値 (--monitor-threshold)
DEFAULT_MONITOR_THRESHOLD = 0.5
# 監視の Jev の失敗が続いたら監視を止める回数の既定値 (--max-monitor-errors)
DEFAULT_MAX_MONITOR_ERRORS = 5
# run のループが 1 回の回りで待つ秒数の上限 (操作の秒数の期限と、apply・stop を確かめ直す間隔)
POLL_INTERVAL_S = 0.01
# 操作を離してから、監視の要求に使うフレームを撮るまでに置く最小の間 (秒)。キャプチャーの遅れとゲームが操作に
# 反応して画面を変えるまでの遅れを見込む値で、実機では測っていない
MONITOR_FRAME_SETTLE_S = 0.3


def wait_for_wake(wake: threading.Event, timeout: float) -> None:
    """run のループの待ちの既定 (OrchestratorParts.wait): wake が立つか timeout 秒たつまで待つ (wake.wait(timeout))。"""
    wake.wait(timeout)


@dataclass(frozen=True)
class OrchestratorParts:
    """OrchestratorController が使う部品。

    pad_output       操作の送信 (ボタンの制限を含む。押せるボタンの集合と、operations.jsonl に書く on_operation は
                     作った側が決める)
    jev              監視の要求の送り先 (surface.jev_client.JevClient と同じ ask を持つもの)
    latest_frame     キャプチャーの最新のフレームを返す関数 (run では FrameBuffer.latest)。まだ無ければ None
    clock            実行の経過秒の時計。操作の秒数と monitor.jsonl の時刻に使う (PadOutput の clock と同じ monotonic
                     から作る)
    paths            実行ディレクトリー (orchestrator/monitor.jsonl に書く)
    wait             run のループが回りの最後に待つ関数 (wake, timeout を受け取る)。既定 wait_for_wake。
                     テストでは、回りの数を数える関数を渡せる
    make_reference   画像の参照文字列を作る関数 (既定 jev_client.new_asset_reference。呼ぶたびに異なる文字列を返す)
    """

    pad_output: PadOutput
    jev: MonitorAsker
    latest_frame: Callable[[], CapturedFrame | None]
    clock: RunClock
    paths: RunPaths
    wait: Callable[[threading.Event, float], None] = wait_for_wake
    make_reference: Callable[[], str] = new_asset_reference


@dataclass(frozen=True)
class OrchestratorSettings:
    """OrchestratorController の設定。

    monitor_threshold    監視の当たりのしきい値 (--monitor-threshold。0 より大きく 1 未満)
    max_monitor_errors   監視の Jev の失敗がこの回数続いたら監視を止める (--max-monitor-errors。1 以上)
    poll_interval_s      run のループが 1 回の回りで待つ秒数の上限
    image_width          監視に送る画像の幅の上限 (安全の監視と同じ)
    jpeg_quality         監視に送る画像の JPEG の品質
    """

    monitor_threshold: float
    max_monitor_errors: int
    poll_interval_s: float = POLL_INTERVAL_S
    image_width: int = SAFETY_IMAGE_WIDTH
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass
class _CaseHitCount:
    """監視の世代と条件の位置の組 1 つの、前回の取り出し以降に当たった回数 (CaseHit の元)。"""

    monitor: str
    case_index: int
    condition: str
    actions: tuple[Action, ...]
    count: int


@dataclass(frozen=True)
class _ReturnedRequest:
    """返った監視の要求 1 つ。

    result          要求の結果
    actions_serial  要求を送ったときの、空でない actions を受け付けた回数 (送った後に受け付けたかを見分ける)
    """

    result: MonitorRequestResult
    actions_serial: int


class OrchestratorController:
    """主ループの Decision を当て、操作の列と監視を run のループで動かす (モジュールの docstring)。"""

    def __init__(
        self, parts: OrchestratorParts, settings: OrchestratorSettings
    ) -> None:
        """マクロも監視も操作の列も無く、set_started の前の状態で作る。レポートは送らず、スレッドも起動しない。"""
        self._parts = parts
        self._settings = settings
        # 下の状態は、すべてこのロックの中で読み書きする (run のループの 1 回の回りは、ロックを持ったまま行う)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._macros = MacroRegistry()
        self._executor = ActionExecutor(parts.pad_output, parts.clock)
        self._monitor_log = MonitorLog(parts.paths.orchestrator_monitor_log)
        self._started = False
        self._run_called = False
        # run が戻った後は、返った要求を記録しない
        self._closed = False
        self._monitor: ActiveMonitor | None = None
        self._next_generation = 1
        # apply が受け付け、次の回りで始める主ループの列。無ければ None
        self._pending_main: tuple[PrimitiveAction, ...] | None = None
        # 空でない actions を受け付けた回数
        self._actions_serial = 0
        # 実行中の監視の列の (世代, 条件の位置)。監視の列を実行していなければ None
        self._running_case: tuple[int, int] | None = None
        # 止めたか置き換えた監視の列を、次の回りで捨てる
        self._discard_monitor_operations = False
        self._request_in_flight = False
        self._returned: list[_ReturnedRequest] = []
        # 主ループへの知らせ (take_events で取り出す)
        self._case_hits: dict[tuple[int, int], _CaseHitCount] = {}
        self._monitor_ends: list[MonitorEnd] = []
        self._discarded = 0
        self._blocked: set[str] = set()

    def apply(self, decision: Decision) -> None:
        """decision のマクロ・監視・操作を、この順に当てる (モジュールの docstring の apply)。

        登録に無いマクロを呼んでいれば、何も変えずに ValueError を出す。
        """
        with self._lock:
            # 1: 確かめと展開を、何も変える前に済ませる
            self._check_macro_calls_locked(decision)
            macros_after = self._macros.snapshot()
            macros_after.update(decision.macros_set)
            for name in decision.macros_delete:
                macros_after.pop(name, None)
            main_operations = (
                expand_actions(decision.actions, macros_after)
                if decision.actions
                else None
            )
            new_monitor: ActiveMonitor | None = None
            if isinstance(decision.monitor, MonitorCreate):
                new_monitor = ActiveMonitor(
                    decision.monitor,
                    self._next_generation,
                    [
                        expand_actions(case.actions, macros_after)
                        for case in decision.monitor.cases
                    ],
                    self._settings.monitor_threshold,
                    self._settings.max_monitor_errors,
                )
            # 2: マクロ
            self._macros.apply_changes(decision.macros_set, decision.macros_delete)
            # 3: 監視
            if decision.monitor is not None:
                self._stop_monitor_locked()
            if new_monitor is not None:
                self._monitor = new_monitor
                self._next_generation += 1
            # 4: 操作
            if main_operations is not None:
                self._discarded += self._main_remaining_locked()
                self._pending_main = main_operations
                self._actions_serial += 1
        self._wake.set()

    def take_events(self) -> OrchestratorEvents:
        """前回の呼び出し以降に起きたことと、今のマクロと監視の一覧を返す (モジュールの docstring の take_events)。"""
        with self._lock:
            remaining = self._main_remaining_locked()
            events = OrchestratorEvents(
                case_hits=tuple(
                    CaseHit(
                        monitor=hit.monitor,
                        case_index=hit.case_index,
                        condition=hit.condition,
                        count=hit.count,
                        actions=hit.actions,
                    )
                    for hit in self._case_hits.values()
                ),
                monitor_ends=tuple(self._monitor_ends),
                discarded_action_count=self._discarded,
                blocked_buttons=tuple(sort_buttons(self._blocked)),
                running=remaining > 0,
                remaining_action_count=remaining,
                macros=self._macros.snapshot(),
                monitor=None if self._monitor is None else self._monitor.monitor,
            )
            self._case_hits = {}
            self._monitor_ends = []
            self._discarded = 0
            self._blocked = set()
            return events

    def macro_names(self) -> frozenset[str]:
        """今登録しているマクロの名前の集合を返す。出来事は取り出さない。

        decision_parser.parse_decision の registered_macros に渡す。
        """
        with self._lock:
            return self._macros.names()

    def set_started(self) -> None:
        """実行を始めるときの安全の確かめが通ったことを伝える。これより前は、操作も監視の要求も送らない。

        2 回目以降の呼び出しは何もしない。
        """
        with self._lock:
            self._started = True
        self._wake.set()

    def run(self, stop: threading.Event) -> None:
        """操作の実行と監視のループ (モジュールの docstring の run)。stop が立ったら、押下状態を中立に戻して戻る。

        ループの中の想定外の例外 (monitor.jsonl の追記の失敗など) は、PadOutput.neutral で中立に戻してから、そのまま
        外に出す (組み立てる側が全体を止める)。1 つの OrchestratorController で 1 回だけ呼ぶ (2 回目は RuntimeError)。
        """
        with self._lock:
            if self._run_called:
                raise RuntimeError("OrchestratorController.run can be called only once")
            self._run_called = True
        try:
            while not stop.is_set():
                # 回りの途中で立った wake は残し、次の待ちをすぐ終える
                self._wake.clear()
                with self._lock:
                    self._run_round_locked()
                if stop.is_set():
                    break
                self._parts.wait(self._wake, self._settings.poll_interval_s)
        finally:
            with self._lock:
                self._closed = True
            self._parts.pad_output.neutral()

    # --- run のループの 1 回の回り (呼び出し側がロックを持つ) ---

    def _run_round_locked(self) -> None:
        """モジュールの docstring の「run のループの 1 回の回り」の 1〜4 を行う。"""
        if self._started:
            self._apply_pending_changes_locked()
        self._handle_returned_requests_locked()
        if not self._started:
            return
        self._executor.advance()
        self._blocked.update(self._executor.take_blocked_buttons())
        if self._executor.take_finished_owner() == "monitor":
            self._finish_running_case_locked()
        self._start_request_if_possible_locked()

    def _apply_pending_changes_locked(self) -> None:
        """apply が受け付けた変更を送信に当てる (回りの 1)。"""
        if self._discard_monitor_operations:
            self._discard_monitor_operations = False
            if self._executor.owner == "monitor":
                self._executor.cancel()
        if self._pending_main is not None:
            if self._executor.owner == "monitor":
                self._running_case = None
            self._executor.start(self._pending_main, "orchestrator")
            self._pending_main = None

    def _handle_returned_requests_locked(self) -> None:
        """返った監視の要求を処理し、1 つごとに monitor.jsonl に 1 行書く (回りの 2)。"""
        returned, self._returned = self._returned, []
        for request in returned:
            result = request.result
            hit = self._judge_result_locked(request)
            self._monitor_log.write(
                MonitorRecord(
                    t_sent=result.t_sent,
                    t_received=result.t_received,
                    monitor=result.monitor.name,
                    frame_t=result.frame_t,
                    probabilities=result.probabilities,
                    hit=hit,
                    usage=result.usage,
                    error=result.error,
                )
            )

    def _judge_result_locked(self, request: _ReturnedRequest) -> int | None:
        """要求の結果で失敗の回数と当たりを決め、当たった条件の操作を始める。操作を始めた条件の位置を返す。"""
        monitor = self._monitor
        result = request.result
        if monitor is None or monitor.generation != result.generation:
            return None
        if result.error is not None or result.probabilities is None:
            end = monitor.record_failure(
                result.error or "jev returned no probabilities"
            )
            if end is not None:
                self._end_monitor_locked(end)
            return None
        case_index = monitor.record_answer(result.probabilities)
        if case_index is None or request.actions_serial != self._actions_serial:
            return None
        operations = monitor.operations_of(case_index)
        if not operations:
            # 列を始める時点で出力が止まっていたら、空でない列と同じく当たりに数えない
            if self._executor.output_stopped:
                return None
            self._count_case_hit_locked(monitor, case_index)
            self._case_done_locked(monitor, case_index)
            return case_index
        # 列を始めてすぐ 1 回進め、押す操作を 1 つも送らないうちに出力の停止が分かったら当たりに数えない
        self._executor.start(operations, "monitor")
        self._executor.advance()
        if self._executor.stopped_before_sending:
            return None
        self._count_case_hit_locked(monitor, case_index)
        self._running_case = (monitor.generation, case_index)
        return case_index

    def _count_case_hit_locked(self, monitor: ActiveMonitor, case_index: int) -> None:
        """監視の世代と条件の位置の組の、当たった回数を 1 足す。"""
        key = (monitor.generation, case_index)
        hit = self._case_hits.get(key)
        if hit is not None:
            hit.count += 1
            return
        case = monitor.monitor.cases[case_index]
        self._case_hits[key] = _CaseHitCount(
            monitor=monitor.monitor.name,
            case_index=case_index,
            condition=case.condition,
            actions=case.actions,
            count=1,
        )

    def _finish_running_case_locked(self) -> None:
        """監視の列を最後まで実行し終えたときの扱い (回りの 3 の後)。"""
        running = self._running_case
        self._running_case = None
        monitor = self._monitor
        if running is None or monitor is None or monitor.generation != running[0]:
            return
        self._case_done_locked(monitor, running[1])

    def _case_done_locked(self, monitor: ActiveMonitor, case_index: int) -> None:
        """条件 case_index の実行を終えたことを記録し、once の条件なら監視を終える。"""
        end = monitor.case_finished(case_index)
        if end is not None:
            self._end_monitor_locked(end)

    def _end_monitor_locked(self, end: MonitorEnd) -> None:
        """今の監視を終え、終わったことを知らせに入れる。"""
        self._monitor_ends.append(end)
        self._monitor = None
        self._running_case = None

    def _stop_monitor_locked(self) -> None:
        """今の監視を止める (MonitorEnd は入れない)。監視の列を実行中なら、次の回りで捨てる。"""
        if self._monitor is None:
            return
        self._monitor = None
        if self._running_case is not None:
            self._running_case = None
            self._discard_monitor_operations = True

    def _start_request_if_possible_locked(self) -> None:
        """要求を出せるなら、別のスレッドで監視の要求を 1 回送る (回りの 4)。"""
        monitor = self._monitor
        if monitor is None or self._request_in_flight or self._executor.output_stopped:
            return
        if self._pending_main is not None or self._executor.remaining_count > 0:
            return
        frame = self._parts.latest_frame()
        if frame is None:
            return
        last_release_t = self._executor.last_release_t
        # 時刻はどちらもミリ秒に丸めた値なので、足した値も丸めてから比べる (丸めないと、float の和が
        # ミリ秒の境界よりわずかに小さくなり、境界ちょうどのフレームで問うことがある)
        if last_release_t is not None and frame.t <= round_to_milliseconds(
            last_release_t + MONITOR_FRAME_SETTLE_S
        ):
            return
        self._request_in_flight = True
        thread = threading.Thread(
            target=self._send_request,
            args=(monitor.monitor, monitor.generation, frame, self._actions_serial),
            name="orchestrator-monitor-request",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            self._request_in_flight = False
            raise

    def _send_request(
        self,
        monitor: MonitorCreate,
        generation: int,
        frame: CapturedFrame,
        actions_serial: int,
    ) -> None:
        """監視の要求を 1 回送り、結果をループに返す (要求のスレッドの本体)。"""
        result: MonitorRequestResult | None = None
        try:
            result = ask_monitor(
                self._parts.jev,
                monitor,
                generation,
                frame,
                self._parts.clock,
                self._settings.image_width,
                self._settings.jpeg_quality,
                self._parts.make_reference,
            )
        except Exception as error:
            logger.debug("monitor request thread failed", exc_info=True)
            now = self._parts.clock.now()
            result = MonitorRequestResult(
                monitor=monitor,
                generation=generation,
                frame_t=frame.t,
                t_sent=now,
                t_received=now,
                probabilities=None,
                usage=None,
                error=describe_exception(error),
            )
        finally:
            with self._lock:
                self._request_in_flight = False
                if result is not None and not self._closed:
                    self._returned.append(_ReturnedRequest(result, actions_serial))
            self._wake.set()

    # --- apply と take_events の下請け (呼び出し側がロックを持つ) ---

    def _check_macro_calls_locked(self, decision: Decision) -> None:
        """decision が呼ぶマクロが、decision の macros を当てた後の登録にあることを確かめる。無ければ ValueError。"""
        available = self._macros.names_after(
            decision.macros_set, decision.macros_delete
        )
        actions = list(decision.actions)
        if isinstance(decision.monitor, MonitorCreate):
            for case in decision.monitor.cases:
                actions.extend(case.actions)
        missing = sorted(
            {
                action.name
                for action in actions
                if isinstance(action, MacroAction) and action.name not in available
            }
        )
        if missing:
            raise ValueError(f"macro is not registered: {', '.join(missing)}")

    def _main_remaining_locked(self) -> int:
        """主ループの列の残りの操作の数 (実行中の操作と、まだ始めていない列を含む)。"""
        if self._pending_main is not None:
            return len(self._pending_main)
        if self._executor.owner == "orchestrator":
            return self._executor.remaining_count
        return 0
