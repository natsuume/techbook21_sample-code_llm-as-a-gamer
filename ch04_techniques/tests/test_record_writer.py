import logging
import threading
import time

import pytest
from switch2_remote.error_message import describe_error

from switch2_agent.record_writer import BoundedRecordWriter

WAIT_TIMEOUT = 2.0  # テストがスレッドの進みを待つ上限 (秒)
SHORT_WAIT = 0.05  # 書き出しスレッドが (誤って) 進むのを待つ短い時間 (秒)


class Writes:
    """write_item の代わり。gate が開くまで止まってから、item を items に入れる。

    calls      write_item が呼ばれた item (止まる前に入れる)
    items      書き終えた item
    started    最初の write_item が呼ばれたら立つ
    finished   最初の write_item が戻る直前に立つ
    gate       開いている (set) 間は止まらない。open_gate=False なら閉じた状態で作る
    failures   item → write_item が出す例外
    """

    def __init__(self, open_gate: bool = True, failures: dict | None = None):
        self.calls: list[int] = []
        self.items: list[int] = []
        self.started = threading.Event()
        self.finished = threading.Event()
        self.gate = threading.Event()
        if open_gate:
            self.gate.set()
        self.failures = failures or {}

    def __call__(self, item: int) -> None:
        self.calls.append(item)
        self.started.set()
        self.gate.wait(WAIT_TIMEOUT)
        try:
            if item in self.failures:
                raise self.failures[item]
            self.items.append(item)
        finally:
            self.finished.set()


class ErrorRecorder:
    """on_error の代わり。受け取った (kind, error) を calls に入れる。fail_with があれば、その例外を出す。"""

    def __init__(self, fail_with: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.called = threading.Event()
        self.fail_with = fail_with

    def __call__(self, kind: str, error: str) -> None:
        self.calls.append((kind, error))
        self.called.set()
        if self.fail_with is not None:
            raise self.fail_with


class FinishRecorder:
    """on_finish の代わり。呼ばれた回数と、呼んだスレッドを数える。fail_with があれば、その例外を出す。

    order があれば、呼ばれたときに "on_finish" を足す (on_error との順を確かめるため)
    """

    def __init__(
        self, fail_with: Exception | None = None, order: list[str] | None = None
    ):
        self.calls = 0
        self.threads: list[threading.Thread] = []
        self.called = threading.Event()
        self.fail_with = fail_with
        self.order = order

    def __call__(self) -> None:
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.order is not None:
            self.order.append("on_finish")
        self.called.set()
        if self.fail_with is not None:
            raise self.fail_with


class OrderedErrorRecorder(ErrorRecorder):
    """呼ばれたときに order に "on_error" を足す ErrorRecorder。"""

    def __init__(self, order: list[str]):
        super().__init__()
        self.order = order

    def __call__(self, kind: str, error: str) -> None:
        self.order.append("on_error")
        super().__call__(kind, error)


@pytest.fixture
def thread_errors(monkeypatch) -> list[threading.ExceptHookArgs]:
    """スレッドの外に出た例外 (threading.excepthook に渡されたもの) を集める。"""
    errors: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    return errors


def make_writer(
    writes: Writes,
    errors: ErrorRecorder | None = None,
    capacity: int = 10,
    kind: str = "frames",
    on_finish: FinishRecorder | None = None,
) -> BoundedRecordWriter[int]:
    """BoundedRecordWriter を作る。on_finish が None なら、on_finish の引数を渡さない (既定値を使う)。"""
    on_error = errors if errors is not None else ErrorRecorder()
    if on_finish is None:
        return BoundedRecordWriter(kind, capacity, writes, on_error)
    return BoundedRecordWriter(kind, capacity, writes, on_error, on_finish=on_finish)


def finish(writer: BoundedRecordWriter[int]) -> bool:
    writer.stop_accepting()
    return writer.wait_until_written(WAIT_TIMEOUT)


def wait_for(event: threading.Event, what: str) -> None:
    if not event.wait(WAIT_TIMEOUT):
        pytest.fail(f"{what} did not happen within {WAIT_TIMEOUT} seconds")


# --- 書き出し ---


def test_writes_items_in_put_order():
    writes = Writes()
    writer = make_writer(writes)
    assert [writer.put(item) for item in range(1, 6)] == [True] * 5
    assert finish(writer) is True
    assert writes.items == [1, 2, 3, 4, 5]
    assert (writer.written, writer.dropped) == (5, 0)


def test_wait_until_written_writes_all_queued_items():
    writes = Writes()
    writer = make_writer(writes, capacity=1000)
    for item in range(200):
        writer.put(item)
    assert finish(writer) is True
    assert writes.items == list(range(200))
    assert (writer.written, writer.dropped) == (200, 0)


def test_writer_thread_is_daemon_thread():
    before = set(threading.enumerate())
    writer = make_writer(Writes())
    started = [thread for thread in threading.enumerate() if thread not in before]
    assert len(started) == 1
    assert started[0].daemon is True
    assert finish(writer) is True


def test_put_does_not_wait_for_item_being_written():
    writes = Writes(open_gate=False)
    writer = make_writer(writes)
    writer.put(1)
    wait_for(writes.started, "first write")
    # 書き出しスレッドが write_item の中で止まっていても、put はすぐに戻る
    started_at = time.monotonic()
    assert writer.put(2) is True
    assert time.monotonic() - started_at < WAIT_TIMEOUT / 4
    writes.gate.set()
    assert finish(writer) is True
    assert writes.items == [1, 2]


# --- 満杯 ---


def test_put_drops_new_item_when_queue_is_full():
    writes = Writes(open_gate=False)
    writer = make_writer(writes, capacity=2)
    assert writer.put(1) is True
    wait_for(writes.started, "first write")
    # 書いている途中の 1 件はキューの件数に含めない
    assert writer.put(2) is True
    assert writer.put(3) is True
    started_at = time.monotonic()
    assert writer.put(4) is False
    assert writer.put(5) is False
    # 満杯でも待たずに戻る
    assert time.monotonic() - started_at < WAIT_TIMEOUT / 4
    assert writer.dropped == 2
    writes.gate.set()
    assert finish(writer) is True
    # キューに入っていた分は古い順に書く
    assert writes.items == [1, 2, 3]
    assert (writer.written, writer.dropped) == (3, 2)


def test_put_accepts_again_after_queue_has_room():
    writes = Writes(open_gate=False)
    writer = make_writer(writes, capacity=1)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    assert writer.put(3) is False
    writes.gate.set()
    deadline = time.monotonic() + WAIT_TIMEOUT
    while writes.items != [1, 2]:
        if time.monotonic() >= deadline:
            pytest.fail(f"queued items were not written within {WAIT_TIMEOUT} seconds")
        time.sleep(0.01)
    assert writer.put(4) is True
    assert finish(writer) is True
    assert writes.items == [1, 2, 4]
    assert (writer.written, writer.dropped) == (3, 1)


# --- 受け付けの停止 ---


def test_put_after_stop_accepting_is_dropped():
    writes = Writes()
    writer = make_writer(writes)
    writer.put(1)
    writer.stop_accepting()
    assert writer.put(2) is False
    assert writer.wait_until_written(WAIT_TIMEOUT) is True
    assert writes.items == [1]
    assert (writer.written, writer.dropped) == (1, 1)


def test_put_after_wait_until_written_returns_is_not_counted():
    writes = Writes()
    writer = make_writer(writes)
    writer.put(1)
    assert finish(writer) is True
    # 記録を閉じた後に届いた記録 (片付けの途中のフレームなど) は、書かず、件数も変えない
    assert writer.put(2) is False
    assert writes.items == [1]
    assert (writer.written, writer.dropped) == (1, 0)


def test_put_after_wait_until_written_times_out_is_not_counted():
    writes = Writes(open_gate=False)
    writer = make_writer(writes)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.stop_accepting()
    assert writer.wait_until_written(SHORT_WAIT) is False
    assert (writer.written, writer.dropped) == (0, 1)
    assert writer.put(2) is False
    assert (writer.written, writer.dropped) == (0, 1)
    writes.gate.set()


def test_stop_accepting_keeps_writing_queued_items():
    writes = Writes(open_gate=False)
    writer = make_writer(writes)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writer.put(3)
    writer.stop_accepting()
    writes.gate.set()
    assert writer.wait_until_written(WAIT_TIMEOUT) is True
    assert writes.items == [1, 2, 3]
    assert (writer.written, writer.dropped) == (3, 0)


# --- 時間切れ ---


def test_wait_until_written_gives_up_after_timeout():
    writes = Writes(open_gate=False)
    writer = make_writer(writes)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writer.put(3)
    writer.stop_accepting()
    started_at = time.monotonic()
    assert writer.wait_until_written(SHORT_WAIT) is False
    assert time.monotonic() - started_at < WAIT_TIMEOUT / 2
    # 書いている途中の 1 件とキューに残っていた 2 件を、捨てた件数に足す
    assert (writer.written, writer.dropped) == (0, 3)
    # 止まっていた 1 件が戻っても、件数は変わらず、残りは書かない
    writes.gate.set()
    wait_for(writes.finished, "first write to return")
    time.sleep(SHORT_WAIT * 2)
    assert writes.calls == [1]
    assert (writer.written, writer.dropped) == (0, 3)


# --- 書き込みの失敗 ---


@pytest.mark.parametrize(
    "error",
    [
        OSError("No space left on device"),
        ValueError("I/O operation on closed file."),
        RuntimeError("unexpected failure"),
    ],
)
def test_write_failure_stops_recording_and_reports_once(error):
    writes = Writes(open_gate=False, failures={2: error})
    errors = ErrorRecorder()
    writer = make_writer(writes, errors, kind="pad")
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writer.put(3)
    writer.put(4)
    writes.gate.set()
    wait_for(errors.called, "on_error")
    # 止めた後に届いた記録は入れずに捨てる
    assert writer.put(5) is False
    writer.stop_accepting()
    writer.wait_until_written(WAIT_TIMEOUT)
    assert errors.calls == [("pad", describe_error(error))]
    # 失敗した 1 件の後は write_item を呼ばない
    assert writes.calls == [1, 2]
    assert writes.items == [1]
    # 失敗した 1 件・キューに残っていた 2 件・止めた後の 1 件を捨てた件数に数え、put の回数と合う
    assert (writer.written, writer.dropped) == (1, 4)


def test_on_error_failure_does_not_escape_writer_thread(thread_errors, caplog):
    caplog.set_level(logging.WARNING)
    writes = Writes(open_gate=False, failures={1: OSError("disk full")})
    errors = ErrorRecorder(fail_with=RuntimeError("log failed"))
    writer = make_writer(writes, errors)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writes.gate.set()
    wait_for(errors.called, "on_error")
    assert writer.put(3) is False
    writer.stop_accepting()
    writer.wait_until_written(WAIT_TIMEOUT)
    assert errors.calls == [("frames", "disk full")]
    assert (writer.written, writer.dropped) == (0, 3)
    assert thread_errors == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_unexpected_write_failure_logs_traceback(caplog):
    caplog.set_level(logging.WARNING)
    writes = Writes(failures={1: RuntimeError("unexpected failure")})
    errors = ErrorRecorder()
    writer = make_writer(writes, errors)
    writer.put(1)
    wait_for(errors.called, "on_error")
    writer.stop_accepting()
    writer.wait_until_written(WAIT_TIMEOUT)
    # 想定していない例外は、原因を追えるようトレースバックも書く
    assert any(record.exc_info is not None for record in caplog.records)


def test_write_failure_does_not_escape_writer_thread(thread_errors):
    writes = Writes(failures={1: RuntimeError("unexpected failure")})
    errors = ErrorRecorder()
    writer = make_writer(writes, errors)
    writer.put(1)
    wait_for(errors.called, "on_error")
    writer.stop_accepting()
    writer.wait_until_written(WAIT_TIMEOUT)
    assert thread_errors == []
    assert (writer.written, writer.dropped) == (0, 1)


# --- 書き出しスレッドの終わり (on_finish) ---


def test_on_finish_is_optional():
    # on_finish を渡さなければ (既定値 None)、呼ぶものが無いまま書き出しスレッドが終わる
    writes = Writes()
    writer = BoundedRecordWriter("frames", 10, writes, ErrorRecorder())
    writer.put(1)
    assert finish(writer) is True
    assert writes.items == [1]
    assert (writer.written, writer.dropped) == (1, 0)


def test_on_finish_is_called_once_after_writing_all_items():
    writes = Writes()
    finished = FinishRecorder()
    writer = make_writer(writes, on_finish=finished)
    for item in range(1, 4):
        writer.put(item)
    assert finish(writer) is True
    # wait_until_written が True を返したときには、on_finish も戻っている
    assert finished.calls == 1
    assert writes.items == [1, 2, 3]
    # 書き出しスレッドから呼ぶ
    assert finished.threads[0] is not threading.current_thread()
    time.sleep(SHORT_WAIT)
    assert finished.calls == 1


def test_on_finish_is_not_called_before_stop_accepting():
    writes = Writes()
    finished = FinishRecorder()
    writer = make_writer(writes, on_finish=finished)
    writer.put(1)
    deadline = time.monotonic() + WAIT_TIMEOUT
    while writes.items != [1]:
        if time.monotonic() >= deadline:
            pytest.fail(f"item was not written within {WAIT_TIMEOUT} seconds")
        time.sleep(0.01)
    # キューが空になっても、受け付けている間は書き出しスレッドを終えない
    time.sleep(SHORT_WAIT)
    assert finished.calls == 0
    assert finish(writer) is True
    assert finished.calls == 1


def test_on_finish_is_called_after_on_error_when_writing_fails():
    order: list[str] = []
    writes = Writes(open_gate=False, failures={1: OSError("disk full")})
    errors = OrderedErrorRecorder(order)
    finished = FinishRecorder(order=order)
    writer = make_writer(writes, errors, on_finish=finished)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writes.gate.set()
    # 失敗で記録を止めたら、stop_accepting を待たずに書き出しスレッドを終える
    wait_for(finished.called, "on_finish")
    assert order == ["on_error", "on_finish"]
    assert writer.put(3) is False
    writer.stop_accepting()
    assert writer.wait_until_written(WAIT_TIMEOUT) is True
    assert finished.calls == 1
    assert (writer.written, writer.dropped) == (0, 3)


def test_on_finish_is_called_after_abandoned_write_returns():
    writes = Writes(open_gate=False)
    finished = FinishRecorder()
    writer = make_writer(writes, on_finish=finished)
    writer.put(1)
    wait_for(writes.started, "first write")
    writer.put(2)
    writer.stop_accepting()
    assert writer.wait_until_written(SHORT_WAIT) is False
    assert (writer.written, writer.dropped) == (0, 2)
    # 書いている途中の 1 件が戻るまでは、on_finish を呼ばない
    assert finished.calls == 0
    writes.gate.set()
    wait_for(finished.called, "on_finish")
    time.sleep(SHORT_WAIT)
    assert finished.calls == 1
    # 手放した後に on_finish を呼んでも、件数は変わらず、残りは書かない
    assert (writer.written, writer.dropped) == (0, 2)
    assert writes.calls == [1]


def test_on_finish_failure_does_not_escape_writer_thread(thread_errors, caplog):
    caplog.set_level(logging.WARNING)
    writes = Writes()
    finished = FinishRecorder(fail_with=OSError("close failed"))
    writer = make_writer(writes, on_finish=finished)
    writer.put(1)
    assert finish(writer) is True
    assert finished.calls == 1
    assert (writer.written, writer.dropped) == (1, 0)
    assert thread_errors == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)
