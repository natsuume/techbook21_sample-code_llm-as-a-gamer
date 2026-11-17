from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest
from harness_fakes import (
    FakeClock,
    FakeDaemon,
    FakeSleep,
    ImageWritingDaemon,
    shot_color,
    solid_image,
)

from switch2_harness.multi_frame import (
    multi_frame_line,
    shoot_burst,
    shot_paths,
    tile_frames,
    tile_path_for,
    write_tile,
)

START_TIME = 1000.0


def make_shot_paths(root: Path) -> tuple[Path, ...]:
    """root の下に frames/ と multi-frames/ を作り、frames/000012.png を保存する look の 6 つの保存先を返す。"""
    frames_dir = root / "frames"
    multi_frames_dir = root / "multi-frames"
    frames_dir.mkdir()
    multi_frames_dir.mkdir()
    return shot_paths(frames_dir / "000012.png", multi_frames_dir)


class TimedSend:
    """send の fake の包み。送るたびに、送る直前の時計の値を times に記録してから send に渡す。"""

    def __init__(self, send: FakeDaemon | ImageWritingDaemon, clock: FakeClock) -> None:
        self.send = send
        self.clock = clock
        self.times: list[float] = []

    def __call__(self, args: list[str], *, timeout_s: float | None = None) -> str:
        self.times.append(self.clock())
        return self.send(args, timeout_s=timeout_s)


# --- shot_paths / tile_path_for / multi_frame_line ---


def test_shot_paths_puts_five_shots_in_multi_frames_and_the_last_in_frames():
    assert shot_paths(Path("frames/000012.png"), Path("multi-frames")) == (
        Path("multi-frames/000012-1.png"),
        Path("multi-frames/000012-2.png"),
        Path("multi-frames/000012-3.png"),
        Path("multi-frames/000012-4.png"),
        Path("multi-frames/000012-5.png"),
        Path("frames/000012.png"),
    )


def test_tile_path_for_uses_the_frame_name_in_multi_frames():
    assert tile_path_for(Path("frames/000012.png"), Path("multi-frames")) == Path(
        "multi-frames/000012.png"
    )


def test_multi_frame_line():
    tile_path = Path("/w/multi-frames/000012.png")
    assert multi_frame_line(tile_path) == "複数フレーム: /w/multi-frames/000012.png"


# --- shoot_burst ---


def test_shoot_burst_shoots_every_interval_in_order(tmp_path):
    paths = make_shot_paths(tmp_path)
    clock = FakeClock(START_TIME)
    daemon = FakeDaemon(clock, step_s=0.05)
    send = TimedSend(daemon, clock)
    sleep = FakeSleep(clock)
    reply = shoot_burst(paths, send=send, clock=clock, sleep=sleep)
    assert daemon.sent == [["shot", str(path)] for path in paths]
    # look の送信には待ち時間の上限を付けない
    assert daemon.timeouts == [None] * 6
    # 送信に 0.05 秒かかるので、撮影の間に 0.15 秒ずつ待つ (最後の撮影の後は待たない)
    assert sleep.calls == pytest.approx([0.15] * 5)
    gaps = [later - earlier for earlier, later in pairwise(send.times)]
    assert gaps == pytest.approx([0.2] * 5)
    assert reply == f"ok {paths[-1]}"


def test_shoot_burst_does_not_sleep_when_a_shot_takes_the_interval(tmp_path):
    paths = make_shot_paths(tmp_path)
    clock = FakeClock(START_TIME)
    daemon = FakeDaemon(clock, step_s=0.3)
    sleep = FakeSleep(clock)
    reply = shoot_burst(paths, send=daemon, clock=clock, sleep=sleep)
    assert len(daemon.sent) == 6
    assert sleep.calls == []
    assert reply == f"ok {paths[-1]}"


def test_shoot_burst_stops_at_an_error_reply(tmp_path):
    paths = make_shot_paths(tmp_path)
    clock = FakeClock(START_TIME)
    daemon = ImageWritingDaemon(clock, step_s=0.05, fail_on_shot=3)
    sleep = FakeSleep(clock)
    reply = shoot_burst(paths, send=daemon, clock=clock, sleep=sleep)
    assert daemon.sent == [["shot", str(path)] for path in paths[:3]]
    assert sleep.calls == pytest.approx([0.15] * 2)
    assert reply == "error no frame yet"


# --- tile_frames / write_tile ---


def test_tile_frames_places_the_frames_in_shooting_order():
    images = [solid_image(shot_color(k)) for k in range(1, 7)]
    tile = tile_frames(images)
    assert tile.shape == (720, 1920, 3)
    for k in range(1, 7):
        row, column = (k - 1) // 3, (k - 1) % 3
        # k 枚目のコマ (640x360) の中央の画素は、k 枚目の色のまま
        center = tile[row * 360 + 180, column * 640 + 320]
        assert tuple(int(value) for value in center) == shot_color(k)


def test_tile_frames_draws_a_number_at_the_top_left_of_each_frame():
    images = [solid_image(shot_color(k)) for k in range(1, 7)]
    tile = tile_frames(images)
    for k in range(1, 7):
        row, column = (k - 1) // 3, (k - 1) % 3
        # コマの左上の 80x60 画素に、塗った色と違う画素 (描いた番号) がある。番号のフォントや色には結合しない
        corner = tile[row * 360 : row * 360 + 60, column * 640 : column * 640 + 80]
        assert np.any(corner != np.array(shot_color(k), dtype=np.uint8))


def test_write_tile_writes_the_tiled_png(tmp_path):
    image_paths = []
    for k in range(1, 7):
        path = tmp_path / f"shot-{k}.png"
        assert cv2.imwrite(str(path), solid_image(shot_color(k)))
        image_paths.append(path)
    tile_path = tmp_path / "tile.png"
    write_tile(image_paths, tile_path)
    tile = cv2.imread(str(tile_path))
    assert tile is not None
    assert tile.shape == (720, 1920, 3)
