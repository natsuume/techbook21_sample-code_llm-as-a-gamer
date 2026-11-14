from switch2_agent.timeline.advisor_calls import (
    AdvisorCallTracker,
    format_advisor_calls,
)


def test_format_advisor_calls_before_any_call():
    lines = format_advisor_calls(AdvisorCallTracker(), now=1.0, origin=0.0).splitlines()
    assert lines[0] == "## advisor を最後に呼んだ時刻"
    assert lines[1:] == [
        "- 操作方法の更新: この実行ではまだ呼んでいない",
        "- 目標・戦略の更新: この実行ではまだ呼んでいない",
        "- 問題の解決の助言: この実行ではまだ呼んでいない",
        "- 攻略情報・Tips の更新: この実行ではまだ呼んでいない",
    ]


def test_format_advisor_calls_writes_latest_call_of_each_role():
    tracker = AdvisorCallTracker()
    tracker.record("strategy", 15.0)
    tracker.record("strategy", 18.0)
    tracker.record("tips", 12.0)
    assert format_advisor_calls(tracker, now=20.0, origin=10.0) == (
        "## advisor を最後に呼んだ時刻\n"
        "- 操作方法の更新: この実行ではまだ呼んでいない\n"
        "- 目標・戦略の更新: t=8.0 秒 (2.0 秒前)\n"
        "- 問題の解決の助言: この実行ではまだ呼んでいない\n"
        "- 攻略情報・Tips の更新: t=2.0 秒 (8.0 秒前)"
    )
