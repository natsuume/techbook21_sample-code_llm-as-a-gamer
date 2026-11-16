"""signal_guard のテスト。

pytest はメインスレッドで動くため、本物のシグナルを自分のプロセスに送って確かめる。
ガードが正しく働かなかったときに SIGTERM でテストのプロセスが終わったり、SIGINT でテストの実行全体が
止まったりしないように、先に受けたシグナルを記録するだけのハンドラーを入れてからガードに入る。
"""

import os
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

import pytest

from switch2_harness.signal_guard import (
    INTERRUPT_SIGNALS,
    TERMINATE_SIGNALS,
    RunTerminated,
    ignore_interrupts,
    terminate_signals_as_exception,
)

# os.kill で送ったシグナルのハンドラーが動くまで待つ秒数の上限 (ハンドラーが例外を送出すると待ちは途中で終わる)
HANDLER_WAIT_S = 2.0


class SignalRecorder:
    """受けたシグナルの番号を記録するハンドラー。"""

    def __init__(self) -> None:
        self.received: list[int] = []

    def __call__(self, signal_number: int, frame: FrameType | None) -> None:
        self.received.append(signal_number)


@contextmanager
def recording_handlers(
    signals: tuple[signal.Signals, ...],
) -> Iterator[SignalRecorder]:
    """with の中にいる間、signals のハンドラーを記録用のものにし、抜けるときに元に戻す。"""
    recorder = SignalRecorder()
    previous = {sig: signal.getsignal(sig) for sig in signals}
    for sig in signals:
        signal.signal(sig, recorder)
    try:
        yield recorder
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)


def send_to_self(sig: signal.Signals) -> None:
    """自分のプロセスに sig を送り、ハンドラーが動く時間をとる。"""
    os.kill(os.getpid(), sig)
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        time.sleep(0.01)


def test_constants():
    assert TERMINATE_SIGNALS == (signal.SIGTERM, signal.SIGHUP)
    assert INTERRUPT_SIGNALS == (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


# --- terminate_signals_as_exception ---


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_terminate_signal_raises_run_terminated(sig):
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with (
            terminate_signals_as_exception(),
            pytest.raises(RunTerminated) as error,
        ):
            os.kill(os.getpid(), sig)
            time.sleep(HANDLER_WAIT_S)
        assert error.value.signal_number == sig
        assert recorder.received == []


def test_terminate_signals_as_exception_restores_handlers():
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with terminate_signals_as_exception():
            pass
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert signal.getsignal(signal.SIGHUP) is recorder
        send_to_self(signal.SIGTERM)
        assert recorder.received == [signal.SIGTERM]


def test_terminate_signals_as_exception_restores_handlers_after_an_exception():
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with pytest.raises(RunTerminated), terminate_signals_as_exception():
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(HANDLER_WAIT_S)
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert signal.getsignal(signal.SIGHUP) is recorder


def test_terminate_signals_as_exception_leaves_sigint_alone():
    before = signal.getsignal(signal.SIGINT)
    with terminate_signals_as_exception():
        assert signal.getsignal(signal.SIGINT) is before


# --- ignore_interrupts ---


def test_ignore_interrupts_ignores_sigint_sigterm_and_sighup():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with ignore_interrupts():
            for sig in INTERRUPT_SIGNALS:
                assert signal.getsignal(sig) == signal.SIG_IGN
            send_to_self(signal.SIGINT)
            send_to_self(signal.SIGTERM)
            send_to_self(signal.SIGHUP)
        assert recorder.received == []


def test_ignore_interrupts_does_not_raise_keyboard_interrupt():
    with recording_handlers((signal.SIGINT,)):
        signal.signal(signal.SIGINT, signal.default_int_handler)
        # KeyboardInterrupt がテストの外に出るとテストの実行全体が止まるため、ここで受けて失敗にする
        try:
            with ignore_interrupts():
                send_to_self(signal.SIGINT)
        except KeyboardInterrupt:
            pytest.fail("SIGINT raised KeyboardInterrupt inside ignore_interrupts")


def test_ignore_interrupts_restores_handlers():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with ignore_interrupts():
            pass
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder
        send_to_self(signal.SIGINT)
        assert recorder.received == [signal.SIGINT]


def test_ignore_interrupts_restores_handlers_after_an_exception():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with pytest.raises(ValueError), ignore_interrupts():
            raise ValueError("cleanup failed")
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder


def test_ignore_interrupts_inside_terminate_guard_restores_the_guard():
    with recording_handlers(INTERRUPT_SIGNALS), terminate_signals_as_exception():
        guard_handler = signal.getsignal(signal.SIGTERM)
        with ignore_interrupts():
            assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) is guard_handler


# --- メインスレッド以外 ---


def test_guards_do_nothing_outside_the_main_thread():
    before = {sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS}
    seen: list[dict[signal.Signals, object]] = []
    completed: list[bool] = []

    def use_guards() -> None:
        # signal.signal を呼ぶと ValueError になるスレッドで、例外にならずに最後まで進むこと
        with terminate_signals_as_exception(), ignore_interrupts():
            seen.append({sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS})
        completed.append(True)

    thread = threading.Thread(target=use_guards)
    thread.start()
    thread.join(timeout=10)
    assert completed == [True]
    assert seen == [before]
    assert {sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS} == before
