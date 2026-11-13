from switch2_remote.controller_state import PadState

from switch2_agent.action_decision import PadAction
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.operation_log import (
    NeutralRecord,
    OperationLog,
    OperationRecord,
    format_action,
    format_record,
    record_time,
)


def make_action(
    buttons: set[str] | None = None,
    sticks: dict[str, tuple[float, float]] | None = None,
    taps: set[str] | None = None,
    **advisor_probabilities: float,
) -> PadAction:
    """操作を返す。advisor の問いの確率は、advisor_probabilities (役割の name → 確率) の役割のほかは 0.0 にする。"""
    probabilities = {role.name: 0.0 for role in ADVISOR_ROLES}
    probabilities.update(advisor_probabilities)
    return PadAction(
        held=PadState(frozenset(buttons or ()), dict(sticks or {})),
        taps=frozenset(taps or ()),
        advisor_probabilities=probabilities,
        advisor_requested=frozenset(),
    )


def record(received_at: float, action: PadAction | None = None) -> OperationRecord:
    return OperationRecord(
        sent_at=received_at - 0.3,
        received_at=received_at,
        action=action if action is not None else make_action(),
    )


def neutral(
    neutralized_at: float, reason: str = "Decision Model の失敗"
) -> NeutralRecord:
    return NeutralRecord(neutralized_at=neutralized_at, reason=reason)


def received(records: list[OperationRecord]) -> list[float]:
    return [r.received_at for r in records]


def test_since_returns_records_received_after_time():
    log = OperationLog()
    for t in [1.0, 2.0, 3.0]:
        log.add(record(t))
    assert received(log.since(1.5)) == [2.0, 3.0]
    assert log.since(3.5) == []


def test_since_excludes_record_received_exactly_at_time():
    log = OperationLog()
    for t in [1.0, 2.0, 3.0]:
        log.add(record(t))
    assert received(log.since(2.0)) == [3.0]


def test_log_drops_oldest_record_over_capacity():
    log = OperationLog(capacity=2)
    for t in [1.0, 2.0, 3.0]:
        log.add(record(t))
    assert received(log.since(0.0)) == [2.0, 3.0]


def test_default_capacity_is_500():
    assert OperationLog().capacity == 500


def test_recent_returns_newest_records_oldest_first():
    log = OperationLog()
    for t in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]:
        log.add(record(t))
    assert received(log.recent(5)) == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_recent_returns_all_records_when_fewer_than_count():
    log = OperationLog()
    for t in [1.0, 2.0]:
        log.add(record(t))
    assert received(log.recent(5)) == [1.0, 2.0]


def test_recent_is_empty_without_records():
    assert OperationLog().recent(5) == []


def test_recent_returns_exactly_count_records():
    log = OperationLog()
    for t in [1.0, 2.0, 3.0]:
        log.add(record(t))
    assert received(log.recent(3)) == [1.0, 2.0, 3.0]
    assert received(log.recent(1)) == [3.0]


def test_recent_is_empty_for_count_of_zero_or_less():
    log = OperationLog()
    for t in [1.0, 2.0, 3.0]:
        log.add(record(t))
    assert log.recent(0) == []
    assert log.recent(-1) == []


def test_format_action_writes_held_and_taps():
    action = make_action(buttons={"A"}, sticks={"LS": (0.8, 0.0)}, taps={"B"})
    assert format_action(action) == "押下 A+LS:0.8,0 / 短押し B"


def test_format_action_writes_none_for_empty_action():
    assert format_action(make_action()) == "押下 none / 短押し none"


def test_format_action_orders_taps_like_format_state():
    assert format_action(make_action(taps={"UP", "A"})) == "押下 none / 短押し A+UP"


def test_format_since_writes_one_line_per_record():
    log = OperationLog()
    log.add(record(1.0))
    log.add(
        record(
            6.79,
            make_action(
                buttons={"A"},
                sticks={"LS": (0.8, 0.0)},
                taps={"B"},
                strategy=0.12,
            ),
        )
    )
    log.add(record(9.5, make_action(strategy=0.9)))
    assert log.format_since(2.0, 10.0, "strategy") == (
        "-3.21 秒: 押下 A+LS:0.8,0 / 短押し B / advisor 0.12\n"
        "-0.50 秒: 押下 none / 短押し none / advisor 0.90"
    )


def test_format_since_writes_probability_of_given_role():
    log = OperationLog()
    log.add(
        record(9.5, make_action(controls=0.25, strategy=0.5, advice=0.75, tips=0.4))
    )
    # 行の advisor の確率は、渡した役割の問いの確率
    assert log.format_since(0.0, 10.0, "controls") == (
        "-0.50 秒: 押下 none / 短押し none / advisor 0.25"
    )
    assert log.format_since(0.0, 10.0, "advice") == (
        "-0.50 秒: 押下 none / 短押し none / advisor 0.75"
    )
    assert log.format_since(0.0, 10.0, "tips") == (
        "-0.50 秒: 押下 none / 短押し none / advisor 0.40"
    )


def test_format_since_is_empty_without_records():
    log = OperationLog()
    log.add(record(1.0))
    assert log.format_since(1.0, 2.0, "strategy") == ""


# --- 中立に戻した記録 ---


def test_record_time_is_received_at_or_neutralized_at():
    assert record_time(record(3.0)) == 3.0
    assert record_time(neutral(4.5)) == 4.5


def test_format_record_writes_action_or_neutral_reason():
    action = make_action(buttons={"RIGHT"})
    assert format_record(record(1.0, action)) == format_action(action)
    assert format_record(neutral(2.0)) == "中立に戻した (Decision Model の失敗)"
    assert (
        format_record(neutral(2.0, "コントローラーの接続の復旧待ち"))
        == "中立に戻した (コントローラーの接続の復旧待ち)"
    )


def test_since_and_recent_order_neutral_records_by_neutralized_at():
    log = OperationLog()
    log.add(record(1.0))
    log.add(neutral(2.0))
    log.add(record(3.0))
    assert log.since(1.5) == [neutral(2.0), record(3.0)]
    # 中立に戻した時刻ちょうどの time では、その記録を含めない
    assert log.since(2.0) == [record(3.0)]
    assert log.recent(2) == [neutral(2.0), record(3.0)]


def test_format_since_writes_neutral_record_without_advisor_probability():
    log = OperationLog()
    log.add(
        record(
            6.79,
            make_action(
                buttons={"A"},
                sticks={"LS": (0.8, 0.0)},
                taps={"B"},
                strategy=0.12,
            ),
        )
    )
    log.add(neutral(8.95))
    assert log.format_since(2.0, 10.0, "strategy") == (
        "-3.21 秒: 押下 A+LS:0.8,0 / 短押し B / advisor 0.12\n"
        "-1.05 秒: 中立に戻した (Decision Model の失敗)"
    )
