"""実行中の記録を 1 回読んだ行 (LiveRead) から、SSE で送るイベント (live_events) と次の読み取りの状態を作る。

ファイルを読むのは live_reader で、このモジュールは読んだ行を受け取って状態にためる純粋な関数を持つ (I/O を持たない)。
1 回の読み取りの行は、状態だけに入れる行 (接続時の cursor より前の行) と、送る行 (cursor より後の行と、接続後に
追記された行) に分かれる。どちらも状態に入れ、送る行だけをイベントにする。渡した状態は変えず、新しい状態を返す。

状態に入れるもの:
    requests.jsonl  event が "header" の最初の行を header にする。event が "request" の行を
                    run_records.lighten_request_lines(..., previous=前の要求の行) で軽くして足し、
                    run_records.add_received_time で受信時刻を足す。t_sent・t_received (数のもの) を経過秒の最大値に入れる
    run.jsonl       行を足す。start の行 (event が "start" の最初の行) を読むまでは経過秒を求めない (t は None)。
                    start の行を読んだら run_records.start_run_log_clock で時計を作り、それまでの行の t を、start より
                    前は elapsed_before_start、start は 0.0 で求める。以後はファイルの順に advance_run_log_clock。
                    decision の行の decide_s (数のもの)、record_end の有無、経過秒の最大値を持つ
    frames・pad     行の数 (JSON の object の行の数) と、数の mono (bool を除く) の最大値を持つ。start の行の
                    origin_mono が数なら、mono の最大値 - origin_mono を経過秒の最大値に使う
    advisor         実行ごとに stream.jsonl の全行 (状態だけの行を含む)、送った step の数、result.json

イベント:
    request・run・frame・pad・advisor_step は、start の行を読んだ後だけ作る。start の行を読む前の読み取りは送る行を
    ためておき、start の行を読んだ読み取りでファイルの順に作る (start の行の無い実行では作らない)。
    advisor_run・note_version・stats・end は、start の行によらず、読み取りのたびに下の規則で作る (stats の件数は
    start の行を読む前も数える)。
    request       送る要求の行ごとに run_views.build_request_item(行, run_views.request_item_context(header,
                  start の args))。古い形の実行 (question_values.is_old_format) では作らない
    run           送る run.jsonl の行ごとに run_views.build_event_item(RunEvent) (None の行は作らない)
    frame・pad    送る行ごとに run_views.build_frame_item・build_pad_item。origin_mono が None なら作らない
    advisor_step  古い形でなければ、advisor のディレクトリーの順に、advisor_runs.build_steps(その実行の stream.jsonl
                  の全行, origin_mono, その実行の denied_tool_use_ids) のうちまだ送っていない分 (状態だけの行の step は
                  送ったものとして数える) を {"name": 実行の名前, "t", "kind", "tool", "summary"} にする。kind は
                  送った時点の値 (後で変わっても送り直さない)
    advisor_run   advisor_runs = [] if 古い形 else advisor_runs.build_advisor_runs(一覧, 要求の行, RunEvent, 状態の
                  result.json を返す関数) の各実行の run_views.summary_advisor_run。前に送った値 (名前で引く) と
                  違うものと新しいものだけ (接続の最初の読み取りでは、前に送った値が無いのですべて)。並びは advisor_runs の順
    note_version  [] if 古い形 else note_versions.build_note_versions(要求の行, advisor_runs) の各版の
                  run_views.summary_note_version。前に送った値 (番号で引く) と違うものと新しいものだけ。番号の昇順
    stats         {"t_end": run_views.max_elapsed([経過秒の最大値] + [各 advisor_run の t_end]),
                   "counts": {"requests": 要求の行の数, "frames": frames の行の数, "pad": pad の行の数},
                   "decide_s_median": decide_s の statistics.median (無ければ None)}。前に送った値と違うときだけ
                  (接続の最初の読み取りではいつも)
    end           run_activity.is_in_progress(run.jsonl の更新時刻, record_end の有無, now) が偽なら
                  {"record_end": record_end の有無} を最後に足し、読み取りを終える
    前に送った値との比較は live_events.json_compatible にした値で行う (有限でない数を含む値を、毎回送り直さないため)。
1 回の読み取りの events の順は request → run → frame → pad → advisor_step → advisor_run → note_version →
stats → end。同じ種類の中はファイルの順 (advisor_step はディレクトリーの順の中でファイルの順)。
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from switch2_agent.advisors.claude_code_headless import RESULT_FILE_NAME

from run_dashboard.advisor_runs import AdvisorRun, build_advisor_runs, build_steps
from run_dashboard.elapsed_time import mono_elapsed
from run_dashboard.jsonl_lines import is_json_number, number_or_none
from run_dashboard.live_events import LiveEvent, json_compatible
from run_dashboard.note_versions import build_note_versions
from run_dashboard.question_values import is_old_format
from run_dashboard.run_activity import has_record_end_line, is_in_progress
from run_dashboard.run_directory import AdvisorRunDir
from run_dashboard.run_records import (
    RunEvent,
    RunLogClock,
    add_received_time,
    advance_run_log_clock,
    elapsed_before_start,
    lighten_request_lines,
    start_run_log_clock,
)
from run_dashboard.run_views import (
    build_event_item,
    build_frame_item,
    build_pad_item,
    build_request_item,
    max_elapsed,
    request_item_context,
    summary_advisor_run,
    summary_note_version,
)

# 行を状態にためる JSON Lines の種類 (この順に状態に入れる。要求の行を run.jsonl の行より先に入れ、decision の行の
# 起点にする要求の受信時刻をそろえる)
LINE_KINDS = ("requests", "run", "frames", "pad")
# mono の最大値と件数だけを持つ種類
MonoKind = Literal["frames", "pad"]


@dataclass(frozen=True)
class AdvisorRead:
    """1 回の読み取りで、advisor の実行 1 つについて読んだもの。

    advisor_dir   実行のディレクトリー (run_directory.list_advisor_run_dirs の 1 件)
    state_lines   状態だけに入れる stream.jsonl の行 (接続時にあったファイルの行。その step は送ったものとして数える)
    send_lines    送る stream.jsonl の行 (ファイルの順)
    result_json   この読み取りで読めた result.json の中身 (読まなかったか、読めなかったら None)
    """

    advisor_dir: AdvisorRunDir
    state_lines: list[dict]
    send_lines: list[dict]
    result_json: dict | None


@dataclass(frozen=True)
class LiveRead:
    """1 回の読み取りで読んだもの (live_reader が作る)。

    state_lines    LINE_KINDS の種類 → 状態だけに入れる行 (ファイルの順。接続の最初の読み取りだけにある)
    send_lines     LINE_KINDS の種類 → 送る行 (ファイルの順)。無い種類は行なし
    advisors       advisor の実行のディレクトリーの一覧の順の AdvisorRead。一覧を取れなかったら None (前の一覧を使う)
    run_log_mtime  run.jsonl の更新時刻 (無ければ None)
    """

    state_lines: Mapping[str, list[dict]]
    send_lines: Mapping[str, list[dict]]
    advisors: list[AdvisorRead] | None
    run_log_mtime: float | None


@dataclass(frozen=True)
class AdvisorStream:
    """advisor の実行 1 つの、読んだ stream.jsonl の行と送った step の数。

    advisor_dir    実行のディレクトリー
    lines          読んだ stream.jsonl の全行 (ファイルの順)
    sent_steps     送った (状態だけの行の分を含む) step の数
    stepped_lines  sent_steps を求めたときの lines の数 (同じなら、新しい step は無い)
    result_json    読めた result.json の中身 (まだ読めていなければ None)
    """

    advisor_dir: AdvisorRunDir
    lines: list[dict]
    sent_steps: int
    stepped_lines: int
    result_json: dict | None


@dataclass(frozen=True)
class LiveRecords:
    """1 つの接続で読んだ記録をためた状態 (モジュールの docstring の「状態に入れるもの」と、送った値)。

    header              requests.jsonl の event が "header" の最初の行 (無ければ None)
    requests            軽くした要求の行 (ファイルの順)
    received_times      要求の number → 受信時刻 (run_records.add_received_time)
    sent_requests       requests のうち、送った (か状態だけの) 行の数 (先頭からの数)
    start               run.jsonl の start の行 (まだ読んでいなければ None)
    clock               start の行より後の行の経過秒を求める時計 (start の行の time が読めなければ None)
    run_events          run.jsonl の行と経過秒 (ファイルの順。start の行を読む前の行の t は None)
    sent_run_events     run_events のうち、送った (か状態だけの) 行の数 (先頭からの数)
    decide_s            decision の行の decide_s (数のもの)
    has_record_end      run.jsonl に record_end の行があるか
    max_t               要求の t_sent・t_received と run.jsonl の行の t のうちの最大 (無ければ None)
    line_counts         frames・pad の行の数
    mono_max            frames・pad の数の mono の最大値 (無ければ None)
    pending             frames・pad の、start の行を読む前に読んだ送る行
    advisors            advisor の実行の名前 → AdvisorStream
    advisor_dirs        最後に取れた advisor の実行のディレクトリーの一覧 (一覧の順)
    sent_advisor_runs   advisor の実行の名前 → 前に送った advisor_run の data (json_compatible にした値)
    sent_note_versions  版の番号 → 前に送った note_version の data (json_compatible にした値)
    sent_stats          前に送った stats の data (json_compatible にした値。まだ送っていなければ None)
    """

    header: dict | None = None
    requests: list[dict] = field(default_factory=list)
    received_times: dict[object, float] = field(default_factory=dict)
    sent_requests: int = 0
    start: dict | None = None
    clock: RunLogClock | None = None
    run_events: list[RunEvent] = field(default_factory=list)
    sent_run_events: int = 0
    decide_s: list[float] = field(default_factory=list)
    has_record_end: bool = False
    max_t: float | None = None
    line_counts: dict[str, int] = field(default_factory=lambda: {"frames": 0, "pad": 0})
    mono_max: dict[str, object] = field(
        default_factory=lambda: {"frames": None, "pad": None}
    )
    pending: dict[str, list[dict]] = field(
        default_factory=lambda: {"frames": [], "pad": []}
    )
    advisors: dict[str, AdvisorStream] = field(default_factory=dict)
    advisor_dirs: list[AdvisorRunDir] = field(default_factory=list)
    sent_advisor_runs: dict[str, object] = field(default_factory=dict)
    sent_note_versions: dict[object, object] = field(default_factory=dict)
    sent_stats: object | None = None


def apply_read(
    records: LiveRecords, read: LiveRead, now: float
) -> tuple[list[LiveEvent], LiveRecords, bool]:
    """1 回の読み取り read を records に入れ、(送るイベント, 次の状態, end を送ったか) を返す。

    LINE_KINDS の順に、種類ごとに状態だけの行、送る行の順に入れ、advisor の行を入れてから、モジュールの docstring の
    規則でイベントを作る。now は今の時刻 (end の判定に使う)。records は変えない。
    """
    for kind in LINE_KINDS:
        records = _add_lines(records, kind, read.state_lines.get(kind, []), send=False)
        records = _add_lines(records, kind, read.send_lines.get(kind, []), send=True)
    if read.advisors is not None:
        records = _add_advisor_reads(records, read.advisors)
    return _take_events(records, read.run_log_mtime, now)


def _add_lines(
    records: LiveRecords, kind: str, lines: list[dict], send: bool
) -> LiveRecords:
    """種類 kind の行 lines を状態に入れた新しい状態を返す。send が偽なら、その行を送ったものとして数える。"""
    if not lines:
        return records
    if kind == "requests":
        return _add_request_lines(records, lines, send)
    if kind == "run":
        return _add_run_lines(records, lines, send)
    if kind in ("frames", "pad"):
        return _add_mono_lines(records, kind, lines, send)
    raise ValueError(f"unknown kind: {kind}")


def _add_request_lines(
    records: LiveRecords, lines: list[dict], send: bool
) -> LiveRecords:
    """requests.jsonl の行を入れる (header・軽くした要求の行・受信時刻・経過秒の最大値)。"""
    previous = records.requests[-1] if records.requests else None
    header = records.header
    received_times = dict(records.received_times)
    max_t = records.max_t
    added: list[dict] = []
    for line in lighten_request_lines(lines, previous):
        event = line.get("event")
        if event == "header" and header is None:
            header = line
        elif event == "request":
            added.append(line)
            add_received_time(received_times, line)
            max_t = max_elapsed(
                [
                    max_t,
                    number_or_none(line.get("t_sent")),
                    number_or_none(line.get("t_received")),
                ]
            )
    return replace(
        records,
        header=header,
        requests=records.requests + added,
        received_times=received_times,
        sent_requests=records.sent_requests + (0 if send else len(added)),
        max_t=max_t,
    )


def _add_run_lines(records: LiveRecords, lines: list[dict], send: bool) -> LiveRecords:
    """run.jsonl の行を入れる (RunEvent・start の行と時計・decide_s・record_end の有無・経過秒の最大値)。"""
    run_events = list(records.run_events)
    start = records.start
    clock = records.clock
    max_t = records.max_t
    for line in lines:
        if start is None:
            run_events.append(RunEvent(t=None, record=line))
            if line.get("event") == "start":
                start = line
                clock = start_run_log_clock(line)
                if clock is not None:
                    run_events = _timed_until_start(run_events, clock)
                    max_t = max_elapsed([max_t, *(event.t for event in run_events)])
            continue
        if clock is None:
            run_events.append(RunEvent(t=None, record=line))
            continue
        t, clock = advance_run_log_clock(clock, line, records.received_times)
        run_events.append(RunEvent(t=t, record=line))
        max_t = max_elapsed([max_t, t])
    decide_s = [
        value
        for value in (
            number_or_none(line.get("decide_s"))
            for line in lines
            if line.get("event") == "decision"
        )
        if value is not None
    ]
    return replace(
        records,
        start=start,
        clock=clock,
        run_events=run_events,
        sent_run_events=records.sent_run_events + (0 if send else len(lines)),
        decide_s=records.decide_s + decide_s,
        has_record_end=records.has_record_end or has_record_end_line(lines),
        max_t=max_t,
    )


def _timed_until_start(
    run_events: list[RunEvent], clock: RunLogClock
) -> list[RunEvent]:
    """最後が start の行の run_events に、経過秒を付けた新しいリストを返す (start より前の行と start の行)。"""
    timed = [
        RunEvent(t=elapsed_before_start(clock, event.record), record=event.record)
        for event in run_events[:-1]
    ]
    timed.append(RunEvent(t=0.0, record=run_events[-1].record))
    return timed


def _add_mono_lines(
    records: LiveRecords, kind: MonoKind, lines: list[dict], send: bool
) -> LiveRecords:
    """frames.jsonl か pad.jsonl の行を入れる (行の数・mono の最大値・送る行)。"""
    mono_max = records.mono_max[kind]
    for line in lines:
        mono = line.get("mono")
        if is_json_number(mono) and (mono_max is None or mono > mono_max):
            mono_max = mono
    return replace(
        records,
        line_counts={
            **records.line_counts,
            kind: records.line_counts[kind] + len(lines),
        },
        mono_max={**records.mono_max, kind: mono_max},
        pending=(
            {**records.pending, kind: records.pending[kind] + lines}
            if send
            else records.pending
        ),
    )


def _add_advisor_reads(
    records: LiveRecords, reads: Sequence[AdvisorRead]
) -> LiveRecords:
    """advisor の実行ごとに読んだ stream.jsonl の行と result.json を入れ、一覧を reads の順にする。"""
    advisors = dict(records.advisors)
    for read in reads:
        name = read.advisor_dir.name
        stream = advisors.get(name) or AdvisorStream(
            advisor_dir=read.advisor_dir,
            lines=[],
            sent_steps=0,
            stepped_lines=0,
            result_json=None,
        )
        lines = stream.lines
        sent_steps = stream.sent_steps
        stepped_lines = stream.stepped_lines
        if read.state_lines:
            lines = lines + read.state_lines
            # step の数は origin_mono と拒否の id によらないため、状態だけの行の step の数をここで数える
            sent_steps = len(build_steps(lines, None, frozenset()))
            stepped_lines = len(lines)
        if read.send_lines:
            lines = lines + read.send_lines
        advisors[name] = AdvisorStream(
            advisor_dir=read.advisor_dir,
            lines=lines,
            sent_steps=sent_steps,
            stepped_lines=stepped_lines,
            result_json=(
                stream.result_json
                if stream.result_json is not None
                else read.result_json
            ),
        )
    return replace(
        records,
        advisors=advisors,
        advisor_dirs=[read.advisor_dir for read in reads],
    )


def _take_events(
    records: LiveRecords, run_log_mtime: float | None, now: float
) -> tuple[list[LiveEvent], LiveRecords, bool]:
    """records から送るイベントを作り、(イベント, 送った後の状態, end を送ったか) を返す。"""
    events: list[LiveEvent] = []
    old_format = is_old_format(records.header)
    start = records.start
    origin_mono = number_or_none(start.get("origin_mono")) if start else None
    if start is not None:
        line_events, records = _line_events(records, start, origin_mono, old_format)
        events.extend(line_events)

    advisor_runs = [] if old_format else _advisor_runs(records)
    if start is not None and not old_format:
        step_events, records = _advisor_step_events(records, origin_mono, advisor_runs)
        events.extend(step_events)

    sent_advisor_runs = dict(records.sent_advisor_runs)
    for run in advisor_runs:
        data = summary_advisor_run(run)
        if _changed(sent_advisor_runs, run.name, data):
            events.append(LiveEvent(kind="advisor_run", data=data))
            sent_advisor_runs[run.name] = json_compatible(data)

    sent_note_versions = dict(records.sent_note_versions)
    versions = [] if old_format else build_note_versions(records.requests, advisor_runs)
    for version in versions:
        data = summary_note_version(version)
        if _changed(sent_note_versions, version.version, data):
            events.append(LiveEvent(kind="note_version", data=data))
            sent_note_versions[version.version] = json_compatible(data)

    stats = _stats(records, origin_mono, advisor_runs)
    sent_stats = records.sent_stats
    if json_compatible(stats) != sent_stats:
        events.append(LiveEvent(kind="stats", data=stats))
        sent_stats = json_compatible(stats)

    finished = not is_in_progress(run_log_mtime, records.has_record_end, now)
    if finished:
        events.append(
            LiveEvent(kind="end", data={"record_end": records.has_record_end})
        )
    records = replace(
        records,
        sent_advisor_runs=sent_advisor_runs,
        sent_note_versions=sent_note_versions,
        sent_stats=sent_stats,
    )
    return events, records, finished


def _line_events(
    records: LiveRecords, start: dict, origin_mono: float | None, old_format: bool
) -> tuple[list[LiveEvent], LiveRecords]:
    """start の行を読んだ後の、request・run・frame・pad のイベントと、送った後の状態を返す。"""
    events: list[LiveEvent] = []
    unsent_requests = records.requests[records.sent_requests :]
    if unsent_requests and not old_format:
        args = start.get("args")
        context = request_item_context(
            records.header, args if isinstance(args, dict) else None
        )
        events.extend(
            LiveEvent(kind="request", data=build_request_item(request, context))
            for request in unsent_requests
        )
    for event in records.run_events[records.sent_run_events :]:
        item = build_event_item(event)
        if item is not None:
            events.append(LiveEvent(kind="run", data=item))
    if origin_mono is not None:
        events.extend(
            LiveEvent(kind="frame", data=build_frame_item(frame, origin_mono))
            for frame in records.pending["frames"]
        )
        events.extend(
            LiveEvent(kind="pad", data=build_pad_item(report, origin_mono))
            for report in records.pending["pad"]
        )
    records = replace(
        records,
        sent_requests=len(records.requests),
        sent_run_events=len(records.run_events),
        pending={"frames": [], "pad": []},
    )
    return events, records


def _advisor_runs(records: LiveRecords) -> list[AdvisorRun]:
    """records の advisor の実行の一覧を、状態に持つ result.json で作る (advisor_runs.build_advisor_runs)。"""
    result_jsons = {
        stream.advisor_dir.path / RESULT_FILE_NAME: stream.result_json
        for stream in records.advisors.values()
    }
    return build_advisor_runs(
        records.advisor_dirs, records.requests, records.run_events, result_jsons.get
    )


def _advisor_step_events(
    records: LiveRecords, origin_mono: float | None, advisor_runs: Sequence[AdvisorRun]
) -> tuple[list[LiveEvent], LiveRecords]:
    """まだ送っていない advisor_step のイベントと、送った後の状態を返す (ディレクトリーの順の中でファイルの順)。"""
    denied = {run.name: run.denied_tool_use_ids for run in advisor_runs}
    advisors = dict(records.advisors)
    events: list[LiveEvent] = []
    for advisor_dir in records.advisor_dirs:
        stream = advisors.get(advisor_dir.name)
        if stream is None or len(stream.lines) == stream.stepped_lines:
            continue
        steps = build_steps(
            stream.lines, origin_mono, denied.get(advisor_dir.name, frozenset())
        )
        events.extend(
            LiveEvent(kind="advisor_step", data={"name": advisor_dir.name, **step})
            for step in steps[stream.sent_steps :]
        )
        advisors[advisor_dir.name] = replace(
            stream, sent_steps=len(steps), stepped_lines=len(stream.lines)
        )
    return events, replace(records, advisors=advisors)


def _stats(
    records: LiveRecords, origin_mono: float | None, advisor_runs: Sequence[AdvisorRun]
) -> dict:
    """stats の data {"t_end", "counts", "decide_s_median"} を返す (summary の同名の値と同じ規則)。"""
    return {
        "t_end": max_elapsed(
            [
                records.max_t,
                mono_elapsed(records.mono_max["frames"], origin_mono),
                mono_elapsed(records.mono_max["pad"], origin_mono),
                *(run.t_end for run in advisor_runs),
            ]
        ),
        "counts": {
            "requests": len(records.requests),
            "frames": records.line_counts["frames"],
            "pad": records.line_counts["pad"],
        },
        "decide_s_median": (
            statistics.median(records.decide_s) if records.decide_s else None
        ),
    }


def _changed(sent: Mapping[object, object], key: object, data: dict) -> bool:
    """key の data が、前に送った値 sent[key] (json_compatible にした値) と違うか、まだ送っていなければ True。"""
    return key not in sent or sent[key] != json_compatible(data)
