"""追跡の状態と、問い直す対象の選び方。追いつかせ (早送り) は、合成の画像と NCC の追跡で確かめる。

合成の画像は test_layers_box_tracker.py と同じ作り方 (乱数の種を固定した背景と、模様のある四角い対象)。
"""

import numpy as np
import pytest

from switch2_layers.frames.frame_buffer import CapturedFrame
from switch2_layers.surface.box_tracker import NormalizedBox, TrackingFrame, TrackResult
from switch2_layers.surface.box_tracking_state import (
    DEFAULT_RETAIN_S,
    BoxTrackingState,
    TargetStatus,
    select_requery_targets,
)
from switch2_layers.surface.surface_types import Box, BoxSource

FULL_WIDTH = 1280
FULL_HEIGHT = 720
SPRITE_SIZE = 48
SPRITE_Y = 300
STEP = 8


def background() -> np.ndarray:
    rng = np.random.default_rng(1)
    base = np.linspace(60, 140, FULL_WIDTH, dtype=np.float32)[None, :]
    base = base.repeat(FULL_HEIGHT, axis=0)
    noise = rng.normal(0, 6, (FULL_HEIGHT, FULL_WIDTH)).astype(np.float32)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def sprite() -> np.ndarray:
    rng = np.random.default_rng(2)
    blocks = rng.integers(0, 2, (SPRITE_SIZE // 8, SPRITE_SIZE // 8)) * 255
    return np.kron(blocks, np.ones((8, 8))).astype(np.uint8)


def captured(t: float, x: int | None) -> CapturedFrame:
    y_plane = background()
    if x is not None:
        y_plane[SPRITE_Y : SPRITE_Y + SPRITE_SIZE, x : x + SPRITE_SIZE] = sprite()
    uv_plane = np.full((FULL_HEIGHT // 2, FULL_WIDTH), 128, np.uint8)
    return CapturedFrame(t=t, nv12=np.vstack([y_plane, uv_plane]))


def sprite_box(x: int) -> NormalizedBox:
    return NormalizedBox(
        left=x / FULL_WIDTH,
        top=SPRITE_Y / FULL_HEIGHT,
        right=(x + SPRITE_SIZE) / FULL_WIDTH,
        bottom=(SPRITE_Y + SPRITE_SIZE) / FULL_HEIGHT,
    )


def x_at(i: int) -> int:
    """i 番目 (t = i × 0.1) のフレームの対象の左端。"""
    return 200 + STEP * i


def t_at(i: int) -> float:
    return round(i * 0.1, 3)


def feed(
    state: BoxTrackingState, indexes: range, hidden: set[int] | None = None
) -> None:
    for i in indexes:
        state.on_frame(captured(t_at(i), None if i in (hidden or set()) else x_at(i)))


def center_error_px(box: NormalizedBox | Box, x: int) -> float:
    if isinstance(box, Box):
        box = NormalizedBox(box.x_min, box.y_min, box.x_max, box.y_max)
    expected = sprite_box(x)
    dx = ((box.left + box.right) - (expected.left + expected.right)) / 2 * FULL_WIDTH
    dy = ((box.top + box.bottom) - (expected.top + expected.bottom)) / 2 * FULL_HEIGHT
    return float(np.hypot(dx, dy))


class StillTracker:
    """BoxTracker の偽物。start の box をいつも見えているものとして返す (found_until の t より後は見失う)。"""

    def __init__(self, found_until: float = 1e9):
        self.found_until = found_until
        self.box: NormalizedBox | None = None
        self.updated: list[float] = []

    def start(self, frame: TrackingFrame, box: NormalizedBox) -> None:
        self.box = box

    def update(self, frame: TrackingFrame) -> TrackResult:
        assert self.box is not None
        self.updated.append(frame.t)
        found = frame.t <= self.found_until
        return TrackResult(self.box, 0.9 if found else 0.1, found)


# --- 問い直す対象の選び方 ---


def test_retain_seconds_cover_jev_timeout_and_frame_window():
    assert DEFAULT_RETAIN_S == 12.0


def test_targets_without_box_are_requeried():
    assert select_requery_targets(["自機", "敵"], {}, 1, 5) == ("自機", "敵")


def test_lost_targets_are_requeried():
    statuses = {
        "自機": TargetStatus(lost=False, last_requery_index=1),
        "敵": TargetStatus(lost=True, last_requery_index=1),
    }
    assert select_requery_targets(["自機", "敵"], statuses, 2, 5) == ("敵",)


def test_targets_are_requeried_every_n_requests():
    statuses = {"自機": TargetStatus(lost=False, last_requery_index=1)}
    # 番号 1 で問い直した対象は、2〜5 では問い直さず、6 で問い直す
    for index in range(2, 6):
        assert select_requery_targets(["自機"], statuses, index, 5) == ()
    assert select_requery_targets(["自機"], statuses, 6, 5) == ("自機",)
    assert select_requery_targets(["自機"], statuses, 7, 5) == ("自機",)


def test_requery_every_one_requeries_every_request():
    statuses = {"自機": TargetStatus(lost=False, last_requery_index=3)}
    assert select_requery_targets(["自機"], statuses, 4, 1) == ("自機",)


def test_requery_follows_target_order_and_ignores_unknown_statuses():
    statuses = {
        "宝箱": TargetStatus(lost=True, last_requery_index=1),
        "敵": TargetStatus(lost=False, last_requery_index=4),
    }
    assert select_requery_targets(["目標地点", "敵", "自機"], statuses, 5, 5) == (
        "目標地点",
        "自機",
    )


def test_requery_every_below_one_is_an_error():
    with pytest.raises(ValueError):
        select_requery_targets(["自機"], {}, 1, 0)


# --- 追跡の状態 ---


def test_invalid_retain_seconds_is_an_error():
    with pytest.raises(ValueError):
        BoxTrackingState(retain_s=0.0)


def test_new_state_has_no_targets():
    state = BoxTrackingState()
    assert state.statuses() == {}
    assert state.boxes_at(0.0) == ()


def test_jev_box_is_caught_up_to_latest_frame_with_ncc():
    state = BoxTrackingState()
    state.set_targets(["自機"])
    feed(state, range(11))
    # 問い合わせた画像は t = 0.3 のフレーム。応答が返る前に t = 1.0 まで届いている
    assert state.start_from_jev("自機", sprite_box(x_at(3)), t_at(3), request_index=1)
    for i in range(4, 11):
        result = state.result_at("自機", t_at(i))
        assert result is not None and result.found, f"not found at frame {i}"
        assert center_error_px(result.box, x_at(i)) <= 2.0
    # 問い合わせたフレーム自体の位置は記録しない
    assert state.result_at("自機", t_at(3)) is None
    assert state.statuses() == {"自機": TargetStatus(lost=False, last_requery_index=1)}
    # 追いついた後も、新しいフレームごとに追う
    feed(state, range(11, 13))
    result = state.result_at("自機", t_at(12))
    assert result is not None and result.found
    assert center_error_px(result.box, x_at(12)) <= 2.0


def test_loss_is_reported_in_status():
    state = BoxTrackingState()
    state.set_targets(["自機"])
    feed(state, range(3))
    assert state.start_from_jev("自機", sprite_box(x_at(2)), t_at(2), request_index=4)
    feed(state, range(3, 5), hidden={4})
    assert state.statuses()["自機"] == TargetStatus(lost=True, last_requery_index=4)
    assert state.boxes_at(t_at(4)) == ()


def test_start_fails_when_queried_frame_is_not_retained():
    state = BoxTrackingState(tracker_factory=StillTracker)
    state.set_targets(["自機"])
    feed(state, range(3))
    assert not state.start_from_jev("自機", sprite_box(x_at(5)), t_at(5), 1)
    assert state.statuses() == {}


def test_old_frames_are_dropped_after_retain_seconds():
    state = BoxTrackingState(tracker_factory=StillTracker, retain_s=1.0)
    state.set_targets(["自機"])
    feed(state, range(21))
    # 最新は t = 2.0。t = 0.5 は 1 秒より古いので捨てた
    assert not state.start_from_jev("自機", sprite_box(x_at(5)), t_at(5), 1)
    assert state.start_from_jev("自機", sprite_box(x_at(15)), t_at(15), 1)


def test_frames_not_newer_than_latest_are_ignored():
    trackers: list[StillTracker] = []

    def factory() -> StillTracker:
        trackers.append(StillTracker())
        return trackers[-1]

    state = BoxTrackingState(tracker_factory=factory)
    state.set_targets(["自機"])
    feed(state, range(3))
    assert state.start_from_jev("自機", sprite_box(x_at(0)), t_at(0), 1)
    state.on_frame(captured(t_at(1), x_at(1)))
    assert trackers[0].updated == [t_at(1), t_at(2)]


def test_requery_restarts_tracking_and_replaces_newer_positions():
    trackers: list[StillTracker] = []

    def factory() -> StillTracker:
        trackers.append(StillTracker())
        return trackers[-1]

    state = BoxTrackingState(tracker_factory=factory)
    state.set_targets(["自機"])
    feed(state, range(3))
    first = NormalizedBox(0.1, 0.1, 0.2, 0.2)
    second = NormalizedBox(0.5, 0.5, 0.6, 0.6)
    assert state.start_from_jev("自機", first, t_at(0), 1)
    feed(state, range(3, 6))
    assert state.start_from_jev("自機", second, t_at(3), 6)
    assert state.result_at("自機", t_at(2)).box == first
    assert state.result_at("自機", t_at(4)).box == second
    assert state.statuses()["自機"].last_requery_index == 6


def test_boxes_at_returns_found_targets_in_target_order_as_tracker_boxes():
    state = BoxTrackingState(tracker_factory=StillTracker)
    state.set_targets(["敵", "自機"])
    feed(state, range(3))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    assert state.start_from_jev("敵", NormalizedBox(0.5, 0.6, 0.7, 0.8), t_at(0), 1)
    assert state.boxes_at(t_at(2)) == (
        Box("敵", 0.5, 0.6, 0.7, 0.8, 0.9, BoxSource.TRACKER),
        Box("自機", 0.1, 0.2, 0.3, 0.4, 0.9, BoxSource.TRACKER),
    )
    # 位置が無い時刻には描かない
    assert state.boxes_at(t_at(9)) == ()


def test_boxes_at_clamps_score_to_confidence_range():
    class OverconfidentTracker(StillTracker):
        def update(self, frame: TrackingFrame) -> TrackResult:
            assert self.box is not None
            return TrackResult(self.box, 1.0000001, True)

    state = BoxTrackingState(tracker_factory=OverconfidentTracker)
    state.set_targets(["自機"])
    feed(state, range(2))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    assert state.boxes_at(t_at(1))[0].confidence == 1.0


def test_set_targets_forgets_removed_targets():
    state = BoxTrackingState(tracker_factory=StillTracker)
    state.set_targets(["自機", "敵"])
    feed(state, range(2))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    assert state.start_from_jev("敵", NormalizedBox(0.5, 0.6, 0.7, 0.8), t_at(0), 1)
    state.set_targets(["敵", "扉"])
    assert set(state.statuses()) == {"敵"}
    assert [box.label for box in state.boxes_at(t_at(1))] == ["敵"]


def test_forget_drops_tracking_and_positions():
    state = BoxTrackingState(tracker_factory=StillTracker)
    state.set_targets(["自機"])
    feed(state, range(2))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    state.forget("自機")
    state.forget("いない対象")
    assert state.statuses() == {}
    assert state.result_at("自機", t_at(1)) is None


def test_tracker_error_stops_only_that_target():
    class BrokenTracker(StillTracker):
        def update(self, frame: TrackingFrame) -> TrackResult:
            raise RuntimeError("broken")

    made: list[str] = []

    def factory() -> StillTracker:
        made.append("x")
        return BrokenTracker() if len(made) == 1 else StillTracker()

    state = BoxTrackingState(tracker_factory=factory)
    state.set_targets(["自機", "敵"])
    feed(state, range(1))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    assert state.start_from_jev("敵", NormalizedBox(0.5, 0.6, 0.7, 0.8), t_at(0), 1)
    feed(state, range(1, 2))
    assert set(state.statuses()) == {"敵"}


def test_restart_replaces_position_at_queried_frame_with_jev_box():
    trackers: list[StillTracker] = []

    def factory() -> StillTracker:
        trackers.append(StillTracker())
        return trackers[-1]

    state = BoxTrackingState(tracker_factory=factory)
    state.set_targets(["自機"])
    feed(state, range(3))
    first = NormalizedBox(0.1, 0.1, 0.2, 0.2)
    second = NormalizedBox(0.5, 0.5, 0.6, 0.6)
    assert state.start_from_jev("自機", first, t_at(0), 1)
    feed(state, range(3, 6))
    # t = 0.3 には、最初の追跡の位置がある
    assert state.result_at("自機", t_at(3)).box == first
    assert state.start_from_jev("自機", second, t_at(3), 6)
    # 問い合わせたフレームちょうどの古い追跡の位置は残らず、Jev の box (出どころ jev) になる
    assert state.result_at("自機", t_at(3)) is None
    assert state.boxes_at(t_at(3)) == (
        Box("自機", 0.5, 0.5, 0.6, 0.6, None, BoxSource.JEV),
    )
    # それより古いフレームは最初の追跡の位置のまま、新しいフレームは始め直した追跡の位置
    assert state.boxes_at(t_at(2)) == (
        Box("自機", 0.1, 0.1, 0.2, 0.2, 0.9, BoxSource.TRACKER),
    )
    assert state.boxes_at(t_at(4)) == (
        Box("自機", 0.5, 0.5, 0.6, 0.6, 0.9, BoxSource.TRACKER),
    )
    # 最初に始めたフレームの Jev の box も残る
    assert state.boxes_at(t_at(0)) == (
        Box("自機", 0.1, 0.1, 0.2, 0.2, None, BoxSource.JEV),
    )


def test_jev_box_at_queried_frame_is_dropped_after_retain_seconds():
    state = BoxTrackingState(tracker_factory=StillTracker, retain_s=1.0)
    state.set_targets(["自機"])
    feed(state, range(3))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(2), 1)
    assert [box.source for box in state.boxes_at(t_at(2))] == [BoxSource.JEV]
    # 最新が t = 3.5 になると、t = 0.2 は 1 秒より古いので捨てる
    feed(state, range(3, 36))
    assert state.boxes_at(t_at(2)) == ()
    assert [box.source for box in state.boxes_at(t_at(35))] == [BoxSource.TRACKER]


def test_forget_drops_jev_box_at_queried_frame():
    state = BoxTrackingState(tracker_factory=StillTracker)
    state.set_targets(["自機"])
    feed(state, range(2))
    assert state.start_from_jev("自機", NormalizedBox(0.1, 0.2, 0.3, 0.4), t_at(0), 1)
    state.forget("自機")
    assert state.boxes_at(t_at(0)) == ()
