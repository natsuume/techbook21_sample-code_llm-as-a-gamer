import json
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np
import pytest
from harness_fakes import FakeClock, FakeDaemon, make_paths

from switch2_harness.screen_recorder import (
    SHOT_TIMEOUT_S,
    next_run_dir,
    record_sample,
    recording_screens,
    run_recording,
    sample_file_name,
)

START_TIME = 1000.0

# recording_screens のテストで、samples.jsonl に最初の行が書かれるまで待つ上限の秒数
FIRST_LINE_TIMEOUT_S = 5.0

# recording_screens を抜けた後に、行が増えないことを確かめるまで待つ秒数
AFTER_EXIT_WAIT_S = 0.3


def shot_image() -> np.ndarray:
    """デーモンが保存する画面の代わりの、模様のある 320x180 のグレースケールの画像。"""
    image = np.zeros((180, 320), dtype=np.uint8)
    image[:, 160:] = 200
    image[90:, :80] = 100
    return image


class ShotWritingDaemon:
    """send の fake。shot PATH を受けると、PATH に shot_image() の PNG を書いて `ok PATH` を返す。

    送るたびに、送った引数を sent に、受け取った timeout_s を timeouts に記録し、clock を step_s 秒進める
    (撮影にかかった時間の代わり)。
    replies に呼び出しの回 (1 から数える) があれば、その回は PNG を書かずにその応答を返す。
    stop と stop_at_call を渡すと、その回の呼び出しで stop を set する (記録を止める合図の代わり)。
    max_calls を渡すと、呼ばれた回数がそれを超えたら AssertionError にし、ループの誤りでテストが止まらなくなるのを防ぐ。
    """

    def __init__(
        self,
        clock: FakeClock,
        *,
        step_s: float = 0.5,
        replies: Mapping[int, str] | None = None,
        stop: threading.Event | None = None,
        stop_at_call: int | None = None,
        max_calls: int | None = None,
    ) -> None:
        self.clock = clock
        self.step_s = step_s
        self.replies = dict(replies or {})
        self.stop = stop
        self.stop_at_call = stop_at_call
        self.max_calls = max_calls
        self.sent: list[list[str]] = []
        self.timeouts: list[float | None] = []

    def __call__(self, args: list[str], *, timeout_s: float | None = None) -> str:
        self.sent.append(list(args))
        self.timeouts.append(timeout_s)
        calls = len(self.sent)
        if self.max_calls is not None and calls > self.max_calls:
            raise AssertionError("send was called too many times")
        self.clock.advance(self.step_s)
        if self.stop is not None and calls == self.stop_at_call:
            self.stop.set()
        if calls in self.replies:
            return self.replies[calls]
        assert cv2.imwrite(args[1], shot_image())
        return f"ok {args[1]}"


def read_sample_lines(run_dir: Path) -> list[dict[str, object]]:
    text = (run_dir / "samples.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def count_complete_lines(index_file: Path) -> int:
    """index_file の、改行まで書き終わった行の数を返す。ファイルが無ければ 0。"""
    if not index_file.exists():
        return 0
    return index_file.read_text(encoding="utf-8").count("\n")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_TIME)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "run-001"
    directory.mkdir()
    return directory


# --- next_run_dir ---


def test_next_run_dir_creates_run_001_in_an_empty_directory(tmp_path):
    samples_dir = tmp_path / "screen-samples"
    samples_dir.mkdir()
    run_dir = next_run_dir(samples_dir)
    assert run_dir == samples_dir / "run-001"
    assert run_dir.is_dir()


def test_next_run_dir_uses_the_number_after_the_largest(tmp_path):
    samples_dir = tmp_path / "screen-samples"
    (samples_dir / "run-001").mkdir(parents=True)
    (samples_dir / "run-007").mkdir()
    run_dir = next_run_dir(samples_dir)
    assert run_dir == samples_dir / "run-008"
    assert run_dir.is_dir()


def test_next_run_dir_creates_missing_parents(tmp_path):
    samples_dir = tmp_path / "records" / "screen-samples"
    run_dir = next_run_dir(samples_dir)
    assert run_dir == samples_dir / "run-001"
    assert run_dir.is_dir()


# --- sample_file_name ---


def test_sample_file_name_pads_the_index_to_six_digits():
    assert sample_file_name(1) == "000001.png"


# --- record_sample ---


def test_record_sample_writes_a_shrunk_grayscale_image(run_dir, clock):
    daemon = ShotWritingDaemon(clock)
    entry = record_sample(run_dir, 1, send=daemon, clock=clock)
    # 時刻はデーモンの応答を受け取った後 (fake の送信で 0.5 秒進んだ後) の時計の値
    assert entry == {"index": 1, "time": START_TIME + 0.5, "file": "000001.png"}
    assert daemon.sent == [["shot", str(run_dir / "shot.png")]]
    assert daemon.timeouts == [SHOT_TIMEOUT_S]
    sample = cv2.imread(str(run_dir / "000001.png"), cv2.IMREAD_UNCHANGED)
    assert sample is not None
    assert sample.shape == (90, 160)


def test_record_sample_returns_the_error_reply_without_writing_an_image(run_dir, clock):
    daemon = FakeDaemon(clock, {"shot": "error no frame yet"})
    entry = record_sample(run_dir, 1, send=daemon, clock=clock)
    assert entry == {"time": START_TIME + 0.5, "error": "error no frame yet"}
    assert not (run_dir / "000001.png").exists()


def test_record_sample_reports_a_missing_shot_file(run_dir, clock):
    # FakeDaemon は `ok PATH` を返すが、PATH に画像を書かない
    daemon = FakeDaemon(clock)
    entry = record_sample(run_dir, 1, send=daemon, clock=clock)
    assert entry == {
        "time": START_TIME + 0.5,
        "error": f"error cannot read image: {run_dir / 'shot.png'}",
    }


# --- run_recording ---


def test_run_recording_records_until_stopped(run_dir, clock):
    stop = threading.Event()
    daemon = ShotWritingDaemon(clock, stop=stop, stop_at_call=3, max_calls=10)
    run_recording(run_dir, stop, send=daemon, clock=clock, interval_s=0.0)
    assert read_sample_lines(run_dir) == [
        {"index": 1, "time": START_TIME + 0.5, "file": "000001.png"},
        {"index": 2, "time": START_TIME + 1.0, "file": "000002.png"},
        {"index": 3, "time": START_TIME + 1.5, "file": "000003.png"},
    ]
    assert not (run_dir / "shot.png").exists()
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "000001.png",
        "000002.png",
        "000003.png",
        "samples.jsonl",
    ]


def test_run_recording_does_not_skip_image_numbers_after_an_error(run_dir, clock):
    stop = threading.Event()
    daemon = ShotWritingDaemon(
        clock,
        replies={2: "error no frame yet"},
        stop=stop,
        stop_at_call=3,
        max_calls=10,
    )
    run_recording(run_dir, stop, send=daemon, clock=clock, interval_s=0.0)
    assert read_sample_lines(run_dir) == [
        {"index": 1, "time": START_TIME + 0.5, "file": "000001.png"},
        {"time": START_TIME + 1.0, "error": "error no frame yet"},
        {"index": 2, "time": START_TIME + 1.5, "file": "000002.png"},
    ]
    assert not (run_dir / "shot.png").exists()
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "000001.png",
        "000002.png",
        "samples.jsonl",
    ]


# --- recording_screens ---


def test_recording_screens_records_in_the_background_until_exit(tmp_path, clock):
    paths = make_paths(tmp_path / "play")
    # 送信ごとに撮影の間隔より長く時計を進め、撮影の間の待ちを 0 にする。
    # 記録を止め損ねると、抜けた後の短い待ちの間にも行が増えるようにするため
    daemon = ShotWritingDaemon(clock, step_s=1.0)
    with recording_screens(paths, send=daemon, clock=clock) as run_dir:
        assert run_dir == paths.records_dir / "screen-samples" / "run-001"
        index_file = run_dir / "samples.jsonl"
        deadline = time.monotonic() + FIRST_LINE_TIMEOUT_S
        while count_complete_lines(index_file) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert count_complete_lines(index_file) >= 1
    lines_at_exit = count_complete_lines(index_file)
    time.sleep(AFTER_EXIT_WAIT_S)
    assert count_complete_lines(index_file) == lines_at_exit
