"""API が返す辞書を、読んだ記録 (run_records.RunRecords) から組み立てる純粋な関数。

ファイルを読まない。読むのは呼び出し側 (app) が run_records と run_directory の関数で行い、結果を引数で渡す
(advisor の実行の一覧 advisor_runs、stream.jsonl の行、advisor のディレクトリーのファイルの一覧)。
時刻はすべて経過秒 (第4章の Pipeline の起点からの秒) で返す。

区間 (t_from・t_to) の共通の規則:
    項目の経過秒 t が t_from <= t <= t_to のものだけを返す。t_from が None なら下限を、t_to が None なら上限を
    設けない。t_from か t_to のどちらかが None でないとき、t が None の項目は入れない
    (どちらも None なら、t が None の項目も入れる)。

古い形の実行 (question_values.is_old_format(records.header) が真) では、問いの値・advisor の実行・ゲームの情報の版を
返さない (summary の old_format を true にし、questions・advisor_runs・note_versions を空のリストにする。
要求の API は reason を "old_format" にして available を false にし、notes と advisor の API は None (404))。
run.jsonl から求めるもの (events と、summary の decide_s_median) は返す。
"""

import statistics
from collections.abc import Sequence

from switch2_agent.action_decision import DEFAULT_THRESHOLD
from switch2_agent.frame_selection.meaningful_frames import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_VALLEY_RATIO,
)

from run_dashboard.advisor_runs import AdvisorRun, build_steps
from run_dashboard.elapsed_time import mono_elapsed
from run_dashboard.jsonl_lines import number_or_none
from run_dashboard.note_versions import build_note_versions, note_version_of_request
from run_dashboard.question_values import (
    adopted_count,
    header_question_list,
    is_old_format,
    questions_for_decision,
    request_adopted,
    request_question_values,
)
from run_dashboard.run_records import RunOverview, RunRecords

# 古い形の実行の reason
OLD_FORMAT_REASON = "old_format"
# 要求の詳細の timings に入れる、run.jsonl の decision の行の所要時間のキー
TIMING_KEYS = ("select_s", "encode_s", "decide_s", "apply_s")


def _unavailable() -> dict:
    """記録の無い種類の区間 API の辞書 {"available": False, "items": []} を返す。"""
    return {"available": False, "items": []}


def _in_range(t: float | None, t_from: float | None, t_to: float | None) -> bool:
    """経過秒 t が区間 (モジュールの docstring の区間の共通の規則) に入るなら True を返す。"""
    if t_from is None and t_to is None:
        return True
    if t is None:
        return False
    return (t_from is None or t >= t_from) and (t_to is None or t <= t_to)


def _advisor_threshold(records: RunRecords) -> tuple[float, bool]:
    """advisor の問いのしきい値と、既定値を入れたか (args に数の threshold_advisor が無いか) を返す。"""
    value = number_or_none((records.args or {}).get("threshold_advisor"))
    if value is None:
        return DEFAULT_THRESHOLD, True
    return value, False


def _arg_number(records: RunRecords, name: str, default: float) -> float:
    """args の name の値が数ならそれを、そうでなければ default を返す。"""
    value = number_or_none((records.args or {}).get(name))
    return default if value is None else value


def build_run_list_item(overview: RunOverview) -> dict:
    """実行の一覧 (GET /api/runs) の 1 件を返す。

    overview は run_records.load_run_overview の結果か、RunRecords.overview。
    {"run": overview.name, "game": start の game, "model": start の model, "started": start の time,
     "recorded": {"dashboard": origin_mono が None でないか, "requests": has_requests_file},
     "in_progress": False}。start の行が無いか、そのキーが無ければ game・model・started は None。
    """
    start = overview.start or {}
    return {
        "run": overview.name,
        "game": start.get("game"),
        "model": start.get("model"),
        "started": start.get("time"),
        "recorded": {
            "dashboard": overview.origin_mono is not None,
            "requests": overview.has_requests_file,
        },
        "in_progress": False,
    }


def build_summary(records: RunRecords, advisor_runs: Sequence[AdvisorRun]) -> dict:
    """実行の summary (GET /api/runs/{run}) を返す。advisor_runs は advisor_runs.build_advisor_runs の結果。

    キー:
        run・game・model・started   build_run_list_item と同じ
        args                        records.args (無ければ None)
        thresholds                  {"advisor": args の threshold_advisor}。args の threshold_advisor が数
                                    (bool を除く) でなければ switch2_agent.action_decision.DEFAULT_THRESHOLD (0.5)
        thresholds_assumed          thresholds に既定値を入れたら True、args の値なら False
        valley_ratio                args の valley_ratio (数でなければ
                                    switch2_agent.frame_selection.meaningful_frames.DEFAULT_VALLEY_RATIO)
        similarity_threshold        args の similarity_threshold (数でなければ同じモジュールの
                                    DEFAULT_SIMILARITY_THRESHOLD)
        questions                   question_values.header_question_list(records.header)
        old_format                  question_values.is_old_format(records.header)
        t_end                       run_events の t、要求の t_sent と t_received、frames と pad の mono の経過秒、
                                    advisor の実行の t_end のうち、数の最大値。無ければ None
        counts                      {"requests": 要求の行の数, "frames": frames の行の数 (None なら 0),
                                     "pad": pad の行の数 (None なら 0)}
        decide_s_median             run_events のうち event が "decision" の行の decide_s (数のもの) の
                                    statistics.median。無ければ None
        advisor_runs                advisor_runs の各実行の {"name", "role", "request", "t_start", "t_end",
                                    "returncode", "denials", "result"}
        note_versions               note_versions.build_note_versions(records.requests, advisor_runs) の各版の
                                    {"version", "t", "request", "initial", "changed"}
        recorded                    {"dashboard": origin_mono が None でないか, "requests": has_requests_file,
                                     "advisor_steps": has_stream_file}
        dropped                     run_events のうち event が "record_end" の最後の行の dropped。無ければ None
        skipped_lines               records.skipped_lines
    古い形の実行では old_format を True にし、questions・advisor_runs・note_versions を空のリストにする。
    """
    old_format = is_old_format(records.header)
    # 古い形の実行は advisor の実行を返さないため、t_end にも使わない
    runs = [] if old_format else list(advisor_runs)
    threshold, threshold_assumed = _advisor_threshold(records)
    list_item = build_run_list_item(records.overview)
    return {
        "run": list_item["run"],
        "game": list_item["game"],
        "model": list_item["model"],
        "started": list_item["started"],
        "args": records.args,
        "thresholds": {"advisor": threshold},
        "thresholds_assumed": threshold_assumed,
        "valley_ratio": _arg_number(records, "valley_ratio", DEFAULT_VALLEY_RATIO),
        "similarity_threshold": _arg_number(
            records, "similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
        ),
        "questions": header_question_list(records.header),
        "old_format": old_format,
        "t_end": _last_elapsed(records, runs),
        "counts": {
            "requests": len(records.requests),
            "frames": len(records.frames) if records.frames is not None else 0,
            "pad": len(records.pad) if records.pad is not None else 0,
        },
        "decide_s_median": _decide_s_median(records),
        "advisor_runs": [
            {
                "name": run.name,
                "role": run.role,
                "request": run.request,
                "t_start": run.t_start,
                "t_end": run.t_end,
                "returncode": run.returncode,
                "denials": run.denials,
                "result": run.result,
            }
            for run in runs
        ],
        "note_versions": (
            []
            if old_format
            else [
                {
                    "version": version.version,
                    "t": version.t,
                    "request": version.request,
                    "initial": version.initial,
                    "changed": version.changed,
                }
                for version in build_note_versions(records.requests, runs)
            ]
        ),
        "recorded": {
            "dashboard": records.origin_mono is not None,
            "requests": records.has_requests_file,
            "advisor_steps": records.has_stream_file,
        },
        "dropped": _last_dropped(records),
        "skipped_lines": dict(records.skipped_lines),
    }


def _last_elapsed(
    records: RunRecords, advisor_runs: Sequence[AdvisorRun]
) -> float | None:
    """summary の t_end (記録の経過秒のうち最大のもの) を返す。数の経過秒が 1 つも無ければ None。"""
    times: list[float | None] = [event.t for event in records.run_events]
    for request in records.requests:
        times.append(number_or_none(request.get("t_sent")))
        times.append(number_or_none(request.get("t_received")))
    for lines in (records.frames, records.pad):
        for line in lines or []:
            times.append(mono_elapsed(line.get("mono"), records.origin_mono))
    times.extend(run.t_end for run in advisor_runs)
    numbers = [t for t in times if t is not None]
    return max(numbers) if numbers else None


def _decide_s_median(records: RunRecords) -> float | None:
    """run.jsonl の decision の行の decide_s (数のもの) の中央値を返す。無ければ None。"""
    values = [
        value
        for value in (
            number_or_none(event.record.get("decide_s"))
            for event in records.run_events
            if event.record.get("event") == "decision"
        )
        if value is not None
    ]
    return statistics.median(values) if values else None


def _last_dropped(records: RunRecords) -> object:
    """run.jsonl の最後の record_end の行の dropped を返す。record_end の行が無ければ None。"""
    record_ends = [
        event.record
        for event in records.run_events
        if event.record.get("event") == "record_end"
    ]
    return record_ends[-1].get("dropped") if record_ends else None


def _role_names(value: object) -> object:
    """要求の行の advisor_requested・advisor_accepted の値を返す。リストならそのまま、それ以外 (null) は []。"""
    return value if isinstance(value, list) else []


def _request_item(
    request: dict, question_list: list[dict], adopted: dict[str, str | None] | None
) -> dict:
    """要求の区間 API の 1 件の項目を返す (build_request_items の docstring のキー)。

    question_list は question_values.header_question_list の結果、adopted は request_adopted の結果。
    """
    return {
        "n": request.get("number"),
        "t_sent": request.get("t_sent"),
        "t_received": request.get("t_received"),
        "result": request.get("result"),
        "action": request.get("action"),
        "advisor_requested": _role_names(request.get("advisor_requested")),
        "advisor_accepted": _role_names(request.get("advisor_accepted")),
        "p": request_question_values(question_list, request),
        "adopted_count": adopted_count(adopted),
    }


def build_request_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """要求の区間 API (GET /api/runs/{run}/requests) の辞書を返す。

    requests.jsonl が無ければ (has_requests_file が偽) {"available": False, "items": []}。
    古い形の実行なら {"available": False, "reason": "old_format", "items": []}。
    それ以外は {"available": True, "items": [...]}。items は t_sent が区間に入る要求 (ファイルの順) の
        {"n": number, "t_sent", "t_received", "result", "action",
         "advisor_requested": リストならそのまま、null なら [],
         "advisor_accepted": リストならそのまま、null なら [],
         "p": question_values.request_question_values(questions の一覧, 要求の行),
         "adopted_count": question_values.adopted_count(question_values.request_adopted(...))}
    questions の一覧は question_values.header_question_list(records.header)、adopted の判定に渡す問いは
    その questions_for_decision、しきい値は build_summary の thresholds の advisor の値。
    """
    if not records.has_requests_file:
        return _unavailable()
    if is_old_format(records.header):
        return {"available": False, "reason": OLD_FORMAT_REASON, "items": []}
    question_list = header_question_list(records.header)
    questions = questions_for_decision(question_list)
    threshold, _ = _advisor_threshold(records)
    items = [
        _request_item(
            request, question_list, request_adopted(questions, request, threshold)
        )
        for request in records.requests
        if _in_range(number_or_none(request.get("t_sent")), t_from, t_to)
    ]
    return {"available": True, "items": items}


def build_request_detail(
    records: RunRecords, n: int, advisor_runs: Sequence[AdvisorRun]
) -> dict | None:
    """要求の詳細 API (GET /api/runs/{run}/requests/{n}) の辞書を返す。

    number が n の要求の行が無ければ None (404)。古い形の実行なら {"available": False, "reason": "old_format"}。
    それ以外は、build_request_items の 1 件の項目に次のキーを足したもの:
        images                    要求の行の images の各要素の {"label", "t", "bytes", "thumb", "file"}
                                  (thumb と file は無いか null なら None)
        timings                   run_events のうち event が "decision" で number が n の行の
                                  {"select_s", "encode_s", "decide_s", "apply_s"} (行が無ければ 4 つとも None)
        payload_bytes             同じ行の payload_bytes (無ければ None)
        history_text・previous_operation_text   要求の行の同名の値
        note_version              note_versions.note_version_of_request(
                                  build_note_versions(records.requests, advisor_runs), n, records.requests)
        probabilities・choices・advisor_probabilities   要求の行の同名の値 (応答が無ければ None)
        adopted                   question_values.request_adopted の結果 (問いの name → 値。応答が無ければ None)
    """
    request = next((line for line in records.requests if line.get("number") == n), None)
    if request is None:
        return None
    if is_old_format(records.header):
        return {"available": False, "reason": OLD_FORMAT_REASON}
    question_list = header_question_list(records.header)
    threshold, _ = _advisor_threshold(records)
    adopted = request_adopted(questions_for_decision(question_list), request, threshold)
    decision = next(
        (
            event.record
            for event in records.run_events
            if event.record.get("event") == "decision"
            and event.record.get("number") == n
        ),
        None,
    )
    images = request.get("images")
    versions = build_note_versions(records.requests, advisor_runs)
    return {
        **_request_item(request, question_list, adopted),
        "images": [
            {
                "label": image.get("label"),
                "t": image.get("t"),
                "bytes": image.get("bytes"),
                "thumb": image.get("thumb"),
                "file": image.get("file"),
            }
            for image in (images if isinstance(images, list) else [])
            if isinstance(image, dict)
        ],
        "timings": {
            key: decision.get(key) if decision is not None else None
            for key in TIMING_KEYS
        },
        "payload_bytes": (
            decision.get("payload_bytes") if decision is not None else None
        ),
        "history_text": request.get("history_text"),
        "previous_operation_text": request.get("previous_operation_text"),
        "note_version": note_version_of_request(versions, n, records.requests),
        "probabilities": request.get("probabilities"),
        "choices": request.get("choices"),
        "advisor_probabilities": request.get("advisor_probabilities"),
        "adopted": adopted,
    }


def _frame_event(event: object, origin_mono: float) -> object:
    """frames.jsonl の行の event を返す。object なら、candidate_mono を経過秒の candidate_t に置き換える。

    ほかのキーと、キーの並びはそのまま。object でない (null など) なら、そのまま返す。
    """
    if not isinstance(event, dict):
        return event
    return {
        ("candidate_t" if key == "candidate_mono" else key): (
            mono_elapsed(value, origin_mono) if key == "candidate_mono" else value
        )
        for key, value in event.items()
    }


def build_frame_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """フレームの判定の区間 API (GET /api/runs/{run}/frames) の辞書を返す。

    frames が None か origin_mono が None なら {"available": False, "items": []}。
    それ以外は {"available": True, "items": [...]}。items は経過秒 t が区間に入る frames の行 (ファイルの順) の
        {"t": mono の経過秒, "sharpness", "diff", "phase", "peak_t": peak_mono の経過秒, "peak_sharpness",
         "ref", "event", "thumb"}
    経過秒は elapsed_time.mono_elapsed(値, origin_mono)。event が object なら、その candidate_mono を
    経過秒の candidate_t に置き換えた辞書 (ほかのキーはそのまま) にする。
    """
    origin_mono = records.origin_mono
    if records.frames is None or origin_mono is None:
        return _unavailable()
    items = []
    for frame in records.frames:
        t = mono_elapsed(frame.get("mono"), origin_mono)
        if not _in_range(t, t_from, t_to):
            continue
        items.append(
            {
                "t": t,
                "sharpness": frame.get("sharpness"),
                "diff": frame.get("diff"),
                "phase": frame.get("phase"),
                "peak_t": mono_elapsed(frame.get("peak_mono"), origin_mono),
                "peak_sharpness": frame.get("peak_sharpness"),
                "ref": frame.get("ref"),
                "event": _frame_event(frame.get("event"), origin_mono),
                "thumb": frame.get("thumb"),
            }
        )
    return {"available": True, "items": items}


def build_pad_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """送ったレポートの区間 API (GET /api/runs/{run}/pad) の辞書を返す。

    pad が None か origin_mono が None なら {"available": False, "items": []}。
    それ以外は {"available": True, "items": [...]}。items は経過秒 t が区間に入る pad の行 (ファイルの順) の
    {"t": mono の経過秒, "source", "state", "ok", "error"}。
    """
    origin_mono = records.origin_mono
    if records.pad is None or origin_mono is None:
        return _unavailable()
    items = []
    for report in records.pad:
        t = mono_elapsed(report.get("mono"), origin_mono)
        if not _in_range(t, t_from, t_to):
            continue
        items.append(
            {
                "t": t,
                "source": report.get("source"),
                "state": report.get("state"),
                "ok": report.get("ok"),
                "error": report.get("error"),
            }
        )
    return {"available": True, "items": items}


def build_event_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """run.jsonl の出来事の区間 API (GET /api/runs/{run}/events) の辞書を返す。

    run.jsonl が無いか、start の行が無ければ {"available": False, "items": []}。
    それ以外は {"available": True, "items": [...]}。items は run_events のうち、event が "decision" でなく、
    t が数で区間に入る行 (ファイルの順) の {"t": t, "event": その行の event, そのほかのキーと値}。
    壁時計の time のキーは入れない。行に t のキーがあっても、経過秒の t で置き換える。
    """
    # run.jsonl が無い実行は、start の行も無い
    if records.start is None:
        return _unavailable()
    items = []
    for event in records.run_events:
        record = event.record
        if record.get("event") == "decision" or event.t is None:
            continue
        if not _in_range(event.t, t_from, t_to):
            continue
        item = {"t": event.t, "event": record.get("event")}
        item.update(
            (key, value)
            for key, value in record.items()
            if key not in ("time", "event", "t")
        )
        items.append(item)
    return {"available": True, "items": items}


def build_note_detail(
    records: RunRecords, version: int, advisor_runs: Sequence[AdvisorRun]
) -> dict | None:
    """ゲームの情報の版の API (GET /api/runs/{run}/notes/{version}) の辞書を返す。

    古い形の実行か、note_versions.build_note_versions(records.requests, advisor_runs) にその version の版が
    無ければ None (404)。それ以外は {"version", "t", "changed", "files"} (files は版の files)。
    """
    if is_old_format(records.header):
        return None
    found = next(
        (
            note_version
            for note_version in build_note_versions(records.requests, advisor_runs)
            if note_version.version == version
        ),
        None,
    )
    if found is None:
        return None
    return {
        "version": found.version,
        "t": found.t,
        "changed": found.changed,
        "files": found.files,
    }


def build_advisor_detail(
    records: RunRecords,
    name: str,
    advisor_runs: Sequence[AdvisorRun],
    stream_records: Sequence[dict] | None,
    files: Sequence[dict],
) -> dict | None:
    """advisor の実行の API (GET /api/runs/{run}/advisor/{name}) の辞書を返す。

    stream_records は、その実行の stream.jsonl の行 (run_records.read_advisor_stream の結果。ファイルが無ければ None)、
    files は、その実行のディレクトリーのファイルの一覧 (run_directory.list_advisor_files の結果)。
    古い形の実行か、advisor_runs に name の実行が無ければ None (404)。それ以外は
        {"name", "role", "request", "t_start", "t_end", "status", "result_json" (AdvisorRun の同名の値),
         "steps_available": stream_records が None でないか,
         "steps": advisor_runs.build_steps(stream_records, records.origin_mono, 実行の denied_tool_use_ids)
                  (stream_records が None なら []),
         "files": files のリスト}
    """
    if is_old_format(records.header):
        return None
    run = next((item for item in advisor_runs if item.name == name), None)
    if run is None:
        return None
    return {
        "name": run.name,
        "role": run.role,
        "request": run.request,
        "t_start": run.t_start,
        "t_end": run.t_end,
        "status": run.status,
        "result_json": run.result_json,
        "steps_available": stream_records is not None,
        "steps": (
            build_steps(stream_records, records.origin_mono, run.denied_tool_use_ids)
            if stream_records is not None
            else []
        ),
        "files": list(files),
    }
