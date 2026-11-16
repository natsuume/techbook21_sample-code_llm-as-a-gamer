"""run の間のシグナルの扱い (SIGTERM・SIGHUP を例外にすることと、後片付けの間の割り込みの無視)。

Python の既定では、SIGTERM と SIGHUP を受けると finally を通らずにプロセスが終わる。それでは
デーモンへの up ALL が送られず、起動した claude も残るため、run の間はこれらを例外 RunTerminated にして
finally の後片付けを通す。また、後片付け (claude を止める待ちと up ALL の送信) の途中で 2 回目の Ctrl-C などが
来ると up ALL の前で抜けてしまうため、後片付けの間は SIGINT・SIGTERM・SIGHUP を無視する。

signal.signal はメインスレッドでしか使えない。どちらのコンテキストマネージャーも、メインスレッド以外で
使われたときはハンドラーを変えず、何もしないコンテキストマネージャーとして動く (例外にしない)。
"""

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

# RunTerminated にするシグナル
TERMINATE_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGHUP)

# 後片付けの間に無視するシグナル
INTERRUPT_SIGNALS: tuple[signal.Signals, ...] = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
)

# signal.signal に渡せるハンドラーの型
SignalHandler = Callable[[int, FrameType | None], None] | signal.Handlers


class RunTerminated(Exception):
    """run の間に SIGTERM か SIGHUP を受けた。signal_number は受けたシグナルの番号。"""

    def __init__(self, signal_number: int) -> None:
        super().__init__(f"terminated by signal {signal_number}")
        self.signal_number = signal_number


def _raise_run_terminated(signal_number: int, frame: FrameType | None) -> None:
    raise RunTerminated(signal_number)


def _in_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


@contextmanager
def _replaced_handlers(
    signals: tuple[signal.Signals, ...], handler: SignalHandler
) -> Iterator[None]:
    """with の中にいる間だけ signals のハンドラーを handler にし、抜けるときに (例外のときも) 元に戻す。

    メインスレッド以外では何もしない。元のハンドラーが Python から設定されたものでない
    (signal.getsignal が None を返す) ときは、signal.SIG_DFL に戻す。
    """
    if not _in_main_thread():
        yield
        return
    previous = {sig: signal.getsignal(sig) for sig in signals}
    try:
        for sig in signals:
            signal.signal(sig, handler)
        yield
    finally:
        for sig, old_handler in previous.items():
            signal.signal(
                sig, old_handler if old_handler is not None else signal.SIG_DFL
            )


@contextmanager
def terminate_signals_as_exception() -> Iterator[None]:
    """with の中にいる間、SIGTERM と SIGHUP を受けたら RunTerminated を送出する。

    抜けるときに (例外のときも) 元のハンドラーに戻す。メインスレッド以外では何もしない。
    """
    with _replaced_handlers(TERMINATE_SIGNALS, _raise_run_terminated):
        yield


@contextmanager
def ignore_interrupts() -> Iterator[None]:
    """with の中にいる間、SIGINT・SIGTERM・SIGHUP を無視する (signal.SIG_IGN)。

    抜けるときに (例外のときも) 元のハンドラーに戻す。メインスレッド以外では何もしない。
    この中で子プロセスを起動すると、子も無視の設定を受け継ぐため、この中では起動しない。
    """
    with _replaced_handlers(INTERRUPT_SIGNALS, signal.SIG_IGN):
        yield
