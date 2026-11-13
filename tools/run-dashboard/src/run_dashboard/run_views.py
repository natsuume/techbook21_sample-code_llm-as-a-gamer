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

from collections.abc import Sequence

from run_dashboard.advisor_runs import AdvisorRun
from run_dashboard.run_records import RunRecords


def build_run_list_item(records: RunRecords) -> dict:
    """実行の一覧 (GET /api/runs) の 1 件を返す。

    {"run": records.name, "game": start の game, "model": start の model, "started": start の time,
     "recorded": {"dashboard": origin_mono が None でないか, "requests": has_requests_file},
     "in_progress": False}。start の行が無いか、そのキーが無ければ game・model・started は None。
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


def build_pad_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """送ったレポートの区間 API (GET /api/runs/{run}/pad) の辞書を返す。

    pad が None か origin_mono が None なら {"available": False, "items": []}。
    それ以外は {"available": True, "items": [...]}。items は経過秒 t が区間に入る pad の行 (ファイルの順) の
    {"t": mono の経過秒, "source", "state", "ok", "error"}。
    """
    raise NotImplementedError


def build_event_items(
    records: RunRecords, t_from: float | None, t_to: float | None
) -> dict:
    """run.jsonl の出来事の区間 API (GET /api/runs/{run}/events) の辞書を返す。

    run.jsonl が無いか、start の行が無ければ {"available": False, "items": []}。
    それ以外は {"available": True, "items": [...]}。items は run_events のうち、event が "decision" でなく、
    t が数で区間に入る行 (ファイルの順) の {"t": t, "event": その行の event, そのほかのキーと値}。
    壁時計の time のキーは入れない。
    """
    raise NotImplementedError


def build_note_detail(
    records: RunRecords, version: int, advisor_runs: Sequence[AdvisorRun]
) -> dict | None:
    """ゲームの情報の版の API (GET /api/runs/{run}/notes/{version}) の辞書を返す。

    古い形の実行か、note_versions.build_note_versions(records.requests, advisor_runs) にその version の版が
    無ければ None (404)。それ以外は {"version", "t", "changed", "files"} (files は版の files)。
    """
    raise NotImplementedError


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
    raise NotImplementedError
