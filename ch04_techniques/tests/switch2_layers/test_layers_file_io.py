import hashlib
import json
import os
from pathlib import Path

import pytest

from switch2_layers.file_io import (
    JsonLinesChunk,
    append_json_line,
    format_json,
    format_json_line,
    is_temporary_file_name,
    read_json_lines,
    read_json_lines_from,
    read_text_or_empty,
    sha256_bytes,
    sha256_file,
    write_bytes_atomic,
    write_json_atomic,
    write_text_atomic,
)

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --- 一時ファイルの名前の規則 ---


@pytest.mark.parametrize(
    "name, expected",
    [
        (".places.md.k3j9x.tmp", True),
        (".a.tmp", True),
        ("places.md", False),
        ("places.tmp", False),
        (".places.md", False),
        (".publish.lock", False),
    ],
)
def test_is_temporary_file_name(name, expected):
    assert is_temporary_file_name(name) is expected


def test_temporary_file_used_for_replacement_follows_the_rule(tmp_path, monkeypatch):
    sources: list[str] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        sources.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)
    write_bytes_atomic(tmp_path / "places.md", b"x")
    monkeypatch.undo()
    assert len(sources) == 1
    assert is_temporary_file_name(sources[0])
    assert sources[0].startswith(".places.md.")


# --- 置き換えの書き込み ---


def test_write_bytes_atomic_creates_file_without_leaving_temp_files(tmp_path):
    path = tmp_path / "image.jpg"
    write_bytes_atomic(path, b"\xff\xd8data")
    assert path.read_bytes() == b"\xff\xd8data"
    assert [p.name for p in tmp_path.iterdir()] == ["image.jpg"]


def test_write_bytes_atomic_replaces_existing_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    write_bytes_atomic(path, b"new")
    assert path.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_write_bytes_atomic_does_not_create_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_bytes_atomic(tmp_path / "missing" / "a.md", b"x")
    assert not (tmp_path / "missing").exists()


def test_write_bytes_atomic_keeps_old_content_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "tasks.md"
    path.write_bytes(b"old")

    def failing_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_bytes_atomic(path, b"new")
    monkeypatch.undo()
    # 置き換えに失敗しても、元の内容のままで、一時ファイルも残らない
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["tasks.md"]


def test_write_text_atomic_uses_utf8(tmp_path):
    path = tmp_path / "advice.md"
    write_text_atomic(path, "# 助言\n詰まりの原因\n")
    assert path.read_bytes() == "# 助言\n詰まりの原因\n".encode()


def test_write_json_atomic_writes_readable_json(tmp_path):
    path = tmp_path / "state.json"
    value = {"scene_name": "町", "surface_wait_s": 0.5, "path": tmp_path}
    write_json_atomic(path, value)
    text = path.read_text(encoding="utf-8")
    assert text == format_json(value)
    assert text.endswith("\n")
    # 日本語は \\u エスケープにしない
    assert "町" in text
    # JSON にできない値 (Path) は文字列にする
    assert json.loads(text) == {
        "scene_name": "町",
        "surface_wait_s": 0.5,
        "path": str(tmp_path),
    }


# --- 追記の書き込み ---


def test_format_json_line_is_one_line():
    line = format_json_line({"event": "start", "note": "改行\nを含む"})
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert "改行" in line
    assert json.loads(line) == {"event": "start", "note": "改行\nを含む"}


def test_append_json_line_creates_and_appends(tmp_path):
    path = tmp_path / "run.jsonl"
    append_json_line(path, {"t": 0.0, "event": "start"})
    append_json_line(path, {"t": 1.5, "event": "stop"})
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(lines) == 2
    assert all(line.endswith("\n") for line in lines)
    assert [json.loads(line) for line in lines] == [
        {"t": 0.0, "event": "start"},
        {"t": 1.5, "event": "stop"},
    ]


def test_append_json_line_does_not_create_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_json_line(tmp_path / "missing" / "run.jsonl", {"t": 0.0})


# --- 追記したファイルの読み込み ---


def test_read_json_lines_of_missing_file_is_empty(tmp_path):
    assert read_json_lines(tmp_path / "surface.jsonl") == []


def test_read_json_lines_skips_last_line_without_newline(tmp_path):
    path = tmp_path / "surface.jsonl"
    path.write_text('{"t": 1}\n{"t": 2}\n{"t": 3', encoding="utf-8")
    assert read_json_lines(path) == [{"t": 1}, {"t": 2}]


def test_read_json_lines_skips_complete_json_without_newline(tmp_path):
    # JSON として読める行でも、改行で終わっていなければ書いている途中とみなす
    path = tmp_path / "surface.jsonl"
    path.write_text('{"t": 1}\n{"t": 2}', encoding="utf-8")
    assert read_json_lines(path) == [{"t": 1}]


def test_read_json_lines_keeps_last_line_with_newline(tmp_path):
    path = tmp_path / "surface.jsonl"
    path.write_text('{"t": 1}\n{"t": 2}\n', encoding="utf-8")
    assert read_json_lines(path) == [{"t": 1}, {"t": 2}]


def test_read_json_lines_skips_blank_broken_and_non_object_lines(tmp_path):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(b'{"t": 1}\n\n   \n{broken\n[1, 2]\n"text"\n\xff\xfe\n{"t": 2}\n')
    assert read_json_lines(path) == [{"t": 1}, {"t": 2}]


def test_read_json_lines_of_empty_file_is_empty(tmp_path):
    path = tmp_path / "frames.jsonl"
    path.write_bytes(b"")
    assert read_json_lines(path) == []


def test_append_then_read_round_trip(tmp_path):
    path = tmp_path / "calls.jsonl"
    rows = [{"t": i / 10, "name": f"場面{i}"} for i in range(5)]
    for row in rows:
        append_json_line(path, row)
    assert read_json_lines(path) == rows


# --- 文字列の読み込みと SHA-256 ---


def test_read_text_or_empty(tmp_path):
    path = tmp_path / "strategy.md"
    assert read_text_or_empty(path) == ""
    path.write_text("# 大方針\n", encoding="utf-8")
    assert read_text_or_empty(path) == "# 大方針\n"
    path.write_bytes(b"\xff\xfe\xfa")
    assert read_text_or_empty(path) == ""


def test_sha256_bytes():
    assert sha256_bytes(b"abc") == ABC_SHA256
    assert sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()


def test_sha256_file(tmp_path):
    path = tmp_path / "controls.md"
    path.write_bytes(b"abc")
    assert sha256_file(path) == ABC_SHA256
    assert sha256_file(tmp_path / "missing.md") is None
    # 通常のファイルでないもの (ディレクトリー) も None
    assert sha256_file(tmp_path) is None


# --- バイト位置から完全な行を読む ---


def test_read_from_start_returns_complete_lines_and_end_offset(tmp_path):
    path = tmp_path / "operations.jsonl"
    data = b'{"t": 1.0}\n{"t": 1.0, "n": 2}\n'
    path.write_bytes(data)
    assert read_json_lines_from(path, 0) == JsonLinesChunk(
        rows=[{"t": 1.0}, {"t": 1.0, "n": 2}], end_offset=len(data), restarted=False
    )


def test_partial_last_line_is_not_read_and_not_counted(tmp_path):
    path = tmp_path / "surface.jsonl"
    complete = b'{"t_received": 1.0}\n'
    path.write_bytes(complete + b'{"t_received": 2.0')
    chunk = read_json_lines_from(path, 0)
    assert chunk.rows == [{"t_received": 1.0}]
    assert chunk.end_offset == len(complete)
    # 書きかけの行が完全になってから、その位置から読むと読める
    with path.open("ab") as file:
        file.write(b"}\n")
    later = read_json_lines_from(path, chunk.end_offset)
    assert later.rows == [{"t_received": 2.0}]
    assert later.end_offset == path.stat().st_size


def test_reading_from_end_offset_returns_only_appended_lines(tmp_path):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(b'{"t": 13.0, "n": 1}\n')
    first = read_json_lines_from(path, 0)
    # 同じ t の行が、読んだ後に追記される
    with path.open("ab") as file:
        file.write(b'{"t": 13.0, "n": 2}\n')
    second = read_json_lines_from(path, first.end_offset)
    assert second.rows == [{"t": 13.0, "n": 2}]
    # 新しい行が無ければ空で、位置は変わらない
    third = read_json_lines_from(path, second.end_offset)
    assert third == JsonLinesChunk(
        rows=[], end_offset=second.end_offset, restarted=False
    )


def test_offset_beyond_file_size_reads_from_start(tmp_path):
    path = tmp_path / "surface.jsonl"
    data = b'{"t_received": 1.0}\n'
    path.write_bytes(data)
    assert read_json_lines_from(path, 1000) == JsonLinesChunk(
        rows=[{"t_received": 1.0}], end_offset=len(data), restarted=True
    )
    assert read_json_lines_from(path, -1).restarted is True


def test_missing_file_reads_nothing(tmp_path):
    path = tmp_path / "surface.jsonl"
    assert read_json_lines_from(path, 0) == JsonLinesChunk(
        rows=[], end_offset=0, restarted=False
    )
    assert read_json_lines_from(path, 50) == JsonLinesChunk(
        rows=[], end_offset=0, restarted=True
    )


def test_skipped_lines_still_advance_offset_and_offsets_count_bytes(tmp_path):
    path = tmp_path / "surface.jsonl"
    data = b"{broken\n[1]\n\n" + '{"name": "町"}\n'.encode()
    path.write_bytes(data)
    chunk = read_json_lines_from(path, 0)
    assert chunk.rows == [{"name": "町"}]
    # 位置はバイトで数える (「町」は UTF-8 で 3 バイト)
    assert chunk.end_offset == len(data)
    assert chunk.end_offset > len(data.decode())
