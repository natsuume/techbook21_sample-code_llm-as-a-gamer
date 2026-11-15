from switch2_layers.run_clock import (
    RunClock,
    frozen_clock,
    round_to_milliseconds,
    t_to_milliseconds,
)


class FakeMonotonic:
    """time.monotonic の代わり。now を書き換えて時間を進める。"""

    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_round_to_milliseconds_keeps_three_decimals():
    assert round_to_milliseconds(1.23456) == 1.235
    assert round_to_milliseconds(2.0) == 2.0


def test_t_to_milliseconds():
    assert t_to_milliseconds(12.345) == 12345
    assert t_to_milliseconds(0.0) == 0


def test_now_is_elapsed_seconds_since_creation():
    monotonic = FakeMonotonic(100.0)
    clock = RunClock(monotonic)
    assert clock.origin == 100.0
    assert clock.now() == 0.0
    monotonic.now = 101.2346
    assert clock.now() == 1.235


def test_explicit_origin():
    monotonic = FakeMonotonic(50.0)
    clock = RunClock(monotonic, origin=40.0)
    assert clock.origin == 40.0
    assert clock.now() == 10.0


def test_t_of_converts_monotonic_value():
    clock = RunClock(FakeMonotonic(100.0))
    assert clock.t_of(103.5004) == 3.5
    # 起点より前の値は負の t になる
    assert clock.t_of(99.5) == -0.5


def test_frozen_clock_always_returns_t():
    clock = frozen_clock(42.125)
    assert clock.now() == 42.125
    assert clock.now() == 42.125
