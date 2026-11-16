"""claude の子孫のプロセスを /proc から控え、子孫のセッションごとに止める。

claude を SIGKILL で止めると、claude は自分の子のプロセスを後始末できない。claude の Bash ツールは
コマンドを claude とは別のセッション (別のプロセスグループ) で起動するため、claude のプロセスグループに
シグナルを送っても届かない (Claude Code 2.1.325 で 2026-11-17 に確かめた)。
また、親が SIGKILL された子孫は init などに付け替わるため、claude を止めた後は claude の pid からの親子関係では
たどれない。そこで SIGKILL を送る前に claude の子孫を ppid をたどって控え、子孫のセッション (sid) を集めておき、
claude が終わった後にそのセッションのプロセスを止める (kill_sessions)。Bash ツールのコマンドは
それぞれ別のセッションで起動され、その子は同じセッションに入るため、セッションごとに止めると、
控えた後に作られた子も止められる。

/proc/<pid>/stat の解析 (parse_proc_stat) と子孫の探索 (find_descendants) は純粋関数で、
/proc の読み取り (read_process_entries・read_descendants) とシグナルの送信 (kill_sessions) は I/O。
"""

import os
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

PROC_DIR = Path("/proc")

# /proc/<pid>/stat の、comm (2 番目の項目) の後ろの項目のうちの位置。
# 項目の番号は 1 から数え、3 番目 (state) が comm の後ろの 0 番目になる
_STATE_INDEX = 3 - 3
_PPID_INDEX = 4 - 3
_PGID_INDEX = 5 - 3
_SID_INDEX = 6 - 3
_START_TIME_INDEX = 22 - 3

# 終わっていて、親に回収されるのを待っている (シグナルを送っても意味が無い) プロセスの state
_EXITED_STATES = frozenset({"Z", "X", "x"})

KILL_SESSIONS_INTERVAL_S = 0.1  # kill_sessions が送ってから一覧を読み直すまで待つ秒数
KILL_SESSIONS_MAX_ROUNDS = 20  # kill_sessions が送って読み直すのを繰り返す回数の上限
PROTECTED_SESSION_IDS = (
    0,
    1,
)  # kill_sessions が対象にしないセッションの ID (カーネルのスレッドと init のセッション)


@dataclass(frozen=True)
class ProcessEntry:
    """1 つのプロセス (/proc/<pid>/stat の項目)。

    pid: プロセスの番号 (1 番目の項目)
    state: 状態 (3 番目の項目。"R"・"S"・"Z" など。"Z" は終わって親に回収されるのを待っている)
    ppid: 親のプロセスの番号 (4 番目の項目)
    pgid: プロセスグループの番号 (5 番目の項目)
    sid: セッションの番号 (6 番目の項目)
    start_time: 22 番目の項目 starttime (システムが起動してから、このプロセスが起動するまでの時間)
    """

    pid: int
    state: str
    ppid: int
    pgid: int
    sid: int
    start_time: int

    @property
    def exited(self) -> bool:
        """終わっていて、親に回収されるのを待っている (state が Z・X・x) なら True を返す。"""
        return self.state in _EXITED_STATES


def parse_proc_stat(text: str) -> ProcessEntry:
    """/proc/<pid>/stat の 1 行を解析する。

    2 番目の項目 (comm) は括弧で囲まれ、空白や括弧を含みうるため、最初の "(" の前を pid とし、
    最後の ")" の後ろを空白で分けて state (3 番目)・ppid (4 番目)・pgid (5 番目)・sid (6 番目)・
    starttime (22 番目) の項目を取る。例: "123 (a b) c) S 45 46 47 ..." は pid 123、ppid 45、pgid 46、sid 47。
    括弧が無い、項目が足りない、数でないなど、形が違えば ValueError にする。
    """
    open_index = text.find("(")
    close_index = text.rfind(")")
    if open_index < 0 or close_index < open_index:
        raise ValueError(f"unexpected /proc stat line: {text!r}")
    fields = text[close_index + 1 :].split()
    if len(fields) <= _START_TIME_INDEX:
        raise ValueError(f"unexpected /proc stat line: {text!r}")
    try:
        return ProcessEntry(
            pid=int(text[:open_index]),
            state=fields[_STATE_INDEX],
            ppid=int(fields[_PPID_INDEX]),
            pgid=int(fields[_PGID_INDEX]),
            sid=int(fields[_SID_INDEX]),
            start_time=int(fields[_START_TIME_INDEX]),
        )
    except ValueError:
        raise ValueError(f"unexpected /proc stat line: {text!r}") from None


def find_descendants(
    entries: Iterable[ProcessEntry], root_pid: int
) -> list[ProcessEntry]:
    """entries のうち、ppid をたどって root_pid の子孫になるプロセスを返す (root_pid 自身は含めない)。

    root_pid に近い順 (同じ深さは entries の順) に返す。一度たどったプロセスは二度たどらないため、
    ppid が自分自身や 0 の行があっても止まる。
    """
    children: dict[int, list[ProcessEntry]] = {}
    for entry in entries:
        if entry.pid != entry.ppid:
            children.setdefault(entry.ppid, []).append(entry)
    descendants: list[ProcessEntry] = []
    seen = {root_pid}
    queue = deque([root_pid])
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, []):
            if child.pid in seen:
                continue
            seen.add(child.pid)
            descendants.append(child)
            queue.append(child.pid)
    return descendants


def _read_stat(proc_dir: Path, pid: int | str) -> str:
    return (proc_dir / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")


def read_process_entries(proc_dir: Path = PROC_DIR) -> list[ProcessEntry]:
    """proc_dir の、名前が数字だけのディレクトリーの stat を読んで解析し、順に返す。

    数字でない名前は読まない。読む間に消えたプロセス (FileNotFoundError・ProcessLookupError) と、
    stat の無いディレクトリー、解析できない行 (ValueError) は飛ばす。
    """
    entries: list[ProcessEntry] = []
    for child in proc_dir.iterdir():
        if not (child.name.isascii() and child.name.isdigit()):
            continue
        try:
            text = _read_stat(proc_dir, child.name)
        except (FileNotFoundError, ProcessLookupError, NotADirectoryError):
            continue
        try:
            entries.append(parse_proc_stat(text))
        except ValueError:
            continue
    return entries


def read_descendants(root_pid: int, proc_dir: Path = PROC_DIR) -> list[ProcessEntry]:
    """find_descendants(read_process_entries(proc_dir), root_pid) を返す (root_pid の子孫を今の /proc から控える)。"""
    return find_descendants(read_process_entries(proc_dir), root_pid)


def kill_sessions(
    session_ids: Collection[int],
    sig: int,
    *,
    read_entries: Callable[[], list[ProcessEntry]] = read_process_entries,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = KILL_SESSIONS_MAX_ROUNDS,
) -> None:
    """session_ids のセッションにいるプロセスに sig を送り、残りがなくなるまで繰り返す。

    1 回ごとに read_entries() で今のプロセスの一覧を読み、sid が session_ids に入っていて、
    終わっていない (ProcessEntry.exited でない) プロセスそれぞれに kill(pid, sig) を送る
    (ProcessLookupError と PermissionError は無視する)。送ったら sleep(KILL_SESSIONS_INTERVAL_S) で待ち、一覧を読み直す。
    送る相手がいなくなるか、max_rounds 回送ったら終える。
    自分自身 (os.getpid()) と、自分のセッション (os.getsid(0)) のプロセスには、session_ids に入っていても送らない。
    セッションの ID の 0 (カーネルのスレッド) と 1 (init のセッション) も、session_ids に入っていても対象にしない
    (claude の子孫がこれらのセッションに入ることはないが、渡す値の誤りで無関係なプロセスを止めないため)。
    """
    own_pid = os.getpid()
    targets = set(session_ids) - {os.getsid(0), *PROTECTED_SESSION_IDS}
    if not targets:
        return
    for _ in range(max_rounds):
        victims = [
            entry
            for entry in read_entries()
            if entry.sid in targets and entry.pid != own_pid and not entry.exited
        ]
        if not victims:
            return
        for entry in victims:
            try:
                kill(entry.pid, sig)
            except (ProcessLookupError, PermissionError):
                continue
        sleep(KILL_SESSIONS_INTERVAL_S)
