import pytest
from switch2_agent.game_notes import format_game_notes

from run_dashboard.advisor_runs import AdvisorRun
from run_dashboard.note_versions import (
    EXTERNAL_CHANGE,
    NoteVersion,
    build_note_versions,
    note_version_of_request,
    split_state_text,
)

EMPTY = "(まだ書かれていない)"


def test_constants():
    assert EXTERNAL_CHANGE == "external"


# --- split_state_text ---


def test_split_state_text_reverses_format_game_notes_with_headings_in_content():
    notes = {
        "controls": "- A: ジャンプ",
        "tips": "",
        # strategy.md の節の見出しは、4 つの見出しと一致しないので区切りにしない
        "strategy": "## 目標\n- 大目標: 先に進む\n\n## 戦略\n- 右へ進む\n\n## 問題の解決の助言の例\n- なし",
        "advice": "  前後の空白は除かれる  \n",
    }
    assert split_state_text(format_game_notes(notes)) == {
        "controls": "- A: ジャンプ",
        "tips": EMPTY,
        "strategy": "## 目標\n- 大目標: 先に進む\n\n## 戦略\n- 右へ進む\n\n## 問題の解決の助言の例\n- なし",
        "advice": "前後の空白は除かれる",
    }


def test_split_state_text_of_empty_notes_keeps_empty_note_text():
    assert split_state_text(format_game_notes({})) == {
        "controls": EMPTY,
        "tips": EMPTY,
        "strategy": EMPTY,
        "advice": EMPTY,
    }


def test_split_state_text_of_four_notes():
    notes = {
        "controls": "操作\n2 行目",
        "tips": "攻略",
        "strategy": "戦略",
        "advice": "助言\n\n空行の後",
    }
    assert split_state_text(format_game_notes(notes)) == notes


@pytest.mark.parametrize(
    "state_text",
    [
        "",
        "ゲームの情報",
        # 先頭が見出しでない
        " ## ゲームの操作方法\nx\n\n## 攻略情報・Tips\nx\n\n## 目標・進行状況・戦略\nx\n\n## 問題の解決の助言\nx",
        # 見出しが足りない
        "## ゲームの操作方法\nx\n\n## 攻略情報・Tips\nx\n\n## 目標・進行状況・戦略\nx",
        # 見出しの順が違う
        "## 攻略情報・Tips\nx\n\n## ゲームの操作方法\nx\n\n## 目標・進行状況・戦略\nx\n\n## 問題の解決の助言\nx",
        # 4 つの見出しではない古い形の見出し
        "## ゲームの操作方法\nx\n\n## 攻略情報・Tips\nx\n\n## 進行状況\nx\n\n## 戦略\nx",
    ],
)
def test_split_state_text_is_none_without_headings_in_order(state_text):
    assert split_state_text(state_text) is None


# --- build_note_versions ---

BASE_NOTES = {"controls": "c1", "tips": "", "strategy": "s1", "advice": ""}


def request(number: int, t_sent: object, notes: dict | None) -> dict:
    """number・t_sent と、notes を format_game_notes にした state_text を持つ要求の行を返す。"""
    return {
        "event": "request",
        "number": number,
        "t_sent": t_sent,
        "state_text": format_game_notes(notes) if notes is not None else None,
    }


def advisor_run(
    name: str, role: str, t_start: float | None, t_end: float | None
) -> AdvisorRun:
    return AdvisorRun(
        name=name,
        role=role,
        request=None,
        t_start=t_start,
        t_end=t_end,
        returncode=None,
        denials=None,
        result=None,
        status="done" if t_end is not None else "running",
        result_json=None,
        denied_tool_use_ids=frozenset(),
    )


ADVISOR_RUNS = [
    # 3.0〜6.0 秒の strategy の実行
    advisor_run("advisor-strategy-001", "strategy", 3.0, 6.0),
    # 8.0 秒からの実行中の tips の実行
    advisor_run("advisor-tips-001", "tips", 8.0, None),
    # 10.0 秒からの controls の実行 (9.0 秒の書き換えより後に始まる)
    advisor_run("advisor-controls-001", "controls", 10.0, None),
    # 開始の分からない advice の実行
    advisor_run("advisor-advice-001", "advice", None, None),
]
# 各版の by は、変わった行の t_sent (ts) と、その直前に使った行の t_sent (tp) の間に動いていた実行
REQUESTS = [
    request(1, 1.0, BASE_NOTES),
    # 前の版と同じ
    request(2, 2.0, BASE_NOTES),
    # strategy の実行の開始 (3.0) より前に変わった (ts 2.5)
    request(3, 2.5, {**BASE_NOTES, "strategy": "s2"}),
    # strategy の実行中に変わった (tp 2.5、ts 5.0)
    request(4, 5.0, {**BASE_NOTES, "strategy": "s3"}),
    # 前の版と同じ (strategy の実行の終了 6.0 より前に送った)
    request(5, 5.5, {**BASE_NOTES, "strategy": "s3"}),
    # strategy の実行の終了から 1 秒を超えて後に変わったが、直前の行 (tp 5.5) は終了より前
    request(6, 7.5, {**BASE_NOTES, "strategy": "s4"}),
    # 前の版と同じ (strategy の実行の終了より後に送った)
    request(7, 7.75, {**BASE_NOTES, "strategy": "s4"}),
    # 直前の行 (tp 7.75) より前に strategy の実行が終わっている
    request(8, 8.0, {**BASE_NOTES, "strategy": "s5"}),
    # tips は実行中 (t_end が None) の tips の実行、controls はその後に始まる実行しか無い
    request(9, 9.0, {**BASE_NOTES, "strategy": "s5", "tips": "t1", "controls": "c2"}),
    # state_text か t_sent の無い行は使わない
    request(10, None, {**BASE_NOTES, "advice": "a1"}),
    request(11, 9.5, None),
    # advice は開始の分からない実行しか無い
    request(
        12,
        11.0,
        {
            **BASE_NOTES,
            "strategy": "s5",
            "tips": "t1",
            "controls": "c2",
            "advice": "a1",
        },
    ),
]


def test_build_note_versions_numbers_versions_and_finds_who_changed():
    versions = build_note_versions(REQUESTS, ADVISOR_RUNS)
    assert [
        (version.version, version.t, version.request, version.initial, version.changed)
        for version in versions
    ] == [
        (0, 1.0, 1, True, []),
        (1, 2.5, 3, False, [{"kind": "strategy", "by": "external"}]),
        (2, 5.0, 4, False, [{"kind": "strategy", "by": "advisor-strategy-001"}]),
        (3, 7.5, 6, False, [{"kind": "strategy", "by": "advisor-strategy-001"}]),
        (4, 8.0, 8, False, [{"kind": "strategy", "by": "external"}]),
        (
            5,
            9.0,
            9,
            False,
            [
                {"kind": "controls", "by": "external"},
                {"kind": "tips", "by": "advisor-tips-001"},
            ],
        ),
        (6, 11.0, 12, False, [{"kind": "advice", "by": "external"}]),
    ]


def test_run_ending_exactly_at_previous_sent_time_is_who_changed():
    # tp は使った行 (state_text と t_sent のある行) だけから決める。7.0 秒の行は state_text が無いので tp は 6.0
    requests = [
        request(1, 1.0, BASE_NOTES),
        request(2, 6.0, BASE_NOTES),
        request(3, 7.0, None),
        request(4, 9.0, {**BASE_NOTES, "strategy": "s2"}),
    ]
    versions = build_note_versions(
        requests, [advisor_run("advisor-strategy-001", "strategy", 3.0, 6.0)]
    )
    assert [version.changed for version in versions] == [
        [],
        [{"kind": "strategy", "by": "advisor-strategy-001"}],
    ]


def test_build_note_versions_keeps_split_files():
    versions = build_note_versions(REQUESTS, ADVISOR_RUNS)
    assert versions[0] == NoteVersion(
        version=0,
        t=1.0,
        request=1,
        initial=True,
        changed=[],
        files={"controls": "c1", "tips": EMPTY, "strategy": "s1", "advice": EMPTY},
    )
    assert versions[5].files == {
        "controls": "c2",
        "tips": "t1",
        "strategy": "s5",
        "advice": EMPTY,
    }


def test_unsplittable_state_text_makes_version_without_changed():
    requests = [
        request(1, 1.0, BASE_NOTES),
        {
            "event": "request",
            "number": 2,
            "t_sent": 2.0,
            "state_text": "分けられない文",
        },
        request(3, 3.0, BASE_NOTES),
    ]
    versions = build_note_versions(requests, [])
    assert [
        (version.version, version.request, version.changed, version.files)
        for version in versions
    ] == [
        (
            0,
            1,
            [],
            {"controls": "c1", "tips": EMPTY, "strategy": "s1", "advice": EMPTY},
        ),
        (1, 2, [], None),
        (
            2,
            3,
            [],
            {"controls": "c1", "tips": EMPTY, "strategy": "s1", "advice": EMPTY},
        ),
    ]


def test_build_note_versions_of_no_requests_is_empty():
    assert build_note_versions([], ADVISOR_RUNS) == []


# --- note_version_of_request ---


def test_note_version_of_request_is_last_version_started_by_then():
    versions = build_note_versions(REQUESTS, ADVISOR_RUNS)
    assert note_version_of_request(versions, 1, REQUESTS) == 0
    assert note_version_of_request(versions, 2, REQUESTS) == 0
    assert note_version_of_request(versions, 4, REQUESTS) == 2
    assert note_version_of_request(versions, 5, REQUESTS) == 2
    assert note_version_of_request(versions, 7, REQUESTS) == 3
    # state_text か t_sent の無い要求も、それより前に始まった版の時点にある
    assert note_version_of_request(versions, 10, REQUESTS) == 5
    assert note_version_of_request(versions, 11, REQUESTS) == 5
    assert note_version_of_request(versions, 12, REQUESTS) == 6


def test_note_version_of_request_is_none_for_unknown_number_or_before_first_version():
    requests = [request(1, 1.0, None), request(2, 2.0, BASE_NOTES)]
    versions = build_note_versions(requests, [])
    assert note_version_of_request(versions, 1, requests) is None
    assert note_version_of_request(versions, 2, requests) == 0
    assert note_version_of_request(versions, 99, requests) is None
    assert note_version_of_request([], 1, requests) is None


def test_request_whose_number_cannot_be_key_is_treated_as_missing():
    requests = [
        request(1, 1.0, BASE_NOTES),
        {**request(2, 2.0, {**BASE_NOTES, "strategy": "s2"}), "number": [2]},
        request(3, 3.0, {**BASE_NOTES, "strategy": "s2"}),
    ]
    versions = build_note_versions(requests, [])
    assert [(version.version, version.request) for version in versions] == [
        (0, 1),
        (1, [2]),
    ]
    # number が list の要求の行で始まった版は、始まった位置が分からないため、どの要求の時点の版にもならない
    assert note_version_of_request(versions, 3, requests) == 0
    assert note_version_of_request(versions, 1, requests) == 0
