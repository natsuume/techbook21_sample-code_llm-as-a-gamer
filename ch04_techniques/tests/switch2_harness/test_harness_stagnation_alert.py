import json
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
import pytest

from switch2_harness.event_log import CommandEvent
from switch2_harness.screen_change import change_ratio
from switch2_harness.stagnation_alert import (
    ALERT_COMMAND,
    CANDIDATE_NEAREST_COUNT,
    CANDIDATE_RECENT_COUNT,
    FEATURE_BLUR_SIGMA,
    FEATURE_SIZE,
    LAST_NEW_SCREEN_FILE_NAME,
    STAGNATION_SECONDS,
    NewScreenTracker,
    alert_line,
    alert_seconds,
    build_alert_event,
    candidate_positions,
    is_new_screen,
    read_last_new_screen_time,
    screen_feature,
    write_last_new_screen,
)

# 特徴の長さ (FEATURE_SIZE の幅 20 x 高さ 12)
FEATURE_LENGTH = 240

# 今の撮影の特徴の代わりの、すべて 0 の特徴
ZERO_FEATURE = np.zeros(FEATURE_LENGTH, dtype=np.float32)

ALERT_LINE_135 = "停滞の知らせ: 新しい画面が 135 秒出ていません"


def screen_a() -> np.ndarray:
    """模様のある 160x90 のグレースケールの縮小画像。"""
    image = np.zeros((90, 160), dtype=np.uint8)
    image[:, 80:] = 200
    image[45:, :40] = 100
    return image


def screen_b() -> np.ndarray:
    """screen_a() を反転した画像。すべての画素が screen_a() と 24 より大きく違う。"""
    return 255 - screen_a()


def changed_pixels(count: int) -> np.ndarray:
    """すべて 0 の 160x90 の画像の、先頭から count 画素を 255 にした画像を返す。"""
    image = np.zeros((90, 160), dtype=np.uint8)
    image.flat[:count] = 255
    return image


def make_features(values: Sequence[float]) -> np.ndarray:
    """行 i のすべての値が values[i] の、(len(values), 240) の float32 の特徴の配列を返す。

    今の撮影の特徴を ZERO_FEATURE にすると、行 i の特徴の差は abs(values[i]) になる。
    """
    column = np.asarray(values, dtype=np.float32)[:, np.newaxis]
    return np.repeat(column, FEATURE_LENGTH, axis=1)


def read_state(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "last-new-screen.json").read_text(encoding="utf-8"))


def test_constants():
    assert STAGNATION_SECONDS == 120.0
    assert CANDIDATE_NEAREST_COUNT == 20
    assert CANDIDATE_RECENT_COUNT == 5
    assert FEATURE_BLUR_SIGMA == 4.0
    assert FEATURE_SIZE == (20, 12)
    assert LAST_NEW_SCREEN_FILE_NAME == "last-new-screen.json"
    assert ALERT_COMMAND == "stagnation-alert"


# --- screen_feature ---


def test_screen_feature_returns_240_float32_values():
    feature = screen_feature(screen_a())
    assert feature.shape == (FEATURE_LENGTH,)
    assert feature.dtype == np.float32


def test_screen_feature_of_a_uniform_image_keeps_its_value():
    feature = screen_feature(np.full((90, 160), 100, dtype=np.uint8))
    assert np.allclose(feature, 100.0)


# --- candidate_positions ---


def test_candidate_positions_without_past_samples_is_empty():
    positions = candidate_positions(
        np.empty((0, FEATURE_LENGTH), dtype=np.float32), ZERO_FEATURE
    )
    assert positions.size == 0
    assert np.issubdtype(positions.dtype, np.integer)


@pytest.mark.parametrize("count", [1, 5, CANDIDATE_NEAREST_COUNT])
def test_candidate_positions_returns_every_position_of_few_samples(count):
    rng = np.random.default_rng(0)
    features = rng.random((count, FEATURE_LENGTH), dtype=np.float32) * 255
    positions = candidate_positions(features, ZERO_FEATURE)
    assert positions.tolist() == list(range(count))
    assert np.issubdtype(positions.dtype, np.integer)


def test_candidate_positions_returns_the_nearest_and_the_most_recent():
    # 偶数の行 (0, 2, ..., 38 の 20 行) は特徴の差が小さく (0〜38)、奇数の行は大きい (101〜139)。
    # 直前の 5 行 (35〜39) のうち 35・37・39 は、特徴の差が小さい 20 行に入らない
    values = [float(i) if i % 2 == 0 else 100.0 + i for i in range(40)]
    positions = candidate_positions(make_features(values), ZERO_FEATURE)
    nearest = set(range(0, 40, 2))
    recent = set(range(35, 40))
    assert positions.tolist() == sorted(nearest | recent)
    assert np.issubdtype(positions.dtype, np.integer)


# --- is_new_screen ---


def test_is_new_screen_without_candidates():
    assert is_new_screen(screen_a(), []) is True


def test_is_new_screen_is_false_if_any_candidate_is_almost_the_same():
    sample = changed_pixels(0)
    candidates = [screen_b(), changed_pixels(10), screen_a()]
    assert is_new_screen(sample, candidates) is False


def test_is_new_screen_is_true_if_every_candidate_differs():
    sample = changed_pixels(0)
    candidates = [screen_b(), changed_pixels(5000), screen_a()]
    assert is_new_screen(sample, candidates) is True


@pytest.mark.parametrize(("count", "new"), [(144, True), (143, False)])
def test_is_new_screen_at_one_percent_of_changed_pixels(count, new):
    sample = changed_pixels(0)
    candidate = changed_pixels(count)
    # 前提: 変わった画素の数を 160 * 90 で割ったものが、そのまま変化の割合になる
    assert change_ratio(candidate, sample) == count / (160 * 90)
    assert is_new_screen(sample, [candidate]) is new


# --- alert_seconds / alert_line / build_alert_event ---


@pytest.mark.parametrize(
    ("elapsed", "seconds"),
    [(119.9, None), (120.0, 120), (135.7, 135), (-5.0, None)],
)
def test_alert_seconds(elapsed, seconds):
    last_new_time = 1000.0
    result = alert_seconds(last_new_time + elapsed, last_new_time)
    assert result == seconds
    assert type(result) is type(seconds)


def test_alert_line():
    assert alert_line(135) == ALERT_LINE_135


def test_build_alert_event():
    assert build_alert_event(1000.5, "frames/000003.png", 135) == CommandEvent(
        time=1000.5,
        command="stagnation-alert",
        args=("135",),
        reply=ALERT_LINE_135,
        ok=True,
        elapsed_s=0.0,
        frame="frames/000003.png",
    )


# --- write_last_new_screen / read_last_new_screen_time ---


def test_write_then_read_last_new_screen(tmp_path):
    write_last_new_screen(tmp_path, 3, 1234.5, "000003.png")
    assert read_last_new_screen_time(tmp_path) == 1234.5
    assert read_state(tmp_path) == {"index": 3, "time": 1234.5, "file": "000003.png"}
    # 一時ファイル (.tmp) は置き換えで無くなり、状態ファイルだけが残る
    assert sorted(path.name for path in tmp_path.iterdir()) == ["last-new-screen.json"]


def test_read_last_new_screen_time_without_the_file(tmp_path):
    assert read_last_new_screen_time(tmp_path) is None


@pytest.mark.parametrize(
    "text",
    [
        '{"index": 1, "time": 1000',
        "[1000.0]",
        '{"index": 1, "time": "1000.0", "file": "000001.png"}',
        '{"index": 1, "time": true, "file": "000001.png"}',
    ],
    ids=["broken-json", "not-an-object", "time-is-a-string", "time-is-a-bool"],
)
def test_read_last_new_screen_time_of_an_unusable_file(tmp_path, text):
    (tmp_path / "last-new-screen.json").write_text(text, encoding="utf-8")
    assert read_last_new_screen_time(tmp_path) is None


# --- NewScreenTracker ---


def test_new_screen_tracker_writes_the_state_only_for_new_screens(tmp_path):
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    for name, image in [
        ("000001.png", screen_a()),
        ("000002.png", screen_a()),
        ("000003.png", screen_b()),
        ("000004.png", screen_a()),
    ]:
        assert cv2.imwrite(str(run_dir / name), image)
    tracker = NewScreenTracker(run_dir)
    # 最初の撮影は新しい画面
    assert tracker.add_sample("000001.png", 1, 1000.5) is True
    first_state = {"index": 1, "time": 1000.5, "file": "000001.png"}
    assert read_state(run_dir) == first_state
    # 同じ画像の 2 枚目は新しい画面でなく、状態ファイルは変わらない
    assert tracker.add_sample("000002.png", 2, 1001.0) is False
    assert read_state(run_dir) == first_state
    # 違う画像の 3 枚目は新しい画面で、状態ファイルが変わる
    assert tracker.add_sample("000003.png", 3, 1001.5) is True
    third_state = {"index": 3, "time": 1001.5, "file": "000003.png"}
    assert read_state(run_dir) == third_state
    # 1 枚目と同じ画像に戻った 4 枚目は新しい画面でない
    assert tracker.add_sample("000004.png", 4, 1002.0) is False
    assert read_state(run_dir) == third_state
