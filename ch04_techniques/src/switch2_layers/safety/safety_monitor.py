"""安全の監視 (SafetyMonitor): 実行を始めるときの確かめと、surface とは別のスレッドの定期の確かめ。

surface の判断に依らず、キャプチャーの最新のフレーム 1 枚を SafetyChecker に送り、オンライン・購入・本体の設定の
画面かを確かめ、同じ要求で本体の画面を見本と照合する。止めるかの判定は safety.safety_judge で 3 つの問いだけで行い、
止めるときは on_stop(理由) を呼ぶ (app.LayersApp.request_stop。操作を中立にして以後の操作を送らず、3 層を止める)。
照合の結果 (system_screen.screen_judgment.SystemScreenJudgment) は、答えのあった確かめのたびに on_system_screen で
渡す (app は system_screen.screen_judgment.LatestSystemScreen.update を渡す)。照合の結果では止めない。

スレッドの流れ (run):
    1. 最初のフレームが届くまで、要求を送らずに待つ
    2. 実行を始めるときの確かめを 1 回行う。止まる条件に当たったら on_stop を呼んで終わる。当たらなければ
       on_start_passed を呼ぶ (app は、これを合図に surface と middle のループを始める)
    3. 止める合図が立つまで、settings.interval_s 秒ごとに監視の確かめを行う。前の確かめの応答を待ってから次を送る
       (safety_judge.next_check_wait_s)。止まる条件に当たったら on_stop を呼んで終わる
ブリッジの状態は見ない (ブリッジの復旧中も監視を続ける)。
スレッドが想定外の例外で終わるときも、on_stop(`safety monitor failed: <例外の説明>`) で全体を止める
(安全の監視が動かないまま操作を続けないため。safety_monitor_failure_reason)。
確かめのたびに safety.jsonl に 1 行書く (safety.safety_record)。止める合図は SafetyChecker.check が戻ったときに確かめ、
立っていたら、その回の行を書かず、照合の結果も渡さず、判定もせずに抜ける (止めた後に記録と知らせを増やさない)。
テストでは step を 1 回ずつ呼び、待ちは戻り値の wait_s で確かめる。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from switch2_layers.error_text import describe_exception
from switch2_layers.file_io import append_json_line
from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.frames.image_encoding import (
    DEFAULT_JPEG_QUALITY,
    encode_jpeg,
    nv12_to_bgr,
    shrink_to_width,
)
from switch2_layers.run_clock import RunClock
from switch2_layers.safety.safety_judge import (
    MonitorJudge,
    next_check_wait_s,
    start_stop_reason,
    validate_probabilities,
    validate_system_screen_probabilities,
)
from switch2_layers.safety.safety_record import SafetyRecord, safety_row
from switch2_layers.safety.safety_types import SafetyChecker, SafetyPhase
from switch2_layers.system_screen.screen_judgment import (
    SystemScreenJudgment,
    judge_system_screen,
)

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

FIRST_FRAME_POLL_S = 0.05  # 最初のフレームがまだ無いときに、確かめ直すまで待つ秒数
# 確かめに送る画像の幅の上限 (これより広いフレームは縮小する)
SAFETY_IMAGE_WIDTH = 1280


def safety_monitor_failure_reason(error: BaseException) -> str:
    """安全の監視のスレッドが想定外の例外 error で止まるときの理由 `safety monitor failed: <例外の説明>` を返す。"""
    return f"safety monitor failed: {describe_exception(error)}"


def build_safety_image(
    frame: CapturedFrame, image_width: int, jpeg_quality: int
) -> bytes:
    """frame を、確かめに送る JPEG のバイト列にする。時刻や box は書き込まない。

    frames.image_encoding の nv12_to_bgr → shrink_to_width(image_width) → encode_jpeg(jpeg_quality) の jpeg。
    """
    bgr = shrink_to_width(nv12_to_bgr(frame.nv12), image_width)
    return encode_jpeg(bgr, jpeg_quality).jpeg


@dataclass(frozen=True)
class _CheckResult:
    """1 回の確かめの結果 (step の 3)。

    t_sent          check を呼んだ時刻
    t_received      check が戻った (または例外を出した) 時刻
    probabilities   確かめた 3 つの問いの確率。失敗したら None
    system_screen   確かめた照合の確率。失敗したら None
    usage           API の usage。失敗したら None
    error           失敗の説明。成功したら None
    """

    t_sent: float
    t_received: float
    probabilities: dict[str, float] | None
    system_screen: dict[str, float] | None
    usage: object | None
    error: str | None


class SafetyStepOutcome(StrEnum):
    """1 回の step の結果の種類。"""

    NO_FRAME = "no_frame"  # まだフレームが無く、確かめなかった。行は書かない
    PASSED = "passed"  # 答えがあり、止まる条件に当たらなかった。行を書いた
    # 監視の確かめが失敗したが、止まる条件に当たらなかった。行を書いた
    FAILED = "failed"
    STOP = "stop"  # 止まる条件に当たり、on_stop を呼んだ。行を書いた
    # 止める合図を見て途中で抜けた。行を書かず、照合の結果も渡さず、判定もしていない
    STOPPED = "stopped"


@dataclass(frozen=True)
class SafetyStepResult:
    """1 回の step の結果。

    outcome       結果の種類
    wait_s        次の step までに待つ秒数
    row           safety.jsonl に書いた行。書かなかったら None
    stop_reason   on_stop に渡した理由。呼ばなかったら None
    """

    outcome: SafetyStepOutcome
    wait_s: float
    row: dict[str, object] | None
    stop_reason: str | None


@dataclass(frozen=True)
class SafetyMonitorSettings:
    """安全の監視の設定。

    interval_s     監視の確かめを送る間隔 (秒) (--safety-interval)
    threshold      安全の問いのしきい値 (--threshold-safety)
    max_errors     監視の確かめの失敗がこの回数続いたら止める (--max-safety-errors)
    image_width    送る画像の幅の上限
    jpeg_quality   送る画像の JPEG の品質
    """

    interval_s: float
    threshold: float
    max_errors: int
    image_width: int = SAFETY_IMAGE_WIDTH
    jpeg_quality: int = DEFAULT_JPEG_QUALITY


@dataclass(frozen=True)
class SafetyMonitorParts:
    """安全の監視が使う部品。

    checker            画面を確かめるもの
    latest_frame       キャプチャーの最新のフレームを返す関数 (run では FrameBuffer.latest)。まだ無ければ None
    clock              実行の経過秒の時計
    safety_log         safety.jsonl のパス (run_directory.RunPaths.safety_log)
    on_stop            全体を止める関数 (理由の文字列を受け取る)
    on_start_passed    実行を始めるときの確かめで止まる条件に当たらなかったときに、1 回だけ呼ぶ関数
    on_system_screen   答えのあった確かめのたびに、照合の結果を受け取る関数 (run では LatestSystemScreen.update)。
                       失敗した確かめと、止める合図を見て抜けた確かめでは呼ばない
    """

    checker: SafetyChecker
    latest_frame: Callable[[], CapturedFrame | None]
    clock: RunClock
    safety_log: Path
    on_stop: Callable[[str], None]
    on_start_passed: Callable[[], None]
    on_system_screen: Callable[[SystemScreenJudgment], None]


class SafetyMonitor:
    """実行を始めるときの確かめと、監視の確かめを行う。step は 1 つのスレッドからだけ呼ぶ。"""

    def __init__(
        self, parts: SafetyMonitorParts, settings: SafetyMonitorSettings
    ) -> None:
        """実行を始めるときの確かめをまだ終えていない状態で作る。監視の判定は safety_judge.MonitorJudge で行う。"""
        self.parts = parts
        self.settings = settings
        self._start_passed = False
        self._judge = MonitorJudge(
            threshold=settings.threshold, max_errors=settings.max_errors
        )

    @property
    def start_passed(self) -> bool:
        """実行を始めるときの確かめで、止まる条件に当たらなかったら True。"""
        return self._start_passed

    def step(self, stop: threading.Event | None = None) -> SafetyStepResult:
        """確かめを 1 回行い、結果を返す (待ちはしない)。

        stop は止める合図 (run が渡す)。None なら合図を確かめない。

        1. frame = latest_frame()。None なら NO_FRAME (wait_s = FIRST_FRAME_POLL_S) を返す
        2. 段階は、start_passed が False なら START、True なら MONITOR
        3. jpeg = build_safety_image(frame, image_width, jpeg_quality)。t_sent = clock.now() で checker.check(jpeg) を
           呼び、答えを safety_judge.validate_probabilities(答えの probabilities) と
           safety_judge.validate_system_screen_probabilities(答えの system_screen) で確かめる。戻ったら (例外でも)
           t_received = clock.now()。check か 2 つの確かめのどれかが例外 (Exception) を出したら、その回は失敗とする
        4. stop が立っていたら STOPPED (wait_s = 0.0) を返す (行を書かず、on_system_screen を呼ばず、判定もしない)
        5. 成功なら screen = system_screen.screen_judgment.judge_system_screen(照合の確率)。失敗なら None
        6. safety.jsonl に SafetyRecord(t_sent, t_received, frame.t, 段階, 確率, 照合の確率, screen, usage, error) の
           行を書く (safety_record.safety_row。file_io.append_json_line)。失敗した回は、確率・照合の確率・screen・
           usage を None、error を失敗の説明 (error_text.describe_exception) にする。成功した回の error は None
        7. 成功なら on_system_screen(SystemScreenJudgment(frame_t=frame.t, t_received, screen, 照合の確率)) を呼ぶ
           (段階に依らず、この回の判定で止まるときも呼ぶ)。失敗なら呼ばない
        8. 判定 (3 つの問いの確率だけで決める。照合の確率と screen は使わない):
           START    safety_judge.start_stop_reason(確率 (失敗なら None), threshold)。理由があれば on_stop(理由) を
                    呼んで STOP を返す。無ければ start_passed を True にし、on_start_passed() を呼んで PASSED を返す
           MONITOR  成功なら MonitorJudge.record_answer(確率)、失敗なら MonitorJudge.record_failure()。理由があれば
                    on_stop(理由) を呼んで STOP を返す。無ければ PASSED (成功) か FAILED (失敗) を返す
        STOP の wait_s は 0.0、PASSED と FAILED の wait_s は safety_judge.next_check_wait_s(interval_s, t_sent, t_received)。
        画像の作成・行の書き込み・on_system_screen・on_stop・on_start_passed の例外は外に出す (run が全体を止める)。
        """
        parts = self.parts
        frame = parts.latest_frame()
        if frame is None:
            return SafetyStepResult(
                SafetyStepOutcome.NO_FRAME, FIRST_FRAME_POLL_S, None, None
            )
        phase = SafetyPhase.MONITOR if self._start_passed else SafetyPhase.START
        jpeg = build_safety_image(
            frame, self.settings.image_width, self.settings.jpeg_quality
        )
        result = self._check(jpeg)
        if stop is not None and stop.is_set():
            return SafetyStepResult(SafetyStepOutcome.STOPPED, 0.0, None, None)
        screen = (
            None
            if result.system_screen is None
            else judge_system_screen(result.system_screen)
        )
        row = safety_row(
            SafetyRecord(
                t_sent=result.t_sent,
                t_received=result.t_received,
                frame_t=frame.t,
                phase=phase,
                probabilities=result.probabilities,
                system_screen=result.system_screen,
                system_screen_result=screen,
                usage=result.usage,
                error=result.error,
            )
        )
        append_json_line(parts.safety_log, row)
        if result.system_screen is not None:
            parts.on_system_screen(
                SystemScreenJudgment(
                    frame_t=frame.t,
                    t_received=result.t_received,
                    screen=screen,
                    probabilities=result.system_screen,
                )
            )
        reason = self._judge_result(phase, result.probabilities)
        if reason is not None:
            parts.on_stop(reason)
            return SafetyStepResult(SafetyStepOutcome.STOP, 0.0, row, reason)
        if phase is SafetyPhase.START:
            self._start_passed = True
            parts.on_start_passed()
        outcome = (
            SafetyStepOutcome.FAILED
            if result.probabilities is None
            else SafetyStepOutcome.PASSED
        )
        wait_s = next_check_wait_s(
            self.settings.interval_s, result.t_sent, result.t_received
        )
        return SafetyStepResult(outcome, wait_s, row, None)

    def run(self, stop: threading.Event) -> None:
        """stop が立つまで step を繰り返し、各 step の後に stop.wait(wait_s) で待つ (スレッドの本体)。

        step が STOP か STOPPED を返したら、待たずに戻る。
        step の想定外の例外 (Exception) は外に出さず、on_stop(safety_monitor_failure_reason(例外)) を呼んでから戻る
        (安全の監視が動かないまま操作を続けないため)。
        """
        try:
            while not stop.is_set():
                result = self.step(stop)
                if result.outcome in (
                    SafetyStepOutcome.STOP,
                    SafetyStepOutcome.STOPPED,
                ):
                    return
                stop.wait(result.wait_s)
        except Exception as error:
            logger.warning("safety monitor failed", exc_info=True)
            self.parts.on_stop(safety_monitor_failure_reason(error))

    def _check(self, jpeg: bytes) -> _CheckResult:
        """checker.check(jpeg) を呼んで、答えの 3 つの問いの確率と照合の確率を確かめる (step の 3)。

        check か確かめの例外 (Exception) は外に出さず、失敗の結果 (確率・照合の確率・usage が None、error が説明) にする。
        """
        clock = self.parts.clock
        t_sent = clock.now()
        try:
            answer = self.parts.checker.check(jpeg)
            probabilities = validate_probabilities(answer.probabilities)
            system_screen = validate_system_screen_probabilities(answer.system_screen)
        except Exception as error:
            logger.debug("safety check failed", exc_info=True)
            return _CheckResult(
                t_sent, clock.now(), None, None, None, describe_exception(error)
            )
        return _CheckResult(
            t_sent, clock.now(), probabilities, system_screen, answer.usage, None
        )

    def _judge_result(
        self, phase: SafetyPhase, probabilities: dict[str, float] | None
    ) -> str | None:
        """確かめの結果 (3 つの問いの確率) から止まる理由を決める (step の 8)。止めなければ None。"""
        settings = self.settings
        if phase is SafetyPhase.START:
            return start_stop_reason(probabilities, settings.threshold)
        if probabilities is None:
            return self._judge.record_failure()
        return self._judge.record_answer(probabilities)
