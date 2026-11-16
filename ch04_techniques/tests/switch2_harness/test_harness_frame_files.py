from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from switch2_harness.frame_files import (
    FRAME_NUMBER_DIGITS,
    FRAME_SUFFIX,
    MAX_RESERVE_ATTEMPTS,
    create_exclusively,
    frame_file_name,
    frame_number,
    next_frame_number,
    next_frame_path,
    reserve_next_frame_path,
)


def test_constants():
    assert FRAME_NUMBER_DIGITS == 6
    assert FRAME_SUFFIX == ".png"
    assert MAX_RESERVE_ATTEMPTS == 100


@pytest.mark.parametrize(
    ("file_name", "number"),
    [
        ("000001.png", 1),
        ("000012.png", 12),
        ("123456.png", 123456),
        ("1234567.png", 1234567),
        ("notes.txt", None),
        ("abc.png", None),
        ("12a.png", None),
        ("000001.jpg", None),
        (".png", None),
    ],
)
def test_frame_number(file_name, number):
    assert frame_number(file_name) == number


def test_next_frame_number_starts_at_one():
    assert next_frame_number([]) == 1


def test_next_frame_number_is_max_plus_one():
    names = ["000001.png", "000003.png", "notes.txt", "abc.png"]
    assert next_frame_number(names) == 4


def test_next_frame_number_ignores_gaps_and_order():
    assert next_frame_number(["000010.png", "000002.png"]) == 11


@pytest.mark.parametrize(
    ("number", "file_name"),
    [(1, "000001.png"), (7, "000007.png"), (123456, "123456.png")],
)
def test_frame_file_name(number, file_name):
    assert frame_file_name(number) == file_name


def test_next_frame_path_in_empty_directory(tmp_path):
    assert next_frame_path(tmp_path) == tmp_path / "000001.png"


def test_next_frame_path_after_existing_frames(tmp_path):
    (tmp_path / "000001.png").write_bytes(b"")
    (tmp_path / "000005.png").write_bytes(b"")
    (tmp_path / "memo.txt").write_text("x", encoding="utf-8")
    assert next_frame_path(tmp_path) == tmp_path / "000006.png"


def test_next_frame_path_does_not_create_the_file(tmp_path):
    next_frame_path(tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- 連番のパスの確保 ---


def test_create_exclusively_creates_an_empty_file(tmp_path):
    path = tmp_path / "000001.png"
    create_exclusively(path)
    assert path.read_bytes() == b""
    with pytest.raises(FileExistsError):
        create_exclusively(path)


def test_reserve_next_frame_path_in_empty_directory(tmp_path):
    path = reserve_next_frame_path(tmp_path)
    assert path == tmp_path / "000001.png"
    assert path.is_file()


def test_reserve_next_frame_path_after_existing_frames(tmp_path):
    (tmp_path / "000001.png").write_bytes(b"png")
    (tmp_path / "000004.png").write_bytes(b"png")
    assert reserve_next_frame_path(tmp_path) == tmp_path / "000005.png"
    assert (tmp_path / "000004.png").read_bytes() == b"png"


def test_reserve_next_frame_path_moves_on_when_another_look_wins(tmp_path):
    attempts: list[Path] = []

    def create(path: Path) -> None:
        attempts.append(path)
        if len(attempts) == 1:
            # 番号を決めてからファイルを作るまでの間に、別の look が同じ番号を作った
            path.write_bytes(b"")
        create_exclusively(path)

    assert reserve_next_frame_path(tmp_path, create) == tmp_path / "000002.png"
    assert attempts == [tmp_path / "000001.png", tmp_path / "000002.png"]


def test_reserve_next_frame_path_gives_up_after_the_limit(tmp_path):
    attempts: list[Path] = []

    def always_taken(path: Path) -> None:
        attempts.append(path)
        raise FileExistsError(path)

    with pytest.raises(OSError, match="cannot reserve a frame path") as error:
        reserve_next_frame_path(tmp_path, always_taken)
    # FileExistsError も OSError の一種なので、上限で送出した OSError そのものであることを確かめる
    assert type(error.value) is OSError
    assert len(attempts) == MAX_RESERVE_ATTEMPTS


def test_reserve_next_frame_path_gives_distinct_paths_to_concurrent_looks(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: reserve_next_frame_path(tmp_path), range(40)))
    assert len(set(paths)) == 40
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        frame_file_name(number) for number in range(1, 41)
    ]
