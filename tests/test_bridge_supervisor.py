import queue
import subprocess
from collections.abc import Callable

import pytest

from switch2_remote.bridge_supervisor import BridgeSupervisor
from switch2_remote.controller_bridge import BridgeStartError, UnconfirmedInputError

REPORT_A = bytes.fromhex("04000f8080808000")
RECONNECTING = (
    "controller bridge is reconnecting; wait until status shows bridge_state=ok"
)
FAILED = (
    "controller bridge could not be reconnected; "
    "turn Bluetooth off and on in Windows, then restart the daemon"
)


class FakeBridge:
    """controller_bridge.Bridge の代わりに、送られたレポートと close を記録する偽物。

    fail_with を設定すると set_report がその例外を送出する。dies_on_failure が True なら、
    送出と同時に is_alive() が False になる (ack を確認できず broken になったブリッジ)。
    """

    def __init__(
        self,
        fail_with: Exception | None = None,
        dies_on_failure: bool = True,
    ):
        self.reports: list[bytes] = []
        self.calls = 0
        self.alive = True
        self.closed = False
        self.fail_with = fail_with
        self.dies_on_failure = dies_on_failure

    def set_report(self, report: bytes) -> str:
        self.calls += 1
        if self.fail_with is not None:
            if self.dies_on_failure:
                self.alive = False
            raise self.fail_with
        self.reports.append(bytes(report))
        return f"ack {report.hex()} {self.calls}"

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed = True


class BridgeStarts:
    """start_bridge の代わりに、結果の並び (FakeBridge か送出する例外) を先頭から 1 つずつ返す偽物。

    while_starting を設定すると、各呼び出しで結果を返す前に呼ぶ (起動中に close() される場合を作るため)。
    """

    def __init__(self, results: list[FakeBridge | Exception], events: list[str]):
        self.results = list(results)
        self.events = events
        self.calls = 0
        self.while_starting: Callable[[], None] | None = None

    def __call__(self) -> FakeBridge:
        self.calls += 1
        self.events.append("start_bridge")
        if self.while_starting is not None:
            self.while_starting()
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeRadio:
    """reset_radio の代わりに、呼ばれた回数を記録する偽物。fail_with を設定するとその例外を送出する。"""

    def __init__(self, events: list[str], fail_with: Exception | None = None):
        self.events = events
        self.calls = 0
        self.fail_with = fail_with

    def __call__(self) -> None:
        self.calls += 1
        self.events.append("reset_radio")
        if self.fail_with is not None:
            raise self.fail_with


def start_error(n: int) -> BridgeStartError:
    return BridgeStartError(f"controller bridge failed: attempt {n}")


def run_now(recover: Callable[[], None]) -> None:
    """run_recovery の代わりに、復旧をその場で (同期的に) 実行する。"""
    recover()


class Harness:
    """BridgeSupervisor と、その偽の start_bridge・reset_radio・sleep・log・run_recovery の記録をまとめる。

    pending_recovery が True なら、run_recovery は復旧を呼ばずに pending に保留する
    (recovering の状態を作るため)。False なら run_now でその場で呼ぶ。
    """

    def __init__(
        self,
        results: list[FakeBridge | Exception],
        radio_fails_with: Exception | None = None,
        pending_recovery: bool = False,
        sleep: Callable[[float], None] | None = None,
    ):
        self.events: list[str] = []
        self.starts = BridgeStarts(results, self.events)
        self.radio = FakeRadio(self.events, radio_fails_with)
        self.sleeps: list[float] = []
        self.logs: list[str] = []
        self.pending: list[Callable[[], None]] = []
        self.recovered_states: list[str] = []
        self.supervisor = BridgeSupervisor(
            start_bridge=self.starts,
            reset_radio=self.radio,
            sleep=sleep if sleep is not None else self.sleeps.append,
            log=self.logs.append,
            start_watcher=False,
            run_recovery=self.pending.append if pending_recovery else run_now,
        )
        # on_recovered が呼ばれた時点の state を記録し、新しいブリッジに差し替える前に呼ばれることを確かめる
        self.supervisor.on_recovered = lambda: self.recovered_states.append(
            self.supervisor.state
        )


# --- 起動と通常の受け渡し ---


def test_constructor_starts_bridge_once():
    bridge = FakeBridge()
    harness = Harness([bridge])
    assert harness.starts.calls == 1
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 0
    assert harness.supervisor.last_recovery_error is None


def test_constructor_lets_bridge_start_error_out():
    with pytest.raises(BridgeStartError, match="controller bridge failed"):
        Harness([start_error(1)])


def test_set_report_passes_report_to_bridge_when_ok():
    bridge = FakeBridge()
    harness = Harness([bridge])
    assert harness.supervisor.set_report(REPORT_A) == "ack 04000f8080808000 1"
    assert bridge.reports == [REPORT_A]
    assert harness.supervisor.is_alive()
    assert harness.pending == []


def test_is_alive_is_false_when_bridge_is_dead():
    bridge = FakeBridge()
    harness = Harness([bridge], pending_recovery=True)
    bridge.alive = False
    assert not harness.supervisor.is_alive()


# --- 失敗の検知と復旧 ---


def test_failed_bridge_is_recovered_by_reopening():
    old = FakeBridge(fail_with=UnconfirmedInputError("no ack from bridge in time"))
    new = FakeBridge()
    harness = Harness([old, start_error(1), new])
    # 復旧はその場で終わり、その後に元の例外が外に出る
    with pytest.raises(UnconfirmedInputError, match="no ack"):
        harness.supervisor.set_report(REPORT_A)
    assert old.closed
    assert harness.starts.calls == 3
    assert harness.radio.calls == 0
    assert harness.sleeps == [5.0, 5.0]
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    assert harness.recovered_states == ["recovering"]
    assert harness.supervisor.set_report(REPORT_A) == "ack 04000f8080808000 1"
    assert new.reports == [REPORT_A]
    assert harness.supervisor.is_alive()


def test_radio_is_reset_after_three_failed_reopens():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness([old, start_error(1), start_error(2), start_error(3), new])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.events == [
        "start_bridge",  # コンストラクター
        "start_bridge",
        "start_bridge",
        "start_bridge",
        "reset_radio",
        "start_bridge",
    ]
    assert harness.sleeps == [5.0] * 4
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    assert harness.recovered_states == ["recovering"]
    harness.supervisor.set_report(REPORT_A)
    assert new.reports == [REPORT_A]


def test_recovery_fails_after_all_reopens_fail():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    errors: list[FakeBridge | Exception] = [start_error(n) for n in range(1, 16)]
    harness = Harness([old, *errors])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    # 開き直し 3 回 → 無線のオフ・オン → 開き直し 12 回
    assert harness.starts.calls == 16
    assert harness.radio.calls == 1
    assert harness.sleeps == [5.0] * 15
    assert harness.supervisor.state == "failed"
    assert harness.supervisor.restarts == 0
    assert (
        harness.supervisor.last_recovery_error == "controller bridge failed: attempt 15"
    )
    assert harness.recovered_states == []
    assert not harness.supervisor.is_alive()
    assert any("controller bridge failed: attempt 15" in line for line in harness.logs)


def test_set_report_after_failed_raises_without_sending():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness([old, *[start_error(n) for n in range(1, 16)]])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    calls_before = old.calls
    with pytest.raises(RuntimeError) as excinfo:
        harness.supervisor.set_report(REPORT_A)
    assert str(excinfo.value) == FAILED
    assert old.calls == calls_before


def test_failed_state_does_not_recover_automatically():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness([old, *[start_error(n) for n in range(1, 16)]])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    harness.supervisor.check()
    assert harness.starts.calls == 16
    assert harness.supervisor.state == "failed"


def test_reopening_continues_when_radio_reset_fails():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness(
        [old, start_error(1), start_error(2), start_error(3), new],
        radio_fails_with=RuntimeError(
            "bluetooth radio reset failed: no bluetooth radio"
        ),
    )
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.radio.calls == 1
    assert harness.starts.calls == 5
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    # 復旧に成功しても、無線のオフ・オンの失敗の理由は残す
    assert (
        harness.supervisor.last_recovery_error
        == "bluetooth radio reset failed: no bluetooth radio"
    )
    assert any("no bluetooth radio" in line for line in harness.logs)


def test_os_error_from_start_bridge_is_treated_as_failure():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness([old, OSError("wslpath failed"), new])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.starts.calls == 3
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1


def test_recovery_is_logged():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness([old, FakeBridge()])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    # 開始と成功の少なくとも 2 行を記録する
    assert len(harness.logs) >= 2


def test_start_bridge_failures_of_any_exception_type_are_retried():
    # ready の待ちの期限切れ (queue.Empty) や wslpath の失敗 (CalledProcessError) でも、次の試行に進む
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness(
        [old, queue.Empty(), subprocess.CalledProcessError(1, ["wslpath"]), new]
    )
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.starts.calls == 4
    assert harness.radio.calls == 0
    assert harness.sleeps == [5.0] * 3
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    harness.supervisor.set_report(REPORT_A)
    assert new.reports == [REPORT_A]


def test_all_reopens_failing_with_other_exception_types_end_in_failed():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    errors: list[FakeBridge | Exception] = [queue.Empty() for _ in range(15)]
    harness = Harness([old, *errors])
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.starts.calls == 16
    assert harness.supervisor.state == "failed"
    # メッセージが空の例外は、describe_error がクラス名で表す
    assert harness.supervisor.last_recovery_error == "Empty"


def test_reopening_continues_when_radio_reset_raises_other_exception():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness(
        [old, start_error(1), start_error(2), start_error(3), new],
        radio_fails_with=ValueError("unexpected radio state"),
    )
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.radio.calls == 1
    assert harness.starts.calls == 5
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    assert harness.supervisor.last_recovery_error == "unexpected radio state"


def test_on_recovered_exception_makes_state_failed_without_raising():
    old = FakeBridge()
    new = FakeBridge()
    harness = Harness([old, new])

    def failing_on_recovered() -> None:
        raise ValueError("reset_state failed")

    harness.supervisor.on_recovered = failing_on_recovered
    old.alive = False
    # 復旧の中の例外は外に出さない (check はそのまま戻る)
    harness.supervisor.check()
    assert harness.supervisor.state == "failed"
    assert harness.supervisor.restarts == 0
    assert harness.supervisor.last_recovery_error == "reset_state failed"
    assert any("reset_state failed" in line for line in harness.logs)
    # 差し替えなかった新しいブリッジは閉じる
    assert new.closed
    assert harness.supervisor.bridge is old
    with pytest.raises(RuntimeError) as excinfo:
        harness.supervisor.set_report(REPORT_A)
    assert str(excinfo.value) == FAILED
    assert new.reports == []


# --- recovering の間 ---


def test_set_report_while_recovering_raises_without_sending():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    new = FakeBridge()
    harness = Harness([old, new], pending_recovery=True)
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    assert len(harness.pending) == 1
    assert harness.supervisor.state == "recovering"
    assert not harness.supervisor.is_alive()
    calls_before = old.calls
    with pytest.raises(RuntimeError) as excinfo:
        harness.supervisor.set_report(REPORT_A)
    assert str(excinfo.value) == RECONNECTING
    assert old.calls == calls_before
    assert new.reports == []
    # 保留していた復旧を実行すると ok に戻る
    harness.pending[0]()
    assert harness.supervisor.state == "ok"
    harness.supervisor.set_report(REPORT_A)
    assert new.reports == [REPORT_A]


def test_recovery_runs_only_once_at_a_time():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness([old, FakeBridge()], pending_recovery=True)
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    harness.supervisor.check()
    with pytest.raises(RuntimeError, match="reconnecting"):
        harness.supervisor.set_report(REPORT_A)
    harness.supervisor.check()
    assert len(harness.pending) == 1


def test_exception_from_live_bridge_does_not_start_recovery():
    bridge = FakeBridge(fail_with=RuntimeError("temporary"), dies_on_failure=False)
    harness = Harness([bridge], pending_recovery=True)
    with pytest.raises(RuntimeError, match="temporary"):
        harness.supervisor.set_report(REPORT_A)
    assert harness.pending == []
    assert harness.supervisor.state == "ok"
    assert harness.starts.calls == 1


# --- check (監視スレッドが呼ぶ) ---


def test_check_starts_recovery_when_bridge_is_dead():
    old = FakeBridge()
    harness = Harness([old, FakeBridge()], pending_recovery=True)
    old.alive = False
    harness.supervisor.check()
    assert len(harness.pending) == 1
    assert harness.supervisor.state == "recovering"


def test_check_does_nothing_when_bridge_is_alive():
    harness = Harness([FakeBridge()], pending_recovery=True)
    harness.supervisor.check()
    assert harness.pending == []
    assert harness.supervisor.state == "ok"


def test_check_recovers_dead_bridge_synchronously_with_run_now():
    old = FakeBridge()
    new = FakeBridge()
    harness = Harness([old, new])
    old.alive = False
    harness.supervisor.check()
    assert old.closed
    assert harness.supervisor.state == "ok"
    assert harness.supervisor.restarts == 1
    harness.supervisor.set_report(REPORT_A)
    assert new.reports == [REPORT_A]


# --- close ---


def test_close_closes_current_bridge():
    bridge = FakeBridge()
    harness = Harness([bridge])
    harness.supervisor.close()
    assert bridge.closed


def test_close_closes_recovered_bridge():
    old = FakeBridge()
    new = FakeBridge()
    harness = Harness([old, new])
    old.alive = False
    harness.supervisor.check()
    harness.supervisor.close()
    assert new.closed


def test_recovery_stops_when_closed_before_it_runs():
    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness([old, FakeBridge()], pending_recovery=True)
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    harness.supervisor.close()
    harness.pending[0]()
    assert harness.sleeps == []
    assert harness.starts.calls == 1


def test_recovery_stops_when_closed_during_sleep():
    sleeps: list[float] = []
    holder: dict[str, BridgeSupervisor] = {}

    def sleep_then_close(seconds: float) -> None:
        sleeps.append(seconds)
        holder["supervisor"].close()

    old = FakeBridge(fail_with=RuntimeError("bridge exited"))
    harness = Harness(
        [old, FakeBridge()], pending_recovery=True, sleep=sleep_then_close
    )
    holder["supervisor"] = harness.supervisor
    with pytest.raises(RuntimeError, match="bridge exited"):
        harness.supervisor.set_report(REPORT_A)
    harness.pending[0]()
    assert sleeps == [5.0]
    assert harness.starts.calls == 1


def test_bridge_started_while_closing_is_closed_and_not_adopted():
    old = FakeBridge()
    new = FakeBridge()
    harness = Harness([old, new])

    def close_during_reopen() -> None:
        if harness.starts.calls == 2:
            harness.supervisor.close()

    harness.starts.while_starting = close_during_reopen
    old.alive = False
    harness.supervisor.check()
    assert new.closed
    assert harness.recovered_states == []
    assert harness.supervisor.bridge is old
    assert harness.supervisor.restarts == 0


def test_bridge_is_not_adopted_when_closed_just_before_swap():
    # 起動に成功した後、差し替えの前 (on_recovered の間) に close() された
    old = FakeBridge()
    new = FakeBridge()
    harness = Harness([old, new])
    harness.supervisor.on_recovered = harness.supervisor.close
    old.alive = False
    harness.supervisor.check()
    assert new.closed
    assert harness.supervisor.bridge is old
    assert harness.supervisor.restarts == 0
