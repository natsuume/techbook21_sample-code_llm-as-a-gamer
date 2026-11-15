import json
import os

import pytest
from switch2_agent.action_decision import Thresholds, decide_action
from switch2_agent.decision_request import build_decision_request
from switch2_agent.questions import build_questions
from switch2_agent.request_log import RequestLogEntry, build_request_record
from synthetic_runs import (
    CONTROLS_RUN_NAME,
    FIRST_NOTES,
    STALE_RUN_LOG_MTIME,
    STRATEGY_RUN_NAME,
    UNTERMINATED_FRAME_LINE,
    encode_frame,
    make_decision,
    make_frame,
)

from run_dashboard.record_cache import JsonLinesCache
from run_dashboard.run_directory import AdvisorRunDir, list_run_names
from run_dashboard.run_records import (
    LOAD_PARTS,
    RECORD_KINDS,
    RunLogClock,
    add_received_time,
    advance_run_log_clock,
    elapsed_before_start,
    lighten_request_lines,
    list_run_overviews,
    load_run,
    load_run_overview,
    read_advisor_stream,
    read_result_json,
    start_run_log_clock,
)

NO_SKIPPED_LINES = {"run": 0, "requests": 0, "frames": 0, "pad": 0, "advisor_steps": 0}
SYNTHETIC_RUN_ATTRIBUTES = (
    "dashboard",
    "no_dashboard",
    "save_requests",
    "broken",
    "no_answer_type",
    "single_advisor_question",
)


def test_list_run_overviews_matches_load_run_overview(runs):
    expected = [
        load_run_overview(runs.runs_dir, name) for name in list_run_names(runs.runs_dir)
    ]
    assert list_run_overviews(runs.runs_dir) == expected
    assert list_run_overviews(runs.runs_dir, JsonLinesCache()) == expected


def test_symlinked_record_files_and_advisor_dirs_are_treated_as_missing(tmp_path):
    # 実行のディレクトリーの外にある記録へのシンボリックリンクは、たどらずに無いものとして扱う
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_run_log = outside / "run.jsonl"
    outside_run_log.write_text(
        '{"time": "10:00:00.000", "event": "start", "game": "secret"}\n',
        encoding="utf-8",
    )
    outside_result = outside / "result.json"
    outside_result.write_text('{"returncode": 0, "secret": true}', encoding="utf-8")
    outside_stream = outside / "stream.jsonl"
    outside_stream.write_text('{"mono": 1.0, "raw": "secret"}\n', encoding="utf-8")
    outside_advisor = outside / "advisor-tips-001"
    outside_advisor.mkdir()
    (outside_advisor / "result.json").write_text('{"returncode": 0}', encoding="utf-8")

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "20261101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "run.jsonl").symlink_to(outside_run_log)
    (run_dir / "requests.jsonl").symlink_to(outside_stream)
    advisor_dir = run_dir / "advisor-strategy-001"
    advisor_dir.mkdir()
    (advisor_dir / "result.json").symlink_to(outside_result)
    (advisor_dir / "stream.jsonl").symlink_to(outside_stream)
    (run_dir / "advisor-tips-001").symlink_to(outside_advisor, target_is_directory=True)

    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert records.start is None
    assert records.has_requests_file is False
    assert records.has_stream_file is False
    assert [item.name for item in records.advisor_dirs] == ["advisor-strategy-001"]
    assert read_result_json(advisor_dir / "result.json") is None
    assert read_advisor_stream(records.advisor_dirs[0]) is None
    overview = load_run_overview(runs_dir, "20261101-000000", JsonLinesCache())
    assert overview is not None
    assert overview.start is None
    assert overview.has_requests_file is False


def test_record_kinds():
    assert RECORD_KINDS == ("run", "requests", "frames", "pad", "advisor_steps")


@pytest.mark.parametrize("name", ["20991231-000000", "..", "", "a/b"])
def test_load_run_returns_none_for_unknown_run(runs, name):
    assert load_run(runs.runs_dir, name) is None


@pytest.mark.parametrize("name", ["20991231-000000", "..", "", "a/b"])
def test_load_run_overview_returns_none_for_unknown_run(runs, name):
    assert load_run_overview(runs.runs_dir, name) is None


@pytest.mark.parametrize("attribute", SYNTHETIC_RUN_ATTRIBUTES)
def test_load_run_overview_matches_overview_of_load_run(runs, attribute):
    run = getattr(runs, attribute)
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert load_run_overview(runs.runs_dir, run.name) == records.overview


def test_load_run_overview_of_run_without_requests_file(runs):
    overview = load_run_overview(runs.runs_dir, runs.no_dashboard.name)
    assert overview is not None
    assert overview.has_requests_file is False
    assert overview.origin_mono is None
    assert overview.start is not None


def test_load_run_overview_of_dashboard_run(runs):
    overview = load_run_overview(runs.runs_dir, runs.dashboard.name)
    assert overview is not None
    assert overview.has_requests_file is True
    assert overview.origin_mono == 1000.0
    assert overview.start["time"] == "23:59:50.000"


def test_load_dashboard_run(runs):
    run = runs.dashboard
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.name == run.name
    assert records.run_dir == run.run_dir
    assert records.start is not None
    assert records.start["event"] == "start"
    assert records.start["time"] == "23:59:50.000"
    assert records.origin_mono == 1000.0
    assert records.args == run.args
    assert records.args["threshold_advisor"] == 0.6
    assert [event.record["event"] for event in records.run_events] == [
        "record_error",
        "start",
        "record_error",
        "decision",
        "decision_error",
        "decision",
        "advisor",
        "decision",
        "record_error",
        "decision",
        "capture_stopped",
        "record_end",
    ]
    # start の行より前の record_error は負の秒。start の行より後でも、時刻がわずかに前 (3 ミリ秒) の行は
    # 負の秒のまま。00:00 台の行 (12.75 秒以降) は、日付をまたいだ後の decision の行を起点に求める
    assert [event.t for event in records.run_events] == pytest.approx(
        [-0.5, 0.0, -0.003, 1.25, 2.125, 3.5, 5.25, 6.75, 7.0, 12.75, 14.5, 15.0]
    )
    # 行の辞書は time を含めて読んだまま
    assert records.run_events[0].record == {
        "time": "23:59:49.500",
        "event": "record_error",
        "kind": "pad",
        "error": "No space left on device",
    }
    assert records.header is not None
    assert records.header["event"] == "header"
    assert len(records.header["questions"]) == 37
    assert [request["number"] for request in records.requests] == [1, 2, 3, 4, 5, 6]
    assert records.frames is not None
    assert [frame["mono"] for frame in records.frames] == [
        1000.5,
        1000.75,
        1001.0,
        1001.25,
        1005.5,
        1012.25,
        1013.75,
    ]
    assert records.pad is not None
    assert [report["source"] for report in records.pad] == [
        "apply",
        "tap_release",
        "neutral",
        "apply",
        "apply",
    ]
    assert records.advisor_dirs == [
        AdvisorRunDir(
            CONTROLS_RUN_NAME, "controls", 1, run.run_dir / CONTROLS_RUN_NAME
        ),
        AdvisorRunDir(
            STRATEGY_RUN_NAME, "strategy", 1, run.run_dir / STRATEGY_RUN_NAME
        ),
    ]
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is True


def test_load_run_without_dashboard_records(runs):
    run = runs.no_dashboard
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.start is not None
    assert records.start["model"] == "decisions"
    assert records.origin_mono is None
    assert records.args is None
    assert [event.record["event"] for event in records.run_events] == [
        "start",
        "decision",
        "decision",
        "decision",
        "advisor",
        "interrupted",
    ]
    assert [event.t for event in records.run_events] == pytest.approx(
        [0.0, 1.0, 2.0, 13.5, 20.0, 21.0]
    )
    assert records.header is None
    assert records.requests == []
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == [
        AdvisorRunDir(run.tips_run, "tips", 1, run.run_dir / run.tips_run)
    ]
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is False
    assert records.has_stream_file is False


def test_load_run_with_save_requests_only(runs):
    run = runs.save_requests
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.origin_mono is None
    assert records.args is None
    assert [event.t for event in records.run_events] == pytest.approx([0.0, 1.5, 2.5])
    assert records.header is not None
    assert [request["number"] for request in records.requests] == [1, 2]
    assert records.requests[0]["images"][0]["file"] == run.request_files[1]
    # frames/ (要求の画像) はあっても frames.jsonl は無い
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == []
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is False


def test_load_run_with_broken_lines_counts_skipped_lines(runs):
    run = runs.broken
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.skipped_lines == {
        "run": 1,
        "requests": 1,
        "frames": 1,
        "pad": 1,
        "advisor_steps": 1,
    }
    # 読めた行は記録がそろった実行と同じ。改行で終わっていない frames.jsonl の最後の行は読まない
    assert len(records.run_events) == 12
    assert [request["number"] for request in records.requests] == [1, 2, 3, 4, 5, 6]
    assert records.frames is not None
    assert len(records.frames) == 7
    assert records.pad is not None
    assert len(records.pad) == 5
    assert records.run_events[-1].t == pytest.approx(15.0)


@pytest.mark.parametrize(
    "attribute, question_count",
    [("no_answer_type", 4), ("single_advisor_question", 34)],
)
def test_load_old_format_runs(runs, attribute, question_count):
    run = getattr(runs, attribute)
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    assert records.header is not None
    assert len(records.header["questions"]) == question_count
    assert [request["number"] for request in records.requests] == [1, 2]
    assert [event.record["event"] for event in records.run_events] == [
        "start",
        "decision",
        "decision",
        "advisor",
    ]
    assert [event.t for event in records.run_events] == pytest.approx(
        [0.0, 1.5, 3.0, 5.0]
    )
    # 役割を持たない形の advisor-001/ は、役割ごとの advisor の実行の一覧に入れない
    assert records.advisor_dirs == []
    assert records.frames is None
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is True
    assert records.has_stream_file is False


def write_run_lines(run_dir, lines) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )


def test_lines_with_unreadable_time_have_no_t_and_are_not_skipped(tmp_path):
    runs_dir = tmp_path / "runs"
    write_run_lines(
        runs_dir / "20261101-000000",
        [
            {"time": "10:00:00.000", "event": "start"},
            {"time": "25:00:00.000", "event": "bridge_wait"},
            {"event": "interrupted"},
            {"time": "10:00:01.250", "event": "bridge_wait_end", "waited_s": 1.25},
        ],
    )
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert [event.t for event in records.run_events] == [0.0, None, None, 1.25]
    assert records.skipped_lines["run"] == 0


def test_run_without_start_line_has_no_t(tmp_path):
    runs_dir = tmp_path / "runs"
    write_run_lines(
        runs_dir / "20261101-000000",
        [{"time": "10:00:00.000", "event": "bridge_wait"}],
    )
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert records.start is None
    assert records.origin_mono is None
    assert [event.t for event in records.run_events] == [None]


# 壁時計と monotonic の時計がずれた実行。Pipeline の起点 (monotonic) は SKEWED_ORIGIN_MONO で、run.jsonl の
# decision の行の壁時計の時刻は、同じ number の要求の受信時刻 (monotonic の差) より 10 秒以上進んでいる
SKEWED_RUN_NAME = "20261102-100000"
SKEWED_ORIGIN_MONO = 100.0
SKEWED_RUN_LINES = [
    {"time": "09:59:59.500", "event": "record_error", "kind": "pad", "error": "x"},
    {"time": "10:00:00.000", "event": "start", "game": "smb", "model": "jev"},
    # 要求 1 の受信時刻は 1.25 秒 (壁時計の差は 11.25 秒)
    {"time": "10:00:11.250", "event": "decision", "number": 1, "decide_s": 0.25},
    {"time": "10:00:12.000", "event": "decision_error", "error": "HTTP 500"},
    # requests.jsonl に要求の行が無い number
    {"time": "10:00:13.000", "event": "decision", "number": 2, "decide_s": 0.25},
    # 要求 3 の受信時刻は 5.0 秒 (壁時計の差は 16.0 秒)
    {"time": "10:00:16.000", "event": "decision", "number": 3, "decide_s": 0.25},
    {"time": "10:00:16.500", "event": "bridge_wait"},
]


def skewed_request_record(number: int, sent: float, received: float) -> dict:
    """第4章の build_request_record で作った、経過秒 sent に送って received に受け取った要求の行を返す。"""
    questions = build_questions()
    decision = make_decision()
    request = build_decision_request(
        make_frame(SKEWED_ORIGIN_MONO, sent, 100.0, 50),
        SKEWED_ORIGIN_MONO + sent,
        FIRST_NOTES,
        questions,
        encode_frame,
        origin=SKEWED_ORIGIN_MONO,
        recent_operations=[],
    )
    entry = RequestLogEntry(
        number=number,
        origin=SKEWED_ORIGIN_MONO,
        sent_at=SKEWED_ORIGIN_MONO + sent,
        encoded_at=SKEWED_ORIGIN_MONO + sent,
        received_at=SKEWED_ORIGIN_MONO + received,
        request=request,
        decision=decision,
        action=decide_action(
            decision.probabilities, decision.choices, questions, Thresholds()
        ),
        advisor_accepted=frozenset(),
        result="ok",
        error=None,
    )
    return build_request_record(
        entry, "10:00:00.000", save_frames=False, record_thumbs=False
    )


def write_skewed_run(runs_dir, with_requests: bool) -> None:
    """SKEWED_RUN_LINES の run.jsonl を書く。with_requests が真なら、要求 1 と 3 の requests.jsonl も書く。"""
    run_dir = runs_dir / SKEWED_RUN_NAME
    write_run_lines(run_dir, SKEWED_RUN_LINES)
    if with_requests:
        lines = [
            skewed_request_record(1, 1.0, 1.25),
            skewed_request_record(3, 4.75, 5.0),
        ]
        (run_dir / "requests.jsonl").write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
            encoding="utf-8",
        )


def test_run_events_after_decision_line_are_based_on_received_time_of_request(
    tmp_path,
):
    runs_dir = tmp_path / "runs"
    write_skewed_run(runs_dir, with_requests=True)
    records = load_run(runs_dir, SKEWED_RUN_NAME)
    assert records is not None
    assert [request["t_received"] for request in records.requests] == pytest.approx(
        [1.25, 5.0]
    )
    assert [event.t for event in records.run_events] == pytest.approx(
        [
            # start の行より前の行は、start の行との壁時計の差
            -0.5,
            0.0,
            # 壁時計が 10 秒進んでいても、decision の行は同じ number の要求の受信時刻
            1.25,
            # その後の行は、受信時刻 + decision の行との壁時計の差
            2.0,
            # requests.jsonl に要求の行の無い number の decision の行は起点にせず、その前の起点から求める
            3.0,
            5.0,
            5.5,
        ]
    )


def test_run_events_without_requests_file_are_wall_clock_difference_from_start(
    tmp_path,
):
    runs_dir = tmp_path / "runs"
    write_skewed_run(runs_dir, with_requests=False)
    records = load_run(runs_dir, SKEWED_RUN_NAME)
    assert records is not None
    assert [event.t for event in records.run_events] == pytest.approx(
        [-0.5, 0.0, 11.25, 12.0, 13.0, 16.0, 16.5]
    )


# 23:59 台に始まり、0 時をまたいだ実行。要求 1 の受信時刻は 8.5 秒 (decision の行の壁時計の差は 9.0 秒)
MIDNIGHT_RUN_NAME = "20261103-235950"
MIDNIGHT_RUN_LINES = [
    {"time": "23:59:50.000", "event": "start", "game": "smb", "model": "jev"},
    {"time": "23:59:59.000", "event": "decision", "number": 1, "decide_s": 0.25},
    {"time": "00:00:01.500", "event": "bridge_wait"},
]


@pytest.mark.parametrize(
    "with_requests, expected",
    [
        # decision の行 (8.5 秒) の後の 0 時台の行は、decision の行との差 -86397.5 秒に 24 時間を足して 8.5 + 2.5
        (True, [0.0, 8.5, 11.0]),
        # requests.jsonl が無ければ、start の行との差 -86388.5 秒に 24 時間を足す
        (False, [0.0, 9.0, 11.5]),
    ],
)
def test_run_events_after_midnight_add_a_day_to_difference_from_origin_line(
    tmp_path, with_requests, expected
):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / MIDNIGHT_RUN_NAME
    write_run_lines(run_dir, MIDNIGHT_RUN_LINES)
    if with_requests:
        (run_dir / "requests.jsonl").write_text(
            json.dumps(skewed_request_record(1, 8.25, 8.5), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    records = load_run(runs_dir, MIDNIGHT_RUN_NAME)
    assert records is not None
    assert [event.t for event in records.run_events] == pytest.approx(expected)


def test_empty_run_dir_has_no_records(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "20261101-000000").mkdir(parents=True)
    records = load_run(runs_dir, "20261101-000000")
    assert records is not None
    assert records.start is None
    assert records.run_events == []
    assert records.header is None
    assert records.requests == []
    assert records.frames is None
    assert records.pad is None
    assert records.advisor_dirs == []
    assert records.skipped_lines == NO_SKIPPED_LINES
    assert records.has_requests_file is False
    assert records.has_stream_file is False


# --- lighten_request_lines ---


def test_lighten_request_lines_drops_unused_keys_and_shares_same_texts():
    state_text = "## ゲームの操作方法\n" + "あ" * 100
    header = {"event": "header", "questions": []}
    records = [
        header,
        {
            "event": "request",
            "number": 1,
            "state_text": state_text,
            "history_text": "履歴 1",
            "previous_operation_text": None,
            "raw_response": {"model": "jev"},
            "refusals": 0,
        },
        {
            "event": "request",
            "number": 2,
            # 直前の行と同じ値の、別の文字列のオブジェクト
            "state_text": state_text[:10] + state_text[10:],
            "history_text": "履歴 2",
            "previous_operation_text": None,
            "raw_response": None,
            "refusals": 1,
        },
    ]
    assert records[2]["state_text"] is not records[1]["state_text"]
    lightened = lighten_request_lines(records)
    assert lightened == [
        header,
        {
            "event": "request",
            "number": 1,
            "state_text": state_text,
            "history_text": "履歴 1",
            "previous_operation_text": None,
        },
        {
            "event": "request",
            "number": 2,
            "state_text": state_text,
            "history_text": "履歴 2",
            "previous_operation_text": None,
        },
    ]
    assert lightened[0] is header
    assert lightened[2]["state_text"] is lightened[1]["state_text"]
    # 元の行は変えない
    assert records[1]["raw_response"] == {"model": "jev"}
    assert records[2]["refusals"] == 1


# --- load_run の parts と cache ---


def test_load_parts():
    assert LOAD_PARTS == ("requests", "frames", "pad", "advisor_steps")


def test_load_run_with_only_frames_leaves_other_parts_empty(runs):
    run = runs.dashboard
    full = load_run(runs.runs_dir, run.name)
    records = load_run(runs.runs_dir, run.name, ("frames",))
    assert full is not None
    assert records is not None
    # run.jsonl と advisor のディレクトリーの一覧は、いつも読む
    assert records.start == full.start
    assert records.origin_mono == full.origin_mono
    assert records.args == full.args
    assert [event.record for event in records.run_events] == [
        event.record for event in full.run_events
    ]
    assert records.advisor_dirs == full.advisor_dirs
    assert records.frames == full.frames
    # 読まなかった種類は空か None
    assert records.header is None
    assert records.requests == []
    assert records.has_requests_file is False
    assert records.pad is None
    assert records.has_stream_file is False
    assert records.skipped_lines == NO_SKIPPED_LINES


def test_load_run_with_no_parts_reads_only_run_log(runs):
    records = load_run(runs.runs_dir, runs.broken.name, ())
    assert records is not None
    assert len(records.run_events) == 12
    assert (records.header, records.requests, records.frames, records.pad) == (
        None,
        [],
        None,
        None,
    )
    # run.jsonl の読み飛ばした行だけを数える
    assert records.skipped_lines == {**NO_SKIPPED_LINES, "run": 1}


def test_load_run_rejects_unknown_parts(runs):
    with pytest.raises(ValueError):
        load_run(runs.runs_dir, runs.dashboard.name, ("requests", "thumbs"))


def test_requests_of_load_run_are_lightened(runs):
    records = load_run(runs.runs_dir, runs.dashboard.name, ("requests",))
    assert records is not None
    assert all("raw_response" not in request for request in records.requests)
    assert all("refusals" not in request for request in records.requests)


@pytest.mark.parametrize("attribute", SYNTHETIC_RUN_ATTRIBUTES)
def test_load_run_with_cache_returns_same_records(runs, attribute):
    run = getattr(runs, attribute)
    cache = JsonLinesCache()
    expected = load_run(runs.runs_dir, run.name)
    assert load_run(runs.runs_dir, run.name, cache=cache) == expected
    # 2 回目はキャッシュから読む
    assert load_run(runs.runs_dir, run.name, cache=cache) == expected
    assert load_run_overview(runs.runs_dir, run.name, cache) == load_run_overview(
        runs.runs_dir, run.name
    )


def test_read_advisor_stream_with_cache_returns_same_lines(runs):
    run_dir = runs.dashboard.run_dir
    advisor_dir = AdvisorRunDir(
        STRATEGY_RUN_NAME, "strategy", 1, run_dir / STRATEGY_RUN_NAME
    )
    cache = JsonLinesCache()
    expected = read_advisor_stream(advisor_dir)
    assert read_advisor_stream(advisor_dir, cache) == expected
    assert read_advisor_stream(advisor_dir, cache) == expected


# --- read_advisor_stream ---


def test_read_advisor_stream_returns_lines_including_raw_lines(runs):
    run_dir = runs.dashboard.run_dir
    advisor_dir = AdvisorRunDir(
        STRATEGY_RUN_NAME, "strategy", 1, run_dir / STRATEGY_RUN_NAME
    )
    lines = read_advisor_stream(advisor_dir)
    assert lines is not None
    assert len(lines) == 10
    assert lines[1]["mono"] == 1002.0
    assert lines[1]["data"]["message"]["content"][0]["name"] == "Read"
    assert lines[6] == {"mono": 1003.5, "raw": "not a json line"}
    assert lines[-1]["data"]["type"] == "result"


def test_read_advisor_stream_skips_unreadable_lines(runs):
    run_dir = runs.broken.run_dir
    advisor_dir = AdvisorRunDir(
        STRATEGY_RUN_NAME, "strategy", 1, run_dir / STRATEGY_RUN_NAME
    )
    lines = read_advisor_stream(advisor_dir)
    assert lines is not None
    assert len(lines) == 10


def test_read_advisor_stream_is_none_without_stream_file(runs):
    run = runs.no_dashboard
    advisor_dir = AdvisorRunDir(run.tips_run, "tips", 1, run.run_dir / run.tips_run)
    assert read_advisor_stream(advisor_dir) is None


# --- read_result_json ---


def test_read_result_json_returns_object(tmp_path):
    path = tmp_path / "result.json"
    path.write_text('{"returncode": 0, "duration_s": 4.0}', encoding="utf-8")
    assert read_result_json(path) == {"returncode": 0, "duration_s": 4.0}


@pytest.mark.parametrize(
    "content",
    [b"[1, 2]", b"not json", b'{"returncode": 0', b'{"note": "\xff\xfe"}'],
)
def test_read_result_json_is_none_for_unreadable_content(tmp_path, content):
    path = tmp_path / "result.json"
    path.write_bytes(content)
    assert read_result_json(path) is None


def test_read_result_json_is_none_for_missing_file(tmp_path):
    assert read_result_json(tmp_path / "result.json") is None


# --- next_offsets (区間 API の cursor) ---


def file_sizes(run_dir) -> dict[str, int]:
    """4 つの JSON Lines の種類 → ファイルの大きさ (無ければ 0) を返す。"""
    names = {
        "run": "run.jsonl",
        "requests": "requests.jsonl",
        "frames": "frames.jsonl",
        "pad": "pad.jsonl",
    }
    return {
        kind: (run_dir / name).stat().st_size if (run_dir / name).exists() else 0
        for kind, name in names.items()
    }


@pytest.mark.parametrize("cache", [None, JsonLinesCache()])
def test_next_offsets_of_dashboard_run_are_file_sizes(runs, cache):
    # どのファイルも改行で終わるので、読んだ最後の完全な行の次はファイルの大きさ
    run = runs.dashboard
    records = load_run(runs.runs_dir, run.name, cache=cache)
    assert records is not None
    assert records.next_offsets == file_sizes(run.run_dir)
    assert all(offset > 0 for offset in records.next_offsets.values())


def test_next_offset_of_broken_frames_stops_before_unterminated_line(runs):
    # 改行で終わっていない最後の行の手前
    run = runs.broken
    records = load_run(runs.runs_dir, run.name)
    assert records is not None
    sizes = file_sizes(run.run_dir)
    assert records.next_offsets == {
        **sizes,
        "frames": sizes["frames"] - len(UNTERMINATED_FRAME_LINE.encode("utf-8")),
    }


def test_next_offsets_of_missing_files_and_unread_parts_are_zero(runs):
    # 無いファイルと、parts で読まなかった種類は 0
    no_dashboard = runs.no_dashboard
    records = load_run(runs.runs_dir, no_dashboard.name)
    assert records is not None
    assert records.next_offsets == {
        "run": file_sizes(no_dashboard.run_dir)["run"],
        "requests": 0,
        "frames": 0,
        "pad": 0,
    }
    dashboard = runs.dashboard
    sizes = file_sizes(dashboard.run_dir)
    only_frames = load_run(runs.runs_dir, dashboard.name, ("frames",))
    assert only_frames is not None
    assert only_frames.next_offsets == {
        "run": sizes["run"],
        "requests": 0,
        "frames": sizes["frames"],
        "pad": 0,
    }
    run_log_only = load_run(runs.runs_dir, dashboard.name, ())
    assert run_log_only is not None
    assert run_log_only.next_offsets == {
        "run": sizes["run"],
        "requests": 0,
        "frames": 0,
        "pad": 0,
    }


# --- run_log_mtime と has_record_end (実行中の判定に使う値) ---


@pytest.mark.parametrize("attribute", SYNTHETIC_RUN_ATTRIBUTES)
def test_run_log_mtime_is_mtime_of_run_log(runs, attribute):
    # write_all_runs は run.jsonl の更新時刻を STALE_RUN_LOG_MTIME にする
    run = getattr(runs, attribute)
    records = load_run(runs.runs_dir, run.name)
    overview = load_run_overview(runs.runs_dir, run.name, JsonLinesCache())
    assert records is not None
    assert overview is not None
    mtime = os.stat(run.run_dir / "run.jsonl").st_mtime
    assert mtime == STALE_RUN_LOG_MTIME
    assert records.run_log_mtime == mtime
    assert records.overview.run_log_mtime == mtime
    assert overview.run_log_mtime == mtime


def test_run_log_mtime_is_none_without_run_log_or_for_symlink(tmp_path):
    # run.jsonl が無い実行と、run.jsonl がシンボリックリンクの実行は None
    runs_dir = tmp_path / "runs"
    (runs_dir / "20261101-000000").mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"time": "10:00:00.000", "event": "start"}\n', encoding="utf-8")
    linked = runs_dir / "20261102-000000"
    linked.mkdir()
    (linked / "run.jsonl").symlink_to(outside)
    for name in ("20261101-000000", "20261102-000000"):
        records = load_run(runs_dir, name)
        overview = load_run_overview(runs_dir, name)
        assert records is not None
        assert overview is not None
        assert records.run_log_mtime is None
        assert overview.run_log_mtime is None
        assert overview.has_record_end is False


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("dashboard", True),
        ("broken", True),
        ("no_dashboard", False),
        ("save_requests", False),
        ("no_answer_type", False),
        ("single_advisor_question", False),
    ],
)
def test_has_record_end(runs, attribute, expected):
    # run.jsonl に record_end の行がある実行だけ True。3 つの読み方で同じ値
    run = getattr(runs, attribute)
    records = load_run(runs.runs_dir, run.name)
    overview = load_run_overview(runs.runs_dir, run.name)
    assert records is not None
    assert overview is not None
    listed = {item.name: item for item in list_run_overviews(runs.runs_dir)}
    assert records.has_record_end is expected
    assert records.overview.has_record_end is expected
    assert overview.has_record_end is expected
    assert listed[run.name].has_record_end is expected


# --- RunLogClock (run.jsonl の行の経過秒を 1 行ずつ求める) ---


def times_by_clock(records) -> list[float | None]:
    """records の run.jsonl の行に、RunLogClock の関数を 1 行ずつ当てて求めた経過秒のリストを返す。"""
    received: dict[object, float] = {}
    for request in records.requests:
        add_received_time(received, request)
    lines = [event.record for event in records.run_events]
    start_index = next(
        (index for index, line in enumerate(lines) if line.get("event") == "start"),
        None,
    )
    clock = start_run_log_clock(lines[start_index]) if start_index is not None else None
    if start_index is None or clock is None:
        return [None] * len(lines)
    times: list[float | None] = []
    for index, line in enumerate(lines):
        if index < start_index:
            times.append(elapsed_before_start(clock, line))
        elif index == start_index:
            times.append(0.0)
        else:
            t, clock = advance_run_log_clock(clock, line, received)
            times.append(t)
    return times


@pytest.mark.parametrize("attribute", SYNTHETIC_RUN_ATTRIBUTES)
def test_run_log_clock_gives_same_times_as_load_run(runs, attribute):
    # start より前の行・日付をまたぐ行・起点の切り替えを含む (dashboard と broken)
    records = load_run(runs.runs_dir, getattr(runs, attribute).name)
    assert records is not None
    assert times_by_clock(records) == [event.t for event in records.run_events]


@pytest.mark.parametrize("with_requests", [True, False])
def test_run_log_clock_gives_same_times_as_load_run_for_skewed_run(
    tmp_path, with_requests
):
    # 壁時計が monotonic の時計からずれた実行 (起点の decision の行で時間軸を直す)
    runs_dir = tmp_path / "runs"
    write_skewed_run(runs_dir, with_requests)
    records = load_run(runs_dir, SKEWED_RUN_NAME)
    assert records is not None
    assert times_by_clock(records) == [event.t for event in records.run_events]


def test_start_run_log_clock():
    # start の行の time が読めなければ None
    assert start_run_log_clock(
        {"time": "10:00:01.500", "event": "start"}
    ) == RunLogClock(start_time=36001.5, origin_t=0.0, origin_time=36001.5)
    assert start_run_log_clock({"time": "25:00:00.000", "event": "start"}) is None
    assert start_run_log_clock({"event": "start"}) is None


def test_elapsed_before_start():
    clock = RunLogClock(start_time=36000.0, origin_t=0.0, origin_time=36000.0)
    assert elapsed_before_start(clock, {"time": "09:59:59.500"}) == pytest.approx(-0.5)
    assert elapsed_before_start(clock, {"time": "bad"}) is None
    assert elapsed_before_start(clock, {}) is None


def test_advance_run_log_clock_with_unreadable_anchor_time():
    # 起点にする decision の行の time が読めなければ、その行の t は受信時刻のまま、次の起点まで t は None
    clock = start_run_log_clock({"time": "10:00:00.000", "event": "start"})
    assert clock is not None
    received = {1: 1.25, 2: 5.0}
    t, clock = advance_run_log_clock(
        clock, {"time": "10:00:00.500", "event": "bridge_wait"}, received
    )
    assert t == pytest.approx(0.5)
    t, clock = advance_run_log_clock(
        clock, {"time": "bad", "event": "decision", "number": 1}, received
    )
    assert t == 1.25
    assert clock.origin_t == 1.25
    assert clock.origin_time is None
    t, clock = advance_run_log_clock(
        clock, {"time": "10:00:02.000", "event": "decision_error"}, received
    )
    assert t is None
    # requests.jsonl に無い number の decision の行は起点にしない
    t, clock = advance_run_log_clock(
        clock, {"time": "10:00:03.000", "event": "decision", "number": 9}, received
    )
    assert t is None
    t, clock = advance_run_log_clock(
        clock, {"time": "10:00:05.500", "event": "decision", "number": 2}, received
    )
    assert t == 5.0
    t, clock = advance_run_log_clock(
        clock, {"time": "10:00:06.000", "event": "bridge_wait"}, received
    )
    assert t == pytest.approx(5.5)


def test_advance_run_log_clock_does_not_anchor_on_unhashable_number():
    clock = RunLogClock(start_time=36000.0, origin_t=0.0, origin_time=36000.0)
    line = {"time": "10:00:02.000", "event": "decision", "number": [1]}
    t, next_clock = advance_run_log_clock(clock, line, {1: 1.25})
    assert t == pytest.approx(2.0)
    assert next_clock == clock


def test_add_received_time():
    # 同じ number は最初の値。number がキーにできない行と、t_received が数でない行は足さない
    received: dict[object, float] = {}
    add_received_time(received, {"number": 1, "t_received": 1.25})
    add_received_time(received, {"number": 1, "t_received": 2.0})
    add_received_time(received, {"number": [2], "t_received": 3.0})
    add_received_time(received, {"number": {"n": 3}, "t_received": 3.5})
    add_received_time(received, {"number": 4, "t_received": "4.0"})
    add_received_time(received, {"number": 5, "t_received": True})
    add_received_time(received, {"number": 6, "t_received": None})
    add_received_time(received, {"number": 7, "t_received": 7})
    assert received == {1: 1.25, 7: 7.0}
    assert isinstance(received[7], float)


def test_lighten_request_lines_with_previous_line_shares_texts():
    # 後半を、前半の最後の要求の行を previous にして軽くすると、全体を 1 回で軽くした後半と同じ値で、
    # 同じ文字列のオブジェクトを共有する
    state_text = "## ゲームの操作方法\n" + "あ" * 100
    records = [
        {"event": "header", "questions": []},
        {"event": "request", "number": 1, "state_text": state_text, "refusals": 0},
        {
            "event": "request",
            "number": 2,
            "state_text": state_text[:10] + state_text[10:],
            "raw_response": None,
        },
        {"event": "note", "text": "要求でない行"},
        {
            "event": "request",
            "number": 3,
            "state_text": state_text[:5] + state_text[5:],
        },
    ]
    whole = lighten_request_lines(records)
    first = lighten_request_lines(records[:2])
    second = lighten_request_lines(records[2:], previous=first[-1])
    assert second == whole[2:]
    assert second[0]["state_text"] is first[-1]["state_text"]
    assert second[2]["state_text"] is first[-1]["state_text"]
    # previous が無ければ、後半の最初の要求の行は何とも共有しない
    alone = lighten_request_lines(records[2:])
    assert alone == whole[2:]
    assert alone[0]["state_text"] is records[2]["state_text"]
    assert alone[0]["state_text"] is not first[-1]["state_text"]
