"""run の部品をすべて依存注入で組み立てて、1 つのプロセスのスレッドとして起動・停止する (LayersApp)。

run の型は settings.RunSettings.mode で決まる。advisor 型 (RunMode.ADVISOR) は surface・middle・deep の 3 層を
LayersParts で、Orchestrator 型 (RunMode.ORCHESTRATOR) は主ループと、操作の実行と監視を OrchestratorLayersParts で
組み立てる。Orchestrator 型の組み立て・起動するもの・止まる理由は LayersApp の docstring に書く。以下は advisor 型の説明。

実機の部品 (キャプチャーの読み取り元・ブリッジ) と各層 (surface・middle・deep)、本体の画面の middle は LayersParts で
受け取る。cli の run は実機の部品と各層 (surface は --surface、middle と本体の画面の middle は --middle、deep は --deep で
選ぶ) を渡し、テストはフェイクのキャプチャー・ブリッジ・層を渡して数秒動かす。surface の box の追跡のための接続点
(on_frame・surface_boxes_at・surface_plan_boxes・on_run_directory) も LayersParts で受け取る。安全の監視
(safety.safety_monitor) が画面を確かめるもの (SafetyChecker) も LayersParts で受け取る (cli の run は --safety で選ぶ)。
安全の監視が本体の画面を見本と照合した最新の結果は、start で作る system_screen.screen_judgment.LatestSystemScreen に
渡し、同時に段階の判定 (system_screen.run_phase.PhaseTracker) にも 1 件ずつ渡す。

段階 (system_screen.run_phase の RunPhase。段階を変えるたびに run.jsonl に phase {"phase", "reason"} を書く):
    実行を始めるときの安全の確かめが通ったら prepare に入る (確かめで止まったら、どの段階にも入らない)。
    prepare・recover・clean_up では、本体の画面のループ (system_screen.system_screen_loop) が本体の画面の middle に
    目的 (prepare・recover・clean_up) で操作の列を決めさせて送る。surface と middle のゲーム用のループは play の間だけ
    1 回分ずつ進め、deep の自動の呼び出しと画面の停滞による advice の要求も play の間だけ行う。prepare・recover が
    --system-advice-seconds を超えて続いたら、deep のスケジューラーが advice をきっかけ system で求める。prepare と
    recover には時間切れが無い。play から離れるときは中立のレポートを送り、タスクのキューの部品の現在の場面を場面なしに
    する (play に戻ったら、次の middle の出力で場面が設定される)。middle の 1 回の途中で play から離れたときは、その回が
    終わった後にも場面なしにし直す (その回の出力の書き込みが場面を設定し直しても、play 以外の段階で続いた秒数を
    進めないため)。
    middle のゲーム用の出力の press_home が真なら、play のときだけ recover に移し (phase の行を書き、play から離れる
    中立のレポートを送る)、その後で HOME を短押しする (中立のレポートで短押しを取り消さないため)。
押せるボタン: hardware.pad_output.PadOutput は HOME を含む 17 個 (buttons.ALL_BUTTONS) で 1 つだけ作り、本体の画面の操作と
press_home の HOME はこれで送る。surface は surface.surface_output_gate.SurfaceOutputGate を通し、HOME を除く 16 個に
絞って play のときだけ送る (段階を変える処理と同じロックの中で確かめて送る)。

止まる条件 (片付けを行うものには [片付け] を付ける。run.jsonl の stop に理由を書く):
    - Ctrl+C (wait の中の KeyboardInterrupt)                          REASON_INTERRUPTED [片付け]
    - surface か middle (ゲーム用と本体の画面用) が続けて失敗した
                                                                    surface_loop・middle_loop の理由 [片付け]
    - キャプチャーの最新フレームが 2 秒以上古い・出力が終わった・起動から 10 秒たっても最初のフレームが無い
                                                                    hardware.capture_watcher の理由
    - ブリッジの復旧が失敗した (bridge_state() が "failed")             REASON_BRIDGE_FAILED
    - 安全の確かめでオンライン・購入・本体の設定の画面を見つけたか、確かめが失敗した
                                                                    safety.safety_judge・safety_monitor の理由
    - start の途中の例外、ループの想定外の例外                          start failed*・*loop failed*
片付けを行わない止まり方では、すぐに操作を中立にして出力を止め、全体を止める。
片付け: 片付けを行う理由で止めるとき (段階に入った後で、まだ片付けをしていなければ)、止まる理由を決めてから clean_up に
移り、surface と middle のゲーム用のループを止めたまま、本体の画面の middle に目的 clean_up (HOME メニューに戻り、指定の
ソフトを終了する) で操作の列を決めさせて送る。片付けの間も安全の監視を続ける。片付けの終わり (run_phase.CleanUpResult):
    done            本体の画面の middle が done を真で返し、最新の照合の結果が home_menu
    timeout         片付けを始めてから PhaseTimings.clean_up_timeout_s 秒たった
    middle_failed   片付けの本体の画面の middle が 1 回失敗した
    interrupted     片付けの間に 2 回目の Ctrl+C を受けた
    unsafe          片付けの間に、安全の監視の理由で止めることになった
    stopped         片付けの間に、キャプチャー・ブリッジの理由で止めることになった
片付けが終わったら run.jsonl に clean_up_end {"result"} を書き、操作を中立にして出力を止め、全体を止める (止まる理由は
片付けを始めた理由のまま)。
ブリッジの復旧中は、surface が要求を送らず、操作を中立にして待つ (surface_loop)。安全の監視は続ける。
start の途中で例外 (Ctrl+C の KeyboardInterrupt を含む) が出たら、それまでに作った部品と起動したスレッドだけを
止めて閉じ、run.jsonl の stop に理由を書いてから、その例外を外に出す (LayersApp.start)。後片付けの 1 段階が例外を
出しても、logging で警告を書いて残りの段階を続ける。

スレッド:
    キャプチャーの読み取り (CaptureSource)、画像の書き出し (FrameStore)、短押しのタイマー (PadOutput)、
    surface のループ、middle のループ、本体の画面のループ (SystemScreenLoop)、安全の監視 (SafetyMonitor)、
    画面の停滞の計測のループ (progress.progress_loop)、deep の自動の呼び出し (DeepScheduler) とジョブ。
    wait を呼んだスレッド (run ではメインスレッド) が、止まる条件と片付けの時間切れを MONITOR_INTERVAL_S ごとに確かめる。
"""

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from switch2_layers.buttons import ALL_BUTTONS, HOME_BUTTON, allowed_buttons
from switch2_layers.deep.deep_scheduler import (
    DeepScheduler,
    DeepSchedulerParts,
    DeepSchedulerSettings,
)
from switch2_layers.deep.deep_types import DeepRunner
from switch2_layers.error_text import describe_exception
from switch2_layers.frames.frame_buffer import CapturedFrame, FrameBuffer
from switch2_layers.frames.frame_store import FrameStore, latest_frame_t
from switch2_layers.frames.surface_frames import BoxDrawingPlan
from switch2_layers.hardware.capture_source import CaptureSource, LatestFrameReader
from switch2_layers.hardware.capture_watcher import (
    REASON_CAPTURE_ENDED,
    REASON_NO_FIRST_FRAME,
    REASON_STALE_FRAME,
    CaptureWatcher,
)
from switch2_layers.hardware.pad_output import PadOutput, ReportSender
from switch2_layers.middle.middle_input_builder import has_recorded_frame
from switch2_layers.middle.middle_loop import (
    FIRST_FRAME_POLL_S,
    MiddleLoop,
    MiddleLoopParts,
    MiddleLoopSettings,
)
from switch2_layers.middle.middle_trigger import MiddleCallReason, MiddleTrigger
from switch2_layers.middle.middle_types import MiddleAnalyzer
from switch2_layers.middle.system_screen_middle import SystemScreenMiddle
from switch2_layers.orchestrator.claude_stream_session import (
    SESSION_CLOSE_GRACE_S,
    StreamProcessStarter,
)
from switch2_layers.orchestrator.monitor_loop import MonitorAsker
from switch2_layers.orchestrator.orchestrator_assembly import (
    OrchestratorLoopParts,
    build_orchestrator_loops,
    run_monitor_loop,
)
from switch2_layers.orchestrator.skill_copy import DEFAULT_SWITCH_SKILLS_DIR
from switch2_layers.pad_action import PadAction
from switch2_layers.progress.progress_loop import ProgressLoop
from switch2_layers.progress.progress_state import read_progress_state
from switch2_layers.progress.screen_progress import ScreenProgressTracker
from switch2_layers.records.operation_log import OperationLog
from switch2_layers.records.run_log import (
    EVENT_CLEAN_UP_END,
    EVENT_FRAMES_DROPPED,
    EVENT_PHASE,
    EVENT_START,
    EVENT_STOP,
    RunLog,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import (
    RunPaths,
    create_empty_notes,
    create_run_directory,
    write_initial_instruction,
    write_initial_notes,
)
from switch2_layers.safety.safety_monitor import (
    SafetyMonitor,
    SafetyMonitorParts,
    SafetyMonitorSettings,
)
from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import RunMode, RunSettings, run_settings_to_json
from switch2_layers.surface.middle_state_reader import read_surface_context
from switch2_layers.surface.surface_loop import (
    SurfaceLoop,
    SurfaceLoopParts,
    SurfaceLoopSettings,
    SurfaceStepOutcome,
)
from switch2_layers.surface.surface_output_gate import SurfaceOutputGate
from switch2_layers.surface.surface_types import Box, SurfaceDecider
from switch2_layers.system_screen.run_phase import (
    DEFAULT_PHASE_TIMINGS,
    PHASE_REASON_START_CHECK_PASSED,
    CleanUpResult,
    PhaseEvent,
    PhaseTimings,
    PhaseTracker,
    RunPhase,
)
from switch2_layers.system_screen.screen_judgment import (
    LatestSystemScreen,
    SystemScreenJudgment,
)
from switch2_layers.system_screen.system_operation_runner import (
    JUDGMENT_WAIT_S,
    SystemOperationRunner,
)
from switch2_layers.system_screen.system_screen_loop import (
    SystemScreenLoop,
    SystemScreenLoopParts,
    SystemScreenLoopSettings,
)
from switch2_layers.tasks.task_board import TaskBoard

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

REASON_INTERRUPTED = "interrupted"
REASON_BRIDGE_FAILED = "bridge recovery failed"
REASON_CLOSED = "closed"  # 止まる条件より前に close が呼ばれた
MONITOR_INTERVAL_S = 0.1  # 止まる条件を確かめる間隔 (秒)
# 数えた Ctrl+C (SIGINT) からこの秒数以内に届いた SIGINT は、同じ割り込みとして数えない。timeout -s INT と uv run の
# 下では、1 回の割り込みで SIGINT が 2 回 (プロセスグループへの送信と uv の転送) 届くため
INTERRUPT_REPEAT_WINDOW_S = 1.0
# 終了時に surface と middle のループ・安全の監視・本体の画面のループ・画面の停滞の計測のループ (Orchestrator 型では
# 主ループ以外のループ) のスレッドの終わりを待つ上限 (秒)
LOOP_JOIN_TIMEOUT_S = 5.0
# 終了時に Orchestrator 型の主ループのスレッドの終わりを待つ上限 (秒)。主ループは止めるときに claude のセッションを閉じ、
# 標準入力を閉じてから SIGTERM まで・SIGTERM から SIGKILL までに、それぞれ SESSION_CLOSE_GRACE_S 秒待つため、その分を足す
MAIN_LOOP_JOIN_TIMEOUT_S = 2 * SESSION_CLOSE_GRACE_S + LOOP_JOIN_TIMEOUT_S
# surface と middle のゲーム用のループが、段階が play でない間に、段階を確かめ直す間隔 (秒)
PLAY_POLL_S = 0.05
BRIDGE_FAILED = "failed"  # bridge_state が返す、ブリッジの復旧が失敗した状態
# 本体の画面の操作で、2 つ目以降の操作の前に照合の結果を待つ上限に、安全の監視の間隔へ足す秒数
JUDGMENT_WAIT_MARGIN_S = 2.0
# 片付けを行う止まる理由のうち、回数を含む形 (surface_loop.surface_failure_reason・middle_loop.middle_failure_reason)
_CLEAN_UP_REASON_PATTERN = re.compile(r"(surface|middle) failed \d+ times in a row")
# 安全の監視の止まる理由の書き出し (safety.safety_judge・safety_monitor の理由)
_SAFETY_REASON_PREFIXES = (
    "unsafe screen",
    "safety check failed",
    "safety monitor failed",
)


@dataclass(frozen=True)
class LayersParts:
    """LayersApp に注入する部品。

    frame_reader    キャプチャーの最新フレームの読み取り元 (run では hardware.capture_source.SwitchCaptureReader)
    report_sender   レポートの送信先 (run では switch2_remote.bridge_supervisor.BridgeSupervisor)
    bridge_state    ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数 (run では BridgeSupervisor.state を読む)
    surface         surface の判断
    middle          middle の分析
    deep            deep のジョブの実行
    safety          安全の監視が画面を確かめるもの (run では --safety の safety.safety_assembly の checker)
    system_middle   本体の画面の middle (run では --middle luna なら LunaSystemScreenMiddle、fake なら操作なしで done を
                    真で返し続けるフェイク)
    monotonic       単調な時計 (既定 time.monotonic)。キャプチャーのフレームの時刻と同じ時計にする
    local_now       実行ディレクトリーの名前に使うローカルの時刻を返す関数 (既定 datetime.now)
    on_frame        キャプチャーの新しいフレームを受け取る関数 (CaptureSource の on_frame。キャプチャーのスレッドから
                    呼ばれる)。run の surface が jev なら box の追跡。None (既定) なら渡さない
    surface_boxes_at
                    surface に送る画像に書き込む box を返す関数 (SurfaceLoopParts.boxes_at)。None (既定) なら書き込まない
    on_run_directory
                    実行ディレクトリーを作った後に 1 回呼ぶ関数 (RunPaths を受け取る)。run の surface が jev なら、
                    対象の一覧を読む notes/ を surface に渡す。None (既定) なら呼ばない
    surface_plan_boxes
                    surface に送る画像への box の書き込み方を、要求ごとに場面 ID から返す関数
                    (SurfaceLoopParts.plan_boxes)。None (既定) なら、surface_boxes_at の box をすべて書き込む
    phase_timings   段階の判定の回数と秒数 (既定は system_screen.run_phase の定数の値)。テストで短くできる
    """

    frame_reader: LatestFrameReader
    report_sender: ReportSender
    bridge_state: Callable[[], str]
    surface: SurfaceDecider
    middle: MiddleAnalyzer
    deep: DeepRunner
    safety: SafetyChecker
    system_middle: SystemScreenMiddle
    monotonic: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now
    on_frame: Callable[[CapturedFrame], None] | None = None
    surface_boxes_at: Callable[[float], Sequence[Box]] | None = None
    on_run_directory: Callable[[RunPaths], None] | None = None
    surface_plan_boxes: Callable[[str | None], BoxDrawingPlan] | None = None
    phase_timings: PhaseTimings = DEFAULT_PHASE_TIMINGS


@dataclass(frozen=True)
class OrchestratorLayersParts:
    """Orchestrator 型の run (settings.mode が orchestrator) で LayersApp に注入する部品。

    frame_reader           キャプチャーの最新フレームの読み取り元 (run では hardware.capture_source.SwitchCaptureReader)
    report_sender          レポートの送信先 (run では switch2_remote.bridge_supervisor.BridgeSupervisor)
    bridge_state           ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数 (run では BridgeSupervisor.state を
                           読む)
    safety                 安全の監視が画面を確かめるもの (run では --safety の safety.safety_assembly の checker)
    monitor                監視の要求の送り先 (orchestrator.monitor_loop.MonitorAsker。run では surface.jev_client.JevClient)
    initial_instruction    人の初期指示 (orchestrator.initial_instruction.load_initial_instruction の値)。無ければ None
    monotonic              単調な時計 (既定 time.monotonic)。キャプチャーのフレームの時刻と同じ時計にする
    local_now              実行ディレクトリーの名前に使うローカルの時刻を返す関数 (既定 datetime.now)
    start_process          主ループの claude の子プロセスを起動する関数 (None なら subprocess.Popen)
    signal_process_group   主ループの claude のプロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    environ                主ループの claude の環境変数の元 (None ならセッションを始めるときの os.environ)
    switch_skills_dir      本体の操作のスキルを写す元 (既定 orchestrator.skill_copy.DEFAULT_SWITCH_SKILLS_DIR)
    start_process から switch_skills_dir までの 4 つは、orchestrator.main_loop.OrchestratorMainLoopParts にそのまま渡す
    (テストでは偽の claude を使う)。
    """

    frame_reader: LatestFrameReader
    report_sender: ReportSender
    bridge_state: Callable[[], str]
    safety: SafetyChecker
    monitor: MonitorAsker
    initial_instruction: str | None = None
    monotonic: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now
    start_process: StreamProcessStarter | None = None
    signal_process_group: Callable[[int, int], None] | None = None
    environ: Mapping[str, str] | None = None
    switch_skills_dir: Path = DEFAULT_SWITCH_SKILLS_DIR


def monitor_loop_failure_reason(description: str) -> str:
    """Orchestrator 型の監視のループ (OrchestratorController.run) が想定外の例外で終わるときの理由
    `monitor loop failed: <description>` を返す (description は error_text.describe_exception(例外) の値)。"""
    return f"monitor loop failed: {description}"


@dataclass(frozen=True)
class _StartedParts:
    """start で組み立てた部品 (close で後片付けするもの)。

    deep_scheduler     advisor 型の DeepScheduler。Orchestrator 型では None
    loop_threads       ループのスレッド (起動する順)
    main_loop_thread   loop_threads のうち、Orchestrator 型の主ループのスレッド。advisor 型では None
    board              advisor 型のタスクのキューの部品 (TaskBoard)。Orchestrator 型では None
    """

    paths: RunPaths
    clock: RunClock
    run_log: RunLog
    pad_output: PadOutput
    frame_store: FrameStore
    capture_source: CaptureSource
    capture_watcher: CaptureWatcher
    deep_scheduler: DeepScheduler | None
    loop_threads: tuple[threading.Thread, ...]
    main_loop_thread: threading.Thread | None = None
    board: TaskBoard | None = None


@dataclass
class _PartsBuiltSoFar:
    """start が途中までに作った部品。start の途中で例外が出たときに、作ったものだけを後片付けするために使う。

    run_log            run.jsonl に start を書き終えた RunLog (書き終える前は None)
    pad_output         PadOutput (作る前は None)
    frame_store        FrameStore (作る前は None)
    capture_source     CaptureSource (作る前は None)
    deep_scheduler     DeepScheduler (作る前と、Orchestrator 型では None)
    loop_threads       作ったループのスレッド (advisor 型は surface と middle のループ・安全の監視・本体の画面のループ・
                       画面の停滞の計測のループ、Orchestrator 型は監視のループ・主ループ・安全の監視・停滞の計測のループ。
                       起動したかどうかは Thread.is_alive で見分ける)
    main_loop_thread   loop_threads のうち、Orchestrator 型の主ループのスレッド (作る前と、advisor 型では None)
    """

    run_log: RunLog | None = None
    pad_output: PadOutput | None = None
    frame_store: FrameStore | None = None
    capture_source: CaptureSource | None = None
    deep_scheduler: DeepScheduler | None = None
    loop_threads: list[threading.Thread] = field(default_factory=list)
    main_loop_thread: threading.Thread | None = None


@dataclass(frozen=True)
class _OutputAndCapture:
    """start の 4 と 5 で作る、advisor 型と Orchestrator 型が共有する部品。"""

    pad_output: PadOutput
    frame_buffer: FrameBuffer
    frame_store: FrameStore
    capture_source: CaptureSource
    capture_watcher: CaptureWatcher


def _loop_join_timeout_s(
    thread: threading.Thread, main_loop_thread: threading.Thread | None
) -> float:
    """終了時にループのスレッド thread の終わりを待つ上限 (秒) を返す。

    Orchestrator 型の主ループのスレッドなら MAIN_LOOP_JOIN_TIMEOUT_S、ほかは LOOP_JOIN_TIMEOUT_S。
    """
    if thread is main_loop_thread:
        return MAIN_LOOP_JOIN_TIMEOUT_S
    return LOOP_JOIN_TIMEOUT_S


def _ignore_system_screen(judgment: SystemScreenJudgment) -> None:
    """Orchestrator 型の安全の監視の on_system_screen。照合の結果を使わない (段階の判定も本体の画面のループも無い)。"""


def _current_scene_oldest_active_s(board: TaskBoard, t: float) -> float | None:
    """board の現在の場面の、最も古いタスクの時刻 t での続いた秒数を返す。現在の場面が無ければ None。

    middle.middle_trigger.MiddleTrigger の oldest_active_s に渡す。
    """
    scene_id = board.current_scene_id
    if scene_id is None:
        return None
    return board.oldest_active_s(scene_id, t)


def _start_failure_reason(error: BaseException) -> str:
    """start の途中で error が出て止まるときの理由 `start failed: <例外の説明>` を返す。"""
    return f"start failed: {describe_exception(error)}"


def _run_cleanup_step(step: Callable[[], object], description: str) -> None:
    """start の途中の例外の後片付けの 1 段階 step を呼ぶ。

    step が例外 (Exception) を出したら、logging で警告を書いて捨てる (残りの段階を続け、start の例外を外に出すため)。
    """
    try:
        step()
    except Exception:
        logger.warning(
            "failed to %s while cleaning up a failed start", description, exc_info=True
        )


def _needs_clean_up(reason: str) -> bool:
    """止まる理由 reason が片付けを行う理由 (REASON_INTERRUPTED・surface か middle が続けて失敗した) なら True。"""
    return (
        reason == REASON_INTERRUPTED
        or _CLEAN_UP_REASON_PATTERN.fullmatch(reason) is not None
    )


def _clean_up_end_for(reason: str) -> CleanUpResult | None:
    """片付けの間に来た止まる理由 reason で片付けを終えるときの終わり方を返す。終えない理由なら None。

    安全の監視の理由なら UNSAFE、キャプチャー・ブリッジの理由なら STOPPED。
    """
    if reason.startswith(_SAFETY_REASON_PREFIXES):
        return CleanUpResult.UNSAFE
    if reason in (
        REASON_CAPTURE_ENDED,
        REASON_NO_FIRST_FRAME,
        REASON_STALE_FRAME,
        REASON_BRIDGE_FAILED,
    ):
        return CleanUpResult.STOPPED
    return None


@dataclass(frozen=True)
class _PhaseSnapshot:
    """段階の判定の読み取り (段階に入る前は PREPARE・0.0・False)。"""

    phase: RunPhase
    phase_started_t: float
    wants_system_middle: bool


class _SystemScreenPhases:
    """本体の画面のループに渡す段階 (system_screen_loop.SystemScreenPhases の実装)。

    読み取りは snapshot、入力は give (段階の判定をロックの中で呼び、戻り値を扱う LayersApp の関数) で行う。
    """

    def __init__(
        self,
        snapshot: Callable[[], _PhaseSnapshot],
        give: Callable[[Callable[[PhaseTracker], PhaseEvent]], None],
    ) -> None:
        """読み取りと入力の関数を持つ。"""
        self._snapshot = snapshot
        self._give = give

    @property
    def phase(self) -> RunPhase:
        """今の段階。"""
        return self._snapshot().phase

    @property
    def phase_started_t(self) -> float:
        """今の段階に入った時刻。"""
        return self._snapshot().phase_started_t

    @property
    def wants_system_middle(self) -> bool:
        """本体の画面の middle を呼んでよいなら True。"""
        return self._snapshot().wants_system_middle

    def on_system_output(self, t: float, done: bool) -> None:
        """本体の画面の middle の出力を渡す。"""
        self._give(lambda tracker: tracker.on_system_output(t, done))

    def on_operations_started(self, t: float) -> None:
        """操作の列を送り始めたことを渡す。"""
        self._give(lambda tracker: tracker.on_operations_started(t))

    def on_operations_finished(self, t: float) -> None:
        """操作の列を送り終えたことを渡す。"""
        self._give(lambda tracker: tracker.on_operations_finished(t))

    def on_system_middle_failed(self, t: float) -> None:
        """本体の画面の middle の失敗を渡す。"""
        self._give(lambda tracker: tracker.on_system_middle_failed(t))


class LayersApp:
    """run の型 (settings.mode) の部品を組み立てて動かす。start → wait → close の順に呼ぶ (run はこれをまとめたもの)。

    settings       run の起動引数
    parts          注入する部品。settings.mode が advisor なら LayersParts、orchestrator なら OrchestratorLayersParts
    sleep          wait の見張りの間隔の待ちに使う関数 (既定 time.sleep)。テストでは KeyboardInterrupt を出す関数を渡せる
    start_thread   start がループのスレッド (advisor 型は surface と middle のループ・安全の監視・本体の画面のループ・
                   画面の停滞の計測のループ、Orchestrator 型は下の 4 つ) を起動する関数 (既定 threading.Thread.start)。
                   テストでは、起動の途中で例外を出す関数を渡せる

    advisor 型の組み立ては start の docstring の 1〜10 のとおり。

    Orchestrator 型 (settings.mode が orchestrator) の組み立て (start):
        1. RunClock(parts.monotonic) を作る (ここが t = 0)
        2. run_directory.create_run_directory(settings.runs_dir, parts.local_now()) と
           run_directory.create_empty_notes(paths) (notes/ は空のディレクトリーで始める。write_initial_notes は呼ばない)。
           parts.initial_instruction があれば run_directory.write_initial_instruction(paths, 初期指示) で
           orchestrator/instruction.md に写す (無ければ書かない)
        3. RunLog を作り、start {"run_dir": 実行ディレクトリー, "args": settings.run_settings_to_json(settings)} を書く
           (mode と Orchestrator 型の 6 つの値を含む)
        4. OperationLog と PadOutput(report_sender, buttons.ALL_BUTTONS (HOME を含む 17 個),
           on_operation=OperationLog.write) を作る。surface.surface_output_gate.SurfaceOutputGate は通さない
        5. FrameBuffer、FrameStore(interval_fps, interval_width)、CaptureSource(frame_reader, …)、CaptureWatcher を作る
           (advisor 型と同じ。on_frame は渡さない)
        6〜7 は orchestrator.orchestrator_assembly.build_orchestrator_loops で作る。
        6. orchestrator.orchestrator_controller.OrchestratorController(OrchestratorParts(pad_output = 4 の PadOutput,
           jev = parts.monitor, latest_frame = FrameBuffer.latest, clock, paths),
           OrchestratorSettings(monitor_threshold, max_monitor_errors))
        7. orchestrator.main_loop.OrchestratorMainLoop(OrchestratorMainLoopParts(paths, clock,
           claude = ClaudeSessionOptions(effort = orchestrator_effort),
           parse_decision = text を decision_parser.parse_decision(text, 6 の macro_names()) で読む関数,
           controller = 6 の OrchestratorController, initial_instruction = parts.initial_instruction, run_log,
           on_stop = request_stop, start_process・signal_process_group・environ・switch_skills_dir は parts の値),
           OrchestratorMainLoopSettings(rotate_tokens, turn_timeout_s, max_decision_errors))
        8. SafetyMonitor (checker = parts.safety、latest_frame = FrameBuffer.latest、safety_log = paths.safety_log、
           on_stop = request_stop、on_start_passed = 6 と 7 の set_started を呼ぶ関数、on_system_screen = 何もしない
           関数。interval_s・threshold・max_errors は advisor 型と同じ)
        9. 画面の停滞の計測の ProgressLoop (advisor 型と同じ)
        スレッドは、監視のループ (orchestrator_assembly.run_monitor_loop で 6 の run を包み、想定外の例外 (Exception) で
        抜けたら request_stop(monitor_loop_failure_reason(error_text.describe_exception(例外))) を呼ぶ)、主ループ
        (7 の run)、安全の監視 (8)、停滞の計測のループ (9) の順に start_thread で起動する。
        起動しないもの: DeepScheduler・SurfaceLoop・MiddleLoop・SystemScreenLoop (本体の画面の middle も使わない)。
        段階 (system_screen.run_phase.PhaseTracker) にも入らない (run.jsonl に phase と clean_up_end を書かない)。
        実行を始めるときの安全の確かめが通って set_started が呼ばれるまで、主ループは claude を起動せず、操作の実行と
        監視のループは操作も監視の要求も送らない。確かめで止まったら、どちらも始めない。
        止まる理由 (run.jsonl の stop に書く。どれも片付けを行わず、すぐに操作を中立にして出力を止める。起動したソフトは
        そのまま残る):
            - Ctrl+C (REASON_INTERRUPTED)、キャプチャー・ブリッジ・安全の監視の理由、start の途中の例外は advisor 型と同じ
            - 主ループの理由: main_loop.decision_failure_reason (JSON の誤りが続いた)・session_failure_reason (セッションが
              最初のターンを終えずに終わることが続いた)・main_loop_failure_reason (主ループの想定外の例外)
            - 監視のループの想定外の例外: monitor_loop_failure_reason
        close と start の途中の例外の後片付けは、advisor 型と同じ順に行う (DeepScheduler は無いので飛ばす)。主ループの
        スレッドの終わりは、セッションを閉じる時間 (claude_stream_session.SESSION_CLOSE_GRACE_S の 2 倍) に
        LOOP_JOIN_TIMEOUT_S を足した MAIN_LOOP_JOIN_TIMEOUT_S 秒まで待ち、ほかのループのスレッドは LOOP_JOIN_TIMEOUT_S 秒
        ずつ待つ。
    """

    def __init__(
        self,
        settings: RunSettings,
        parts: LayersParts | OrchestratorLayersParts,
        sleep: Callable[[float], None] = time.sleep,
        start_thread: Callable[[threading.Thread], None] = threading.Thread.start,
    ) -> None:
        """属性を持つ。ディレクトリーとスレッドは start で作る。

        settings.mode と parts の型が合わなければ (orchestrator なのに LayersParts、advisor なのに
        OrchestratorLayersParts)、ValueError にする。
        """
        if settings.mode is RunMode.ORCHESTRATOR and isinstance(parts, LayersParts):
            raise ValueError("the orchestrator mode needs OrchestratorLayersParts")
        if settings.mode is RunMode.ADVISOR and isinstance(
            parts, OrchestratorLayersParts
        ):
            raise ValueError("the advisor mode needs LayersParts")
        self.settings = settings
        self.parts = parts
        self.sleep = sleep
        self.start_thread = start_thread
        self._lock = threading.Lock()
        # ループの停止の合図 (surface と middle のループ・安全の監視・本体の画面のループ・画面の停滞の計測のループが見る)
        self._stop_event = threading.Event()
        self._stop_reason: str | None = None
        # 停止の処理 (PadOutput.stop_output) を終えたか。理由が決まっただけで処理の途中に割り込まれた場合
        # (Ctrl+C など) に、close でやり直すために理由とは別に持つ
        self._output_stopped = False
        self._start_called = False
        self._closed = False
        self._started: _StartedParts | None = None
        # 段階の判定 (PhaseTracker) を読み書きするときと、surface の操作の関門が「play か」を確かめて送るときのロック。
        # 段階の変化を扱う途中で request_stop を呼ぶため、同じスレッドから重ねて取れるロックにする
        self._phase_lock = threading.RLock()
        # 段階の判定。prepare に入る (実行を始めるときの確かめが通る) までは None
        self._tracker: PhaseTracker | None = None
        # 片付けを始めたか、終えたか (clean_up_end を 1 回だけ書くため)
        self._clean_up_started = False
        self._clean_up_ended = False

    def start(self) -> RunPaths:
        """実行ディレクトリーを作り、部品を組み立ててスレッドを起動し、実行ディレクトリーの RunPaths を返す。

        Orchestrator 型の組み立てはクラスの docstring のとおり。advisor 型は次の 1〜10 のとおり。
        1. RunClock(parts.monotonic) を作る (ここが t = 0)
        2. run_directory.create_run_directory(settings.runs_dir, parts.local_now()) と
           write_initial_notes(paths, buttons.allowed_buttons()) (surface の押せる 16 個。HOME は書かない)
        3. RunLog を作り、start {"run_dir": 実行ディレクトリー, "args": settings.run_settings_to_json(settings)} を書く
           (指定のソフトとユーザーの名前を含む)。続けて、parts.on_run_directory があれば on_run_directory(paths) を
           1 回呼ぶ (スレッドを起動する前。notes/ の初期ファイルは書いた後)
        4. OperationLog と PadOutput(report_sender, buttons.ALL_BUTTONS (HOME を含む 17 個),
           on_operation=OperationLog.write) を作る
        5. FrameBuffer、FrameStore(interval_fps, interval_width)、CaptureSource(frame_reader, …,
           on_frame=parts.on_frame)、CaptureWatcher を作る
        6. DeepScheduler (current_scene_id は middle/state.json の scene_id、latest_frame_t は frames.jsonl の最後の t、
           progress_state は progress/state.json の値 (progress.progress_state.read_progress_state)、
           is_play は段階が play かを返す関数、current_phase は段階とその段階に入った時刻の組を返す関数 (段階に入る前は
           None)、timeout_s は deep_timeout_s、stuck_advice_seconds は stuck_advice_seconds、system_advice_seconds は
           system_advice_seconds) を作って start する (タイムアウト・自動の呼び出し・きっかけ progress と system の判定の
           スレッド)
        7. タスクのキューの部品 TaskBoard(paths) を 1 つ作り、middle.middle_trigger.MiddleTrigger(middle_interval_s,
           task_stuck_seconds, clock, oldest_active_s = 時刻 t を受け取り、TaskBoard.current_scene_id の場面の
           TaskBoard.oldest_active_s(場面, t) を返す関数 (現在の場面が無ければ None)) を作る。
           SurfaceLoop (output_gate = SurfaceOutputGate(4 の PadOutput, 段階が play かを返す関数, 段階のロック)、
           deep_requests と deep_status は 6 の DeepScheduler、max_errors = max_surface_errors、
           threshold_task = threshold_task、boxes_at = parts.surface_boxes_at、plan_boxes = parts.surface_plan_boxes、
           board = その TaskBoard、trigger_notify = MiddleTrigger.notify) と
           MiddleLoop (interval_s・window_s・max_errors、board = その TaskBoard、trigger = その MiddleTrigger。
           on_press_home は、段階のロックの中で、段階が play のときだけ
           次の順に行う関数: PhaseTracker.on_press_home で recover に移し、run.jsonl に phase を書き、play から離れる
           PadOutput.neutral() を送ってから、HOME を短押しする (PadOutput.apply(PadAction(taps={"HOME"}),
           source="system"))。中立のレポートを短押しの後に送ると、短押しが取り消されるため) を作り、それぞれのスレッドを
           start_thread で起動する。on_stop には request_stop を渡す。2 つのループは、段階が play の間だけ 1 回分ずつ
           進め (play でなければ短い間隔で待つ)、middle のループは play の間だけ、MiddleTrigger のきっかけで呼ぶ。
           middle の 1 回が終わったときに段階が play でなければ、TaskBoard.set_current_scene(None, clock.now()) を呼ぶ
           (_run_middle_while_playing)
        8. 最新の照合の結果を持つ system_screen.screen_judgment.LatestSystemScreen と、SafetyMonitor
           (checker = parts.safety、latest_frame = FrameBuffer.latest、safety_log = paths.safety_log、
           on_stop = request_stop、on_start_passed = prepare に入る関数 (PhaseTracker(clock.now(),
           parts.phase_timings) を作り、run.jsonl に phase {"phase": "prepare", "reason":
           run_phase.PHASE_REASON_START_CHECK_PASSED} を書く)、on_system_screen = LatestSystemScreen.update と
           PhaseTracker.on_judgment(照合したフレームの時刻, 照合の結果) を呼ぶ関数。interval_s = safety_interval_s、
           threshold = threshold_safety、max_errors = max_safety_errors) を作り、そのスレッドを 7 の 2 つの後に
           start_thread で起動する
        9. SystemOperationRunner(4 の PadOutput, 8 の LatestSystemScreen, clock, judgment_wait_s =
           max(system_operation_runner.JUDGMENT_WAIT_S, safety_interval_s + 2.0)) と SystemScreenLoop (middle =
           parts.system_middle、judgments = 8 の LatestSystemScreen、latest_frame = FrameBuffer.latest、phases = 段階を
           ロックの中で読み書きするもの、software_name・user_name、max_errors = max_middle_errors、on_stop = request_stop)
           を作り、そのスレッドを 8 の後に start_thread で起動する
        10. 画面の停滞の計測の ProgressLoop (ScreenProgressTracker(progress_window_s)) を作り、そのスレッドを 9 の後に
            start_thread で起動する
        段階の判定 (PhaseTracker) の入力と読み取りは、すべて段階のロックの中で行う。入力の戻り値 (run_phase.PhaseEvent) は
        次のように扱う: 段階が変わったら run.jsonl に phase {"phase", "reason"} を書き (play から離れるときは、同じロックの
        中で PadOutput.neutral() を送り、TaskBoard.set_current_scene(None, clock.now()) で現在の場面を場面なしにする)、
        止める理由なら request_stop(理由)、片付けの終わりなら片付けを終える (request_stop の docstring)。
        7 の surface と middle は、prepare に入るまで (実行を始めるときの確かめが通るまで) 操作を 1 つも送らない。
        確かめで止まったら、どの段階にも入らない。10 の計測のループは操作を送らないため、段階に依らず動く。
        2 回目以降の呼び出しは RuntimeError にする。

        途中で例外 (KeyboardInterrupt などの BaseException を含む) が出たら、次の後片付けをしてから、その例外を
        そのまま外に出す:
            - request_stop(`start failed: <error_text.describe_exception(例外)>`) と同じく、止まった理由を決め
              (先に理由が決まっていればそのまま)、ループの停止の合図を立て、PadOutput を作っていれば stop_output で
              操作を中立にする
            - close の後片付けと同じ順に、それまでに作った部品と起動したスレッドだけを止めて閉じる: 起動したループの
              スレッドの終わりを待ち、作った DeepScheduler・CaptureSource・FrameStore を閉じ、run.jsonl に
              frames_dropped (FrameStore を作ったときだけ) と stop {"reason": 止まった理由} を書いて RunLog を閉じ、
              PadOutput を閉じる
        上の 3 で run.jsonl に start を書き終える前の例外では、run.jsonl には何も書かない。
        後片付けを終えた後の close は何もしない (二重に閉じない)。
        """
        with self._lock:
            if self._start_called:
                raise RuntimeError("LayersApp.start can be called only once")
            self._start_called = True
        built = _PartsBuiltSoFar()
        try:
            return self._build_and_start(built)
        except BaseException as error:
            # Ctrl+C の KeyboardInterrupt も、作った部品とスレッドを残さないよう後片付けしてから外に出す
            self._clean_up_failed_start(built, error)
            raise

    def _build_and_start(self, built: _PartsBuiltSoFar) -> RunPaths:
        """start の 1〜10 (Orchestrator 型ではクラスの docstring の 1〜9) を行う。作った部品は、作った直後に built に
        記録する。"""
        settings = self.settings
        parts = self.parts
        if isinstance(parts, OrchestratorLayersParts):
            # settings.mode が orchestrator のとき (__init__ で mode と parts の型をそろえてある)
            return self._build_and_start_orchestrator(parts, built)
        # 1〜3: 時計・実行ディレクトリー・run.jsonl
        clock = RunClock(parts.monotonic)
        paths = create_run_directory(settings.runs_dir, parts.local_now())
        write_initial_notes(paths, allowed_buttons())
        run_log = RunLog(paths.run_log, clock)
        run_log.write(
            EVENT_START, run_dir=str(paths.root), args=run_settings_to_json(settings)
        )
        built.run_log = run_log
        if parts.on_run_directory is not None:
            parts.on_run_directory(paths)
        # 4〜5: 操作の送信とキャプチャーのフレーム
        shared = self._build_output_and_capture(
            parts, parts.on_frame, paths, clock, built
        )
        pad_output = shared.pad_output
        frame_buffer = shared.frame_buffer
        frame_store = shared.frame_store
        capture_source = shared.capture_source
        capture_watcher = shared.capture_watcher
        # 6: deep
        deep_scheduler = DeepScheduler(
            DeepSchedulerParts(
                paths=paths,
                runner=parts.deep,
                clock=clock,
                run_log=run_log,
                current_scene_id=lambda: (
                    read_surface_context(paths.middle_state).scene_id
                ),
                latest_frame_t=lambda: latest_frame_t(paths),
                progress_state=lambda: read_progress_state(paths.progress_state),
                is_play=self._playing,
                current_phase=self._current_phase,
            ),
            DeepSchedulerSettings(
                min_start_interval_s=settings.deep_min_start_interval_s,
                auto_seconds=settings.deep_auto_seconds,
                timeout_s=settings.deep_timeout_s,
                stuck_advice_seconds=settings.stuck_advice_seconds,
                system_advice_seconds=settings.system_advice_seconds,
            ),
        )
        built.deep_scheduler = deep_scheduler
        deep_scheduler.start()
        # 7: タスクのキューの部品と middle を呼ぶきっかけ、surface と middle のループ
        board = TaskBoard(paths)
        trigger = MiddleTrigger(
            settings.middle_interval_s,
            settings.task_stuck_seconds,
            clock,
            lambda t: _current_scene_oldest_active_s(board, t),
        )
        surface_loop = SurfaceLoop(
            SurfaceLoopParts(
                paths=paths,
                decider=parts.surface,
                frame_buffer=frame_buffer,
                frame_store=frame_store,
                output_gate=SurfaceOutputGate(
                    pad_output, is_play=self._is_play, lock=self._phase_lock
                ),
                deep_requests=deep_scheduler,
                deep_status=deep_scheduler,
                run_log=run_log,
                clock=clock,
                bridge_state=parts.bridge_state,
                on_stop=self.request_stop,
                boxes_at=parts.surface_boxes_at,
                plan_boxes=parts.surface_plan_boxes,
                board=board,
                trigger_notify=trigger.notify,
            ),
            SurfaceLoopSettings(
                max_errors=settings.max_surface_errors,
                threshold_task=settings.threshold_task,
            ),
        )
        middle_loop = MiddleLoop(
            MiddleLoopParts(
                paths=paths,
                analyzer=parts.middle,
                clock=clock,
                run_log=run_log,
                deep_requests=deep_scheduler,
                on_stop=self.request_stop,
                board=board,
                trigger=trigger,
                on_press_home=self._press_home,
            ),
            MiddleLoopSettings(
                interval_s=settings.middle_interval_s,
                window_s=settings.middle_window_s,
                max_errors=settings.max_middle_errors,
            ),
        )
        # 8: 安全の監視 (照合の結果は段階の判定と LatestSystemScreen に渡す)
        latest_system_screen = LatestSystemScreen()

        def on_system_screen(judgment: SystemScreenJudgment) -> None:
            self._phase_input(
                lambda tracker: tracker.on_judgment(judgment.frame_t, judgment.screen)
            )
            latest_system_screen.update(judgment)

        safety_monitor = SafetyMonitor(
            SafetyMonitorParts(
                checker=parts.safety,
                latest_frame=frame_buffer.latest,
                clock=clock,
                safety_log=paths.safety_log,
                on_stop=self.request_stop,
                on_start_passed=self._enter_prepare,
                on_system_screen=on_system_screen,
            ),
            SafetyMonitorSettings(
                interval_s=settings.safety_interval_s,
                threshold=settings.threshold_safety,
                max_errors=settings.max_safety_errors,
            ),
        )
        # 9: 本体の画面のループ (段階が prepare・recover・clean_up の間、本体の画面の middle を呼ぶ)
        system_screen_loop = SystemScreenLoop(
            SystemScreenLoopParts(
                paths=paths,
                middle=parts.system_middle,
                runner=SystemOperationRunner(
                    pad_output,
                    latest_system_screen,
                    clock,
                    judgment_wait_s=max(
                        JUDGMENT_WAIT_S,
                        settings.safety_interval_s + JUDGMENT_WAIT_MARGIN_S,
                    ),
                ),
                judgments=latest_system_screen,
                latest_frame=frame_buffer.latest,
                pad_output=pad_output,
                phases=_SystemScreenPhases(self._phase_snapshot, self._phase_input),
                clock=clock,
                bridge_state=parts.bridge_state,
                on_stop=self.request_stop,
            ),
            SystemScreenLoopSettings(
                software_name=settings.software_name,
                user_name=settings.user_name,
                max_errors=settings.max_middle_errors,
            ),
        )
        # 10: 画面の停滞の計測 (操作を送らないため、段階に依らず動く)
        progress_loop = ProgressLoop(
            paths, ScreenProgressTracker(settings.progress_window_s)
        )
        # surface と middle のゲーム用のループは、段階が play の間だけ 1 回分ずつ進める
        loop_threads = (
            threading.Thread(
                target=self._run_surface_while_playing,
                args=(surface_loop,),
                name="surface-loop",
                daemon=True,
            ),
            threading.Thread(
                target=self._run_middle_while_playing,
                args=(middle_loop, paths),
                name="middle-loop",
                daemon=True,
            ),
            threading.Thread(
                target=safety_monitor.run,
                args=(self._stop_event,),
                name="safety-monitor",
                daemon=True,
            ),
            threading.Thread(
                target=system_screen_loop.run,
                args=(self._stop_event,),
                name="system-screen-loop",
                daemon=True,
            ),
            threading.Thread(
                target=progress_loop.run,
                args=(self._stop_event,),
                name="progress-loop",
                daemon=True,
            ),
        )
        built.loop_threads.extend(loop_threads)
        with self._lock:
            self._started = _StartedParts(
                paths=paths,
                clock=clock,
                run_log=run_log,
                pad_output=pad_output,
                frame_store=frame_store,
                capture_source=capture_source,
                capture_watcher=capture_watcher,
                deep_scheduler=deep_scheduler,
                loop_threads=loop_threads,
                board=board,
            )
        for thread in loop_threads:
            self.start_thread(thread)
        return paths

    def _build_output_and_capture(
        self,
        parts: LayersParts | OrchestratorLayersParts,
        on_frame: Callable[[CapturedFrame], None] | None,
        paths: RunPaths,
        clock: RunClock,
        built: _PartsBuiltSoFar,
    ) -> _OutputAndCapture:
        """start の 4 と 5 (advisor 型と Orchestrator 型で同じ) を行う。作った部品は、作った直後に built に記録する。

        4. OperationLog と PadOutput(report_sender, buttons.ALL_BUTTONS (HOME を含む 17 個), clock = parts.monotonic,
           on_operation = OperationLog.write)
        5. FrameBuffer、FrameStore(paths, interval_fps, interval_width)、CaptureSource(frame_reader, clock, FrameBuffer,
           FrameStore, on_frame = on_frame)、CaptureWatcher(frame_reader.is_ended, CaptureSource.latest_t, clock)
        """
        settings = self.settings
        operation_log = OperationLog(paths.operations_log, clock)
        pad_output = PadOutput(
            parts.report_sender,
            ALL_BUTTONS,
            clock=parts.monotonic,
            on_operation=operation_log.write,
        )
        built.pad_output = pad_output
        frame_buffer = FrameBuffer()
        frame_store = FrameStore(paths, settings.interval_fps, settings.interval_width)
        built.frame_store = frame_store
        capture_source = CaptureSource(
            parts.frame_reader,
            clock,
            frame_buffer,
            frame_store,
            on_frame=on_frame,
        )
        built.capture_source = capture_source
        capture_watcher = CaptureWatcher(
            parts.frame_reader.is_ended, capture_source.latest_t, clock
        )
        return _OutputAndCapture(
            pad_output=pad_output,
            frame_buffer=frame_buffer,
            frame_store=frame_store,
            capture_source=capture_source,
            capture_watcher=capture_watcher,
        )

    def _build_and_start_orchestrator(
        self, parts: OrchestratorLayersParts, built: _PartsBuiltSoFar
    ) -> RunPaths:
        """Orchestrator 型の start (クラスの docstring の 1〜9 とスレッドの起動) を行う。

        作った部品は、作った直後に built に記録する。段階 (PhaseTracker) には入らず、DeepScheduler・SurfaceLoop・
        MiddleLoop・SystemScreenLoop は作らない。
        """
        settings = self.settings
        # 1〜3: 時計・実行ディレクトリー (空の notes/ と初期指示の写し)・run.jsonl
        clock = RunClock(parts.monotonic)
        paths = create_run_directory(settings.runs_dir, parts.local_now())
        create_empty_notes(paths)
        if parts.initial_instruction is not None:
            write_initial_instruction(paths, parts.initial_instruction)
        run_log = RunLog(paths.run_log, clock)
        run_log.write(
            EVENT_START, run_dir=str(paths.root), args=run_settings_to_json(settings)
        )
        built.run_log = run_log
        # 4〜5: 操作の送信 (surface の関門を通さない 17 個のボタン) とキャプチャーのフレーム
        shared = self._build_output_and_capture(parts, None, paths, clock, built)
        # 6〜7: 操作の実行と監視・主ループ
        loops = build_orchestrator_loops(
            OrchestratorLoopParts(
                paths=paths,
                clock=clock,
                run_log=run_log,
                pad_output=shared.pad_output,
                latest_frame=shared.frame_buffer.latest,
                monitor=parts.monitor,
                initial_instruction=parts.initial_instruction,
                on_stop=self.request_stop,
                start_process=parts.start_process,
                signal_process_group=parts.signal_process_group,
                environ=parts.environ,
                switch_skills_dir=parts.switch_skills_dir,
            ),
            settings,
        )
        # 8: 安全の監視 (実行を始めるときの確かめが通ったら 6 と 7 を始める。照合の結果は使わない)
        safety_monitor = SafetyMonitor(
            SafetyMonitorParts(
                checker=parts.safety,
                latest_frame=shared.frame_buffer.latest,
                clock=clock,
                safety_log=paths.safety_log,
                on_stop=self.request_stop,
                on_start_passed=loops.set_started,
                on_system_screen=_ignore_system_screen,
            ),
            SafetyMonitorSettings(
                interval_s=settings.safety_interval_s,
                threshold=settings.threshold_safety,
                max_errors=settings.max_safety_errors,
            ),
        )
        # 9: 画面の停滞の計測
        progress_loop = ProgressLoop(
            paths, ScreenProgressTracker(settings.progress_window_s)
        )
        main_loop_thread = threading.Thread(
            target=loops.main_loop.run,
            args=(self._stop_event,),
            name="orchestrator-main-loop",
            daemon=True,
        )
        loop_threads = (
            threading.Thread(
                target=run_monitor_loop,
                args=(
                    loops.controller,
                    self._stop_event,
                    self._stop_for_monitor_loop_failure,
                ),
                name="orchestrator-monitor-loop",
                daemon=True,
            ),
            main_loop_thread,
            threading.Thread(
                target=safety_monitor.run,
                args=(self._stop_event,),
                name="safety-monitor",
                daemon=True,
            ),
            threading.Thread(
                target=progress_loop.run,
                args=(self._stop_event,),
                name="progress-loop",
                daemon=True,
            ),
        )
        built.loop_threads.extend(loop_threads)
        built.main_loop_thread = main_loop_thread
        with self._lock:
            self._started = _StartedParts(
                paths=paths,
                clock=clock,
                run_log=run_log,
                pad_output=shared.pad_output,
                frame_store=shared.frame_store,
                capture_source=shared.capture_source,
                capture_watcher=shared.capture_watcher,
                deep_scheduler=None,
                loop_threads=loop_threads,
                main_loop_thread=main_loop_thread,
            )
        for thread in loop_threads:
            self.start_thread(thread)
        return paths

    def _stop_for_monitor_loop_failure(self, description: str) -> None:
        """Orchestrator 型の監視のループが想定外の例外で終わったときに呼ぶ (監視のループのスレッド)。

        request_stop(monitor_loop_failure_reason(description)) で全体を止める。
        """
        self.request_stop(monitor_loop_failure_reason(description))

    def _is_play(self) -> bool:
        """段階が play なら True を返す (段階のロックを持っているときに呼ぶ)。prepare に入る前は False。"""
        tracker = self._tracker
        return tracker is not None and tracker.phase is RunPhase.PLAY

    def _playing(self) -> bool:
        """段階のロックを取って、段階が play なら True を返す (ロックを持っていないスレッドから呼ぶ)。"""
        with self._phase_lock:
            return self._is_play()

    def _current_phase(self) -> tuple[RunPhase, float] | None:
        """段階のロックを取って、(段階, その段階に入った時刻) を返す。段階に入る前は None (deep のスケジューラーに渡す)。"""
        with self._phase_lock:
            tracker = self._tracker
            if tracker is None:
                return None
            return tracker.phase, tracker.phase_started_t

    def _phase_snapshot(self) -> _PhaseSnapshot:
        """段階のロックの中で、段階の判定の読み取りをまとめて返す (段階に入る前は PREPARE・0.0・False)。"""
        with self._phase_lock:
            tracker = self._tracker
            if tracker is None or self._stop_event.is_set():
                return _PhaseSnapshot(RunPhase.PREPARE, 0.0, False)
            return _PhaseSnapshot(
                tracker.phase, tracker.phase_started_t, tracker.wants_system_middle
            )

    def _enter_prepare(self) -> None:
        """実行を始めるときの確かめが通ったら呼ぶ (安全の監視のスレッド)。prepare に入る (start の 8)。

        先に止める理由が決まっていたら、どの段階にも入らない。
        """
        with self._phase_lock:
            with self._lock:
                started = self._started
                if started is None or self._stop_reason is not None:
                    return
            self._tracker = PhaseTracker(started.clock.now(), self.parts.phase_timings)
            started.run_log.write(
                EVENT_PHASE,
                phase=RunPhase.PREPARE.value,
                reason=PHASE_REASON_START_CHECK_PASSED,
            )

    def _phase_input(self, call: Callable[[PhaseTracker], PhaseEvent]) -> None:
        """段階のロックの中で、段階の判定に入力を渡して (call(tracker))、戻り値を扱う。"""
        with self._phase_lock:
            self._phase_input_locked(call)

    def _phase_input_locked(self, call: Callable[[PhaseTracker], PhaseEvent]) -> None:
        """段階の判定に入力を渡し、戻り値を扱う (start の docstring)。段階のロックを持っているときに呼ぶ。

        段階に入る前と、全体を止めた後は何もしない。
        """
        tracker = self._tracker
        with self._lock:
            started = self._started
        if tracker is None or started is None or self._stop_event.is_set():
            return
        was_play = tracker.phase is RunPhase.PLAY
        event = call(tracker)
        if event.change is not None:
            started.run_log.write(
                EVENT_PHASE,
                phase=event.change.phase.value,
                reason=event.change.reason,
            )
            if was_play and event.change.phase is not RunPhase.PLAY:
                # play から離れるので、surface の操作を中立にし、タスクの現在の場面を場面なしにする
                started.pad_output.neutral()
                if started.board is not None:
                    started.board.set_current_scene(None, started.clock.now())
        if event.stop_reason is not None:
            self.request_stop(event.stop_reason)
        if event.clean_up_end is not None:
            self._end_clean_up_locked(event.clean_up_end)

    def _press_home(self) -> None:
        """middle のゲーム用の出力の press_home が真のときに呼ぶ (middle のスレッド。start の 7)。

        段階のロックの中で、play のときだけ、recover に移して (phase の行と中立のレポート) から HOME を短押しする。
        HOME の送信の失敗は、logging で警告を書いて続ける。
        """
        with self._phase_lock:
            with self._lock:
                started = self._started
            if started is None or not self._is_play() or self._stop_event.is_set():
                return
            now = started.clock.now()
            self._phase_input_locked(lambda tracker: tracker.on_press_home(now))
            try:
                started.pad_output.apply(
                    PadAction(taps=frozenset({HOME_BUTTON})), source="system"
                )
            except Exception:
                logger.warning("pressing HOME for press_home failed", exc_info=True)

    def _end_clean_up_locked(self, result: CleanUpResult) -> None:
        """片付けを result で終える (段階のロックを持っているときに呼ぶ)。2 回目以降は何もしない。

        run.jsonl に clean_up_end を書き、ループの停止の合図を立ててから出力を止める。
        """
        with self._lock:
            if self._clean_up_ended:
                return
            self._clean_up_ended = True
            started = self._started
        if started is not None:
            started.run_log.write(EVENT_CLEAN_UP_END, result=result.value)
        self._stop_output_now(started)

    def _stop_output_now(self, started: _StartedParts | None) -> None:
        """ループの停止の合図を立ててから、PadOutput.stop_output() で操作を中立にして出力を止める。"""
        self._stop_event.set()
        if started is not None:
            started.pad_output.stop_output()
            with self._lock:
                self._output_stopped = True

    def _run_surface_while_playing(self, surface_loop: SurfaceLoop) -> None:
        """surface のループのスレッドの本体。段階が play の間だけ SurfaceLoop.step を 1 回ずつ進める。

        play でなければ PLAY_POLL_S ごとに確かめ直す。step の想定外の例外 (Exception) は外に出さず、
        request_stop(`surface loop failed: <例外の説明>`) を呼んでから戻る (SurfaceLoop.run と同じ扱い)。
        """
        stop = self._stop_event
        while not stop.is_set():
            if not self._playing():
                stop.wait(PLAY_POLL_S)
                continue
            try:
                result = surface_loop.step(stop)
            except Exception as error:
                logger.warning("surface loop failed", exc_info=True)
                self.request_stop(f"surface loop failed: {describe_exception(error)}")
                return
            if result.outcome is SurfaceStepOutcome.STOPPED:
                return
            stop.wait(result.wait_s)

    def _run_middle_while_playing(
        self, middle_loop: MiddleLoop, paths: RunPaths
    ) -> None:
        """middle のゲーム用のループのスレッドの本体。段階が play の間だけ MiddleLoop.run_once を呼ぶ。

        最初のフレームが frames.jsonl に載るまでと、play でない間は呼ばない。最初の実行の理由は
        MiddleTrigger.first_reasons() (最初の実行の直前に呼び、それまでに届いたきっかけは捨てる)。
        run_once が戻ったら (止める合図で抜けたときを除く)、段階のロックの中で段階を確かめ、play でなければ
        TaskBoard.set_current_scene(None, clock.now()) で現在の場面を場面なしにする (run_once の途中で play から出たとき、
        その回の出力の書き込みが場面を設定し直しても、play 以外の段階で続いた秒数を進めないため)。
        続けて、戻った時刻 (clock.now()) を前回の応答の時刻として、MiddleTrigger.wait_next で次の理由を待つ (None なら戻る)。
        run_once の想定外の例外 (Exception) は外に出さず、request_stop(`middle loop failed: <例外の説明>`) を
        呼んでから戻る (MiddleLoop.run と同じ扱い)。
        """
        stop = self._stop_event
        trigger = middle_loop.parts.trigger
        reasons: tuple[MiddleCallReason, ...] | None = None
        try:
            while not stop.is_set() and not has_recorded_frame(paths):
                stop.wait(FIRST_FRAME_POLL_S)
            while not stop.is_set():
                if not self._playing():
                    stop.wait(PLAY_POLL_S)
                    continue
                if reasons is None:
                    reasons = trigger.first_reasons()
                result = middle_loop.run_once(reasons, stop)
                if result.stopped:
                    return
                with self._phase_lock:
                    if not self._is_play():
                        # run_once の途中で play から出た: その回の書き込みが設定した場面を、場面なしに戻す
                        middle_loop.parts.board.set_current_scene(
                            None, middle_loop.parts.clock.now()
                        )
                reasons = trigger.wait_next(stop, middle_loop.parts.clock.now())
                if reasons is None:
                    return
        except Exception as error:
            logger.warning("middle loop failed", exc_info=True)
            self.request_stop(f"middle loop failed: {describe_exception(error)}")

    def _clean_up_failed_start(
        self, built: _PartsBuiltSoFar, error: BaseException
    ) -> None:
        """start の途中で error が出たときの後片付け (start の docstring)。作っていない部品は飛ばす。

        部品を閉じる・run.jsonl に書くそれぞれの段階は _run_cleanup_step で呼び、1 つが例外を出しても残りを続ける。
        """
        self.request_stop(_start_failure_reason(error))
        with self._lock:
            # 後で close を呼んでも、もう一度は閉じない
            self._closed = True
            output_stopped = self._output_stopped
        # 先に別のスレッドが理由だけを決めていた場合も、合図は必ず立てる (close の 0 と同じ)
        self._stop_event.set()
        # request_stop は、_started を記録する前なら合図を立てるだけなので、作った PadOutput をここで中立にする
        pad_output = built.pad_output
        if pad_output is not None and not output_stopped:
            _run_cleanup_step(pad_output.stop_output, "stop the pad output")
            with self._lock:
                self._output_stopped = True
        # close と同じ順に閉じる
        for thread in built.loop_threads:
            if thread.is_alive():  # 起動したスレッドだけを待つ
                thread.join(_loop_join_timeout_s(thread, built.main_loop_thread))
        if built.deep_scheduler is not None:
            _run_cleanup_step(built.deep_scheduler.close, "close the deep scheduler")
        if built.capture_source is not None:
            _run_cleanup_step(built.capture_source.close, "close the capture source")
        frame_store = built.frame_store
        if frame_store is not None:
            _run_cleanup_step(frame_store.close, "close the frame store")
        run_log = built.run_log
        if run_log is not None:
            if frame_store is not None:
                _run_cleanup_step(
                    lambda: run_log.write(
                        EVENT_FRAMES_DROPPED, count=frame_store.dropped
                    ),
                    "write the dropped frames",
                )
            _run_cleanup_step(
                lambda: run_log.write(EVENT_STOP, reason=self._current_reason()),
                "write the stop",
            )
            _run_cleanup_step(run_log.close, "close run.jsonl")
        if pad_output is not None:
            _run_cleanup_step(pad_output.close, "close the pad output")

    @property
    def paths(self) -> RunPaths | None:
        """実行ディレクトリーの RunPaths。start の前は None。"""
        with self._lock:
            return None if self._started is None else self._started.paths

    @property
    def stop_reason(self) -> str | None:
        """止まった理由 (最初に request_stop に渡された理由)。まだ止まっていなければ None。"""
        with self._lock:
            return self._stop_reason

    def request_stop(self, reason: str) -> None:
        """全体を止めるよう求める。どのスレッドから呼んでもよい。

        最初の呼び出しだけが理由を決める (2 回目以降の理由は、片付けの間の扱いを除いて無視する)。
            - 片付けを行う理由 (REASON_INTERRUPTED・surface_loop.surface_failure_reason の形・
              middle_loop.middle_failure_reason の形) で、段階に入っていて (prepare・play・recover)、まだ片付けを
              していなければ: 止める理由を決め、PhaseTracker.begin_clean_up で clean_up に移る (run.jsonl に phase を
              書く。play から離れるときは中立のレポートを送る)。ループの停止の合図と PadOutput.stop_output() は、まだ
              呼ばない (片付けの操作を送るため)
            - 片付けの間に来た理由: 安全の監視の理由 (unsafe screen*・safety check failed*・safety monitor failed*) なら
              片付けを unsafe で終え、キャプチャー・ブリッジの理由 (hardware.capture_watcher の理由・
              REASON_BRIDGE_FAILED) なら stopped で終える。どちらも止める理由は片付けを始めた理由のまま。ほかの理由は
              無視する
            - それ以外 (片付けを行わない理由、段階に入る前、片付けを行う理由でも既に片付けをした後): 止める理由を決め、
              ループの停止の合図を立ててから、すぐに PadOutput.stop_output() (操作を中立にし、以後の操作を送らない) を呼ぶ
        片付けを終えるとき (PhaseTracker が片付けの終わりを返したときと、上の unsafe・stopped と、wait の 2 回目の
        Ctrl+C の interrupted): run.jsonl に clean_up_end {"result": run_phase.CleanUpResult の値} を 1 回だけ書き、
        ループの停止の合図を立ててから PadOutput.stop_output() を呼ぶ。
        後片付けは close で行う。合図を先に立てるのは、出力を止めた後に surface が判断の応答を受け取ったとき、合図を
        見て記録を書かずに抜けられるようにするため (surface_loop.SurfaceLoop.step)。
        """
        with self._phase_lock:
            with self._lock:
                started = self._started
                in_clean_up = self._clean_up_started and not self._clean_up_ended
                if not in_clean_up:
                    if self._stop_reason is not None:
                        return
                    self._stop_reason = reason
                begin_clean_up = (
                    not in_clean_up
                    and started is not None
                    and self._tracker is not None
                    and _needs_clean_up(reason)
                )
                if begin_clean_up:
                    self._clean_up_started = True
            if in_clean_up:
                result = _clean_up_end_for(reason)
                if result is not None:
                    self._end_clean_up_locked(result)
                return
            if begin_clean_up and started is not None:
                now = started.clock.now()
                self._phase_input_locked(
                    lambda tracker: tracker.begin_clean_up(now, reason)
                )
                return
        self._stop_output_now(started)

    def _in_clean_up(self) -> bool:
        """片付けの途中 (始めて、まだ終えていない) なら True。"""
        with self._lock:
            return self._clean_up_started and not self._clean_up_ended

    def _end_clean_up_by_interrupt(self) -> None:
        """片付けの間の 2 回目の Ctrl+C で、片付けを interrupted で終える。"""
        with self._phase_lock:
            self._end_clean_up_locked(CleanUpResult.INTERRUPTED)

    def _check_phase_time(self, started: _StartedParts) -> None:
        """段階の判定の時間切れ (片付けの時間切れ) を確かめる (wait の見張りの 1 回分)。"""
        now = started.clock.now()
        self._phase_input(lambda tracker: tracker.check_time(now))

    def wait(self) -> str:
        """止まるまで待ち、止まった理由を返す。

        ループの停止の合図が立つまで (片付けの間も)、MONITOR_INTERVAL_S ごとに (sleep で待って) 次を確かめる:
            - CaptureWatcher.check_once() が理由を返したら request_stop(その理由)
            - bridge_state() が "failed" なら request_stop(REASON_BRIDGE_FAILED)
            - 段階に入っていれば、PhaseTracker.check_time(clock.now()) の時間切れ (片付けの時間切れは片付けを timeout で
              終える。prepare と recover には時間切れが無い)
        待っている間の KeyboardInterrupt (Ctrl+C) は外に出さずに確かめを続ける。1 回目は request_stop(REASON_INTERRUPTED)
        にし、片付けの間の 2 回目は片付けを interrupted で終える。前に数えた KeyboardInterrupt から
        INTERRUPT_REPEAT_WINDOW_S 秒以内 (parts.monotonic で測る) のものは、同じ割り込みとして数えない。
        KeyboardInterrupt を数えたことがあれば、合図が立った後も、最後に数えた KeyboardInterrupt から
        INTERRUPT_REPEAT_WINDOW_S 秒たつまで sleep で待ち、その間の KeyboardInterrupt を捨ててから戻る。片付けをしない
        止まり方 (Orchestrator 型の Ctrl+C など) では、1 回の割り込みの 2 回目の SIGINT が close の途中に届き、後片付けを
        途中で終わらせるため。
        start の前に呼んだら RuntimeError にする。
        """
        with self._lock:
            started = self._started
        if started is None:
            raise RuntimeError("LayersApp.wait must be called after start")
        counted_interrupt_t = self._watch_until_stopped(started)
        if counted_interrupt_t is not None:
            self._absorb_repeated_interrupts(counted_interrupt_t)
        return self._current_reason()

    def _absorb_repeated_interrupts(self, counted_interrupt_t: float) -> None:
        """counted_interrupt_t から INTERRUPT_REPEAT_WINDOW_S 秒たつまで sleep で待ち、届いた KeyboardInterrupt を捨てる。"""
        while True:
            remaining = (
                counted_interrupt_t + INTERRUPT_REPEAT_WINDOW_S - self.parts.monotonic()
            )
            if remaining <= 0:
                return
            try:
                self.sleep(min(remaining, MONITOR_INTERVAL_S))
            except KeyboardInterrupt:
                continue

    def _watch_until_stopped(self, started: _StartedParts) -> float | None:
        """wait の見張りのループ (wait の docstring)。最後に数えた KeyboardInterrupt の時刻を返す。数えていなければ None。"""
        counted_interrupt_t: float | None = None
        while not self._stop_event.is_set():
            try:
                capture_reason = started.capture_watcher.check_once()
                if capture_reason is not None:
                    self.request_stop(capture_reason)
                elif self.parts.bridge_state() == BRIDGE_FAILED:
                    self.request_stop(REASON_BRIDGE_FAILED)
                else:
                    self._check_phase_time(started)
                    self.sleep(MONITOR_INTERVAL_S)
            except KeyboardInterrupt:
                interrupt_t = self.parts.monotonic()
                if (
                    counted_interrupt_t is not None
                    and interrupt_t - counted_interrupt_t <= INTERRUPT_REPEAT_WINDOW_S
                ):
                    continue
                counted_interrupt_t = interrupt_t
                if self._in_clean_up():
                    self._end_clean_up_by_interrupt()
                else:
                    self.request_stop(REASON_INTERRUPTED)
        return counted_interrupt_t

    def close(self) -> None:
        """全体を止めて後片付けをする。止まる条件より前に呼ばれたら、request_stop(REASON_CLOSED) と同じに扱う。

        1. ループの停止の合図を立て、ループのスレッド (advisor 型は surface と middle のループ・安全の監視・本体の画面の
           ループ・画面の停滞の計測のループ、Orchestrator 型は監視のループ・主ループ・安全の監視・停滞の計測のループ) の
           終わりを最大 LOOP_JOIN_TIMEOUT_S 秒ずつ待つ。Orchestrator 型の主ループのスレッドだけは、claude のセッションを
           閉じる時間を足した MAIN_LOOP_JOIN_TIMEOUT_S 秒まで待つ
        2. DeepScheduler.close() (以後の要求を捨て、実行中の deep のジョブを stop し、5 秒たっても終わらなければ kill する)。
           Orchestrator 型には DeepScheduler が無いので飛ばす
        3. CaptureSource.close()、FrameStore.close()
        4. run_log に frames_dropped {"count": FrameStore.dropped} を書き、続けて stop {"reason": 止まった理由} を書いて、
           RunLog.close() で閉じる (止まり切らなかったスレッドが、stop の後ろに行を書かないようにする)
        5. PadOutput.close()
        実機の部品 (キャプチャー・ブリッジ) は閉じない (作った側が閉じる)。2 回目以降の呼び出しは何もしない。
        start の前に呼んだら、理由を REASON_CLOSED にするだけで、後片付けするものは無い。
        start が途中の例外で後片付けを済ませた後に呼んだら、何もしない (二重に閉じない)。
        先に request_stop が理由だけを決めて、停止の処理 (合図と PadOutput.stop_output) の途中で割り込まれていた場合
        (Ctrl+C など) は、1 の前に停止の処理をやり直す。片付けの途中で呼ばれたら、片付けを待たずに (clean_up_end を
        書かずに) 1 の前に停止の処理を行う (止まる理由は片付けを始めた理由のまま)。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            started = self._started
        self.request_stop(REASON_CLOSED)
        if started is None:
            return
        # 0. 停止の処理が途中で終わっていたらやり直す
        self._stop_event.set()
        with self._lock:
            output_stopped = self._output_stopped
        if not output_stopped:
            started.pad_output.stop_output()
            with self._lock:
                self._output_stopped = True
        # 1. ループの終わりを待つ
        for thread in started.loop_threads:
            thread.join(_loop_join_timeout_s(thread, started.main_loop_thread))
        # 2. deep (Orchestrator 型には無い)
        if started.deep_scheduler is not None:
            started.deep_scheduler.close()
        # 3. キャプチャーと画像の保存
        started.capture_source.close()
        started.frame_store.close()
        # 4. 記録を閉じる
        started.run_log.write(EVENT_FRAMES_DROPPED, count=started.frame_store.dropped)
        started.run_log.write(EVENT_STOP, reason=self._current_reason())
        started.run_log.close()
        # 5. 短押しのタイマー
        started.pad_output.close()

    def run(self) -> str:
        """start → wait → close を行い、止まった理由を返す。wait の途中の例外でも close を呼ぶ。"""
        try:
            self.start()
            self.wait()
        finally:
            self.close()
        return self._current_reason()

    def _current_reason(self) -> str:
        """止まった理由を返す。まだ決まっていなければ REASON_CLOSED。"""
        with self._lock:
            return REASON_CLOSED if self._stop_reason is None else self._stop_reason
