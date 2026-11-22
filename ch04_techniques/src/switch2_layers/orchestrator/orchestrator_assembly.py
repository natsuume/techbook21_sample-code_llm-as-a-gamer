"""Orchestrator 型と委任型の run の、主ループ (OrchestratorMainLoop) と操作の実行と監視・委任 (OrchestratorController)
の組み立て。

app.LayersApp が Orchestrator 型と委任型の start で使う。共有の部品 (実行ディレクトリー・時計・run.jsonl・PadOutput・
キャプチャーのフレーム) と注入された部品を OrchestratorLoopParts で受け取り、2 つの部品をつないで返す
(build_orchestrator_loops。settings.mode で監視型か委任型かを決める)。監視のループのスレッドの本体
(run_monitor_loop) と、委任のループのスレッドの本体 (run_delegate_loop) も置く。
スレッドの起動と止まる理由の決め方は app が行う (このモジュールは app を import しない)。
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.error_text import describe_exception
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.latest_frames import LatestFrame
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.orchestrator.claude_session_command import ClaudeSessionOptions
from switch2_layers.orchestrator.claude_stream_session import StreamProcessStarter
from switch2_layers.orchestrator.codex_instructions import (
    CODEX_FRAMES_GUIDE_TEXT,
    codex_delegate_instructions,
    codex_orchestrator_instructions,
)
from switch2_layers.orchestrator.codex_main_loop_session import (
    CodexMainLoopSessionFactory,
)
from switch2_layers.orchestrator.codex_session_command import codex_skills_dir
from switch2_layers.orchestrator.decision_parser import parse_decision
from switch2_layers.orchestrator.decision_types import Decision, DecisionError
from switch2_layers.orchestrator.delegate_loop import DelegateSettings
from switch2_layers.orchestrator.live_trial import LiveTrial
from switch2_layers.orchestrator.main_loop import (
    DecisionParser,
    OrchestratorMainLoop,
    OrchestratorMainLoopParts,
    OrchestratorMainLoopSettings,
)
from switch2_layers.orchestrator.main_loop_session import MainLoopSessionFactory
from switch2_layers.orchestrator.monitor_loop import MonitorAsker
from switch2_layers.orchestrator.orchestrator_controller import (
    OrchestratorController,
    OrchestratorParts,
    OrchestratorSettings,
)
from switch2_layers.orchestrator.orchestrator_instructions import (
    delegate_instructions,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.turn_input import FRAMES_GUIDE_TEXT
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import AgentKind, RunMode, RunSettings

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorLoopParts:
    """主ループと、操作の実行と監視を組み立てるのに使う部品。

    paths                  実行ディレクトリー
    clock                  実行の経過秒の時計 (PadOutput の clock と同じ monotonic から作ったもの)
    run_log                run.jsonl (主ループがセッションの開始・終了と JSON の誤りを書く)
    pad_output             操作の送信 (HOME を含む 17 個のボタンで作ったもの。operations.jsonl に書く on_operation 付き)
    latest_frame           キャプチャーの最新のフレームを返す関数 (FrameBuffer.latest)
    latest_frames          最新の一定間隔のフレームの画像を新しい順にまとめて返す関数
                           (frames.latest_frames.LatestFrames.snapshot。主ループの入力の画像に使う)
    monitor                監視 (委任型では委任) の要求の送り先 (run では surface.jev_client.JevClient)
    initial_instruction    人の初期指示。無ければ None
    on_stop                実行全体を止める関数 (止まる理由の文字列を受け取る。app.LayersApp.request_stop)
    start_process          主ループの子プロセス (claude か codex app-server) を起動する関数 (None なら subprocess.Popen)
    signal_process_group   主ループの子プロセスのプロセスグループにシグナルを送る関数 (None なら os.killpg)
    environ                主ループの子プロセスの環境変数の元 (None ならセッションを始めるときの os.environ)
    switch_skills_dir      本体の操作のスキルを写す元
    buffered_frames        キャプチャーの元のフレームを古い順に返す関数 (FrameBuffer.frames。委任の要求の画像に使う)。
                           委任型だけで使い、委任型では要る (None なら build_orchestrator_loops が ValueError)
    """

    paths: RunPaths
    clock: RunClock
    run_log: RunLog
    pad_output: PadOutput
    latest_frame: Callable[[], CapturedFrame | None]
    latest_frames: Callable[[], Sequence[LatestFrame]]
    monitor: MonitorAsker
    initial_instruction: str | None
    on_stop: Callable[[str], None]
    start_process: StreamProcessStarter | None
    signal_process_group: Callable[[int, int], None] | None
    environ: Mapping[str, str] | None
    switch_skills_dir: Path
    buffered_frames: Callable[[], Sequence[CapturedFrame]] | None = None
    live: LiveTrial | None = None


@dataclass(frozen=True)
class OrchestratorLoops:
    """組み立てた 2 つの部品。

    controller   操作の実行と監視 (run を監視のループのスレッドで動かす)。委任型では操作の実行と委任 (run を委任の
                 ループのスレッドで動かす)
    main_loop    主ループ (run を主ループのスレッドで動かす)
    """

    controller: OrchestratorController
    main_loop: OrchestratorMainLoop

    def set_started(self) -> None:
        """実行を始めるときの安全の確かめが通ったことを、controller と main_loop の両方に伝える。"""
        self.controller.set_started()
        self.main_loop.set_started()


def registered_macro_parser(controller: OrchestratorController) -> DecisionParser:
    """応答の本文を、controller が今登録しているマクロの名前と照らして読む関数を返す。

    返す関数は text を decision_parser.parse_decision(text, controller.macro_names()) で読む (前のターンまでに登録した
    マクロを呼ぶ JSON を誤りにしないため)。
    """

    def parse(text: str) -> Decision | DecisionError:
        return parse_decision(text, controller.macro_names())

    return parse


def delegate_decision_parser(controller: OrchestratorController) -> DecisionParser:
    """--mode delegate の応答の本文を、controller の今のマクロの登録と委任の状態に照らして読む関数を返す。

    返す関数は text を decision_parser.parse_decision(text, controller.macro_names(), controller.delegate_context())
    で読む (呼ぶたびに今の値を読む)。
    """

    def parse(text: str) -> Decision | DecisionError:
        return parse_decision(
            text, controller.macro_names(), controller.delegate_context()
        )

    return parse


@dataclass(frozen=True)
class MainLoopAgentParts:
    """主ループの部品のうち、エージェント (--agent) で変わるもの (OrchestratorMainLoopParts の同じ名前の項目に渡す)。

    instructions        変わらない指示の文
    session_factory     セッションを作るもの (None なら、主ループが Claude Code のものを作る)
    skills_dir          スキルの写し先 (None なら .claude/skills/)
    frames_guide_text   入力の「## 古いフレーム」の節の本文
    Claude Code では、instructions のほかは既定値 (主ループの Claude Code の動きを変えない)。
    """

    instructions: str
    session_factory: MainLoopSessionFactory | None = None
    skills_dir: Path | None = None
    frames_guide_text: str = FRAMES_GUIDE_TEXT


def codex_agent_parts(
    parts: OrchestratorLoopParts, settings: RunSettings, delegate: bool
) -> MainLoopAgentParts:
    """--agent codex の主ループの部品を返す。

    instructions        delegate なら codex_instructions.codex_delegate_instructions()、そうでなければ
                        codex_orchestrator_instructions()
    session_factory     codex_main_loop_session.CodexMainLoopSessionFactory(paths, instructions,
                        model = settings.codex_model, effort = settings.orchestrator_effort, clock = clock.now,
                        environ, start_process, signal_process_group)
    skills_dir          codex_session_command.codex_skills_dir(paths) (.agents/skills/)
    frames_guide_text   codex_instructions.CODEX_FRAMES_GUIDE_TEXT
    """
    instructions = (
        codex_delegate_instructions() if delegate else codex_orchestrator_instructions()
    )
    return MainLoopAgentParts(
        instructions=instructions,
        session_factory=CodexMainLoopSessionFactory(
            paths=parts.paths,
            instructions=instructions,
            model=settings.codex_model,
            effort=settings.orchestrator_effort,
            clock=parts.clock.now,
            environ=parts.environ,
            start_process=parts.start_process,
            signal_process_group=parts.signal_process_group,
        ),
        skills_dir=codex_skills_dir(parts.paths),
        frames_guide_text=CODEX_FRAMES_GUIDE_TEXT,
    )


def build_orchestrator_loops(
    parts: OrchestratorLoopParts, settings: RunSettings
) -> OrchestratorLoops:
    """OrchestratorController と OrchestratorMainLoop を作り、主ループから controller を使うようにつないで返す。

    スレッドは起動しない。settings.mode が delegate でなければ監視型:
        controller   OrchestratorController(OrchestratorParts(pad_output, jev = monitor, latest_frame, clock, paths),
                     OrchestratorSettings(monitor_threshold, max_monitor_errors))
        main_loop    OrchestratorMainLoop(OrchestratorMainLoopParts(paths, clock,
                     claude = ClaudeSessionOptions(effort = orchestrator_effort),
                     parse_decision = registered_macro_parser(controller), controller, latest_frames,
                     initial_instruction, run_log, on_stop, start_process, signal_process_group, environ,
                     switch_skills_dir),
                     OrchestratorMainLoopSettings(rotate_tokens, turn_timeout_s, max_decision_errors))
    値は settings の Orchestrator 型の 6 つの値。範囲は cli が settings.run_settings_errors で確かめてある
    (主ループが受け付けない値なら、OrchestratorMainLoop が ValueError にする)。
    settings.mode が delegate なら委任型 (監視型との違いだけを書く):
        controller   OrchestratorParts に latest_frames・buffered_frames・run_log も渡し、OrchestratorSettings の
                     delegate を delegate_loop.DelegateSettings(image_width = delegate_image_width,
                     max_errors = max_delegate_errors) にする (monitor_threshold と max_monitor_errors は settings の値の
                     まま渡すが、委任型では使わない)
        main_loop    parse_decision = delegate_decision_parser(controller)、
                     instructions = orchestrator_instructions.delegate_instructions()
    委任型で parts.buffered_frames が None なら ValueError。
    settings.agent が codex なら、どちらの型でも、主ループの instructions・session_factory・skills_dir・
    frames_guide_text を codex_agent_parts の値にする (claude なら instructions のほかは渡さない)。
    """
    delegate = settings.mode is RunMode.DELEGATE
    if delegate:
        if parts.buffered_frames is None:
            raise ValueError("the delegate mode needs buffered_frames")
        controller = OrchestratorController(
            OrchestratorParts(
                pad_output=parts.pad_output,
                jev=parts.monitor,
                latest_frame=parts.latest_frame,
                clock=parts.clock,
                paths=parts.paths,
                latest_frames=parts.latest_frames,
                buffered_frames=parts.buffered_frames,
                live=parts.live,
                run_log=parts.run_log,
            ),
            OrchestratorSettings(
                monitor_threshold=settings.monitor_threshold,
                max_monitor_errors=settings.max_monitor_errors,
                delegate=DelegateSettings(
                    image_width=settings.delegate_image_width,
                    max_errors=settings.max_delegate_errors,
                ),
            ),
        )
        parse = delegate_decision_parser(controller)
        instructions = delegate_instructions()
    else:
        controller = OrchestratorController(
            OrchestratorParts(
                pad_output=parts.pad_output,
                jev=parts.monitor,
                latest_frame=parts.latest_frame,
                clock=parts.clock,
                paths=parts.paths,
            ),
            OrchestratorSettings(
                monitor_threshold=settings.monitor_threshold,
                max_monitor_errors=settings.max_monitor_errors,
            ),
        )
        parse = registered_macro_parser(controller)
        instructions = orchestrator_instructions()
    agent_parts = (
        codex_agent_parts(parts, settings, delegate)
        if settings.agent is AgentKind.CODEX
        else MainLoopAgentParts(instructions=instructions)
    )
    main_loop = OrchestratorMainLoop(
        OrchestratorMainLoopParts(
            paths=parts.paths,
            clock=parts.clock,
            claude=ClaudeSessionOptions(effort=settings.orchestrator_effort),
            parse_decision=parse,
            controller=controller,
            latest_frames=parts.latest_frames,
            initial_instruction=parts.initial_instruction,
            run_log=parts.run_log,
            on_stop=parts.on_stop,
            start_process=parts.start_process,
            signal_process_group=parts.signal_process_group,
            environ=parts.environ,
            switch_skills_dir=parts.switch_skills_dir,
            instructions=agent_parts.instructions,
            session_factory=agent_parts.session_factory,
            skills_dir=agent_parts.skills_dir,
            frames_guide_text=agent_parts.frames_guide_text,
            live=parts.live,
        ),
        OrchestratorMainLoopSettings(
            rotate_tokens=settings.rotate_tokens,
            turn_timeout_s=settings.turn_timeout_s,
            max_decision_errors=settings.max_decision_errors,
        ),
    )
    return OrchestratorLoops(controller=controller, main_loop=main_loop)


def run_monitor_loop(
    controller: OrchestratorController,
    stop: threading.Event,
    on_failure: Callable[[str], None],
) -> None:
    """監視のループのスレッドの本体。controller.run(stop) を呼び、stop が立って戻ったら何もせずに戻る。

    run が想定外の例外 (Exception。orchestrator/monitor.jsonl の追記の失敗など) で抜けたら、外に出さずに
    on_failure(error_text.describe_exception(例外)) を呼んで戻る (app は、説明から止まる理由を作って全体を止める関数を
    渡す)。押下状態は controller.run が中立に戻してから例外を出す。
    """
    try:
        controller.run(stop)
    except Exception as error:
        logger.warning("orchestrator monitor loop failed", exc_info=True)
        on_failure(describe_exception(error))


def run_delegate_loop(
    controller: OrchestratorController,
    stop: threading.Event,
    on_failure: Callable[[str], None],
) -> None:
    """委任のループのスレッドの本体。委任型の controller の controller.run(stop) を呼び、stop が立って戻ったら何もせずに
    戻る。

    run が想定外の例外 (Exception。orchestrator/delegate.jsonl の追記の失敗など) で抜けたら、外に出さずに
    on_failure(error_text.describe_exception(例外)) を呼んで戻る (app は、説明から委任のループの止まる理由
    (app.delegate_loop_failure_reason) を作って全体を止める関数を渡す)。押下状態は controller.run が中立に戻してから
    例外を出す。
    """
    try:
        controller.run(stop)
    except Exception as error:
        logger.warning("orchestrator delegate loop failed", exc_info=True)
        on_failure(describe_exception(error))
