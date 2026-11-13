from pathlib import Path

import pytest
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.advisors.claude_code_headless import (
    build_log_event,
    build_stream_result_record,
)

from run_dashboard.advisor_runs import (
    SUMMARY_INPUT_KEYS,
    SUMMARY_MAX_CHARS,
    AdvisorRun,
    build_advisor_runs,
    build_steps,
    first_line,
    summarize_tool_input,
)
from run_dashboard.run_directory import AdvisorRunDir
from run_dashboard.run_records import RunEvent

ROLES_BY_NAME = {role.name: role for role in ADVISOR_ROLES}


def advisor_dir(run_dir: Path, role: str, index: int) -> AdvisorRunDir:
    name = f"advisor-{role}-{index:03d}"
    return AdvisorRunDir(name, role, index, run_dir / name)


def result_record(
    duration_s: float,
    returncode: int = 0,
    result: str = "更新した",
    denied_ids: tuple[str, ...] = (),
) -> dict:
    """第4章の build_stream_result_record で作った result.json の辞書を返す。"""
    return build_stream_result_record(
        returncode=returncode,
        duration_s=duration_s,
        timed_out=False,
        error=None,
        last_result={
            "type": "result",
            "result": result,
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": tool_use_id, "tool_input": {}}
                for tool_use_id in denied_ids
            ],
        },
        stderr="",
    )


class FakeResultFiles:
    """read_result_json の代わり。パス → result.json の中身を持ち、呼ばれたパスを calls に記録する。"""

    def __init__(self, records: dict[Path, dict]):
        self.records = records
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> dict | None:
        self.calls.append(path)
        return self.records.get(path)


def test_summary_constants():
    assert SUMMARY_INPUT_KEYS == (
        "file_path",
        "path",
        "skill",
        "pattern",
        "command",
        "url",
    )
    assert SUMMARY_MAX_CHARS == 120


# --- build_advisor_runs ---


def test_kth_run_of_each_role_is_matched_with_kth_accepting_request(tmp_path):
    strategy_1 = advisor_dir(tmp_path, "strategy", 1)
    strategy_2 = advisor_dir(tmp_path, "strategy", 2)
    tips_1 = advisor_dir(tmp_path, "tips", 1)
    records = {
        strategy_1.path / "result.json": result_record(4.0, denied_ids=("toolu_a",)),
        strategy_2.path / "result.json": result_record(2.0),
        tips_1.path / "result.json": result_record(
            1.5, returncode=1, result="失敗した"
        ),
    }
    read_result_json = FakeResultFiles(records)
    requests = [
        {"number": 1, "t_received": 1.25, "advisor_accepted": ["strategy"]},
        {"number": 2, "t_received": 2.5, "advisor_accepted": []},
        {"number": 3, "t_received": 3.5, "advisor_accepted": ["strategy", "tips"]},
        {"number": 4, "t_received": None, "advisor_accepted": None},
    ]
    runs = build_advisor_runs(
        [strategy_1, strategy_2, tips_1], requests, [], read_result_json
    )
    assert runs == [
        AdvisorRun(
            name="advisor-strategy-001",
            role="strategy",
            request=1,
            t_start=1.25,
            t_end=5.25,
            returncode=0,
            denials=1,
            result="更新した",
            status="done",
            result_json=records[strategy_1.path / "result.json"],
            denied_tool_use_ids=frozenset({"toolu_a"}),
        ),
        AdvisorRun(
            name="advisor-strategy-002",
            role="strategy",
            request=3,
            t_start=3.5,
            t_end=5.5,
            returncode=0,
            denials=0,
            result="更新した",
            status="done",
            result_json=records[strategy_2.path / "result.json"],
            denied_tool_use_ids=frozenset(),
        ),
        AdvisorRun(
            name="advisor-tips-001",
            role="tips",
            request=3,
            t_start=3.5,
            t_end=5.0,
            returncode=1,
            denials=0,
            result="失敗した",
            status="done",
            result_json=records[tips_1.path / "result.json"],
            denied_tool_use_ids=frozenset(),
        ),
    ]
    assert sorted(read_result_json.calls) == sorted(records)


def advisor_line_event(t: float, directory: AdvisorRunDir, record: dict) -> RunEvent:
    """run.jsonl の advisor の行 (第4章の build_log_event に time を足したもの) の RunEvent を返す。"""
    line = {
        "time": "10:00:20.000",
        **build_log_event(directory.path, record, ROLES_BY_NAME[directory.role]),
    }
    return RunEvent(t=t, record=line)


def test_run_without_request_takes_times_from_advisor_line(tmp_path):
    tips_1 = advisor_dir(tmp_path, "tips", 1)
    strategy_1 = advisor_dir(tmp_path, "strategy", 1)
    record = result_record(6.5, result="Tips を更新した")
    run_events = [
        RunEvent(t=0.0, record={"time": "10:00:00.000", "event": "start"}),
        # ほかの実行の advisor の行とは突き合わせない
        advisor_line_event(10.0, strategy_1, result_record(1.0)),
        advisor_line_event(20.0, tips_1, record),
        RunEvent(t=21.0, record={"time": "10:00:21.000", "event": "interrupted"}),
    ]
    read_result_json = FakeResultFiles({tips_1.path / "result.json": record})
    assert build_advisor_runs([tips_1], [], run_events, read_result_json) == [
        AdvisorRun(
            name="advisor-tips-001",
            role="tips",
            request=None,
            t_start=13.5,
            t_end=20.0,
            returncode=0,
            denials=0,
            result="Tips を更新した",
            status="done",
            result_json=record,
            denied_tool_use_ids=frozenset(),
        )
    ]


def test_run_without_result_json_takes_values_from_advisor_line(tmp_path):
    tips_1 = advisor_dir(tmp_path, "tips", 1)
    line_record = result_record(
        6.5, returncode=2, result="行の結果", denied_ids=("toolu_x",)
    )
    run_events = [advisor_line_event(20.0, tips_1, line_record)]
    assert build_advisor_runs([tips_1], [], run_events, FakeResultFiles({})) == [
        AdvisorRun(
            name="advisor-tips-001",
            role="tips",
            request=None,
            t_start=13.5,
            t_end=20.0,
            returncode=2,
            denials=1,
            result="行の結果",
            status="running",
            result_json=None,
            denied_tool_use_ids=frozenset(),
        )
    ]


def test_running_run_without_result_json_or_advisor_line(tmp_path):
    controls_1 = advisor_dir(tmp_path, "controls", 1)
    tips_1 = advisor_dir(tmp_path, "tips", 1)
    requests = [{"number": 3, "t_received": 3.5, "advisor_accepted": ["controls"]}]
    runs = build_advisor_runs([controls_1, tips_1], requests, [], FakeResultFiles({}))
    assert runs == [
        # 要求があれば開始は分かるが、終了は分からない
        AdvisorRun(
            name="advisor-controls-001",
            role="controls",
            request=3,
            t_start=3.5,
            t_end=None,
            returncode=None,
            denials=None,
            result=None,
            status="running",
            result_json=None,
            denied_tool_use_ids=frozenset(),
        ),
        # 要求も無ければ開始も分からない
        AdvisorRun(
            name="advisor-tips-001",
            role="tips",
            request=None,
            t_start=None,
            t_end=None,
            returncode=None,
            denials=None,
            result=None,
            status="running",
            result_json=None,
            denied_tool_use_ids=frozenset(),
        ),
    ]


# --- summarize_tool_input / first_line ---


@pytest.mark.parametrize(
    "tool_input, expected",
    [
        (
            {"file_path": "/game_notes/smb/strategy.md", "command": "ls"},
            "/game_notes/smb/strategy.md",
        ),
        ({"path": "runs", "pattern": "*.md"}, "runs"),
        ({"skill": "record-tips"}, "record-tips"),
        ({"pattern": "TODO"}, "TODO"),
        ({"command": "ls game_notes\nwc -l"}, "ls game_notes"),
        ({"url": "https://example.com/"}, "https://example.com/"),
        # 文字列でない値のキーは飛ばす
        ({"file_path": 3, "path": "p"}, "p"),
        ({"description": "説明だけ"}, ""),
        ({}, ""),
        ("not an object", ""),
        (None, ""),
        ({"command": "x" * 200}, "x" * 120),
    ],
)
def test_summarize_tool_input(tool_input, expected):
    assert summarize_tool_input(tool_input) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 行目\n2 行目", "1 行目"),
        ("", ""),
        ("y" * 130, "y" * 120),
        (
            [{"type": "image", "source": {}}, {"type": "text", "text": "最初\n次"}],
            "最初",
        ),
        ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "a"),
        ([], ""),
        ([{"type": "image"}], ""),
        (None, ""),
        (5, ""),
    ],
)
def test_first_line(text, expected):
    assert first_line(text) == expected


def test_first_line_with_limit():
    assert first_line("abcdef", limit=3) == "abc"


# --- build_steps ---

ORIGIN_MONO = 100.0


def data_line(mono: float, data: dict) -> dict:
    return {"mono": mono, "data": data}


def tool_use(mono: float, tool_use_id: str, name: str, tool_input: dict) -> dict:
    return data_line(
        mono,
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "考え中"},
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": name,
                        "input": tool_input,
                    },
                ]
            },
        },
    )


def tool_result(mono: float, tool_use_id: str, content: object) -> dict:
    return data_line(
        mono,
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                    }
                ]
            },
        },
    )


def result_line(mono: float, denied_ids: tuple[str, ...]) -> dict:
    return data_line(
        mono,
        {
            "type": "result",
            "result": "完了した\n2 行目",
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": tool_use_id, "tool_input": {}}
                for tool_use_id in denied_ids
            ],
        },
    )


STREAM_WITHOUT_RESULT = [
    data_line(100.5, {"type": "system", "subtype": "init"}),
    tool_use(101.0, "toolu_a", "Read", {"file_path": "game_notes/smb/tips.md"}),
    tool_result(101.5, "toolu_a", "1 行目\n2 行目"),
    data_line(101.75, {"type": "rate_limit_event"}),
    tool_use(102.0, "toolu_b", "Bash", {"command": "ls"}),
    tool_result(102.5, "toolu_b", [{"type": "text", "text": "denied"}]),
    {"mono": 102.75, "raw": "not json"},
    # 知らない tool_use_id の結果
    tool_result(102.875, "toolu_z", "?"),
]


def test_build_steps_marks_denied_tool_results_from_result_line():
    stream = [*STREAM_WITHOUT_RESULT, result_line(103.0, ("toolu_b",))]
    # result の行があれば、result.json の拒否の id (toolu_a) は使わない
    assert build_steps(stream, ORIGIN_MONO, frozenset({"toolu_a"})) == [
        {
            "t": 1.0,
            "kind": "tool_use",
            "tool": "Read",
            "summary": "game_notes/smb/tips.md",
        },
        {"t": 1.5, "kind": "tool_result", "tool": "Read", "summary": "1 行目"},
        {"t": 2.0, "kind": "tool_use", "tool": "Bash", "summary": "ls"},
        {"t": 2.5, "kind": "permission_denied", "tool": "Bash", "summary": "denied"},
        {"t": 2.875, "kind": "tool_result", "tool": None, "summary": "?"},
        {"t": 3.0, "kind": "result", "tool": None, "summary": "完了した"},
    ]


def test_build_steps_uses_result_json_denials_without_result_line():
    steps = build_steps(STREAM_WITHOUT_RESULT, ORIGIN_MONO, frozenset({"toolu_b"}))
    assert [(step["t"], step["kind"]) for step in steps] == [
        (1.0, "tool_use"),
        (1.5, "tool_result"),
        (2.0, "tool_use"),
        (2.5, "permission_denied"),
        (2.875, "tool_result"),
    ]


def test_build_steps_keeps_tool_result_without_result_line_or_result_json():
    steps = build_steps(STREAM_WITHOUT_RESULT, ORIGIN_MONO, frozenset())
    assert [step["kind"] for step in steps] == [
        "tool_use",
        "tool_result",
        "tool_use",
        "tool_result",
        "tool_result",
    ]


def test_build_steps_without_origin_has_no_t():
    steps = build_steps(STREAM_WITHOUT_RESULT, None, frozenset())
    assert len(steps) == 5
    assert all(step["t"] is None for step in steps)


def test_build_steps_lists_each_tool_use_and_tool_result_of_one_message():
    stream = [
        data_line(
            101.0,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Read",
                            "input": {"file_path": "a.md"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_2",
                            "name": "Read",
                            "input": {"file_path": "b.md"},
                        },
                    ]
                },
            },
        ),
        data_line(
            101.5,
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "A",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_2",
                            "content": "B",
                        },
                    ]
                },
            },
        ),
    ]
    assert build_steps(stream, ORIGIN_MONO, frozenset()) == [
        {"t": 1.0, "kind": "tool_use", "tool": "Read", "summary": "a.md"},
        {"t": 1.0, "kind": "tool_use", "tool": "Read", "summary": "b.md"},
        {"t": 1.5, "kind": "tool_result", "tool": "Read", "summary": "A"},
        {"t": 1.5, "kind": "tool_result", "tool": "Read", "summary": "B"},
    ]


def test_build_steps_of_empty_stream_is_empty():
    assert build_steps([], ORIGIN_MONO, frozenset()) == []
