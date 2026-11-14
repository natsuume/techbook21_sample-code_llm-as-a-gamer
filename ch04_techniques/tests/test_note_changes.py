from switch2_agent.timeline.note_changes import NoteChangeTracker, format_note_changes

NOTES = {"controls": "A で決定", "tips": "", "strategy": "右へ進む", "advice": ""}


def test_format_note_changes_is_empty_before_observe():
    assert format_note_changes(NoteChangeTracker(), now=1.0, origin=0.0) == ""


def test_first_observe_records_start_without_changes():
    tracker = NoteChangeTracker()
    tracker.observe(NOTES, now=12.0)
    assert tracker.changed_at == {}
    assert format_note_changes(tracker, now=20.0, origin=10.0) == (
        "## ゲームの情報の更新 (最後に書き換わった時刻)\n"
        "- ゲームの操作方法: この実行の間は書き換わっていない (実行の開始 t=2.0 秒 (8.0 秒前) から同じ)\n"
        "- 攻略情報・Tips: この実行の間は書き換わっていない (実行の開始 t=2.0 秒 (8.0 秒前) から同じ)\n"
        "- 目標・進行状況・戦略: この実行の間は書き換わっていない (実行の開始 t=2.0 秒 (8.0 秒前) から同じ)\n"
        "- 問題の解決の助言: この実行の間は書き換わっていない (実行の開始 t=2.0 秒 (8.0 秒前) から同じ)"
    )


def test_observe_records_time_of_changed_kinds_only():
    tracker = NoteChangeTracker()
    tracker.observe(NOTES, now=12.0)
    tracker.observe({**NOTES, "strategy": "左へ進む"}, now=15.0)
    tracker.observe({**NOTES, "strategy": "左へ進む"}, now=18.0)
    assert tracker.changed_at == {"strategy": 15.0}
    lines = format_note_changes(tracker, now=20.0, origin=10.0).splitlines()
    assert lines[3] == "- 目標・進行状況・戦略: t=5.0 秒 (5.0 秒前) に書き換わった"


def test_observe_ignores_surrounding_whitespace_and_missing_kinds():
    tracker = NoteChangeTracker()
    tracker.observe({"controls": "A で決定"}, now=1.0)
    tracker.observe({"controls": "A で決定\n\n", "tips": "  "}, now=2.0)
    assert tracker.changed_at == {}
