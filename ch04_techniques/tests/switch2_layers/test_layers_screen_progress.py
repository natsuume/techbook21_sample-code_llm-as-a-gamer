import math

import numpy as np
import pytest

from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.screen_progress import (
    DEFAULT_PROGRESS_WINDOW_S,
    RECENT_EXCLUDED_S,
    ScreenProgressTracker,
)

# ハッシュで動かすテストの近さの上限 (ハミング距離 1 までを同じ画面とみなす)
MAX_DISTANCE = 1
WINDOW_S = 10.0
# 互いに近くない画面のハッシュ (どの 2 つもハミング距離 4 以上)
SCREEN_A = 0b0000_0000
SCREEN_B = 0b1111_0000
SCREEN_C = 0b0000_1111
# SCREEN_A に近い画面 (ハミング距離 1)
SCREEN_A_NEAR = 0b0000_0001


def tracker(window_s: float = WINDOW_S) -> ScreenProgressTracker:
    return ScreenProgressTracker(window_s, max_distance=MAX_DISTANCE)


def feed(
    progress: ScreenProgressTracker, frames: list[tuple[float, int]]
) -> list[ProgressState]:
    return [progress.add_hash(t, screen) for t, screen in frames]


def test_constants_follow_the_issue():
    assert DEFAULT_PROGRESS_WINDOW_S == 300.0
    # 新しい画面かを決めるときに、比べる相手から外す直近の秒数
    assert RECENT_EXCLUDED_S == 2.0


def test_first_frame_is_a_new_screen():
    assert tracker().add_hash(5.0, SCREEN_A) == ProgressState(
        t=5.0, static_s=0.0, no_novel_s=0.0, window_s=WINDOW_S
    )


def test_frames_within_two_seconds_from_the_start_are_new_screens():
    # 実行を始めてから 2 秒以内のフレームは、比べる相手のフレームが無いため、同じ画面でも新しい画面
    states = feed(
        tracker(),
        [(0.0, SCREEN_A), (0.5, SCREEN_B), (1.0, SCREEN_A), (1.5, SCREEN_A)],
    )
    assert [state.no_novel_s for state in states] == [0.0, 0.0, 0.0, 0.0]
    # 時刻の差がちょうど 2 秒のフレーム (t=0.0) も、比べる相手から外す
    assert feed(tracker(), [(0.0, SCREEN_A), (2.0, SCREEN_A)])[-1].no_novel_s == 0.0


def test_frame_just_over_two_seconds_old_is_compared():
    states = feed(tracker(), [(0.0, SCREEN_A), (2.0, SCREEN_A), (2.001, SCREEN_A)])
    # t=2.001 は t=0.0 (差 2.001 秒) と比べ、近いため新しい画面ではない。最後の新しい画面は t=2.0
    assert states[-1].no_novel_s == 0.001


def test_two_second_boundary_uses_the_rounded_difference():
    # 4.001 − 2.001 は浮動小数点で 2.0 よりわずかに大きいが、ミリ秒に丸めた差は 2 秒で、比べる相手から外す
    assert 4.001 - 2.001 > 2.0
    states = feed(tracker(), [(2.001, SCREEN_A), (4.001, SCREEN_A)])
    assert states[-1].no_novel_s == 0.0


def test_unchanged_screen_counts_no_novel_after_two_seconds():
    frames = [(t / 2, SCREEN_A) for t in range(11)]  # t=0.0, 0.5, ..., 5.0
    states = feed(tracker(), frames)
    # 2 秒までは比べる相手が無く新しい画面。2 秒を過ぎた後は、最初のフレームと比べて増える
    assert [state.no_novel_s for state in states] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
        3.0,
    ]
    assert states[-1] == ProgressState(
        t=5.0, static_s=5.0, no_novel_s=3.0, window_s=WINDOW_S
    )


def test_gradually_changing_screen_keeps_showing_new_screens():
    # 少しずつ動き続ける画面 (スクロールやカメラのパン): 0.5 秒ごとに 1 ビットずつ変わり、隣どうしは近いが、
    # 2 秒より前のフレームとは近くない。直前のフレームに近いだけでは、新しい画面でなくならない
    frames = [(k / 2, (1 << k) - 1) for k in range(21)]  # t=0.0〜10.0
    states = feed(tracker(), frames)
    assert [state.no_novel_s for state in states] == [0.0] * 21
    # 隣どうしは近いため、画面が変わっていない秒数は 0 にならない (最新と近いのは直前の 1 枚)
    assert states[-1].static_s == 0.5


def test_near_screen_counts_as_the_same_screen():
    states = feed(tracker(), [(0.0, SCREEN_A), (3.0, SCREEN_A_NEAR)])
    assert states[-1].static_s == 3.0
    assert states[-1].no_novel_s == 3.0


def test_new_screen_resets_both_values():
    states = feed(
        tracker(), [(0.0, SCREEN_A), (3.0, SCREEN_A), (6.0, SCREEN_A), (9.0, SCREEN_B)]
    )
    assert states[-2] == ProgressState(
        t=6.0, static_s=6.0, no_novel_s=6.0, window_s=WINDOW_S
    )
    assert states[-1] == ProgressState(
        t=9.0, static_s=0.0, no_novel_s=0.0, window_s=WINDOW_S
    )


def test_going_back_and_forth_keeps_static_zero_and_no_novel_growing():
    # 左右に動きながら同じ範囲にとどまる停滞: 画面は毎回変わるが、過去に見た画面に戻っている
    frames = [(float(t), SCREEN_A if t % 2 == 0 else SCREEN_B) for t in range(8)]
    states = feed(tracker(), frames)
    assert [state.static_s for state in states] == [0.0] * 8
    # t=0〜2 は比べる相手が無く新しい画面。t=3 の B は、比べる相手 (t=0 の A) に近くないため新しい画面。
    # t=4 からは、2 秒より前に見た画面に戻っているため、新しい画面ではない
    assert [state.no_novel_s for state in states] == [
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        2.0,
        3.0,
        4.0,
    ]


def test_static_compares_with_the_latest_screen_not_with_each_neighbor():
    # 少しずつ変わる画面: 隣どうしは近いが、最新と 2 つ前は近くない
    states = feed(tracker(), [(0.0, 0b0000), (1.0, 0b0001), (2.0, 0b0011)])
    assert states[-1].static_s == 1.0


def test_static_is_zero_when_the_previous_frame_is_not_near():
    states = feed(
        tracker(), [(0.0, SCREEN_A), (1.0, SCREEN_A), (2.0, SCREEN_B), (3.0, SCREEN_A)]
    )
    # A に戻ったが、直前の B に近くない
    assert states[-1].static_s == 0.0
    # A は 2 秒より前 (t=0.0) に見ているため、新しい画面ではない (最後の新しい画面は t=2.0 の B)
    assert states[-1].no_novel_s == 1.0


def test_screen_within_the_window_is_not_new():
    frames = [(0.0, SCREEN_A)] + [(float(t), SCREEN_B) for t in range(1, 10)]
    progress = tracker()
    feed(progress, frames)
    # t=0.0 の A との時刻の差はちょうど窓の秒数 (10 秒) で、窓の中に数える (最後の新しい画面は t=3.0 の B)
    state = progress.add_hash(10.0, SCREEN_A)
    assert state.no_novel_s == 7.0


def test_screen_older_than_the_window_is_new_again():
    frames = [(0.0, SCREEN_A)] + [(float(t), SCREEN_B) for t in range(1, 10)]
    progress = tracker()
    feed(progress, frames)
    # t=0.0 の A は窓 (10 秒) より古いため、捨てた。A を再び見たら新しい画面
    state = progress.add_hash(10.5, SCREEN_A)
    assert state.no_novel_s == 0.0


def test_kept_hashes_never_exceed_the_window():
    progress = tracker()
    for t in range(31):
        progress.add_hash(float(t), SCREEN_A if t % 2 else SCREEN_B)
        # 最新との時刻の差が窓の秒数以下のフレームだけを持つ (1 秒ごとなら最大 11 枚)。直近 2 秒のフレームも持つ
        assert progress.kept_count <= 11
    assert progress.kept_count == 11


def test_static_is_counted_only_within_the_window():
    # 持つハッシュを窓の分だけにするため、変わらない時間が窓より長くても static_s は窓の秒数を超えない
    states = feed(tracker(), [(float(t), SCREEN_A) for t in range(31)])
    assert states[-1].static_s == WINDOW_S
    # no_novel_s は時刻で数えるため、窓の秒数を超える (最後の新しい画面は、比べる相手が無かった t=2.0)
    assert states[-1].no_novel_s == 28.0


def test_no_novel_keeps_growing_beyond_the_window_while_screens_repeat():
    frames = [(float(t), SCREEN_A if t % 2 else SCREEN_B) for t in range(41)]
    states = feed(tracker(), frames)
    # 最後に新しい画面が出たのは t=3.0 (比べる相手 t=0.0 の B に近くない A)
    assert states[-1].no_novel_s == 37.0
    assert states[-1].static_s == 0.0


def test_values_are_rounded_to_milliseconds():
    progress = tracker()
    progress.add_hash(0.1, SCREEN_A)
    state = progress.add_hash(0.1 + 0.2, SCREEN_A)
    assert state.t == 0.3
    assert state.static_s == 0.2
    assert state.no_novel_s == 0.0
    state = progress.add_hash(0.1 + 2.2, SCREEN_A)
    assert state.t == 2.3
    assert state.static_s == 2.2
    assert state.no_novel_s == 2.0


def test_new_screen_after_repeats_resets_no_novel():
    states = feed(
        tracker(),
        [(0.0, SCREEN_A), (3.0, SCREEN_B), (6.0, SCREEN_A), (9.0, SCREEN_C)],
    )
    assert states[-2].no_novel_s == 3.0
    assert states[-1].no_novel_s == 0.0


@pytest.mark.parametrize("t", [4.0, 3.9])
def test_frames_must_be_added_in_time_order(t):
    progress = tracker()
    progress.add_hash(4.0, SCREEN_A)
    with pytest.raises(ValueError):
        progress.add_hash(t, SCREEN_B)
    # 何も変えない
    assert progress.kept_count == 1
    assert progress.add_hash(7.0, SCREEN_A).no_novel_s == 3.0


@pytest.mark.parametrize("window_s", [0.0, -1.0, math.inf, math.nan])
def test_window_must_be_finite_and_positive(window_s):
    with pytest.raises(ValueError):
        ScreenProgressTracker(window_s)


# --- 画像を渡す ---


def striped_screen(phase: int) -> np.ndarray:
    """縦じまの BGR の画面 (phase で、しまの位置をずらす)。平らな画像は dHash がどれも同じになるため、模様を付ける。"""
    columns = np.arange(144)
    values = np.where(((columns // 16) + phase) % 2 == 0, 40, 200).astype(np.uint8)
    gray = np.tile(values, (80, 1))
    return np.stack([gray, gray, gray], axis=2)


def test_add_frame_hashes_the_image():
    progress = ScreenProgressTracker(WINDOW_S)
    first = progress.add_frame(0.0, striped_screen(0))
    same = progress.add_frame(3.0, striped_screen(0))
    other = progress.add_frame(6.0, striped_screen(1))
    back = progress.add_frame(9.0, striped_screen(0))
    assert first == ProgressState(0.0, 0.0, 0.0, WINDOW_S)
    assert same == ProgressState(3.0, 3.0, 3.0, WINDOW_S)
    # しまの位置が逆の画面は、明暗の向きがすべて逆で、新しい画面
    assert other == ProgressState(6.0, 0.0, 0.0, WINDOW_S)
    # 前に見た画面に戻った
    assert back == ProgressState(9.0, 0.0, 3.0, WINDOW_S)
