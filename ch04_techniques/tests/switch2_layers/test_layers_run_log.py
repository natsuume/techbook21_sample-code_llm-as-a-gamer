import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock


class FakeMonotonic:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_log_writes_t_and_event_first(tmp_path):
    monotonic = FakeMonotonic(100.0)
    path = tmp_path / "run.jsonl"
    log = RunLog(path, RunClock(monotonic))
    assert not path.exists()
    log.write("start", run_dir=tmp_path, args={"allow_home": False})
    monotonic.now = 101.5
    log.write("stop", reason="interrupted")
    rows = read_rows(path)
    assert rows == [
        {
            "t": 0.0,
            "event": "start",
            "run_dir": str(tmp_path),
            "args": {"allow_home": False},
        },
        {"t": 1.5, "event": "stop", "reason": "interrupted"},
    ]
    assert list(rows[0])[:2] == ["t", "event"]


@pytest.mark.parametrize("key", ["t", "event"])
def test_run_log_rejects_reserved_keys(tmp_path, key):
    path = tmp_path / "run.jsonl"
    log = RunLog(path, RunClock(FakeMonotonic(0.0)))
    with pytest.raises(ValueError):
        log.write("start", **{key: 5.0})
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_run_log_echo(tmp_path):
    lines: list[str] = []
    log = RunLog(
        tmp_path / "run.jsonl", RunClock(FakeMonotonic(0.0)), echo=lines.append
    )
    log.write("deep_start", kind="controls")
    assert len(lines) == 1
    assert "deep_start" in lines[0]
    assert "kind=controls" in lines[0]


def test_run_log_from_many_threads_keeps_lines_whole(tmp_path):
    path = tmp_path / "run.jsonl"
    log = RunLog(path, RunClock(FakeMonotonic(0.0)))

    def write_many(name: str) -> None:
        for i in range(50):
            log.write("surface_error", thread=name, count=i)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(write_many, f"w{i}") for i in range(4)]
        # result() はスレッドの例外を、テストの本体でそのまま出す
        for future in futures:
            future.result()
    rows = read_rows(path)
    assert len(rows) == 200


def test_writes_after_close_are_ignored(tmp_path):
    path = tmp_path / "run.jsonl"
    log = RunLog(path, RunClock(FakeMonotonic(0.0)))
    log.write("stop", reason="interrupted")
    log.close()
    # 終了処理の後に、止まり切らなかったスレッドが書こうとしても、例外にせず何も書かない
    log.write("middle_error", error="late", count=1)
    log.close()
    rows = read_rows(path)
    assert [row["event"] for row in rows] == ["stop"]
