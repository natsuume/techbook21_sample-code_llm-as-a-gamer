import pytest

from switch2_harness.frame_files import (
    FRAME_NUMBER_DIGITS,
    FRAME_SUFFIX,
    frame_file_name,
    frame_number,
    next_frame_number,
    next_frame_path,
)


def test_constants():
    assert FRAME_NUMBER_DIGITS == 6
    assert FRAME_SUFFIX == ".png"


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
