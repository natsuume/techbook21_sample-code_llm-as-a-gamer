import queue
import subprocess
import threading

import pytest

from switch2_remote import controller_bridge
from switch2_remote.controller_bridge import Bridge, UnconfirmedInputError

REPORT_A = bytes.fromhex("04000f8080808000")


class FakeStdin:
    """ブリッジの標準入力の代わりに、書き込まれた行を記録する偽物。"""

    def __init__(self):
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class FakeProcess:
    """subprocess.Popen の代わり。exits_on_quit が False なら、wait は期限切れになる。"""

    def __init__(self, returncode: int | None = None, exits_on_quit: bool = True):
        self.stdin = FakeStdin()
        self.returncode = returncode
        self.exits_on_quit = exits_on_quit
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None and not (
            self.exits_on_quit and "quit\n" in self.stdin.written
        ):
            raise subprocess.TimeoutExpired("powershell.exe", timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def make_bridge(replies: list[str | None], process: FakeProcess | None = None):
    """Bridge.__init__ (PowerShell の起動) を通さずに、偽のプロセスと応答のキューを差し込んだ Bridge を返す。"""
    bridge = Bridge.__new__(Bridge)
    bridge.proc = process if process is not None else FakeProcess()
    bridge.lines = queue.Queue()
    for reply in replies:
        bridge.lines.put(reply)
    bridge.broken = False
    bridge.lock = threading.Lock()
    return bridge


@pytest.fixture(autouse=True)
def short_ack_timeout(monkeypatch):
    # ack のタイムアウトのテストで実時間を待たないようにする
    monkeypatch.setattr(controller_bridge, "BRIDGE_ACK_TIMEOUT", 0.01)


def test_set_report_writes_hex_and_returns_ack():
    bridge = make_bridge(["ack 04000f8080808000 1700000000000"])
    assert bridge.set_report(REPORT_A) == "ack 04000f8080808000 1700000000000"
    assert bridge.proc.stdin.written == ["set 04000f8080808000\n"]
    assert bridge.is_alive()


def test_set_report_raises_unconfirmed_and_quits_bridge_on_ack_timeout():
    bridge = make_bridge([])
    with pytest.raises(UnconfirmedInputError):
        bridge.set_report(REPORT_A)
    assert bridge.broken
    assert bridge.proc.stdin.written == ["set 04000f8080808000\n", "quit\n"]
    assert not bridge.proc.killed


@pytest.mark.parametrize(
    "reply", ["ack 00000f8080808000 1", "error unknown command: set", "ready COM3 1"]
)
def test_set_report_raises_unconfirmed_and_quits_bridge_on_unexpected_reply(reply):
    bridge = make_bridge([reply])
    with pytest.raises(UnconfirmedInputError):
        bridge.set_report(REPORT_A)
    assert bridge.broken
    assert bridge.proc.stdin.written[-1] == "quit\n"


def test_set_report_raises_runtime_error_and_quits_bridge_on_eof():
    bridge = make_bridge([None])
    with pytest.raises(RuntimeError) as excinfo:
        bridge.set_report(REPORT_A)
    assert type(excinfo.value) is RuntimeError
    assert bridge.broken
    assert bridge.proc.stdin.written[-1] == "quit\n"


def test_broken_bridge_is_killed_when_it_does_not_exit_after_quit():
    bridge = make_bridge([], FakeProcess(exits_on_quit=False))
    with pytest.raises(UnconfirmedInputError):
        bridge.set_report(REPORT_A)
    assert bridge.proc.stdin.written[-1] == "quit\n"
    assert bridge.proc.killed


def test_set_report_after_broken_raises_without_writing():
    bridge = make_bridge([None])
    with pytest.raises(RuntimeError):
        bridge.set_report(REPORT_A)
    written_before = list(bridge.proc.stdin.written)
    with pytest.raises(RuntimeError):
        bridge.set_report(REPORT_A)
    assert bridge.proc.stdin.written == written_before


def test_set_report_on_exited_bridge_raises_without_writing():
    bridge = make_bridge(["ack 04000f8080808000 1"], FakeProcess(returncode=1))
    with pytest.raises(RuntimeError):
        bridge.set_report(REPORT_A)
    assert bridge.proc.stdin.written == []


def test_set_report_rejects_report_of_wrong_length():
    bridge = make_bridge([])
    with pytest.raises(ValueError):
        bridge.set_report(b"\x00\x00\x0f")
    assert bridge.proc.stdin.written == []
