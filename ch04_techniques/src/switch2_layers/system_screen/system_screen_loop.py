"""本体の画面のループ (SystemScreenLoop): 段階が prepare・recover・clean_up の間、本体の画面の middle に操作の列を決めさせ、
照合の結果を確かめながら送る。1 回分の処理 (step) と、それを繰り返すスレッドの本体 (run)。

1 回分の流れ (step):
    1. 段階 (SystemScreenPhases) が wants_system_middle でなければ、何もしない
    2. 操作の送信の失敗の後なら、ブリッジの状態が ok に戻るまで (または段階が変わるまで) middle を呼ばない
    3. 照合の結果を待つ。段階に入った時刻より後で、かつ前の列を送り終えた時刻 + system_operation_runner.SETTLE_S より
       後のフレームの結果 (反映前の画面でカーソルの位置を読ませないため。前の段階の列を送っている間に段階が変わった
       場合も、その列の後の画面を待つ)。届くまで middle を呼ばない
    4. 入力の画像は、その照合のフレーム以降の最新のフレーム
    5. SystemScreenInput (目的は段階から PHASE_GOALS で決める。履歴はこの段階のもの。deep の助言は、呼び出しごとに
       notes/advice.md の「本体の画面」の節を読み直したもの) を作り、middle の decide を呼ぶ
    6. 失敗 (SystemScreenMiddleError) なら、middle/system_calls.jsonl に呼び出しの行を書き、続けた失敗を数える。
       段階の判定に on_system_middle_failed を渡してから、max_errors 回に達していれば on_stop を呼ぶ (on_stop で
       片付けに移った後に、同じ失敗を片付けの middle の失敗として渡さないため)
    7. 成功なら、呼び出しの行を書き、段階の判定に on_system_output を渡し、on_operations_started → 列を送る
       (OperationsRunner.run) → on_operations_finished の順に呼ぶ。実行の結果の行を書き、履歴に足す。操作が 0 個の列
       では、on_operations_started と on_operations_finished を段階の判定に渡さない (何も送らない列で、recover の
       照合の結果の続けた数を 0 に戻さないため)
    8. 列を送る途中の SystemOutputError (送信の失敗) なら、PadOutput.neutral() を送り、例外の result で実行の結果の行と
       履歴を作る (on_operations_finished も呼ぶ)。失敗の数には数えない
送り終えたら (打ち切りを含む)、間隔を置かずに次の回に進む (照合の結果を待つ)。middle の呼び出しの間隔
(--middle-interval) は使わない。
段階の読み取りと段階の判定への入力は SystemScreenPhases で受け取る (app が run_phase.PhaseTracker をロックの中で呼び、
戻り値を扱う)。テストでは step を 1 回ずつ呼ぶ。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import append_json_line
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import (
    DEFAULT_JPEG_QUALITY,
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.middle.middle_call_record import take_analyzer_call_details
from switch2_layers.middle.middle_loop import middle_failure_reason
from switch2_layers.middle.system_call_record import (
    SystemCallRecord,
    system_call_row,
    system_run_row,
)
from switch2_layers.middle.system_screen_middle import (
    SystemScreenMiddle,
    SystemScreenMiddleError,
)
from switch2_layers.middle.system_screen_types import (
    SystemGoal,
    SystemRunResult,
    SystemScreenInput,
    SystemScreenOutput,
    SystemStepRecord,
)
from switch2_layers.notes.system_screen_advice import read_system_screen_advice
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.system_screen.run_phase import RunPhase
from switch2_layers.system_screen.system_operation_runner import (
    JUDGMENT_POLL_S,
    SETTLE_S,
    JudgmentResult,
    JudgmentSource,
    SystemOutputError,
)

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# 本体の画面の middle を呼ばない間 (段階が play・ブリッジの復旧を待つ間) に、確かめ直すまで待つ秒数
SYSTEM_LOOP_POLL_S = 0.05
# middle に送る画像の幅の上限 (これより広いフレームは縮小する)
SYSTEM_SCREEN_IMAGE_WIDTH = 1280
# bridge_state が返す、ブリッジを使える状態 (BridgeSupervisor.state と同じ値)
BRIDGE_OK = "ok"

# 段階 → 本体の画面の middle の目的 (play では middle を呼ばない)
PHASE_GOALS: dict[RunPhase, SystemGoal] = {
    RunPhase.PREPARE: SystemGoal.PREPARE,
    RunPhase.RECOVER: SystemGoal.RECOVER,
    RunPhase.CLEAN_UP: SystemGoal.CLEAN_UP,
}


class SystemScreenPhases(Protocol):
    """本体の画面のループが読む段階と、段階の判定への入力 (run_phase.PhaseTracker と同じ名前と意味)。

    app は、run_phase.PhaseTracker をロックの中で呼び、戻り値 (段階の変化・止める理由・片付けの終わり) を扱うものを
    渡す。ループは入力のメソッドの戻り値を使わない。
    """

    @property
    def phase(self) -> RunPhase:
        """今の段階。"""
        ...

    @property
    def phase_started_t(self) -> float:
        """今の段階に入った時刻。"""
        ...

    @property
    def wants_system_middle(self) -> bool:
        """本体の画面の middle を呼んでよいなら True。"""
        ...

    def on_system_output(self, t: float, done: bool) -> object:
        """本体の画面の middle の出力 1 件 (done は出力の done) を渡す。"""
        ...

    def on_operations_started(self, t: float) -> object:
        """操作の列を送り始めたことを渡す。"""
        ...

    def on_operations_finished(self, t: float) -> object:
        """操作の列を送り終えた (打ち切り・送信の失敗を含む) ことを渡す。"""
        ...

    def on_system_middle_failed(self, t: float) -> object:
        """本体の画面の middle が失敗したことを渡す。"""
        ...


class OperationsRunner(Protocol):
    """操作の列を送るもの (run では system_screen.system_operation_runner.SystemOperationRunner)。"""

    def run(
        self,
        output: SystemScreenOutput,
        judged_screen: str | None,
        stop: threading.Event,
    ) -> SystemRunResult:
        """output.operations を、照合の結果を確かめながら前から順に送り、結果を返す。

        送信が失敗したら system_operation_runner.SystemOutputError を出す (その属性 result に、そこまでの結果)。
        """
        ...


class SystemScreenStepOutcome(StrEnum):
    """1 回の step の結果の種類。"""

    IDLE = "idle"  # 段階が本体の画面の middle を呼ばない (wants_system_middle が False)。何もしていない
    # 送信の失敗の後で、ブリッジが ok に戻っていない。middle を呼んでいない
    BRIDGE_NOT_READY = "bridge_not_ready"
    # 待つ照合の結果 (または入力の画像) がまだ無い。middle を呼んでいない
    NO_JUDGMENT = "no_judgment"
    SENT = (
        "sent"  # middle が成功し、操作の列を送った (打ち切りを含む)。2 つの行を書いた
    )
    MIDDLE_ERROR = "middle_error"  # middle が失敗した。呼び出しの行を書いた
    # 列を送る途中で送信が失敗した (SystemOutputError)。中立のレポートを送り、2 つの行を書いた
    SEND_FAILED = "send_failed"
    # middle が戻ったときに段階が変わっていた。呼び出しの行だけを書き、段階の判定に渡さず、操作を送っていない
    DISCARDED = "discarded"
    # 止める合図を見て途中で抜けた。それより後の記録・段階の判定への入力・送信をしていない
    STOPPED = "stopped"


@dataclass(frozen=True)
class SystemScreenStepResult:
    """1 回の step の結果。

    outcome   結果の種類
    wait_s    次の step までに待つ秒数 (IDLE と BRIDGE_NOT_READY は SYSTEM_LOOP_POLL_S、ほかは 0.0)
    """

    outcome: SystemScreenStepOutcome
    wait_s: float


@dataclass(frozen=True)
class SystemScreenLoopSettings:
    """本体の画面のループの設定。

    software_name   指定のソフトの名前 (--software。middle の入力に入れる)
    user_name       指定のユーザーの名前 (--user。middle の入力に入れる)
    max_errors      middle の失敗がこの回数続いたら on_stop を呼ぶ (--max-middle-errors)
    image_width     middle に送る画像の幅の上限
    jpeg_quality    middle に送る画像の JPEG の品質
    """

    software_name: str
    user_name: str
    max_errors: int
    image_width: int = SYSTEM_SCREEN_IMAGE_WIDTH
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass(frozen=True)
class SystemScreenLoopParts:
    """本体の画面のループが使う部品。

    paths          実行ディレクトリー (middle/system_calls.jsonl に書き、notes/advice.md を読む)
    middle         本体の画面の middle
    runner         操作の列を送るもの (run では SystemOperationRunner)
    judgments      最新の照合の結果を読む部品 (run では system_screen.screen_judgment.LatestSystemScreen)
    latest_frame   キャプチャーの最新のフレームを返す関数 (run では FrameBuffer.latest)。まだ無ければ None
    pad_output     送信の失敗の後に中立のレポートを送る PadOutput (runner と同じもの)
    phases         段階の読み取りと、段階の判定への入力
    clock          実行の経過秒の時計 (照合の結果の frame_t と同じ時計)
    bridge_state   ブリッジの状態 ("ok" / "recovering" / "failed") を返す関数
    on_stop        全体を止める関数 (理由の文字列を受け取る)
    """

    paths: RunPaths
    middle: SystemScreenMiddle
    runner: OperationsRunner
    judgments: JudgmentSource
    latest_frame: Callable[[], CapturedFrame | None]
    pad_output: PadOutput
    phases: SystemScreenPhases
    clock: RunClock
    bridge_state: Callable[[], str]
    on_stop: Callable[[str], None]


class SystemScreenLoop:
    """本体の画面のループ。step は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(
        self, parts: SystemScreenLoopParts, settings: SystemScreenLoopSettings
    ) -> None:
        """続けた失敗 0 回、履歴が空、送信の失敗の後の待ちが無い状態で作る。"""
        self.parts = parts
        self.settings = settings
        self._consecutive_errors = 0
        # この段階のこれまでの呼び出しの履歴
        self._history: list[SystemStepRecord] = []
        # 履歴を持っている段階に入った時刻 (段階が変わったかを見分ける)。まだ読んでいなければ None
        self._phase_started_t: float | None = None
        # 次の照合の結果に求める frame_t の下限。None なら、段階に入った時刻
        self._next_after_t: float | None = None
        # 操作の送信の失敗の後で、ブリッジが ok に戻るのを待っているなら True
        self._waiting_for_bridge = False

    @property
    def consecutive_errors(self) -> int:
        """本体の画面の middle の失敗が続いている回数 (成功で 0 に戻る。段階が変わっても戻さない)。"""
        return self._consecutive_errors

    def step(self, stop: threading.Event) -> SystemScreenStepResult:
        """1 回分を処理し、結果を返す (モジュールの docstring の 1〜8)。stop は止める合図。

        1. phases.wants_system_middle が False なら IDLE を返す
        2. 段階 (phases.phase と phases.phase_started_t) を読む。phase_started_t が前の回と違えば (段階が変わった)、
           この段階の履歴を空にし、送信の失敗の後の待ちをやめる
        3. 送信の失敗の後の待ちの間で、bridge_state() が BRIDGE_OK でなければ BRIDGE_NOT_READY を返す。
           BRIDGE_OK なら待ちをやめて続ける
        4. after_t を決める: 前の回の下限と phase_started_t の遅い方 (前の回が無ければ phase_started_t)。前の回の下限は、
           列を送った (SENT・SEND_FAILED) なら前の列を送り終えた時刻 + SETTLE_S、middle の失敗 (MIDDLE_ERROR) なら、
           その回に渡した照合の結果の frame_t (同じ照合の結果で続けて呼ばない)。段階が変わっても前の回の下限は捨てない
           (前の段階の列を送っている間に段階が変わったとき、その列の後の画面で次の列を決めるため)。
           judgments.wait_newer(after_t, JUDGMENT_POLL_S) が None なら NO_JUDGMENT を返す
        5. frame = latest_frame()。None か、frame.t が照合の結果の frame_t より小さければ NO_JUDGMENT を返す
        6. screen_input = SystemScreenInput(t=clock.now(), goal=PHASE_GOALS[phase], software_name, user_name,
           frame_jpeg=frame を image_width 以下に縮小した JPEG, frame_t=frame.t, judged_screen=照合の結果の screen,
           judged_frame_t=照合の結果の frame_t, history=この段階の履歴, deep_advice=
           notes.system_screen_advice.read_system_screen_advice(paths.notes_dir) (notes/ の外を指すシンボリックリンク
           などの読めない文書は無い文書として扱い、空文字列))。t_sent = clock.now() で middle.decide を呼び、
           戻ったら (例外でも) t_received = clock.now()。middle の記録は
           middle.middle_call_record.take_analyzer_call_details(middle) で受け取る。ここで stop を確かめる
        7. phases.phase_started_t が 2 で読んだ値と違えば (middle を呼んでいる間に段階が変わった)、呼び出しの行だけを
           書いて DISCARDED を返す (失敗の数を変えず、段階の判定に渡さず、操作を送らない)
        8. decide が SystemScreenMiddleError を出したら: 呼び出しの行 (system_call_record.system_call_row。output は
           None、error に説明) を paths.system_calls に書き、続けた失敗を 1 増やす。先に
           phases.on_system_middle_failed(t_received) を呼び、その後で、回数が max_errors に達していれば
           on_stop(middle.middle_loop.middle_failure_reason(回数)) を呼ぶ (この順にするのは、on_stop が片付けを始める
           理由のため。逆の順では、片付けに移った直後の clean_up で同じ失敗が片付けの middle の失敗になり、片付けの
           middle を一度も呼ばずに片付けが終わる)。MIDDLE_ERROR を返す
        9. 成功したら: 続けた失敗を 0 にし、呼び出しの行を書く。phases.on_system_output(t_received, output.done)、
           phases.on_operations_started(clock.now()) を呼び、runner.run(output, 照合の結果の screen, stop) で送る。
           finished_t = clock.now() で phases.on_operations_finished(finished_t) を呼び、実行の結果の行
           (system_call_record.system_run_row(finished_t, 結果)) を書き、履歴に SystemStepRecord(t=screen_input.t,
           judged_screen, operations=output.operations, sent_count, cut_reason) を足す。SENT を返す。
           output.operations が空 (操作が 0 個の列) なら、phases.on_operations_started と
           phases.on_operations_finished は呼ばない (runner.run・実行の結果の行・履歴・次の after_t は同じ)
        10. runner.run が SystemOutputError を出したら: pad_output.neutral() を送り、例外の result で 9 と同じく
            on_operations_finished・実行の結果の行・履歴を作る。送信の失敗の後の待ちに入り (失敗の数には数えない)、
            SEND_FAILED を返す
        stop が立っていたら、そこで STOPPED を返し、それより後の記録・段階の判定への入力・送信をしない。
        middle/system_calls.jsonl に書くのはこのスレッドだけ。
        """
        parts = self.parts
        phases = parts.phases
        if not phases.wants_system_middle:
            return SystemScreenStepResult(
                SystemScreenStepOutcome.IDLE, SYSTEM_LOOP_POLL_S
            )
        phase = phases.phase
        phase_started_t = phases.phase_started_t
        if phase_started_t != self._phase_started_t:
            self._phase_started_t = phase_started_t
            self._history = []
            self._waiting_for_bridge = False
        if self._waiting_for_bridge:
            if parts.bridge_state() != BRIDGE_OK:
                return SystemScreenStepResult(
                    SystemScreenStepOutcome.BRIDGE_NOT_READY, SYSTEM_LOOP_POLL_S
                )
            self._waiting_for_bridge = False
        after_t = (
            phase_started_t
            if self._next_after_t is None
            else max(phase_started_t, self._next_after_t)
        )
        judgment = parts.judgments.wait_newer(after_t, JUDGMENT_POLL_S)
        if judgment is None:
            return _result(SystemScreenStepOutcome.NO_JUDGMENT)
        frame = parts.latest_frame()
        if frame is None or frame.t < judgment.frame_t:
            return _result(SystemScreenStepOutcome.NO_JUDGMENT)
        screen_input = self._screen_input(phase, frame, judgment)
        t_sent = parts.clock.now()
        output: SystemScreenOutput | None = None
        error_text: str | None = None
        try:
            output = parts.middle.decide(screen_input)
        except SystemScreenMiddleError as error:
            logger.debug("system screen middle failed", exc_info=True)
            error_text = describe_exception(error)
        finally:
            details = take_analyzer_call_details(parts.middle)
        t_received = parts.clock.now()
        if stop.is_set():
            return _result(SystemScreenStepOutcome.STOPPED)
        append_json_line(
            parts.paths.system_calls,
            system_call_row(
                SystemCallRecord(
                    t_sent=t_sent,
                    t_received=t_received,
                    screen_input=screen_input,
                    output=output,
                    error=error_text,
                    details=details,
                )
            ),
        )
        if phases.phase_started_t != phase_started_t:
            return _result(SystemScreenStepOutcome.DISCARDED)
        if output is None:
            return self._handle_middle_error(judgment, t_received)
        return self._send(screen_input, output, judgment, t_received, stop)

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(wait_s) で待つ (スレッドの本体)。

        step が STOPPED を返したら、待たずに戻る。step の想定外の例外 (Exception) は外に出さず、
        on_stop(`system screen loop failed: <例外の説明>`) を呼んでから戻る。
        """
        while not stop.is_set():
            try:
                result = self.step(stop)
            except Exception as error:
                logger.warning("system screen loop failed", exc_info=True)
                self.parts.on_stop(
                    f"system screen loop failed: {describe_exception(error)}"
                )
                return
            if result.outcome is SystemScreenStepOutcome.STOPPED:
                return
            if result.wait_s > 0:
                stop.wait(result.wait_s)

    def _screen_input(
        self, phase: RunPhase, frame: CapturedFrame, judgment: JudgmentResult
    ) -> SystemScreenInput:
        """step の 6 の入力 (frame を縮小した JPEG と、judgment の照合の結果と、この段階の履歴) を作る。"""
        settings = self.settings
        jpeg = encode_jpeg(
            shrink_to_width(nv12_to_bgr(frame.nv12), settings.image_width),
            settings.jpeg_quality,
        ).jpeg
        return SystemScreenInput(
            t=self.parts.clock.now(),
            goal=PHASE_GOALS[phase],
            software_name=settings.software_name,
            user_name=settings.user_name,
            frame_jpeg=jpeg,
            frame_t=frame.t,
            judged_screen=judgment.screen,
            judged_frame_t=judgment.frame_t,
            history=tuple(self._history),
            deep_advice=read_system_screen_advice(self.parts.paths.notes_dir),
        )

    def _handle_middle_error(
        self, judgment: JudgmentResult, t_received: float
    ) -> SystemScreenStepResult:
        """middle の失敗を数え、段階の判定に渡してから、上限に達していれば on_stop を呼ぶ (step の 8)。"""
        self._consecutive_errors += 1
        # 次は、この回より新しい照合の結果を待って呼ぶ
        self._next_after_t = judgment.frame_t
        self.parts.phases.on_system_middle_failed(t_received)
        if self._consecutive_errors >= self.settings.max_errors:
            self.parts.on_stop(middle_failure_reason(self._consecutive_errors))
        return _result(SystemScreenStepOutcome.MIDDLE_ERROR)

    def _send(
        self,
        screen_input: SystemScreenInput,
        output: SystemScreenOutput,
        judgment: JudgmentResult,
        t_received: float,
        stop: threading.Event,
    ) -> SystemScreenStepResult:
        """成功した出力の列を送り、記録して履歴に足す (step の 9 と 10)。"""
        parts = self.parts
        phases = parts.phases
        self._consecutive_errors = 0
        phases.on_system_output(t_received, output.done)
        # 何も送らない列では、送り始めと送り終えを渡さない (recover の照合の結果の続けた数を 0 に戻さない)
        has_operations = bool(output.operations)
        if has_operations:
            phases.on_operations_started(parts.clock.now())
        send_failed = False
        try:
            result = parts.runner.run(output, judgment.screen, stop)
        except SystemOutputError as error:
            logger.warning("sending the system screen operations failed: %s", error)
            # ブリッジ側でボタンが押されたまま残っているおそれがあるため、押下の状態をそろえる
            parts.pad_output.neutral()
            result = error.result
            send_failed = True
        finished_t = parts.clock.now()
        if stop.is_set():
            return _result(SystemScreenStepOutcome.STOPPED)
        if has_operations:
            phases.on_operations_finished(finished_t)
        append_json_line(parts.paths.system_calls, system_run_row(finished_t, result))
        self._history.append(
            SystemStepRecord(
                t=screen_input.t,
                judged_screen=judgment.screen,
                operations=output.operations,
                sent_count=result.sent_count,
                cut_reason=result.cut_reason,
            )
        )
        self._next_after_t = finished_t + SETTLE_S
        if send_failed:
            self._waiting_for_bridge = True
            return _result(SystemScreenStepOutcome.SEND_FAILED)
        return _result(SystemScreenStepOutcome.SENT)


def _result(outcome: SystemScreenStepOutcome) -> SystemScreenStepResult:
    """待たずに次の step に進む結果 (wait_s = 0.0) を返す。"""
    return SystemScreenStepResult(outcome, 0.0)
