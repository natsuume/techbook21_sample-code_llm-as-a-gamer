import pytest

from run_dashboard.jsonl_lines import (
    LineChunk,
    ParsedLines,
    parse_lines,
    read_complete_lines,
    read_json_lines,
)

# --- read_complete_lines ---


def test_unterminated_last_line_is_not_read(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n{"c": ')
    # 2 行 (9 バイトずつ) だけを読み、書いている途中の最後の行の手前で止まる
    assert read_complete_lines(path) == LineChunk(
        lines=[b'{"a": 1}', b'{"b": 2}'], next_offset=18
    )


def test_reading_from_next_offset_after_append_reads_the_rest(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\n{"c": ')
    first = read_complete_lines(path)
    assert first == LineChunk(lines=[b'{"a": 1}'], next_offset=9)
    with path.open("ab") as file:
        file.write(b'3}\n{"d": 4}\n')
    second = read_complete_lines(path, first.next_offset)
    assert second == LineChunk(lines=[b'{"c": 3}', b'{"d": 4}'], next_offset=27)


def test_without_newline_there_are_no_lines_and_offset_stays(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}')
    assert read_complete_lines(path) == LineChunk(lines=[], next_offset=0)
    path.write_bytes(b'{"a": 1}\n{"b"')
    assert read_complete_lines(path, 9) == LineChunk(lines=[], next_offset=9)


def test_crlf_lines_drop_carriage_return(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
    assert read_complete_lines(path) == LineChunk(
        lines=[b'{"a": 1}', b'{"b": 2}'], next_offset=20
    )


def test_empty_lines_are_kept_in_the_chunk(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\n\n   \n{"b": 2}\n')
    assert read_complete_lines(path) == LineChunk(
        lines=[b'{"a": 1}', b"", b"   ", b'{"b": 2}'], next_offset=23
    )


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_complete_lines(tmp_path / "missing.jsonl")
    with pytest.raises(FileNotFoundError):
        read_json_lines(tmp_path / "missing.jsonl")


# --- parse_lines ---


def test_parse_lines_reads_objects_and_does_not_count_empty_lines():
    chunk = LineChunk(
        lines=[b'{"a": 1}', b"", b"   ", '{"b": "あ"}'.encode()], next_offset=40
    )
    assert parse_lines(chunk) == ParsedLines(
        records=[{"a": 1}, {"b": "あ"}], skipped=0, next_offset=40
    )


def test_parse_lines_counts_unreadable_lines():
    chunk = LineChunk(
        lines=[
            b'{"a": 1}',
            # JSON として読めない
            b"{broken",
            # UTF-8 でない
            b'{"note": "\xff\xfe"}',
            # object でない値
            b"[1, 2]",
            b"3",
            b'"text"',
            b"null",
            b'{"b": 2}',
        ],
        next_offset=99,
    )
    assert parse_lines(chunk) == ParsedLines(
        records=[{"a": 1}, {"b": 2}], skipped=6, next_offset=99
    )


# --- read_json_lines ---


def test_read_json_lines_reads_complete_lines_and_counts_skipped(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\nnot json\n{"b": 2}\n{"c"')
    assert read_json_lines(path) == ParsedLines(
        records=[{"a": 1}, {"b": 2}], skipped=1, next_offset=27
    )


def test_read_json_lines_from_offset(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    assert read_json_lines(path, 9) == ParsedLines(
        records=[{"b": 2}], skipped=0, next_offset=18
    )


# --- 読む範囲の終わり (end) ---

# 9 バイトの行が 3 つ (行の境目は 9・18・27) と、改行で終わっていない最後の行
THREE_LINES = b'{"a": 1}\n{"b": 2}\n{"c": 3}\n{"d"'


def write_three_lines(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(THREE_LINES)
    return path


@pytest.mark.parametrize(
    "offset, end, expected",
    [
        (0, 9, LineChunk(lines=[b'{"a": 1}'], next_offset=9)),
        (0, 18, LineChunk(lines=[b'{"a": 1}', b'{"b": 2}'], next_offset=18)),
        (9, 18, LineChunk(lines=[b'{"b": 2}'], next_offset=18)),
    ],
)
def test_end_at_line_boundary_reads_lines_before_end(tmp_path, offset, end, expected):
    # end が行の境目なら、その手前までの行を読み、next_offset は end
    assert read_complete_lines(write_three_lines(tmp_path), offset, end) == expected


@pytest.mark.parametrize(
    "offset, end, expected",
    [
        # 2 行目の途中 (12) まで: 2 行目は含めず、next_offset は 1 行目の \n の次
        (0, 12, LineChunk(lines=[b'{"a": 1}'], next_offset=9)),
        # 2 行目の \n の直前 (17) まで: 2 行目は改行で終わっていない
        (0, 17, LineChunk(lines=[b'{"a": 1}'], next_offset=9)),
        # offset から end までに \n が無い
        (9, 12, LineChunk(lines=[], next_offset=9)),
    ],
)
def test_end_in_middle_of_line_excludes_that_line(tmp_path, offset, end, expected):
    assert read_complete_lines(write_three_lines(tmp_path), offset, end) == expected


@pytest.mark.parametrize("offset, end", [(9, 9), (18, 9), (0, 0)])
def test_end_not_after_offset_reads_nothing(tmp_path, offset, end):
    # end <= offset なら読まず、next_offset は offset
    assert read_complete_lines(write_three_lines(tmp_path), offset, end) == LineChunk(
        lines=[], next_offset=offset
    )


@pytest.mark.parametrize("offset", [0, 9])
def test_end_beyond_file_size_is_same_as_none(tmp_path, offset):
    # end がファイルの大きさより大きければ、end=None と同じ
    path = write_three_lines(tmp_path)
    beyond = len(THREE_LINES) + 100
    assert read_complete_lines(path, offset, beyond) == read_complete_lines(
        path, offset
    )
    assert read_complete_lines(path, offset, beyond).next_offset == 27


def test_read_json_lines_with_end_parses_complete_lines_before_end(tmp_path):
    # read_json_lines(path, offset, end) は parse_lines(read_complete_lines(path, offset, end))
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"a": 1}\nnot json\n{"b": 2}\n{"c": 3}\n')
    # 3 行目 (位置 18〜26) の途中まで
    assert read_json_lines(path, 0, 22) == parse_lines(read_complete_lines(path, 0, 22))
    assert read_json_lines(path, 0, 22) == ParsedLines(
        records=[{"a": 1}], skipped=1, next_offset=18
    )
    assert read_json_lines(path, 9, 27) == ParsedLines(
        records=[{"b": 2}], skipped=1, next_offset=27
    )
