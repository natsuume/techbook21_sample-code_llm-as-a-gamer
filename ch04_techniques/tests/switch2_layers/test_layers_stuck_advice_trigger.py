import math

import pytest

from switch2_layers.progress.stuck_advice_trigger import (
    DEFAULT_STUCK_ADVICE_SECONDS,
    StuckAdviceTrigger,
)

THRESHOLD_S = 60.0


def test_default_threshold_follows_the_issue():
    assert DEFAULT_STUCK_ADVICE_SECONDS == 60.0


def test_no_value_does_not_request():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    assert trigger.check(100.0, None, advice_running=False) is False


def test_requests_only_after_exceeding_the_threshold():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    # ちょうどしきい値では要求しない (超えたら要求する)
    assert trigger.check(100.0, 60.0, advice_running=False) is False
    assert trigger.check(100.5, 60.001, advice_running=False) is True


def test_requests_again_every_threshold_seconds_while_exceeded():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    assert trigger.check(100.0, 61.0, advice_running=False) is True
    assert trigger.check(100.5, 61.5, advice_running=False) is False
    assert trigger.check(159.9, 120.9, advice_running=False) is False
    assert trigger.check(160.0, 121.0, advice_running=False) is True
    assert trigger.check(219.9, 180.9, advice_running=False) is False
    assert trigger.check(220.0, 181.0, advice_running=False) is True


def test_returning_below_the_threshold_allows_an_immediate_request():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    assert trigger.check(100.0, 61.0, advice_running=False) is True
    # 新しい画面が出て、しきい値以下に戻った
    assert trigger.check(105.0, 0.0, advice_running=False) is False
    # 前回の要求から 60 秒たっていなくても、次に超えたらすぐ要求する
    assert trigger.check(130.0, 60.5, advice_running=False) is True


def test_value_disappearing_also_resets():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    assert trigger.check(100.0, 61.0, advice_running=False) is True
    assert trigger.check(101.0, None, advice_running=False) is False
    assert trigger.check(102.0, 63.0, advice_running=False) is True


def test_running_advice_job_blocks_and_the_next_check_requests():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    # 超えたが、advice のジョブ (きっかけに依らない) が実行中
    assert trigger.check(100.0, 61.0, advice_running=True) is False
    assert trigger.check(130.0, 91.0, advice_running=True) is False
    # ジョブが終わった後の判定で要求する
    assert trigger.check(130.5, 91.5, advice_running=False) is True


def test_running_job_does_not_shift_the_next_request():
    trigger = StuckAdviceTrigger(THRESHOLD_S)
    assert trigger.check(100.0, 61.0, advice_running=False) is True
    # 要求したジョブが 100 秒動いた。終わった後の判定で、前回の要求から 60 秒たっていれば要求する
    assert trigger.check(150.0, 111.0, advice_running=True) is False
    assert trigger.check(200.0, 161.0, advice_running=False) is True


@pytest.mark.parametrize("threshold_s", [0.0, -1.0, math.inf, math.nan])
def test_threshold_must_be_finite_and_positive(threshold_s):
    with pytest.raises(ValueError):
        StuckAdviceTrigger(threshold_s)
