from switch2_agent.default_notes import DEFAULT_STRATEGY_TEXT
from switch2_agent.timeline.subgoals import (
    SubgoalTracker,
    format_subgoals,
    parse_subgoals,
)

# advisor が実機の試行 (2026-11-14) で書いた strategy.md の目標の節 (smb-r3-jev)
SMB_STRATEGY = """\
### 目標

- 大目標: スーパーマリオブラザーズ (ファミコン) を遊び、ゲームを先に進める (状態: 途中)
  - 中目標: ソフトの一覧で「スーパーマリオブラザーズ」を選んで始める (状態: 達成)
  - 中目標: 始め直した 1-1 を、3 本目の土管の右まで進める (状態: 途中)
    - 小目標: 最初のクリボーを越える (状態: 達成)
    - 小目標: 1 本目 (低い) の土管を越える (状態: 達成。根拠: ×2 で 2 本目の土管の左まで進んだ (直近 1/5〜5/5))
    - 小目標: 2 本目 (高い) の土管に乗るか越える (状態: 途中)

### 戦略

- 暗転中は何も押さない。外の町に出たら、この小目標は達成
"""


def test_parse_subgoals_reads_names_and_statuses_from_advisor_notes():
    assert parse_subgoals(SMB_STRATEGY) == {
        "最初のクリボーを越える": "達成",
        "1 本目 (低い) の土管を越える": "達成",
        "2 本目 (高い) の土管に乗るか越える": "途中",
    }


def test_parse_subgoals_reads_default_notes():
    assert parse_subgoals(DEFAULT_STRATEGY_TEXT) == {
        "操作方法を把握する。ボタンとスティックを 1 つずつ試し、画面の反応を確かめる": "途中"
    }


def test_parse_subgoals_accepts_full_width_brackets_and_colons():
    assert parse_subgoals("- 小目標：扉を開ける（状態：未着手）") == {
        "扉を開ける": "未着手"
    }


def test_parse_subgoals_returns_empty_without_subgoal_lines():
    assert parse_subgoals("右へ進む") == {}
    assert parse_subgoals("") == {}


def test_subgoals_present_at_start_are_marked_as_existing():
    tracker = SubgoalTracker()
    tracker.observe("- 小目標: 扉を開ける (状態: 途中)", now=12.0)
    assert format_subgoals(tracker, now=20.0, origin=10.0) == (
        "## 小目標の時刻\n"
        "- 「扉を開ける」: 実行の開始 (t=2.0 秒 (8.0 秒前)) にはすでにあった。"
        "状態が「途中」になったのは t=2.0 秒 (8.0 秒前)"
    )


def test_tracker_records_added_subgoals_and_status_changes():
    tracker = SubgoalTracker()
    tracker.observe("- 小目標: 扉を開ける (状態: 途中)", now=12.0)
    tracker.observe(
        "- 小目標: 扉を開ける (状態: 達成。扉が開いた)\n- 小目標: 外へ出る (状態: 途中)",
        now=15.0,
    )
    tracker.observe(
        "- 小目標: 扉を開ける (状態: 達成)\n- 小目標: 外へ出る (状態: 途中)", now=18.0
    )
    assert format_subgoals(tracker, now=20.0, origin=10.0).splitlines()[1:] == [
        (
            "- 「扉を開ける」: 実行の開始 (t=2.0 秒 (8.0 秒前)) にはすでにあった。"
            "状態が「達成」になったのは t=5.0 秒 (5.0 秒前)"
        ),
        (
            "- 「外へ出る」: t=5.0 秒 (5.0 秒前) に立てた。"
            "状態が「途中」になったのは t=5.0 秒 (5.0 秒前)"
        ),
    ]


def test_removed_subgoal_is_not_written_but_keeps_its_record():
    tracker = SubgoalTracker()
    tracker.observe("- 小目標: 扉を開ける (状態: 途中)", now=12.0)
    tracker.observe("目標は書き直し中", now=15.0)
    assert format_subgoals(tracker, now=20.0, origin=10.0) == ""
    tracker.observe("- 小目標: 扉を開ける (状態: 途中)", now=18.0)
    assert "実行の開始" in format_subgoals(tracker, now=20.0, origin=10.0)
