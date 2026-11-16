"""process_tree のテスト。

/proc の読み取りは、tmp_path に作った偽の /proc と、本物の /proc の両方で確かめる。
本物のプロセスにシグナルを送るのは、このテストが起動した python の子プロセスだけにし、必ず止める。
"""

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from switch2_harness.process_tree import (
    ProcessEntry,
    find_descendants,
    kill_if_unchanged,
    parse_proc_stat,
    read_descendants,
    read_process_entries,
)

REAL_PROC = Path("/proc")


def stat_line(pid: int, comm: str, ppid: int, start_time: int) -> str:
    """/proc/<pid>/stat と同じ形の 1 行を作る (pid・comm・ppid・starttime 以外は適当な値)。"""
    between = " ".join(["1"] * 17)  # 5〜21 番目の項目
    after = " ".join(["0"] * 30)  # 23 番目からの項目
    return f"{pid} ({comm}) S {ppid} {between} {start_time} {after}\n"


def write_fake_proc(proc_dir: Path, pid: int | str, text: str) -> None:
    process_dir = proc_dir / str(pid)
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "stat").write_text(text, encoding="utf-8")


@contextmanager
def sleeping_child() -> Iterator[subprocess.Popen[bytes]]:
    """30 秒眠る python の子プロセスを起動し、抜けるときに必ず止める。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def real_entry(pid: int) -> ProcessEntry:
    return parse_proc_stat((REAL_PROC / str(pid) / "stat").read_text())


# --- parse_proc_stat ---


def test_parse_proc_stat_reads_pid_ppid_and_start_time():
    assert parse_proc_stat(stat_line(123, "bash", 45, 6789)) == ProcessEntry(
        pid=123, ppid=45, start_time=6789
    )


@pytest.mark.parametrize("comm", ["a b) c", "(x)", ") S 9 9 9", "uv run"])
def test_parse_proc_stat_handles_spaces_and_parentheses_in_comm(comm):
    assert parse_proc_stat(stat_line(123, comm, 45, 6789)) == ProcessEntry(
        pid=123, ppid=45, start_time=6789
    )


def test_parse_proc_stat_reads_the_real_proc():
    entry = parse_proc_stat((REAL_PROC / "self" / "stat").read_text())
    assert entry.pid == os.getpid()
    assert entry.ppid == os.getppid()
    assert entry.start_time > 0


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no parentheses at all",
        "123 (bash) S 45",
        "abc (bash) S 45 " + " ".join(["1"] * 17) + " 6789",
        "123 (bash) S x " + " ".join(["1"] * 17) + " 6789",
        "123 (bash) S 45 " + " ".join(["1"] * 17) + " later",
        "123 bash) S 45 " + " ".join(["1"] * 17) + " 6789",
    ],
)
def test_parse_proc_stat_rejects_unexpected_lines(text):
    with pytest.raises(ValueError):
        parse_proc_stat(text)


# --- find_descendants ---

ENTRIES = [
    ProcessEntry(pid=1, ppid=0, start_time=1),
    ProcessEntry(pid=10, ppid=1, start_time=2),
    ProcessEntry(pid=20, ppid=10, start_time=3),
    ProcessEntry(pid=40, ppid=1, start_time=4),
    ProcessEntry(pid=21, ppid=10, start_time=5),
    ProcessEntry(pid=30, ppid=20, start_time=6),
    ProcessEntry(pid=50, ppid=50, start_time=7),
    ProcessEntry(pid=60, ppid=0, start_time=8),
]


def test_find_descendants_includes_grandchildren_in_order():
    descendants = find_descendants(ENTRIES, 10)
    assert [entry.pid for entry in descendants] == [20, 21, 30]


def test_find_descendants_excludes_root_and_unrelated_processes():
    pids = {entry.pid for entry in find_descendants(ENTRIES, 10)}
    assert 10 not in pids
    assert pids.isdisjoint({1, 40, 50, 60})


def test_find_descendants_without_children_is_empty():
    assert find_descendants(ENTRIES, 30) == []
    assert find_descendants(ENTRIES, 999) == []


def test_find_descendants_stops_on_self_parent_and_cycles():
    entries = [
        ProcessEntry(pid=50, ppid=50, start_time=1),
        ProcessEntry(pid=70, ppid=71, start_time=2),
        ProcessEntry(pid=71, ppid=70, start_time=3),
        ProcessEntry(pid=0, ppid=0, start_time=0),
    ]
    assert find_descendants(entries, 50) == []
    assert [entry.pid for entry in find_descendants(entries, 70)] == [71]
    assert find_descendants(entries, 0) == []


# --- read_process_entries ---


def test_read_process_entries_reads_a_fake_proc(tmp_path):
    write_fake_proc(tmp_path, 123, stat_line(123, "claude", 1, 100))
    write_fake_proc(tmp_path, 456, stat_line(456, "a b) c", 123, 200))
    write_fake_proc(tmp_path, "self", stat_line(999, "self", 1, 300))
    write_fake_proc(tmp_path, 789, "garbage")
    (tmp_path / "555").mkdir()  # stat の無いディレクトリー
    (tmp_path / "321").write_text("not a directory", encoding="utf-8")
    entries = sorted(read_process_entries(tmp_path), key=lambda entry: entry.pid)
    assert entries == [
        ProcessEntry(pid=123, ppid=1, start_time=100),
        ProcessEntry(pid=456, ppid=123, start_time=200),
    ]


def test_read_process_entries_reads_the_real_proc():
    pids = {entry.pid for entry in read_process_entries()}
    assert os.getpid() in pids


def test_read_descendants_finds_a_real_child():
    with sleeping_child() as child:
        descendants = read_descendants(os.getpid())
        assert child.pid in {entry.pid for entry in descendants}


def test_read_descendants_of_a_fake_proc(tmp_path):
    write_fake_proc(tmp_path, 123, stat_line(123, "claude", 1, 100))
    write_fake_proc(tmp_path, 456, stat_line(456, "bash", 123, 200))
    write_fake_proc(tmp_path, 457, stat_line(457, "sleep", 456, 201))
    write_fake_proc(tmp_path, 900, stat_line(900, "other", 1, 300))
    pids = sorted(entry.pid for entry in read_descendants(123, tmp_path))
    assert pids == [456, 457]


# --- kill_if_unchanged ---


def test_kill_if_unchanged_kills_the_same_process():
    with sleeping_child() as child:
        entry = real_entry(child.pid)
        assert kill_if_unchanged(entry, signal.SIGKILL) is True
        assert child.wait(timeout=10) == -signal.SIGKILL


def test_kill_if_unchanged_skips_a_different_start_time():
    with sleeping_child() as child:
        entry = real_entry(child.pid)
        reused = replace(entry, start_time=entry.start_time + 1)
        assert kill_if_unchanged(reused, signal.SIGKILL) is False
        assert child.poll() is None


def test_kill_if_unchanged_skips_a_process_that_is_gone():
    with sleeping_child() as child:
        entry = real_entry(child.pid)
        child.kill()
        child.wait(timeout=10)
        assert kill_if_unchanged(entry, signal.SIGKILL) is False


def test_kill_if_unchanged_reads_the_given_proc_dir(tmp_path):
    with sleeping_child() as child:
        entry = real_entry(child.pid)
        # 偽の /proc の start_time が違えば送らない
        write_fake_proc(tmp_path, child.pid, stat_line(child.pid, "python", 1, 1))
        assert kill_if_unchanged(entry, signal.SIGKILL, tmp_path) is False
        assert child.poll() is None
        # 偽の /proc の start_time が同じなら送る
        write_fake_proc(
            tmp_path,
            child.pid,
            stat_line(child.pid, "python", entry.ppid, entry.start_time),
        )
        assert kill_if_unchanged(entry, signal.SIGKILL, tmp_path) is True
        assert child.wait(timeout=10) == -signal.SIGKILL


def test_kill_if_unchanged_skips_missing_and_broken_stat(tmp_path):
    entry = ProcessEntry(pid=123, ppid=1, start_time=100)
    assert kill_if_unchanged(entry, signal.SIGKILL, tmp_path) is False
    write_fake_proc(tmp_path, 123, "garbage")
    assert kill_if_unchanged(entry, signal.SIGKILL, tmp_path) is False
