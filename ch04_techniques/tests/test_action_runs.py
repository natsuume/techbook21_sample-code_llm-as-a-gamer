from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.operation_log import NeutralRecord, OperationRecord
from switch2_agent.timeline.action_runs import (
    ActionRun,
    format_action_runs,
    normalize_action,
    stick_direction_text,
    summarize_action_runs,
)

HEADING = (
    "## もっと前からの操作のまとめ (直近 120 秒。"
    "同じ操作が続いた区間ごと、古い順。スティックの強さは省く)"
)


def make_action(
    buttons: set[str] | None = None,
    sticks: dict[str, tuple[float, float]] | None = None,
    taps: set[str] | None = None,
) -> PadAction:
    return PadAction(
        held=PadState(frozenset(buttons or ()), dict(sticks or {})),
        taps=frozenset(taps or ()),
        advisor_probabilities={role.name: 0.0 for role in ADVISOR_ROLES},
        advisor_requested=frozenset(),
    )


def record(received_at: float, action: PadAction) -> OperationRecord:
    return OperationRecord(received_at - 0.2, received_at, action)


def test_stick_direction_text_writes_vertical_then_horizontal():
    assert stick_direction_text("LS", 0.0, -0.55) == "左スティック下"
    assert stick_direction_text("RS", 0.5, 0.5) == "右スティック上右"
    assert stick_direction_text("LS", -1.0, 0.0) == "左スティック左"
    assert stick_direction_text("LS", 0.0, 0.0) == "左スティック"


def test_normalize_action_drops_stick_magnitude():
    a = make_action(sticks={"LS": (0.0, -0.55)}, taps={"DOWN"})
    b = make_action(sticks={"LS": (0.0, -0.5725)}, taps={"DOWN"})
    assert normalize_action(a) == "押下 左スティック下 / 短押し DOWN"
    assert normalize_action(a) == normalize_action(b)


def test_normalize_action_orders_buttons_and_writes_none():
    action = make_action(
        buttons={"RIGHT", "A"}, sticks={"LS": (0.6, 0.0)}, taps={"B", "X"}
    )
    assert normalize_action(action) == "押下 A+RIGHT+左スティック右 / 短押し B+X"
    assert normalize_action(make_action()) == "押下 none / 短押し none"


def test_summarize_action_runs_merges_same_actions_and_splits_on_neutral():
    down = make_action(sticks={"LS": (0.0, -0.5)}, taps={"DOWN"})
    down2 = make_action(sticks={"LS": (0.0, -0.6)}, taps={"DOWN"})
    records = [
        record(10.0, make_action()),
        record(10.4, down),
        record(10.8, down2),
        NeutralRecord(11.0, "Decision Model の失敗"),
        record(11.4, down),
    ]
    assert summarize_action_runs(records) == [
        ActionRun(10.0, 10.0, 1, "押下 none / 短押し none"),
        ActionRun(10.4, 10.8, 2, "押下 左スティック下 / 短押し DOWN"),
        ActionRun(11.0, 11.0, 1, "中立に戻した (Decision Model の失敗)"),
        ActionRun(11.4, 11.4, 1, "押下 左スティック下 / 短押し DOWN"),
    ]
    assert summarize_action_runs([]) == []


def test_format_action_runs_writes_runs_and_marks_last_as_continuing():
    runs = [
        ActionRun(110.0, 128.0, 41, "押下 none / 短押し none"),
        ActionRun(130.5, 229.8, 235, "押下 左スティック下 / 短押し DOWN"),
    ]
    assert format_action_runs(runs, origin=30.0, window_s=120.0, max_runs=8) == (
        f"{HEADING}\n"
        "- t=80.0〜98.0 秒 (18.0 秒、41 回): 押下 none / 短押し none\n"
        "- t=100.5〜199.8 秒 (99.3 秒、235 回): 押下 左スティック下 / 短押し DOWN (今も続いている)"
    )


def test_format_action_runs_omits_old_runs_over_limit():
    runs = [ActionRun(float(i), float(i), 1, f"op{i}") for i in range(5)]
    text = format_action_runs(runs, origin=0.0, window_s=120.0, max_runs=2)
    assert text.splitlines() == [
        HEADING,
        "- (それより前の 3 区間は省略)",
        "- t=3.0〜3.0 秒 (0.0 秒、1 回): op3",
        "- t=4.0〜4.0 秒 (0.0 秒、1 回): op4 (今も続いている)",
    ]


def test_format_action_runs_marks_truncated_log_and_empty_runs():
    runs = [ActionRun(1.0, 2.0, 3, "op")]
    lines = format_action_runs(
        runs, origin=0.0, window_s=120.0, max_runs=8, truncated=True
    ).splitlines()
    assert lines[1] == "- (これより前の記録は残っていない)"
    assert format_action_runs([], origin=0.0, window_s=60.0, max_runs=8) == (
        "## もっと前からの操作のまとめ (直近 60 秒。"
        "同じ操作が続いた区間ごと、古い順。スティックの強さは省く)\n(記録なし)"
    )
