"""実行中の実行のディレクトリーを接続ごとに読み、追記された完全な行から SSE のイベント (live_events) を作る。

1 つの接続 (GET /api/runs/{run}/live) ごとに 1 つの読み取りの状態 (LiveState) を持ち、open_live で最初の読み取り、
poll_live で 2 回目以降の読み取りを行う。1 回の読み取りは「状態と今の時刻を受け取り、送るイベントの列と次の状態を
返す」形で、待ちと時計は外から渡す (stream_live)。このモジュールはファイルを読み (I/O)、読んだ行から状態とイベントを
作る規則は live_state にある。ファイルには何も書かない。

1 回の読み取りの手順 (open_live と poll_live で共通):
    1. run.jsonl の大きさ S を os.stat で取る (無い・シンボリックリンク・OSError なら、無いものとする)
    2. requests.jsonl を、今の位置から終わりまで読む
    3. run.jsonl を、今の位置から S まで読む (jsonl_lines.read_complete_lines の end=S)。手順 2 の後に書かれた
       decision の行を読まないため、読んだ decision の行の要求の行は、必ず手順 2 までに読めている
    4. frames.jsonl・pad.jsonl を、今の位置から終わりまで読む
    5. advisor の実行のディレクトリーの一覧 (run_directory.list_advisor_run_dirs)。実行ごとに、result.json を
       まだ読めていなければ run_records.read_result_json で読み (一度読めたら読み直さない)、stream.jsonl を
       今の位置から読む
    6. run.jsonl の更新時刻を os.stat で取る (実行中の判定に使う)
    JSON Lines は jsonl_lines.read_complete_lines を `jsonl_lines.read_complete_lines(...)` の形 (モジュールの属性)
    で呼んで読み、jsonl_lines.parse_lines で読む (テストがこの関数を差し替えて、読み取りの間に行を書き足す)。
    シンボリックリンク・無いファイル・OSError は、その読み取りでは行なし (位置は進めない。例外を出さない)。
    位置より小さくなったファイルは、行なしとして扱う。

接続時 (open_live) の位置:
    4 つの JSON Lines は位置 0 から今の終わり (run.jsonl は S) まで読む。cursor のあるファイルは、cursor より前
    (end=cursor で読んだ行) を「状態だけの行」、cursor から後 (位置 cursor から読んだ行) を「送る行」にする。
    cursor が行の途中なら、その行はどちらにも入れない。cursor がファイルの大きさより大きければ、その位置より後に
    始まる行だけが送る行になる (その位置をまたぐ行は、書き終わっても送らない)。cursor を省いた (None の) ファイルは、
    今の終わりまでのすべてを状態だけの行にする。advisor の stream.jsonl は、接続時にあったファイルはすべてを状態だけの
    行にし、接続後にできたファイル (接続時にディレクトリーがあってファイルが無かったものを含む) は位置 0 から送る行に
    する。2 回目以降の読み取りで読んだ行は、すべて送る行。
"""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from anyio import to_thread
from switch2_agent.advisors.claude_code_headless import RESULT_FILE_NAME
from switch2_agent.advisors.stream_json_log import STREAM_FILE_NAME

from run_dashboard import jsonl_lines
from run_dashboard.jsonl_lines import LineChunk, parse_lines
from run_dashboard.live_events import LiveEvent
from run_dashboard.live_state import (
    LINE_KINDS,
    AdvisorRead,
    LiveRead,
    LiveRecords,
    apply_read,
)
from run_dashboard.run_directory import AdvisorRunDir, list_advisor_run_dirs
from run_dashboard.run_records import (
    RECORD_FILE_NAMES,
    read_result_json,
    read_run_log_mtime,
)

# 実行のディレクトリーを読む間隔 (秒)
LIVE_POLL_INTERVAL_S = 0.25


@dataclass(frozen=True)
class LiveCursors:
    """接続のクエリーの、読み始める位置 (バイトの位置。区間 API の cursor の値)。

    None は省いたファイル (接続した時点の完全な行の終わりから送る)。
    run       run.jsonl の位置 (クエリーの run)
    requests  requests.jsonl の位置
    frames    frames.jsonl の位置
    pad       pad.jsonl の位置
    """

    run: int | None = None
    requests: int | None = None
    frames: int | None = None
    pad: int | None = None


@dataclass(frozen=True)
class FilePosition:
    """JSON Lines のファイル 1 つの、次に読む位置。

    offset   次に読み始めるバイトの位置
    partial  offset が行の途中かもしれない (接続の cursor が完全な行の終わりでなかった)。次に行を読めたとき、
             offset をまたぐ最初の行を読み捨てる
    """

    offset: int
    partial: bool = False


@dataclass(frozen=True)
class LiveState:
    """1 つの接続の読み取りの状態。

    フィールドは live_reader の中の約束で、呼び出し側は中を見ない・変えない。読み取りのたびに新しい状態を作り、
    渡された状態は変えない。
    run_dir         実行のディレクトリー
    positions       LINE_KINDS の種類 → 次に読む位置
    stream_offsets  advisor の実行の名前 → stream.jsonl の次に読む位置
    records         読んだ記録をためた状態 (live_state.LiveRecords)
    finished        end のイベントを送ったか
    """

    run_dir: Path
    positions: dict[str, FilePosition] = field(default_factory=dict)
    stream_offsets: dict[str, int] = field(default_factory=dict)
    records: LiveRecords = field(default_factory=LiveRecords)
    finished: bool = False


@dataclass(frozen=True)
class LivePoll:
    """1 回の読み取りの結果。

    events    送るイベント (送る順)
    state     次の読み取りに渡す状態
    finished  end のイベントを送ったか (真なら、以後は読まない)
    """

    events: list[LiveEvent]
    state: LiveState
    finished: bool


def open_live(run_dir: Path, cursors: LiveCursors, now: float) -> LivePoll:
    """接続の最初の読み取りを行い、送るイベントと次の状態を返す (モジュールの docstring の手順と接続時の位置)。

    run_dir は照合済みの実行のディレクトリー (無ければ、すべてのファイルが無いものとして扱う)。cursors は接続の
    クエリーの位置、now は今の時刻 (time.time() と同じ時計の秒)。
    events は、cursor 以後の行のイベント、advisor_run と note_version のすべて、stats (と、実行中でなければ end)。
    cursors のどれかが負なら ValueError を出す。
    """
    cursor_of = {
        "run": cursors.run,
        "requests": cursors.requests,
        "frames": cursors.frames,
        "pad": cursors.pad,
    }
    for kind, cursor in cursor_of.items():
        if cursor is not None and cursor < 0:
            raise ValueError(f"cursor must not be negative: {kind}={cursor}")
    run_log_size = _run_log_size(run_dir)
    state_lines: dict[str, list[dict]] = {}
    send_lines: dict[str, list[dict]] = {}
    positions: dict[str, FilePosition] = {}
    for kind in LINE_KINDS:
        path = run_dir / RECORD_FILE_NAMES[kind]
        if kind == "run" and run_log_size is None:
            state_lines[kind], send_lines[kind] = [], []
            positions[kind] = _position_without_file(cursor_of[kind])
            continue
        end = run_log_size if kind == "run" else None
        state_lines[kind], send_lines[kind], positions[kind] = _open_file(
            path, cursor_of[kind], end
        )
    advisors, stream_offsets = _open_advisors(run_dir)
    read = LiveRead(
        state_lines=state_lines,
        send_lines=send_lines,
        advisors=advisors,
        run_log_mtime=read_run_log_mtime(run_dir),
    )
    events, records, finished = apply_read(LiveRecords(), read, now)
    state = LiveState(
        run_dir=run_dir,
        positions=positions,
        stream_offsets=stream_offsets,
        records=records,
        finished=finished,
    )
    return LivePoll(events=events, state=state, finished=finished)


def poll_live(state: LiveState, now: float) -> LivePoll:
    """2 回目以降の読み取りを行い、前の読み取りから後に追記された完全な行のイベントと、次の状態を返す。

    渡した state は変えない (ファイルが同じなら、同じ state で 2 回呼ぶと同じ結果になる)。finished の読み取りの
    state を渡すと、読まずに LivePoll([], state, True) を返す。
    """
    if state.finished:
        return LivePoll(events=[], state=state, finished=True)
    run_dir = state.run_dir
    run_log_size = _run_log_size(run_dir)
    send_lines: dict[str, list[dict]] = {}
    positions = dict(state.positions)
    for kind in LINE_KINDS:
        if kind == "run" and run_log_size is None:
            continue
        end = run_log_size if kind == "run" else None
        send_lines[kind], positions[kind] = _read_from(
            run_dir / RECORD_FILE_NAMES[kind], positions[kind], end
        )
    advisors, stream_offsets = _poll_advisors(run_dir, state)
    read = LiveRead(
        state_lines={},
        send_lines=send_lines,
        advisors=advisors,
        run_log_mtime=read_run_log_mtime(run_dir),
    )
    events, records, finished = apply_read(state.records, read, now)
    next_state = LiveState(
        run_dir=run_dir,
        positions=positions,
        stream_offsets=stream_offsets,
        records=records,
        finished=finished,
    )
    return LivePoll(events=events, state=next_state, finished=finished)


async def stream_live(
    run_dir: Path,
    cursors: LiveCursors,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    interval_s: float = LIVE_POLL_INTERVAL_S,
) -> AsyncIterator[LiveEvent]:
    """接続のイベントを順に返す非同期のジェネレーター。

    open_live を anyio.to_thread.run_sync で (スレッドで) 呼んでイベントを順に yield し、finished でなければ
    `await sleep(interval_s)` → poll_live (スレッド) → yield を繰り返す。finished になったら戻る。
    now は読み取りのたびに clock() で取る。poll_live はモジュールの属性として、呼ぶたびに名前で引いて呼ぶ
    (テストが呼んだ回数を数える関数に差し替える)。取り消し (クライアントが接続を切った) は sleep か
    スレッドの待ちで届き、それ以上は読まない。
    """
    poll = await to_thread.run_sync(open_live, run_dir, cursors, clock())
    for event in poll.events:
        yield event
    while not poll.finished:
        await sleep(interval_s)
        poll = await to_thread.run_sync(poll_live, poll.state, clock())
        for event in poll.events:
            yield event


def _run_log_size(run_dir: Path) -> int | None:
    """run_dir の run.jsonl の大きさを返す。無い・シンボリックリンク・OSError なら None。"""
    path = run_dir / RECORD_FILE_NAMES["run"]
    if path.is_symlink():
        return None
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def _read_chunk(path: Path, offset: int, end: int | None = None) -> LineChunk | None:
    """path の offset から (end があれば end の手前まで) の完全な行を読む。無い・シンボリックリンク・OSError なら None。"""
    if path.is_symlink():
        return None
    try:
        return jsonl_lines.read_complete_lines(path, offset, end)
    except OSError:
        return None


def _read_from(
    path: Path, position: FilePosition, end: int | None
) -> tuple[list[dict], FilePosition]:
    """path を position から (end があれば end の手前まで) 読み、(JSON の object の行, 次の位置) を返す。

    position.partial なら、offset の 1 バイト前から読んで最初の行 (offset をまたぐ行か、offset の直前の改行で
    終わる空の行) を読み捨てる。行を読めなければ、行なしで同じ位置を返す。
    """
    partial = position.partial and position.offset > 0
    start = position.offset - 1 if partial else position.offset
    chunk = _read_chunk(path, start, end)
    if chunk is None or not chunk.lines:
        return [], position
    lines = chunk.lines[1:] if partial else chunk.lines
    parsed = parse_lines(LineChunk(lines=lines, next_offset=chunk.next_offset))
    return parsed.records, FilePosition(offset=chunk.next_offset)


def _open_file(
    path: Path, cursor: int | None, end: int | None
) -> tuple[list[dict], list[dict], FilePosition]:
    """接続時に path を読み、(状態だけの行, 送る行, 次の位置) を返す (モジュールの docstring の接続時の位置)。"""
    if cursor is None:
        chunk = _read_chunk(path, 0, end)
        if chunk is None:
            return [], [], FilePosition(offset=0)
        return parse_lines(chunk).records, [], FilePosition(offset=chunk.next_offset)
    state_end = cursor if end is None else min(cursor, end)
    chunk = _read_chunk(path, 0, state_end)
    state_lines = parse_lines(chunk).records if chunk is not None else []
    complete_end = chunk.next_offset if chunk is not None else 0
    position = FilePosition(offset=cursor, partial=complete_end != cursor)
    send_lines, position = _read_from(path, position, end)
    return state_lines, send_lines, position


def _position_without_file(cursor: int | None) -> FilePosition:
    """接続時にファイルが無かったときの、次に読む位置 (cursor が無いか 0 なら 0、それ以外は行の途中かもしれない)。"""
    if cursor is None or cursor == 0:
        return FilePosition(offset=0)
    return FilePosition(offset=cursor, partial=True)


def _list_advisor_dirs(run_dir: Path) -> list[AdvisorRunDir] | None:
    """run_dir の advisor の実行のディレクトリーの一覧を返す。一覧を取れなければ (OSError) None。"""
    try:
        return list_advisor_run_dirs(run_dir)
    except OSError:
        return None


def _open_advisors(run_dir: Path) -> tuple[list[AdvisorRead], dict[str, int]]:
    """接続時に advisor の実行を読む。接続時にあった stream.jsonl の行はすべて状態だけの行にする。"""
    reads: list[AdvisorRead] = []
    offsets: dict[str, int] = {}
    for advisor_dir in _list_advisor_dirs(run_dir) or []:
        chunk = _read_chunk(advisor_dir.path / STREAM_FILE_NAME, 0)
        offsets[advisor_dir.name] = chunk.next_offset if chunk is not None else 0
        reads.append(
            AdvisorRead(
                advisor_dir=advisor_dir,
                state_lines=parse_lines(chunk).records if chunk is not None else [],
                send_lines=[],
                result_json=read_result_json(advisor_dir.path / RESULT_FILE_NAME),
            )
        )
    return reads, offsets


def _poll_advisors(
    run_dir: Path, state: LiveState
) -> tuple[list[AdvisorRead] | None, dict[str, int]]:
    """2 回目以降の読み取りで advisor の実行を読む。一覧を取れなければ (None, 前の位置) を返す。

    stream.jsonl は前の位置 (接続後にできた実行は 0) から読み、result.json はまだ読めていない実行だけ読む。
    """
    advisor_dirs = _list_advisor_dirs(run_dir)
    if advisor_dirs is None:
        return None, state.stream_offsets
    offsets = dict(state.stream_offsets)
    reads: list[AdvisorRead] = []
    for advisor_dir in advisor_dirs:
        offset = offsets.get(advisor_dir.name, 0)
        lines, position = _read_from(
            advisor_dir.path / STREAM_FILE_NAME, FilePosition(offset=offset), None
        )
        offsets[advisor_dir.name] = position.offset
        known = state.records.advisors.get(advisor_dir.name)
        needs_result = known is None or known.result_json is None
        reads.append(
            AdvisorRead(
                advisor_dir=advisor_dir,
                state_lines=[],
                send_lines=lines,
                result_json=(
                    read_result_json(advisor_dir.path / RESULT_FILE_NAME)
                    if needs_result
                    else None
                ),
            )
        )
    return reads, offsets
