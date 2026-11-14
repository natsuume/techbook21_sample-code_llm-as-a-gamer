import numpy as np
import pytest
from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.frame_history import TimedFrame
from switch2_agent.operation_log import OperationLog, OperationRecord
from switch2_agent.timeline.builder import (
    SECTION_NAMES,
    TimelineBuilder,
    TimelineSettings,
)
from switch2_agent.timeline.screen_change import ScreenChangeTracker

NOTES = {
    "controls": "A で決定",
    "tips": "",
    "strategy": "- 小目標: 扉を開ける (状態: 途中)",
    "advice": "",
}


def down_action() -> PadAction:
    return PadAction(
        held=PadState(frozenset(), {"LS": (0.0, -0.55)}),
        taps=frozenset({"DOWN"}),
        advisor_probabilities={role.name: 0.0 for role in ADVISOR_ROLES},
        advisor_requested=frozenset(),
    )


def gray_frame(time: float) -> TimedFrame:
    return TimedFrame(
        time=time,
        nv12=np.zeros((3, 2), np.uint8),
        gray_small=np.zeros((4, 4), np.uint8),
        sharpness=0.0,
    )


def builder(sections: tuple[str, ...] = SECTION_NAMES, **settings) -> TimelineBuilder:
    log = OperationLog()
    for t in (101.0, 101.4, 101.8):
        log.add(OperationRecord(t - 0.2, t, down_action()))
    screen = ScreenChangeTracker()
    screen.feed(gray_frame(100.5))
    return TimelineBuilder(TimelineSettings(sections, **settings), log, screen)


def test_settings_reject_unknown_and_duplicate_sections_and_bad_values():
    with pytest.raises(ValueError):
        TimelineSettings(("actions", "unknown"))
    with pytest.raises(ValueError):
        TimelineSettings(("actions", "actions"))
    with pytest.raises(ValueError):
        TimelineSettings(action_window_s=0.0)
    with pytest.raises(ValueError):
        TimelineSettings(max_action_runs=0)


def test_screen_section_requires_screen_tracker():
    with pytest.raises(ValueError):
        TimelineBuilder(TimelineSettings(("screen",)), OperationLog())
    # screen の節が無ければ不要
    TimelineBuilder(TimelineSettings(("actions",)), OperationLog())


def test_build_is_empty_without_sections():
    assert builder(()).build(now=110.0, origin=100.0) == ""


def test_build_writes_sections_in_fixed_order_separated_by_blank_line():
    timeline = builder(("subgoals", "actions", "screen", "advisor_calls", "notes"))
    timeline.observe_notes(NOTES, now=101.0)
    timeline.record_advisor_call("strategy", 101.5)
    text = timeline.build(now=110.0, origin=100.0)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## もっと前からの操作のまとめ (直近 120 秒。同じ操作が続いた区間ごと、古い順。スティックの強さは省く)",
        "## 画面の変化",
        "## ゲームの情報の更新 (最後に書き換わった時刻)",
        "## advisor を最後に呼んだ時刻",
        "## 小目標の時刻",
    ]
    assert "\n\n## 画面の変化\n" in text
    assert (
        "- t=1.0〜1.8 秒 (0.8 秒、3 回): 押下 左スティック下 / 短押し DOWN (今も続いている)"
        in text
    )
    assert "- 最新の画面は、t=0.5 秒 (9.5 秒前) から、ほぼ変わっていない" in text


def test_build_skips_empty_sections():
    timeline = builder(("notes", "subgoals"))
    # まだ observe していないので、どちらの節も空
    assert timeline.build(now=110.0, origin=100.0) == ""


def test_actions_section_uses_only_records_in_window():
    # 窓の始まりは 110.0 - 8.7 = 101.3 秒。101.0 秒の記録は入らない
    timeline = builder(("actions",), action_window_s=8.7)
    text = timeline.build(now=110.0, origin=100.0)
    assert "(直近 8.7 秒。" in text
    assert "- t=1.4〜1.8 秒 (0.4 秒、2 回)" in text


def test_actions_section_marks_truncated_log():
    log = OperationLog(capacity=2)
    for t in (101.0, 101.4, 101.8):
        log.add(OperationRecord(t - 0.2, t, down_action()))
    timeline = TimelineBuilder(TimelineSettings(("actions",)), log)
    text = timeline.build(now=110.0, origin=100.0)
    assert "- (これより前の記録は残っていない)" in text
