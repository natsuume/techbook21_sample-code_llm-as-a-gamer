"""3 層をすべて依存注入で組み立てて、1 つのプロセスのスレッドとして起動・停止する (LayersApp)。

実機の部品 (キャプチャーの読み取り元・ブリッジ) と各層 (surface・middle・deep) は LayersParts で受け取る。
cli の run は実機の部品と各層 (surface は --surface で選ぶ。middle と deep はフェイク) を渡し、テストはフェイクの
キャプチャー・ブリッジ・層を渡して数秒動かす。surface の box の追跡のための接続点 (on_frame・surface_boxes_at・
surface_plan_boxes・on_run_directory) も LayersParts で受け取る。安全の監視 (safety.safety_monitor) が画面を
確かめるもの (SafetyChecker) も LayersParts で受け取る (cli の run は --safety で選ぶ)。

止まる条件 (どれも、操作を中立にして出力を止め、全体を止めて、run.jsonl の stop に理由を書く):
    - Ctrl+C (wait の中の KeyboardInterrupt)                          REASON_INTERRUPTED
    - キャプチャーの最新フレームが 2 秒以上古い・出力が終わった・起動から 10 秒たっても最初のフレームが無い
                                                                    hardware.capture_watcher の理由
    - ブリッジの復旧が失敗した (bridge_state() が "failed")             REASON_BRIDGE_FAILED
    - surface か middle が続けて失敗した                                surface_loop・middle_loop の理由
    - 安全の確かめで HOME メニューなどの画面を見つけたか、確かめが失敗した
                                                                    safety.safety_judge・safety_monitor の理由
ブリッジの復旧中は、surface が要求を送らず、操作を中立にして待つ (surface_loop)。安全の監視は続ける。
surface と middle のループは、安全の監視のスレッドが実行を始めるときの確かめ (最初のフレームが届いた後に 1 回) で
止まる条件に当たらなかったと分かるまで、ループを始めずに待つ (それまで操作を 1 つも送らない)。
start の途中で例外 (Ctrl+C の KeyboardInterrupt を含む) が出たら、それまでに作った部品と起動したスレッドだけを
止めて閉じ、run.jsonl の stop に理由を書いてから、その例外を外に出す (LayersApp.start)。後片付けの 1 段階が例外を
出しても、logging で警告を書いて残りの段階を続ける。

スレッド:
    キャプチャーの読み取り (CaptureSource)、画像の書き出し (FrameStore)、短押しのタイマー (PadOutput)、
    surface のループ、middle のループ、安全の監視 (SafetyMonitor)、deep の自動の呼び出し (DeepScheduler) とジョブ。
    wait を呼んだスレッド (run ではメインスレッド) が、止まる条件を MONITOR_INTERVAL_S ごとに確かめる。
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from switch2_layers.buttons import allowed_buttons
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
from switch2_layers.hardware.capture_watcher import CaptureWatcher
from switch2_layers.hardware.pad_output import PadOutput, ReportSender
from switch2_layers.middle.middle_loop import (
    MiddleLoop,
    MiddleLoopParts,
    MiddleLoopSettings,
)
from switch2_layers.middle.middle_types import MiddleAnalyzer
from switch2_layers.records.operation_log import OperationLog
from switch2_layers.records.run_log import (
    EVENT_FRAMES_DROPPED,
    EVENT_START,
    EVENT_STOP,
    RunLog,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import (
    RunPaths,
    create_run_directory,
    write_initial_notes,
)
from switch2_layers.safety.safety_types import SafetyChecker
from switch2_layers.settings import RunSettings, run_settings_to_json
from switch2_layers.surface.middle_state_reader import read_surface_context
from switch2_layers.surface.surface_loop import (
    SurfaceLoop,
    SurfaceLoopParts,
    SurfaceLoopSettings,
)
from switch2_layers.surface.surface_types import Box, SurfaceDecider

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

REASON_INTERRUPTED = "interrupted"
REASON_BRIDGE_FAILED = "bridge recovery failed"
REASON_CLOSED = "closed"  # 止まる条件より前に close が呼ばれた
MONITOR_INTERVAL_S = 0.1  # 止まる条件を確かめる間隔 (秒)
# 終了時に surface と middle のループのスレッドの終わりを待つ上限 (秒)
LOOP_JOIN_TIMEOUT_S = 5.0
BRIDGE_FAILED = "failed"  # bridge_state が返す、ブリッジの復旧が失敗した状態


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
    """

    frame_reader: LatestFrameReader
    report_sender: ReportSender
    bridge_state: Callable[[], str]
    surface: SurfaceDecider
    middle: MiddleAnalyzer
    deep: DeepRunner
    safety: SafetyChecker
    monotonic: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now
    on_frame: Callable[[CapturedFrame], None] | None = None
    surface_boxes_at: Callable[[float], Sequence[Box]] | None = None
    on_run_directory: Callable[[RunPaths], None] | None = None
    surface_plan_boxes: Callable[[str | None], BoxDrawingPlan] | None = None


@dataclass(frozen=True)
class _StartedParts:
    """start で組み立てた部品 (close で後片付けするもの)。"""

    paths: RunPaths
    run_log: RunLog
    pad_output: PadOutput
    frame_store: FrameStore
    capture_source: CaptureSource
    capture_watcher: CaptureWatcher
    deep_scheduler: DeepScheduler
    loop_threads: tuple[threading.Thread, ...]


@dataclass
class _PartsBuiltSoFar:
    """start が途中までに作った部品。start の途中で例外が出たときに、作ったものだけを後片付けするために使う。

    run_log          run.jsonl に start を書き終えた RunLog (書き終える前は None)
    pad_output       PadOutput (作る前は None)
    frame_store      FrameStore (作る前は None)
    capture_source   CaptureSource (作る前は None)
    deep_scheduler   DeepScheduler (作る前は None)
    loop_threads     作った surface と middle のループのスレッド (起動したかどうかは Thread.is_alive で見分ける)
    """

    run_log: RunLog | None = None
    pad_output: PadOutput | None = None
    frame_store: FrameStore | None = None
    capture_source: CaptureSource | None = None
    deep_scheduler: DeepScheduler | None = None
    loop_threads: list[threading.Thread] = field(default_factory=list)


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


class LayersApp:
    """3 層を組み立てて動かす。start → wait → close の順に呼ぶ (run はこれをまとめたもの)。

    settings       run の起動引数
    parts          注入する部品
    sleep          wait の見張りの間隔の待ちに使う関数 (既定 time.sleep)。テストでは KeyboardInterrupt を出す関数を渡せる
    start_thread   start が surface と middle のループのスレッドを起動する関数 (既定 threading.Thread.start)。
                   テストでは、起動の途中で例外を出す関数を渡せる
    """

    def __init__(
        self,
        settings: RunSettings,
        parts: LayersParts,
        sleep: Callable[[float], None] = time.sleep,
        start_thread: Callable[[threading.Thread], None] = threading.Thread.start,
    ) -> None:
        """属性を持つ。ディレクトリーとスレッドは start で作る。"""
        self.settings = settings
        self.parts = parts
        self.sleep = sleep
        self.start_thread = start_thread
        self._lock = threading.Lock()
        # ループの停止の合図 (surface と middle のループが見る)
        self._stop_event = threading.Event()
        self._stop_reason: str | None = None
        # 停止の処理 (PadOutput.stop_output) を終えたか。理由が決まっただけで処理の途中に割り込まれた場合
        # (Ctrl+C など) に、close でやり直すために理由とは別に持つ
        self._output_stopped = False
        self._start_called = False
        self._closed = False
        self._started: _StartedParts | None = None

    def start(self) -> RunPaths:
        """実行ディレクトリーを作り、部品を組み立ててスレッドを起動し、実行ディレクトリーの RunPaths を返す。

        1. RunClock(parts.monotonic) を作る (ここが t = 0)
        2. run_directory.create_run_directory(settings.runs_dir, parts.local_now()) と
           write_initial_notes(paths, buttons.allowed_buttons(settings.allow_home))
        3. RunLog を作り、start {"run_dir": 実行ディレクトリー, "args": settings.run_settings_to_json(settings)} を書く。
           続けて、parts.on_run_directory があれば on_run_directory(paths) を 1 回呼ぶ (スレッドを起動する前。
           notes/ の初期ファイルは書いた後)
        4. OperationLog と PadOutput(report_sender, 押せるボタン, on_operation=OperationLog.write) を作る
        5. FrameBuffer、FrameStore(interval_fps, interval_width)、CaptureSource(frame_reader, …,
           on_frame=parts.on_frame)、CaptureWatcher を作る
        6. DeepScheduler (current_scene_id は middle/state.json の scene_id、latest_frame_t は frames.jsonl の最後の t、
           timeout_s は deep_timeout_s) を作って start する (タイムアウトと自動の呼び出しの判定のスレッド)
        7. SurfaceLoop (max_errors = max_surface_errors、boxes_at = parts.surface_boxes_at、
           plan_boxes = parts.surface_plan_boxes) と
           MiddleLoop (interval_s・window_s・max_errors) を作り、それぞれのスレッドを start_thread で起動する。
           on_stop には request_stop を渡す
        8. SafetyMonitor (checker = parts.safety、latest_frame = FrameBuffer.latest、safety_log = paths.safety_log、
           on_stop = request_stop、on_start_passed = 実行を始めるときの確かめを終えた合図を立てる関数。
           interval_s = safety_interval_s、threshold = threshold_safety、max_errors = max_safety_errors、
           allow_home = allow_home) を作り、そのスレッドを 7 の 2 つの後に start_thread で起動する
        7 の surface と middle のスレッドは、8 の確かめを終えた合図か、ループの停止の合図が立つまで待ってから、
        ループを始める (停止の合図が先に立ったら、ループを始めずに終わる)。
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
        """start の 1〜7 を行う。作った部品は、作った直後に built に記録する。"""
        settings = self.settings
        parts = self.parts
        # 1〜3: 時計・実行ディレクトリー・run.jsonl
        clock = RunClock(parts.monotonic)
        paths = create_run_directory(settings.runs_dir, parts.local_now())
        buttons = allowed_buttons(settings.allow_home)
        write_initial_notes(paths, buttons)
        run_log = RunLog(paths.run_log, clock)
        run_log.write(
            EVENT_START, run_dir=str(paths.root), args=run_settings_to_json(settings)
        )
        built.run_log = run_log
        if parts.on_run_directory is not None:
            parts.on_run_directory(paths)
        # 4: 操作の送信
        operation_log = OperationLog(paths.operations_log, clock)
        pad_output = PadOutput(
            parts.report_sender,
            buttons,
            clock=parts.monotonic,
            on_operation=operation_log.write,
        )
        built.pad_output = pad_output
        # 5: キャプチャーのフレーム
        frame_buffer = FrameBuffer()
        frame_store = FrameStore(paths, settings.interval_fps, settings.interval_width)
        built.frame_store = frame_store
        capture_source = CaptureSource(
            parts.frame_reader,
            clock,
            frame_buffer,
            frame_store,
            on_frame=parts.on_frame,
        )
        built.capture_source = capture_source
        capture_watcher = CaptureWatcher(
            parts.frame_reader.is_ended, capture_source.latest_t, clock
        )
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
            ),
            DeepSchedulerSettings(
                min_start_interval_s=settings.deep_min_start_interval_s,
                auto_seconds=settings.deep_auto_seconds,
                timeout_s=settings.deep_timeout_s,
            ),
        )
        built.deep_scheduler = deep_scheduler
        deep_scheduler.start()
        # 7: surface と middle のループ
        surface_loop = SurfaceLoop(
            SurfaceLoopParts(
                paths=paths,
                decider=parts.surface,
                frame_buffer=frame_buffer,
                frame_store=frame_store,
                pad_output=pad_output,
                deep_requests=deep_scheduler,
                run_log=run_log,
                clock=clock,
                bridge_state=parts.bridge_state,
                on_stop=self.request_stop,
                boxes_at=parts.surface_boxes_at,
                plan_boxes=parts.surface_plan_boxes,
            ),
            SurfaceLoopSettings(max_errors=settings.max_surface_errors),
        )
        middle_loop = MiddleLoop(
            MiddleLoopParts(
                paths=paths,
                analyzer=parts.middle,
                clock=clock,
                run_log=run_log,
                deep_requests=deep_scheduler,
                on_stop=self.request_stop,
            ),
            MiddleLoopSettings(
                interval_s=settings.middle_interval_s,
                window_s=settings.middle_window_s,
                max_errors=settings.max_middle_errors,
            ),
        )
        loop_threads = (
            threading.Thread(
                target=surface_loop.run,
                args=(self._stop_event,),
                name="surface-loop",
                daemon=True,
            ),
            threading.Thread(
                target=middle_loop.run,
                args=(self._stop_event,),
                name="middle-loop",
                daemon=True,
            ),
        )
        built.loop_threads.extend(loop_threads)
        with self._lock:
            self._started = _StartedParts(
                paths=paths,
                run_log=run_log,
                pad_output=pad_output,
                frame_store=frame_store,
                capture_source=capture_source,
                capture_watcher=capture_watcher,
                deep_scheduler=deep_scheduler,
                loop_threads=loop_threads,
            )
        for thread in loop_threads:
            self.start_thread(thread)
        return paths

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
                thread.join(LOOP_JOIN_TIMEOUT_S)
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

        最初の呼び出しだけが理由を決める (2 回目以降の理由は無視する)。最初の呼び出しで、ループの停止の合図を立ててから、
        すぐに PadOutput.stop_output() (操作を中立にし、以後の操作を送らない) を呼ぶ。後片付けは close で行う。
        合図を先に立てるのは、出力を止めた後に surface が判断の応答を受け取ったとき、合図を見て記録を書かずに
        抜けられるようにするため (surface_loop.SurfaceLoop.step)。
        """
        with self._lock:
            if self._stop_reason is not None:
                return
            self._stop_reason = reason
            started = self._started
        self._stop_event.set()
        if started is not None:
            started.pad_output.stop_output()
            with self._lock:
                self._output_stopped = True

    def wait(self) -> str:
        """止まるまで待ち、止まった理由を返す。

        request_stop が呼ばれるまで、MONITOR_INTERVAL_S ごとに (sleep で待って) 次を確かめる:
            - CaptureWatcher.check_once() が理由を返したら request_stop(その理由)
            - bridge_state() が "failed" なら request_stop(REASON_BRIDGE_FAILED)
        待っている間の KeyboardInterrupt (Ctrl+C) は外に出さず、request_stop(REASON_INTERRUPTED) にする。
        start の前に呼んだら RuntimeError にする。
        """
        with self._lock:
            started = self._started
        if started is None:
            raise RuntimeError("LayersApp.wait must be called after start")
        try:
            while not self._stop_event.is_set():
                capture_reason = started.capture_watcher.check_once()
                if capture_reason is not None:
                    self.request_stop(capture_reason)
                elif self.parts.bridge_state() == BRIDGE_FAILED:
                    self.request_stop(REASON_BRIDGE_FAILED)
                else:
                    self.sleep(MONITOR_INTERVAL_S)
        except KeyboardInterrupt:
            self.request_stop(REASON_INTERRUPTED)
        return self._current_reason()

    def close(self) -> None:
        """全体を止めて後片付けをする。止まる条件より前に呼ばれたら、request_stop(REASON_CLOSED) と同じに扱う。

        1. ループの停止の合図を立て、surface と middle のループと安全の監視のスレッドの終わりを
           最大 LOOP_JOIN_TIMEOUT_S 秒ずつ待つ
        2. DeepScheduler.close() (以後の要求を捨て、実行中の deep のジョブを stop し、5 秒たっても終わらなければ kill する)
        3. CaptureSource.close()、FrameStore.close()
        4. run_log に frames_dropped {"count": FrameStore.dropped} を書き、続けて stop {"reason": 止まった理由} を書いて、
           RunLog.close() で閉じる (止まり切らなかったスレッドが、stop の後ろに行を書かないようにする)
        5. PadOutput.close()
        実機の部品 (キャプチャー・ブリッジ) は閉じない (作った側が閉じる)。2 回目以降の呼び出しは何もしない。
        start の前に呼んだら、理由を REASON_CLOSED にするだけで、後片付けするものは無い。
        start が途中の例外で後片付けを済ませた後に呼んだら、何もしない (二重に閉じない)。
        先に request_stop が理由だけを決めて、停止の処理 (合図と PadOutput.stop_output) の途中で割り込まれていた場合
        (Ctrl+C など) は、1 の前に停止の処理をやり直す。
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
            thread.join(LOOP_JOIN_TIMEOUT_S)
        # 2. deep
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
