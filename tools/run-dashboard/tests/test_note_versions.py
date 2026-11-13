import pytest
from switch2_agent.game_notes import format_game_notes

from run_dashboard.advisor_runs import AdvisorRun
from run_dashboard.note_versions import (
    EXTERNAL_CHANGE,
    NOTE_CHANGE_GRACE_S,
    NoteVersion,
    build_note_versions,
    note_version_of_request,
    split_state_text,
)

EMPTY = "(まだ書かれていない)"


def test_constants():
    assert NOTE_CHANGE_GRACE_S == 1.0
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
    # 3.0〜6.0 秒の strategy の実行 (終了の 1 秒後の 7.0 秒までの書き換えも、この実行のものとみなす)
    advisor_run("advisor-strategy-001", "strategy", 3.0, 6.0),
    # 8.0 秒からの実行中の tips の実行
    advisor_run("advisor-tips-001", "tips", 8.0, None),
    # 10.0 秒からの controls の実行 (9.0 秒の書き換えより後に始まる)
    advisor_run("advisor-controls-001", "controls", 10.0, None),
    # 開始の分からない advice の実行
    advisor_run("advisor-advice-001", "advice", None, None),
]
REQUESTS = [
    request(1, 1.0, BASE_NOTES),
    # 前の版と同じ
    request(2, 2.0, BASE_NOTES),
    # strategy の実行の開始より前
    request(3, 2.5, {**BASE_NOTES, "strategy": "s2"}),
    # strategy の実行中
    request(4, 5.0, {**BASE_NOTES, "strategy": "s3"}),
    # strategy の実行の終了のちょうど 1 秒後
    request(5, 7.0, {**BASE_NOTES, "strategy": "s4"}),
    # strategy の実行の終了から 1 秒を過ぎた後
    request(6, 7.25, {**BASE_NOTES, "strategy": "s5"}),
    # tips は実行中 (t_end が None) の tips の実行、controls はその後に始まる実行しか無い
    request(7, 9.0, {**BASE_NOTES, "strategy": "s5", "tips": "t1", "controls": "c2"}),
    # state_text か t_sent の無い行は使わない
    request(8, None, {**BASE_NOTES, "advice": "a1"}),
    request(9, 9.5, None),
    # advice は開始の分からない実行しか無い
    request(
        10,
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
        (3, 7.0, 5, False, [{"kind": "strategy", "by": "advisor-strategy-001"}]),
        (4, 7.25, 6, False, [{"kind": "strategy", "by": "external"}]),
        (
            5,
            9.0,
            7,
            False,
            [
                {"kind": "controls", "by": "external"},
                {"kind": "tips", "by": "advisor-tips-001"},
            ],
        ),
        (6, 11.0, 10, False, [{"kind": "advice", "by": "external"}]),
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
    # state_text の無い要求も、それより前に始まった版の時点にある
    assert note_version_of_request(versions, 9, REQUESTS) == 5
    assert note_version_of_request(versions, 10, REQUESTS) == 6


def test_note_version_of_request_is_none_for_unknown_number_or_before_first_version():
    requests = [request(1, 1.0, None), request(2, 2.0, BASE_NOTES)]
    versions = build_note_versions(requests, [])
    assert note_version_of_request(versions, 1, requests) is None
    assert note_version_of_request(versions, 2, requests) == 0
    assert note_version_of_request(versions, 99, requests) is None
    assert note_version_of_request([], 1, requests) is None
