from switch2_layers.middle.fake_middle import (
    FAKE_SCENE_NAME,
    FAKE_SURFACE_WAIT_S,
    FakeMiddleAnalyzer,
)
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput


def middle_input(t: float) -> MiddleInput:
    return MiddleInput(
        t=t,
        frames=(),
        surface_rows=(),
        operation_rows=(),
        scene_change_reported=False,
        scene_labels=(),
        previous_state=None,
        strategy="",
        current_tasks="",
        surface_rows_until_t=None,
        operations_rows_until_t=None,
    )


def test_fake_middle_returns_fixed_scene_and_wait():
    assert FAKE_SURFACE_WAIT_S == 0.2
    analyzer = FakeMiddleAnalyzer()
    expected = MiddleOutput(
        scene_name=FAKE_SCENE_NAME,
        is_stuck=False,
        stuck_description="",
        surface_wait_s=0.2,
        timeline="",
        tasks="",
    )
    assert analyzer.analyze(middle_input(1.0)) == expected
    # 入力によらず同じ出力
    assert analyzer.analyze(middle_input(99.0)) == expected
