"""主ループの操作の列が終わって画面が落ち着いたかの決め方 (settled_time・settled_frame_after・actions_settled) と、
それを待つ処理 (wait_for_settled_actions) を確かめる。

待ちは、テストが進める時計と、主ループの列の時刻・最新のフレームの時刻を返す偽の関数で動かす。「まだ返らない」は、
偽の関数が読まれた回数を待ち合わせに使って確かめる (実時間の長さに頼らない)。
"""

import logging
import threading
import time
from dataclasses import dataclass, field

from switch2_layers.orchestrator.action_settle import (
    ACTIONS_SETTLE_S,
    actions_settled,
    settled_frame_after,
    settled_time,
    wait_for_settled_actions,
)
from switch2_layers.orchestrator.orchestrator_events import MainActionsRun
from switch2_layers.run_clock import RunClock

logger = logging.getLogger(__name__)

# 待つ処理の 1 回の待ちの上限 (秒)
POLL_S = 0.002
# テストが待ち合わせる上限 (秒)。正しい実装ではここまで待たない
WAIT_LIMIT_S = 2.0
RUNNING = MainActionsRun(started_t=10.0, released_t=None, ended_t=None)
# t=11.0 に最後に離して終わった列 (時計の条件は t=11.3 から、フレームは t=11.3 より厳密に後に撮ったものが要る)
RELEASED_AT_11 = MainActionsRun(started_t=10.0, released_t=11.0, ended_t=11.0)
# 押す操作の無い (wait だけの) 列。t=10.5 に終わった
WAIT_ONLY = MainActionsRun(started_t=10.0, released_t=None, ended_t=10.5)


# --- 時計の条件 (settled_time) ---


def test_settle_time_is_three_tenths_of_a_second():
    assert ACTIONS_SETTLE_S == 0.3


def test_actions_not_ended_are_not_settled():
    assert settled_time(RUNNING) is None
    assert settled_time(MainActionsRun(10.0, 10.1, None)) is None


def test_actions_settle_three_tenths_after_the_last_release():
    assert settled_time(RELEASED_AT_11) == 11.3
    # 最後の wait が 0.3 秒より短ければ、離してから 0.3 秒たった時刻
    assert settled_time(MainActionsRun(10.0, 11.0, 11.1)) == 11.3


def test_settled_time_is_rounded_to_milliseconds():
    # 1.1 + 0.3 は float では 1.4000000000000001 になる
    assert settled_time(MainActionsRun(1.0, 1.1, 1.1)) == 1.4


def test_trailing_wait_longer_than_the_settle_time_settles_at_the_end():
    assert settled_time(MainActionsRun(10.0, 11.0, 12.0)) == 12.0


def test_actions_without_a_release_settle_when_they_end():
    assert settled_time(WAIT_ONLY) == 10.5


# --- フレームの条件 (settled_frame_after) ---


def test_frame_must_be_taken_after_the_settle_time_of_the_last_release():
    assert settled_frame_after(RELEASED_AT_11) == 11.3
    # 最後の wait の長さに依らず、最後に離した時刻から決める
    assert settled_frame_after(MainActionsRun(10.0, 11.0, 12.0)) == 11.3
    # ミリ秒に丸めた値
    assert settled_frame_after(MainActionsRun(1.0, 1.1, 1.1)) == 1.4


def test_actions_without_a_release_have_no_frame_condition():
    assert settled_frame_after(WAIT_ONLY) is None


# --- 2 つの条件 (actions_settled) ---


def test_not_settled_before_the_actions_end():
    assert actions_settled(RUNNING, now_t=20.0, latest_frame_t=19.9) is False


def test_not_settled_before_the_settle_time_even_with_a_new_frame():
    assert actions_settled(RELEASED_AT_11, now_t=11.299, latest_frame_t=11.299) is False


def test_settled_time_passed_but_no_frame_after_it_is_not_settled():
    # 時計は落ち着く時刻を過ぎたが、その後に撮ったフレームがまだ無い
    assert actions_settled(RELEASED_AT_11, now_t=11.5, latest_frame_t=11.2) is False
    assert actions_settled(RELEASED_AT_11, now_t=11.5, latest_frame_t=None) is False


def test_frame_exactly_at_the_settle_time_is_not_enough():
    # 監視と同じく、ちょうど同じ時刻のフレームは通さない (厳密に後だけ)
    assert actions_settled(RELEASED_AT_11, now_t=11.5, latest_frame_t=11.3) is False
    # ミリ秒に丸めた境界でも同じ
    released_at_1_1 = MainActionsRun(1.0, 1.1, 1.1)
    assert actions_settled(released_at_1_1, now_t=2.0, latest_frame_t=1.4) is False
    assert actions_settled(released_at_1_1, now_t=2.0, latest_frame_t=1.401) is True


def test_settled_with_a_frame_after_the_settle_time():
    assert actions_settled(RELEASED_AT_11, now_t=11.5, latest_frame_t=11.301) is True


def test_actions_without_a_release_do_not_use_the_frame_condition():
    assert actions_settled(WAIT_ONLY, now_t=10.5, latest_frame_t=None) is True
    assert actions_settled(WAIT_ONLY, now_t=10.5, latest_frame_t=9.0) is True
    assert actions_settled(WAIT_ONLY, now_t=10.499, latest_frame_t=None) is False


# --- 待つ処理 ---


class ManualMonotonic:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RunSource:
    """main_actions_run の偽物。run を書き換えて列の時刻を変え、読まれた回数を数える。"""

    def __init__(self, run: MainActionsRun | None) -> None:
        self.run = run
        self._reads = 0
        self._changed = threading.Condition()

    def read(self) -> MainActionsRun | None:
        with self._changed:
            self._reads += 1
            self._changed.notify_all()
        return self.run

    @property
    def reads(self) -> int:
        with self._changed:
            return self._reads


class FrameTimeSource:
    """最新のフレームの時刻を返す関数の偽物。latest_t を書き換えて、新しいフレームが入ったことにする。"""

    def __init__(self, latest_t: float | None) -> None:
        self.latest_t = latest_t

    def read(self) -> float | None:
        return self.latest_t


@dataclass
class WaitInThread:
    """wait_for_settled_actions を別のスレッドで呼び、戻り値か例外を残す。"""

    source: RunSource
    frames: FrameTimeSource
    clock: RunClock
    stop: threading.Event = field(default_factory=threading.Event)
    results: list[MainActionsRun | None] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.results.append(
                wait_for_settled_actions(
                    self.source.read, self.frames.read, self.clock, self.stop, POLL_S
                )
            )
        except Exception as error:
            # テストのスレッドの raise_error で出し直すため、待つスレッドでは記録だけする
            logger.debug("wait_for_settled_actions failed", exc_info=True)
            self.errors.append(error)

    def is_waiting(self) -> bool:
        assert self.thread is not None
        return self.thread.is_alive()

    def reads_again(self, times: int = 3) -> None:
        """待つ処理が、今からさらに times 回 run を読むまで待つ (待ち続けていることの待ち合わせ)。"""
        target = self.source.reads + times
        deadline = time.monotonic() + WAIT_LIMIT_S
        while self.source.reads < target and self.is_waiting():
            assert time.monotonic() < deadline, "the run was not read again"
            time.sleep(POLL_S)
        self.raise_error()
        assert self.source.reads >= target, "the wait returned before reading again"

    def join(self) -> None:
        assert self.thread is not None
        self.thread.join(WAIT_LIMIT_S)
        self.raise_error()
        assert not self.thread.is_alive(), "the wait did not return"

    def raise_error(self) -> None:
        if self.errors:
            raise self.errors[0]


def start_wait(
    run: MainActionsRun | None, now: float, latest_frame_t: float | None
) -> tuple[WaitInThread, ManualMonotonic]:
    monotonic = ManualMonotonic(now)
    waiting = WaitInThread(
        RunSource(run), FrameTimeSource(latest_frame_t), RunClock(monotonic, origin=0.0)
    )
    waiting.start()
    return waiting, monotonic


def test_wait_returns_when_the_clock_passes_and_a_frame_after_it_arrives():
    waiting, monotonic = start_wait(RELEASED_AT_11, now=11.0, latest_frame_t=11.0)
    try:
        waiting.reads_again()
        monotonic.now = 11.299
        waiting.reads_again()
        assert waiting.is_waiting()
        # 時計は落ち着く時刻を過ぎたが、その後に撮ったフレームがまだ無い
        monotonic.now = 11.5
        waiting.reads_again()
        assert waiting.is_waiting()
        # ちょうど落ち着く時刻のフレームでは、まだ待つ
        waiting.frames.latest_t = 11.3
        waiting.reads_again()
        assert waiting.is_waiting()
        waiting.frames.latest_t = 11.4
        waiting.join()
        assert waiting.results == [RELEASED_AT_11]
    finally:
        waiting.stop.set()


def test_wait_for_actions_without_a_release_ignores_the_frames():
    # wait だけの列は、フレームが無くても、列が終わったら返る
    waiting, _ = start_wait(WAIT_ONLY, now=10.5, latest_frame_t=None)
    try:
        waiting.join()
        assert waiting.results == [WAIT_ONLY]
    finally:
        waiting.stop.set()


def test_wait_reads_the_run_again_until_the_actions_end():
    waiting, _ = start_wait(RUNNING, now=20.0, latest_frame_t=19.9)
    try:
        waiting.reads_again()
        assert waiting.is_waiting()
        ended = MainActionsRun(started_t=10.0, released_t=12.0, ended_t=12.0)
        waiting.source.run = ended
        waiting.join()
        assert waiting.results == [ended]
    finally:
        waiting.stop.set()


def test_wait_ends_with_none_when_stopped_while_the_actions_run():
    waiting, _ = start_wait(RUNNING, now=10.0, latest_frame_t=10.0)
    try:
        waiting.reads_again()
        waiting.stop.set()
        waiting.join()
        assert waiting.results == [None]
    finally:
        waiting.stop.set()


def test_wait_ends_with_none_when_stopped_while_no_frame_comes():
    # 列は終わって時計も過ぎたが、その後のフレームが来ない (キャプチャーが止まったときなど)
    waiting, _ = start_wait(RELEASED_AT_11, now=12.0, latest_frame_t=11.0)
    try:
        waiting.reads_again()
        assert waiting.is_waiting()
        waiting.stop.set()
        waiting.join()
        assert waiting.results == [None]
    finally:
        waiting.stop.set()


def test_wait_returns_none_at_once_without_a_run():
    waiting, _ = start_wait(None, now=10.0, latest_frame_t=None)
    try:
        waiting.join()
        assert waiting.results == [None]
    finally:
        waiting.stop.set()
