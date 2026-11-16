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
    defer_interrupts,
    ignore_interrupts,
    interrupts_as_exceptions,
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


# --- interrupts_as_exceptions ---


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_terminate_signal_raises_run_terminated(sig):
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with (
            interrupts_as_exceptions(),
            pytest.raises(RunTerminated) as error,
        ):
            os.kill(os.getpid(), sig)
            time.sleep(HANDLER_WAIT_S)
        assert error.value.signal_number == sig
        assert recorder.received == []


def test_interrupts_as_exceptions_restores_handlers():
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with interrupts_as_exceptions():
            pass
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert signal.getsignal(signal.SIGHUP) is recorder
        send_to_self(signal.SIGTERM)
        assert recorder.received == [signal.SIGTERM]


def test_interrupts_as_exceptions_restores_handlers_after_an_exception():
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        with pytest.raises(RunTerminated), interrupts_as_exceptions():
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(HANDLER_WAIT_S)
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert signal.getsignal(signal.SIGHUP) is recorder


def test_sigint_raises_keyboard_interrupt_after_ignoring_interrupts():
    ignored_when_raised: list[dict[signal.Signals, object]] = []
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with interrupts_as_exceptions():
            # 記録用のハンドラーが残っていれば KeyboardInterrupt にならず、pytest.raises が失敗する
            with pytest.raises(KeyboardInterrupt):
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(HANDLER_WAIT_S)
            ignored_when_raised.append(
                {sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS}
            )
            # 後片付けに入るまでの間の 2 回目の Ctrl-C は無視される
            # (KeyboardInterrupt がテストの外に出るとテストの実行全体が止まるため、ここで受けて失敗にする)
            try:
                send_to_self(signal.SIGINT)
            except KeyboardInterrupt:
                pytest.fail("the second SIGINT raised KeyboardInterrupt")
        assert ignored_when_raised == [dict.fromkeys(INTERRUPT_SIGNALS, signal.SIG_IGN)]
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder
        assert recorder.received == []


def test_interrupts_as_exceptions_restores_sigint():
    with recording_handlers((signal.SIGINT,)) as recorder:
        with interrupts_as_exceptions():
            assert signal.getsignal(signal.SIGINT) is not recorder
        assert signal.getsignal(signal.SIGINT) is recorder
        send_to_self(signal.SIGINT)
        assert recorder.received == [signal.SIGINT]


def test_interrupts_as_exceptions_leaves_an_ignored_sigint_ignored():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        with interrupts_as_exceptions():
            assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
            # 無視されたままなら KeyboardInterrupt にならない
            # (KeyboardInterrupt がテストの外に出るとテストの実行全体が止まるため、ここで受けて失敗にする)
            try:
                send_to_self(signal.SIGINT)
            except KeyboardInterrupt:
                pytest.fail("an ignored SIGINT raised KeyboardInterrupt")
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert recorder.received == []


def test_terminate_guard_leaves_an_ignored_signal_ignored():
    # nohup で起動した run のように、SIGHUP が無視されている状態で入る
    with recording_handlers(TERMINATE_SIGNALS) as recorder:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with interrupts_as_exceptions():
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
            assert signal.getsignal(signal.SIGTERM) is not recorder
            # 無視されたままなら RunTerminated にならない (なれば、ここで例外になってテストが失敗する)
            send_to_self(signal.SIGHUP)
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) is recorder
        assert recorder.received == []


def test_terminate_handler_ignores_interrupts_before_raising():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with interrupts_as_exceptions():
            with pytest.raises(RunTerminated):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(HANDLER_WAIT_S)
            # 後片付けに入るまでの間の 2 回目のシグナルで後片付けが飛ばされないように、無視になっている
            for sig in INTERRUPT_SIGNALS:
                assert signal.getsignal(sig) == signal.SIG_IGN
            send_to_self(signal.SIGTERM)
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder
        assert recorder.received == []


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
    with recording_handlers(INTERRUPT_SIGNALS), interrupts_as_exceptions():
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
        with interrupts_as_exceptions(), ignore_interrupts():
            seen.append({sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS})
        completed.append(True)

    thread = threading.Thread(target=use_guards)
    thread.start()
    thread.join(timeout=10)
    assert completed == [True]
    assert seen == [before]
    assert {sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS} == before


# --- defer_interrupts ---


def test_defer_interrupts_delivers_sigint_after_leaving():
    reached_end: list[bool] = []
    with recording_handlers((signal.SIGINT,)):
        signal.signal(signal.SIGINT, signal.default_int_handler)
        # 中で KeyboardInterrupt にならず最後まで進み、抜けた時点で KeyboardInterrupt になる
        with pytest.raises(KeyboardInterrupt), defer_interrupts():
            send_to_self(signal.SIGINT)
            reached_end.append(True)
    assert reached_end == [True]


def test_defer_interrupts_redelivers_sigterm_to_the_original_handler():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with defer_interrupts():
            send_to_self(signal.SIGTERM)
            assert recorder.received == []
        # 抜けた時点で送り直され、元のハンドラーが受ける
        deadline = time.monotonic() + HANDLER_WAIT_S
        while not recorder.received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert recorder.received == [signal.SIGTERM]


def test_defer_interrupts_redelivers_each_signal_once():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with defer_interrupts():
            send_to_self(signal.SIGHUP)
            send_to_self(signal.SIGTERM)
            send_to_self(signal.SIGHUP)
        deadline = time.monotonic() + HANDLER_WAIT_S
        while len(recorder.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        send_to_self(signal.SIGINT)  # 送り直しが終わったことを確かめるための目印
        assert recorder.received == [signal.SIGHUP, signal.SIGTERM, signal.SIGINT]


def test_defer_interrupts_restores_handlers():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with defer_interrupts():
            for sig in INTERRUPT_SIGNALS:
                assert signal.getsignal(sig) is not recorder
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder
        assert recorder.received == []


def test_defer_interrupts_restores_handlers_after_an_exception():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        with pytest.raises(ValueError), defer_interrupts():
            raise ValueError("launch failed")
        for sig in INTERRUPT_SIGNALS:
            assert signal.getsignal(sig) is recorder


def test_defer_interrupts_leaves_ignored_signals_alone():
    with recording_handlers(INTERRUPT_SIGNALS) as recorder:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with defer_interrupts():
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
            send_to_self(signal.SIGHUP)
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        send_to_self(signal.SIGTERM)  # 送り直しが無いことを確かめるための目印
        assert recorder.received == [signal.SIGTERM]


def test_defer_interrupts_does_nothing_outside_the_main_thread():
    before = {sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS}
    seen: list[dict[signal.Signals, object]] = []
    completed: list[bool] = []

    def use_defer() -> None:
        with defer_interrupts():
            seen.append({sig: signal.getsignal(sig) for sig in INTERRUPT_SIGNALS})
        completed.append(True)

    thread = threading.Thread(target=use_defer)
    thread.start()
    thread.join(timeout=10)
    assert completed == [True]
    assert seen == [before]
