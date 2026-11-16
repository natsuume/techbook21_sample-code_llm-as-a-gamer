"""process_tree のテスト。

/proc の読み取りは、tmp_path に作った偽の /proc と、本物の /proc の両方で確かめる。
本物のプロセスにシグナルを送るのは、このテストが起動した python の子と孫だけにし、必ず止める。
kill_sessions の本物のテストに渡すのは、このテストが新しいセッションで起動した子のセッションだけにする。
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from switch2_harness.process_tree import (
    KILL_SESSIONS_INTERVAL_S,
    KILL_SESSIONS_MAX_ROUNDS,
    ProcessEntry,
    find_descendants,
    kill_sessions,
    parse_proc_stat,
    read_descendants,
    read_process_entries,
)

REAL_PROC = Path("/proc")


def stat_line(
    pid: int,
    comm: str,
    ppid: int,
    start_time: int,
    pgid: int = 46,
    sid: int = 47,
    state: str = "S",
) -> str:
    """/proc/<pid>/stat と同じ形の 1 行を作る (指定した項目以外は適当な値)。"""
    between = " ".join(["1"] * 15)  # 7〜21 番目の項目
    after = " ".join(["0"] * 30)  # 23 番目からの項目
    return (
        f"{pid} ({comm}) {state} {ppid} {pgid} {sid} {between} {start_time} {after}\n"
    )


def entry(
    pid: int, ppid: int = 1, sid: int = 47, state: str = "S", start_time: int = 0
) -> ProcessEntry:
    return ProcessEntry(
        pid=pid, state=state, ppid=ppid, pgid=sid, sid=sid, start_time=start_time
    )


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


# --- parse_proc_stat ---


def test_parse_proc_stat_reads_the_fields():
    assert parse_proc_stat(
        stat_line(123, "bash", 45, 6789, pgid=46, sid=47)
    ) == ProcessEntry(pid=123, state="S", ppid=45, pgid=46, sid=47, start_time=6789)


@pytest.mark.parametrize("comm", ["a b) c", "(x)", ") S 9 9 9", "uv run"])
def test_parse_proc_stat_handles_spaces_and_parentheses_in_comm(comm):
    assert parse_proc_stat(
        stat_line(123, comm, 45, 6789, pgid=46, sid=47, state="Z")
    ) == ProcessEntry(pid=123, state="Z", ppid=45, pgid=46, sid=47, start_time=6789)


def test_parse_proc_stat_reads_the_real_proc():
    parsed = parse_proc_stat((REAL_PROC / "self" / "stat").read_text())
    assert parsed.pid == os.getpid()
    assert parsed.ppid == os.getppid()
    assert parsed.pgid == os.getpgid(0)
    assert parsed.sid == os.getsid(0)
    assert parsed.start_time > 0
    assert parsed.exited is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no parentheses at all",
        "123 (bash) S 45",
        "abc (bash) S 45 46 47 " + " ".join(["1"] * 15) + " 6789",
        "123 (bash) S x 46 47 " + " ".join(["1"] * 15) + " 6789",
        "123 (bash) S 45 x 47 " + " ".join(["1"] * 15) + " 6789",
        "123 (bash) S 45 46 x " + " ".join(["1"] * 15) + " 6789",
        "123 (bash) S 45 46 47 " + " ".join(["1"] * 15) + " later",
        "123 bash) S 45 46 47 " + " ".join(["1"] * 15) + " 6789",
    ],
)
def test_parse_proc_stat_rejects_unexpected_lines(text):
    with pytest.raises(ValueError):
        parse_proc_stat(text)


@pytest.mark.parametrize(
    ("state", "exited"), [("S", False), ("R", False), ("Z", True), ("X", True)]
)
def test_process_entry_exited(state, exited):
    assert entry(1, state=state).exited is exited


# --- find_descendants ---

ENTRIES = [
    entry(1, ppid=0),
    entry(10, ppid=1),
    entry(20, ppid=10),
    entry(40, ppid=1),
    entry(21, ppid=10),
    entry(30, ppid=20),
    entry(50, ppid=50),
    entry(60, ppid=0),
]


def test_find_descendants_includes_grandchildren_in_order():
    descendants = find_descendants(ENTRIES, 10)
    assert [found.pid for found in descendants] == [20, 21, 30]


def test_find_descendants_excludes_root_and_unrelated_processes():
    pids = {found.pid for found in find_descendants(ENTRIES, 10)}
    assert 10 not in pids
    assert pids.isdisjoint({1, 40, 50, 60})


def test_find_descendants_without_children_is_empty():
    assert find_descendants(ENTRIES, 30) == []
    assert find_descendants(ENTRIES, 999) == []


def test_find_descendants_stops_on_self_parent_and_cycles():
    entries = [
        entry(50, ppid=50),
        entry(70, ppid=71),
        entry(71, ppid=70),
        entry(0, ppid=0),
    ]
    assert find_descendants(entries, 50) == []
    assert [found.pid for found in find_descendants(entries, 70)] == [71]
    assert find_descendants(entries, 0) == []


# --- read_process_entries ---


def test_read_process_entries_reads_a_fake_proc(tmp_path):
    write_fake_proc(tmp_path, 123, stat_line(123, "claude", 1, 100, pgid=123, sid=123))
    write_fake_proc(
        tmp_path, 456, stat_line(456, "a b) c", 123, 200, pgid=456, sid=456)
    )
    write_fake_proc(tmp_path, "self", stat_line(999, "self", 1, 300))
    write_fake_proc(tmp_path, 789, "garbage")
    (tmp_path / "555").mkdir()  # stat の無いディレクトリー
    (tmp_path / "321").write_text("not a directory", encoding="utf-8")
    entries = sorted(read_process_entries(tmp_path), key=lambda found: found.pid)
    assert entries == [
        ProcessEntry(pid=123, state="S", ppid=1, pgid=123, sid=123, start_time=100),
        ProcessEntry(pid=456, state="S", ppid=123, pgid=456, sid=456, start_time=200),
    ]


def test_read_process_entries_reads_the_real_proc():
    pids = {found.pid for found in read_process_entries()}
    assert os.getpid() in pids


def test_read_descendants_finds_a_real_child():
    with sleeping_child() as child:
        descendants = read_descendants(os.getpid())
        assert child.pid in {found.pid for found in descendants}


def test_read_descendants_of_a_fake_proc(tmp_path):
    write_fake_proc(tmp_path, 123, stat_line(123, "claude", 1, 100))
    write_fake_proc(tmp_path, 456, stat_line(456, "bash", 123, 200))
    write_fake_proc(tmp_path, 457, stat_line(457, "sleep", 456, 201))
    write_fake_proc(tmp_path, 900, stat_line(900, "other", 1, 300))
    pids = sorted(found.pid for found in read_descendants(123, tmp_path))
    assert pids == [456, 457]


# --- kill_sessions (fake) ---


class FakeProcessTable:
    """kill_sessions の read_entries・kill・sleep の fake。

    rounds の i 番目が i 回目に読んだ一覧 (使い切ったら最後のものを繰り返す)。送ったシグナルを killed に記録する。
    """

    def __init__(
        self,
        rounds: list[list[ProcessEntry]],
        missing_pids: frozenset[int] = frozenset(),
    ) -> None:
        self.rounds = rounds
        self.missing_pids = missing_pids
        self.reads = 0
        self.killed: list[tuple[int, int]] = []
        self.sleeps: list[float] = []

    def read(self) -> list[ProcessEntry]:
        rows = self.rounds[min(self.reads, len(self.rounds) - 1)]
        self.reads += 1
        return list(rows)

    def kill(self, pid: int, sig: int) -> None:
        self.killed.append((pid, sig))
        if pid in self.missing_pids:
            raise ProcessLookupError(pid)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def run_kill_sessions(
    table: FakeProcessTable, session_ids: set[int], max_rounds: int = 20
) -> None:
    kill_sessions(
        session_ids,
        signal.SIGKILL,
        read_entries=table.read,
        kill=table.kill,
        sleep=table.sleep,
        max_rounds=max_rounds,
    )


def test_kill_sessions_constants():
    assert KILL_SESSIONS_INTERVAL_S == 0.1
    assert KILL_SESSIONS_MAX_ROUNDS == 20


def test_kill_sessions_sends_only_to_the_given_sessions():
    table = FakeProcessTable(
        [[entry(101, sid=500), entry(102, sid=500), entry(201, sid=600)], []]
    )
    run_kill_sessions(table, {500})
    assert table.killed == [(101, signal.SIGKILL), (102, signal.SIGKILL)]
    assert table.sleeps == [KILL_SESSIONS_INTERVAL_S]
    assert table.reads == 2


def test_kill_sessions_sends_to_a_process_that_appears_later():
    # 1 回目に送った後に、同じセッションに新しい子が現れた
    table = FakeProcessTable([[entry(101, sid=500)], [entry(103, sid=500)], []])
    run_kill_sessions(table, {500})
    assert table.killed == [(101, signal.SIGKILL), (103, signal.SIGKILL)]
    assert table.reads == 3


def test_kill_sessions_stops_when_nothing_is_left():
    table = FakeProcessTable([[entry(201, sid=600)]])
    run_kill_sessions(table, {500})
    assert table.killed == []
    assert table.sleeps == []
    assert table.reads == 1


def test_kill_sessions_stops_after_max_rounds():
    table = FakeProcessTable([[entry(101, sid=500)]])
    run_kill_sessions(table, {500}, max_rounds=3)
    assert table.killed == [(101, signal.SIGKILL)] * 3
    assert table.sleeps == [KILL_SESSIONS_INTERVAL_S] * 3
    assert table.reads == 3


def test_kill_sessions_never_sends_to_itself_or_its_own_session():
    own_sid = os.getsid(0)
    table = FakeProcessTable(
        [
            [
                entry(os.getpid(), sid=500),
                entry(301, sid=own_sid),
                entry(101, sid=500),
            ],
            [],
        ]
    )
    run_kill_sessions(table, {500, own_sid})
    assert table.killed == [(101, signal.SIGKILL)]


def test_kill_sessions_never_sends_to_kernel_or_init_sessions():
    table = FakeProcessTable(
        [
            [entry(2, sid=0), entry(300, sid=1), entry(101, sid=500)],
            [],
        ]
    )
    run_kill_sessions(table, {0, 1, 500})
    assert table.killed == [(101, signal.SIGKILL)]


def test_kill_sessions_with_only_protected_sessions_reads_nothing():
    table = FakeProcessTable([[entry(2, sid=0), entry(300, sid=1)]])
    run_kill_sessions(table, {0, 1})
    assert table.reads == 0
    assert table.killed == []


def test_kill_sessions_ignores_permission_errors():
    sent: list[int] = []

    def kill(pid: int, sig: int) -> None:
        sent.append(pid)
        if pid == 101:
            raise PermissionError(1, "Operation not permitted")

    rounds = iter([[entry(101, sid=500), entry(102, sid=500)], []])
    kill_sessions(
        {500},
        signal.SIGKILL,
        read_entries=lambda: next(rounds),
        kill=kill,
        sleep=lambda seconds: None,
    )
    assert sent == [101, 102]


def test_kill_sessions_with_only_its_own_session_reads_nothing():
    table = FakeProcessTable([[entry(301, sid=os.getsid(0))]])
    run_kill_sessions(table, {os.getsid(0)})
    assert table.reads == 0
    assert table.killed == []


def test_kill_sessions_ignores_processes_that_are_gone():
    table = FakeProcessTable(
        [[entry(101, sid=500), entry(102, sid=500)], []],
        missing_pids=frozenset({101}),
    )
    run_kill_sessions(table, {500})
    assert table.killed == [(101, signal.SIGKILL), (102, signal.SIGKILL)]


def test_kill_sessions_skips_exited_processes():
    table = FakeProcessTable([[entry(101, sid=500, state="Z")]])
    run_kill_sessions(table, {500})
    assert table.killed == []
    assert table.reads == 1


# --- kill_sessions (本物の子と孫) ---

CHILD_SCRIPT = (
    "import subprocess, sys, time\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "print(grandchild.pid, flush=True)\n"
    "time.sleep(30)\n"
)


def alive_in_session(pid: int, session: int) -> bool:
    """pid のプロセスが session にいて、終わっていなければ True を返す (pid の再利用を区別する)。"""
    try:
        found = parse_proc_stat((REAL_PROC / str(pid) / "stat").read_text())
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False
    return found.sid == session and not found.exited


@contextmanager
def session_with_grandchild() -> Iterator[tuple[subprocess.Popen[str], int]]:
    """新しいセッションで python の子を起動し、子がさらに孫を起動する。抜けるときに必ず両方を止める。"""
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    grandchild_pid: int | None = None
    try:
        assert child.stdout is not None
        grandchild_pid = int(child.stdout.readline())
        yield child, grandchild_pid
    finally:
        # 孫は、まだ子のセッションにいるときだけ止める (pid が別のプロセスに使われていたら送らない)
        if grandchild_pid is not None and alive_in_session(grandchild_pid, child.pid):
            os.kill(grandchild_pid, signal.SIGKILL)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()


def test_kill_sessions_kills_a_real_session_with_a_grandchild():
    with session_with_grandchild() as (child, grandchild_pid):
        session = os.getsid(child.pid)
        assert session == child.pid
        assert session != os.getsid(0)
        assert os.getsid(grandchild_pid) == session
        kill_sessions({session}, signal.SIGKILL)
        assert child.wait(timeout=10) == -signal.SIGKILL
        deadline = time.monotonic() + 10
        while alive_in_session(grandchild_pid, session) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive_in_session(grandchild_pid, session)
