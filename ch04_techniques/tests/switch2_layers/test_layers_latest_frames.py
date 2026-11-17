"""LatestFrames (frames/latest/<n>.jpg に置き換え続ける最新の 5 枚) と make_latest_frame を確かめる。

確かめるのは、外から呼ぶもの (add・add_image・snapshot・make_latest_frame) と、frames/latest/ のファイルだけにする。
"""

import contextlib
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from switch2_layers.frames.image_encoding import DEFAULT_JPEG_QUALITY, encode_jpeg
from switch2_layers.frames.latest_frames import (
    LATEST_FRAME_COUNT,
    LatestFrame,
    LatestFrames,
    make_latest_frame,
    newest_frame_t,
)
from switch2_layers.frames.surface_frames import draw_frame_time
from switch2_layers.run_directory import RunPaths


def make_paths(tmp_path: Path) -> RunPaths:
    """frames/interval/ だけを持つ実行ディレクトリー (パッケージのコードは使わない)。"""
    root = tmp_path / "run"
    (root / "frames" / "interval").mkdir(parents=True)
    return RunPaths(root)


def frame(t: float) -> LatestFrame:
    """時刻 t の最新のフレームの画像 (JPEG の代わりに、t の分かるバイト列)。"""
    return LatestFrame(
        t=t,
        source_file=f"frames/interval/{round(t * 1000):09d}.jpg",
        jpeg=f"stamped-{t:.3f}".encode(),
    )


def latest_dir(paths: RunPaths) -> Path:
    return paths.root / "frames" / "latest"


def latest_files(paths: RunPaths) -> dict[str, bytes]:
    """frames/latest/ のファイルの名前 → 中身。ディレクトリーが無ければ空。"""
    directory = latest_dir(paths)
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def gray_image(width: int = 160, height: int = 90) -> np.ndarray:
    return np.full((height, width, 3), 128, np.uint8)


# --- 1 枚目から 5 枚目まで・6 枚目から ---


def test_latest_frame_count_is_five():
    assert LATEST_FRAME_COUNT == 5


def test_nothing_is_written_before_the_first_frame(tmp_path):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    assert latest.snapshot() == ()
    assert not latest_dir(paths).exists()


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_frames_up_to_five_fill_only_their_numbers_with_1_as_the_newest(
    tmp_path, count
):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    for t in range(1, count + 1):
        latest.add(frame(float(t)))
    # 無い番号のファイルは作らない。1.jpg が最新、番号が大きいほど古い
    assert latest_files(paths) == {
        f"{number}.jpg": frame(float(count - number + 1)).jpeg
        for number in range(1, count + 1)
    }


def test_sixth_frame_and_later_drop_the_oldest(tmp_path):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    for t in range(1, 8):
        latest.add(frame(float(t)))
    # 7 枚足した後: 1.jpg が t=7、5.jpg が t=3。t=1 と t=2 は外れる
    assert latest_files(paths) == {
        "1.jpg": frame(7.0).jpeg,
        "2.jpg": frame(6.0).jpeg,
        "3.jpg": frame(5.0).jpeg,
        "4.jpg": frame(4.0).jpeg,
        "5.jpg": frame(3.0).jpeg,
    }


def test_snapshot_returns_the_latest_frames_together_newest_first(tmp_path):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    for t in range(1, 7):
        latest.add(frame(float(t)))
    # 元の frames/interval/ のファイル名と時刻を、画像と一緒に返す
    assert latest.snapshot() == tuple(frame(float(t)) for t in (6, 5, 4, 3, 2))


def test_newest_frame_t_is_the_time_of_the_first_frame():
    # 主ループは、操作の後に撮ったフレームが入ったかを、最新のフレームの時刻で確かめる
    assert newest_frame_t((frame(6.0), frame(5.0), frame(4.0))) == 6.0
    assert newest_frame_t(()) is None


def test_files_are_replaced_without_leaving_temporary_files(tmp_path):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    for t in range(1, 8):
        latest.add(frame(float(t)))
    # 一時ファイルに書いてから名前を変えて置き換えるので、置き換えの途中のファイルは残らない
    # (置き換えそのものは file_io.write_bytes_atomic の規則)
    assert sorted(latest_files(paths)) == [f"{n}.jpg" for n in range(1, 6)]


def test_each_number_is_rewritten_from_memory_not_renamed_from_another_number(
    tmp_path,
):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    for t in range(1, 6):
        latest.add(frame(float(t)))
    directory = latest_dir(paths)
    with contextlib.ExitStack() as stack:
        # 前のファイルを開いたままにして (inode の番号を使い回させずに)、6 枚目を足す
        old_files = [
            stack.enter_context((directory / f"{n}.jpg").open("rb"))
            for n in range(1, 6)
        ]
        old_inodes = {os.fstat(file.fileno()).st_ino for file in old_files}
        latest.add(frame(6.0))
        new_inodes = {(directory / f"{n}.jpg").stat().st_ino for n in range(1, 6)}
        # 番号を rename で送ると、前の 1.jpg がそのまま 2.jpg になる (同じ inode)。どの番号も、メモリーの画像から
        # 一時ファイルに書いて置き換えた新しいファイルになる
        assert new_inodes.isdisjoint(old_inodes)
        # 開いていた前の 1.jpg は、置き換えの後も書きかけにならず、前の画像のまま読める
        assert old_files[0].read() == frame(5.0).jpeg
    assert latest_files(paths)["1.jpg"] == frame(6.0).jpeg


# --- 時刻の書き込み ---


def test_latest_frame_has_the_time_written_at_the_top_left_in_the_same_width():
    image = gray_image(160, 90)
    original = image.copy()
    made = make_latest_frame(12.345, "frames/interval/000012345.jpg", image)
    assert made == LatestFrame(
        t=12.345,
        source_file="frames/interval/000012345.jpg",
        jpeg=encode_jpeg(draw_frame_time(image, 12.345), DEFAULT_JPEG_QUALITY).jpeg,
    )
    decoded = cv2.imdecode(np.frombuffer(made.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == 160
    # 元の画像は変えない
    assert np.array_equal(image, original)


def test_add_image_writes_the_frame_with_the_time(tmp_path):
    paths = make_paths(tmp_path)
    latest = LatestFrames(paths)
    image = gray_image(160, 90)
    latest.add_image(3.25, "frames/interval/000003250.jpg", image, jpeg_quality=70)
    expected = encode_jpeg(draw_frame_time(image, 3.25), 70).jpeg
    assert latest_files(paths) == {"1.jpg": expected}
    assert latest.snapshot() == (
        LatestFrame(t=3.25, source_file="frames/interval/000003250.jpg", jpeg=expected),
    )
