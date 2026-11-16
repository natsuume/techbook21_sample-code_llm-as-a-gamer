"""claude の子孫のプロセスを /proc から控え、pid の再利用を見分けて止める。

claude を SIGKILL で止めると、claude は自分の子のプロセスを後始末できない。claude の Bash ツールは
コマンドを claude とは別のセッション (別のプロセスグループ) で起動するため、claude のプロセスグループに
シグナルを送っても届かない (Claude Code 2.1.325 で 2026-11-17 に確かめた)。そこで SIGKILL を送る前に
claude の子孫を ppid をたどって控え、claude が終わった後に 1 つずつ止める。
控えてから止めるまでの間にプロセスが終わり、同じ pid が別のプロセスに使われることがあるため、
/proc/<pid>/stat の starttime (起動した時刻) が控えたときと同じときだけシグナルを送る。

/proc/<pid>/stat の解析 (parse_proc_stat) と子孫の探索 (find_descendants) は純粋関数で、
/proc の読み取り (read_process_entries・read_descendants) とシグナルの送信 (kill_if_unchanged) は I/O。
"""

import os
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROC_DIR = Path("/proc")

# /proc/<pid>/stat の、comm (2 番目の項目) の後ろの項目のうちの位置。
# 項目の番号は 1 から数え、3 番目 (state) が comm の後ろの 0 番目になる
_PPID_INDEX = 4 - 3
_START_TIME_INDEX = 22 - 3


@dataclass(frozen=True)
class ProcessEntry:
    """1 つのプロセス。

    pid: プロセスの番号
    ppid: 親のプロセスの番号
    start_time: /proc/<pid>/stat の 22 番目の項目 starttime (システムが起動してから、このプロセスが起動するまでの時間)。
        同じ pid が別のプロセスに使われたことを見分けるために使う
    """

    pid: int
    ppid: int
    start_time: int


def parse_proc_stat(text: str) -> ProcessEntry:
    """/proc/<pid>/stat の 1 行を解析する。

    2 番目の項目 (comm) は括弧で囲まれ、空白や括弧を含みうるため、最初の "(" の前を pid とし、
    最後の ")" の後ろを空白で分けて ppid (4 番目の項目) と starttime (22 番目の項目) を取る。
    例: "123 (a b) c) S 45 ..." は pid 123、ppid 45。
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
            ppid=int(fields[_PPID_INDEX]),
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


def kill_if_unchanged(entry: ProcessEntry, sig: int, proc_dir: Path = PROC_DIR) -> bool:
    """entry のプロセスが控えたときと同じなら sig を送って True を返す。

    proc_dir/<pid>/stat を読み直し、start_time が entry と同じときだけ os.kill(entry.pid, sig) を送る。
    プロセスが消えていた (FileNotFoundError・ProcessLookupError)、stat を解析できない、start_time が違う
    (同じ pid が別のプロセスに使われた)、送るときに消えていた (ProcessLookupError) ときは、何もせずに False を返す。
    """
    try:
        current = parse_proc_stat(_read_stat(proc_dir, entry.pid))
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False
    if current.pid != entry.pid or current.start_time != entry.start_time:
        return False
    try:
        os.kill(entry.pid, sig)
    except ProcessLookupError:
        return False
    return True
