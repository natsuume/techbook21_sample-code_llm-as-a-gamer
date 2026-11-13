import io
import json
import threading
from pathlib import Path

import pytest

from switch2_agent.advisors.stream_json_log import (
    STREAM_FILE_NAME,
    ProcessOutputReader,
    StreamJsonLog,
    build_stream_line_record,
    is_result_message,
)

WAIT_TIMEOUT = 2.0  # テストがスレッドの進みを待つ上限 (秒)
RESULT_LINE = {"type": "result", "subtype": "success", "result": "戦略を更新した"}


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class ErrorRecorder:
    def __init__(self):
        self.errors: list[str] = []

    def __call__(self, error: str) -> None:
        self.errors.append(error)


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_log(
    tmp_path: Path, **kwargs
) -> tuple[StreamJsonLog, FakeClock, ErrorRecorder]:
    clock = FakeClock(10.0)
    errors = ErrorRecorder()
    log = StreamJsonLog(tmp_path / STREAM_FILE_NAME, clock, errors, **kwargs)
    return log, clock, errors


class FailingFile:
    """書き込み用のファイルの代わり。ok_writes 回だけ書け、その次から write が OSError を出す。"""

    def __init__(self, ok_writes: int):
        self.ok_writes = ok_writes
        self.lines: list[str] = []
        self.closed = False

    def write(self, text: str) -> int:
        if len(self.lines) >= self.ok_writes:
            raise OSError("No space left on device")
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class BlockingLines:
    """標準出力の代わり。lines を返した後、release が set されるまで止まる (子孫のプロセスがパイプを持ち続ける場合)。"""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.release = threading.Event()

    def __iter__(self):
        yield from self.lines
        self.release.wait(WAIT_TIMEOUT)


class BlockingText:
    """標準エラー出力の代わり。release が set されるまで read が止まる。"""

    def __init__(self, text: str):
        self.text = text
        self.release = threading.Event()

    def read(self) -> str:
        self.release.wait(WAIT_TIMEOUT)
        return self.text


class BrokenLines:
    """標準出力の代わり。lines を返した後、パイプが閉じられたときのように ValueError を出す。"""

    def __init__(self, lines: list[str]):
        self.lines = lines

    def __iter__(self):
        yield from self.lines
        raise ValueError("I/O operation on closed file.")


class BrokenText:
    def read(self) -> str:
        raise OSError("read failed")


# --- build_stream_line_record ---


def test_line_record_reads_json_object_and_drops_newline():
    line = json.dumps(RESULT_LINE, ensure_ascii=False) + "\n"
    assert build_stream_line_record(line, 12.5) == {"mono": 12.5, "data": RESULT_LINE}


def test_line_record_keeps_line_that_is_not_json_as_raw():
    assert build_stream_line_record("not json\n", 1.0) == {
        "mono": 1.0,
        "raw": "not json",
    }


def test_line_record_accepts_last_line_without_newline():
    assert build_stream_line_record('{"type": "assis', 2.0) == {
        "mono": 2.0,
        "raw": '{"type": "assis',
    }
    assert build_stream_line_record('{"type": "system"}', 3.0) == {
        "mono": 3.0,
        "data": {"type": "system"},
    }


def test_line_record_drops_carriage_return_and_newline():
    assert build_stream_line_record("text\r\n", 1.0) == {"mono": 1.0, "raw": "text"}


def test_line_record_puts_json_that_is_not_object_in_data():
    assert build_stream_line_record("[1, 2]\n", 1.0) == {"mono": 1.0, "data": [1, 2]}
    assert build_stream_line_record("3\n", 1.0) == {"mono": 1.0, "data": 3}


def test_line_record_keeps_empty_line_as_raw():
    assert build_stream_line_record("\n", 1.0) == {"mono": 1.0, "raw": ""}


# --- is_result_message ---


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"type": "result"}, True),
        (RESULT_LINE, True),
        ({"type": "assistant"}, False),
        ({"subtype": "success"}, False),
        (["result"], False),
        ("result", False),
        (None, False),
    ],
)
def test_is_result_message(data, expected):
    assert is_result_message(data) is expected


# --- StreamJsonLog ---


def test_log_writes_one_line_per_received_line_with_clock_time(tmp_path):
    log, clock, errors = make_log(tmp_path)
    clock.now = 11.0
    log.add_line('{"type": "system", "subtype": "init"}\n')
    clock.now = 12.5
    log.add_line("not json\n")
    log.stop()
    assert read_records(tmp_path / STREAM_FILE_NAME) == [
        {"mono": 11.0, "data": {"type": "system", "subtype": "init"}},
        {"mono": 12.5, "raw": "not json"},
    ]
    assert errors.errors == []


def test_log_lines_are_readable_before_stop(tmp_path):
    log, _, _ = make_log(tmp_path)
    log.add_line('{"type": "system"}\n')
    # stop の前でも、書いた行をほかの読み手が読める (1 行ごとに書き出す)
    assert read_records(tmp_path / STREAM_FILE_NAME) == [
        {"mono": 10.0, "data": {"type": "system"}}
    ]
    log.add_line('{"type": "assistant"}\n')
    assert len(read_records(tmp_path / STREAM_FILE_NAME)) == 2
    log.stop()


def test_log_writes_non_ascii_text_as_is(tmp_path):
    log, _, _ = make_log(tmp_path)
    log.add_line(json.dumps(RESULT_LINE, ensure_ascii=False) + "\n")
    log.stop()
    assert "戦略を更新した" in (tmp_path / STREAM_FILE_NAME).read_text(encoding="utf-8")


def test_log_keeps_last_result_line(tmp_path):
    log, _, _ = make_log(tmp_path)
    assert log.last_result is None
    log.add_line('{"type": "result", "result": "first"}\n')
    log.add_line('{"type": "assistant"}\n')
    log.add_line("[1, 2]\n")
    assert log.last_result == {"type": "result", "result": "first"}
    log.add_line('{"type": "result", "result": "second"}\n')
    assert log.last_result == {"type": "result", "result": "second"}
    log.stop()


def test_log_does_not_write_after_stop_but_keeps_result(tmp_path):
    log, _, _ = make_log(tmp_path)
    log.add_line('{"type": "system"}\n')
    log.stop()
    log.add_line('{"type": "result", "result": "late"}\n')
    assert len(read_records(tmp_path / STREAM_FILE_NAME)) == 1
    assert log.last_result == {"type": "result", "result": "late"}
    # 2 回目の stop は何もしない
    log.stop()


def test_log_reports_open_failure_and_keeps_result(tmp_path):
    (tmp_path / STREAM_FILE_NAME).mkdir()
    log, _, errors = make_log(tmp_path)
    assert len(errors.errors) == 1
    log.add_line('{"type": "result", "result": "done"}\n')
    log.stop()
    assert log.last_result == {"type": "result", "result": "done"}
    assert len(errors.errors) == 1


def test_log_stops_writing_after_write_failure_and_reports_once(tmp_path):
    file = FailingFile(ok_writes=1)
    log, _, errors = make_log(tmp_path, open_file=lambda path: file)
    log.add_line('{"type": "system"}\n')
    log.add_line('{"type": "assistant"}\n')
    log.add_line('{"type": "result", "result": "done"}\n')
    log.stop()
    assert len(file.lines) == 1
    assert json.loads(file.lines[0]) == {"mono": 10.0, "data": {"type": "system"}}
    assert file.closed is True
    assert errors.errors == ["No space left on device"]
    assert log.last_result == {"type": "result", "result": "done"}


# --- ProcessOutputReader ---


def test_reader_passes_stdout_lines_in_order_and_reads_stderr():
    lines: list[str] = []
    reader = ProcessOutputReader(
        io.StringIO("first\nsecond\nlast"), io.StringIO("warning\n"), lines.append
    )
    reader.start()
    assert reader.join(WAIT_TIMEOUT) is True
    assert lines == ["first\n", "second\n", "last"]
    assert reader.stderr_text == "warning\n"


def test_reader_join_returns_false_while_pipe_stays_open():
    lines: list[str] = []
    stdout = BlockingLines(["first\n"])
    stderr = BlockingText("")
    reader = ProcessOutputReader(stdout, stderr, lines.append)
    reader.start()
    assert reader.join(0.05) is False
    assert reader.stderr_text is None
    assert lines == ["first\n"]
    stdout.release.set()
    stderr.release.set()
    assert reader.join(WAIT_TIMEOUT) is True
    assert reader.stderr_text == ""


def test_reader_join_with_zero_timeout_does_not_wait():
    stdout = BlockingLines([])
    stderr = BlockingText("")
    reader = ProcessOutputReader(stdout, stderr, lambda line: None)
    reader.start()
    assert reader.join(0.0) is False
    assert reader.join(-1.0) is False
    stdout.release.set()
    stderr.release.set()
    assert reader.join(WAIT_TIMEOUT) is True


def test_reader_stops_reading_on_closed_pipe():
    lines: list[str] = []
    reader = ProcessOutputReader(BrokenLines(["first\n"]), BrokenText(), lines.append)
    reader.start()
    assert reader.join(WAIT_TIMEOUT) is True
    assert lines == ["first\n"]
    assert reader.stderr_text is None


def test_reader_threads_are_daemon_threads():
    stdout = BlockingLines([])
    stderr = BlockingText("")
    before = set(threading.enumerate())
    reader = ProcessOutputReader(stdout, stderr, lambda line: None)
    reader.start()
    started = [thread for thread in threading.enumerate() if thread not in before]
    assert len(started) == 2
    assert all(thread.daemon for thread in started)
    stdout.release.set()
    stderr.release.set()
    assert reader.join(WAIT_TIMEOUT) is True
