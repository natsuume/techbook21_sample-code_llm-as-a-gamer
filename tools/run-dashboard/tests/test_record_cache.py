import json
import os
import threading
from pathlib import Path

from run_dashboard.jsonl_lines import ParsedLines, read_json_lines
from run_dashboard.record_cache import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    FileSignature,
    JsonLinesCache,
    file_signature,
)
from run_dashboard.run_records import lighten_request_lines

# スレッドを使うテストで、相手を待つ時間の上限 (秒)。超えたらテストを失敗にする
WAIT_SECONDS = 5.0


class CountingReader:
    """read_json_lines の代わり。呼ばれた (パス, 読み始める位置) を calls に記録してから、実際に読む。"""

    def __init__(self):
        self.calls: list[tuple[Path, int]] = []

    def __call__(self, path: Path, offset: int) -> ParsedLines:
        self.calls.append((path, offset))
        return read_json_lines(path, offset)


class CountingTransform:
    """records を受け取り、各行に "seen": True を足した新しい行のリストを返す。呼ばれた回数を count に数える。"""

    def __init__(self):
        self.count = 0

    def __call__(self, records: list[dict]) -> list[dict]:
        self.count += 1
        return [{**record, "seen": True} for record in records]


def write_lines(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_default_limits():
    assert DEFAULT_MAX_FILES == 1024
    assert DEFAULT_MAX_BYTES == 256 * 1024 * 1024
    cache = JsonLinesCache()
    assert cache.max_files == DEFAULT_MAX_FILES
    assert cache.max_bytes == DEFAULT_MAX_BYTES


def test_file_signature_has_size_mtime_and_inode(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    stat = os.stat(path)
    assert file_signature(path) == FileSignature(
        size=stat.st_size, mtime_ns=stat.st_mtime_ns, inode=stat.st_ino
    )


def test_same_signature_is_not_read_again(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\nnot json\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    first = cache.read(path)
    second = cache.read(path)
    assert first == ParsedLines(records=[{"a": 1}], skipped=1, next_offset=18)
    assert second is first
    assert reader.calls == [(path, 0)]


def test_changed_size_is_read_again(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    assert cache.read(path).records == [{"a": 1}]
    # 記録が伸びたときは、前に読んだ位置 (9) から後だけを読み、前の結果に足す
    with path.open("a", encoding="utf-8") as file:
        file.write('{"b": 2}\n')
    assert cache.read(path).records == [{"a": 1}, {"b": 2}]
    assert reader.calls == [(path, 0), (path, 9)]


def test_changed_mtime_is_read_again(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    cache.read(path)
    # 大きさが同じでも、更新時刻が変われば読み直す
    write_lines(path, '{"a": 2}\n')
    mtime_ns = os.stat(path).st_mtime_ns
    os.utime(path, ns=(mtime_ns + 1_000_000_000, mtime_ns + 1_000_000_000))
    assert cache.read(path).records == [{"a": 2}]
    assert reader.calls == [(path, 0), (path, 0)]


def test_missing_file_is_none(tmp_path):
    cache = JsonLinesCache()
    assert cache.read(tmp_path / "missing.jsonl") is None


def test_deleted_file_is_none_after_it_was_read(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    cache = JsonLinesCache()
    assert cache.read(path) is not None
    path.unlink()
    assert cache.read(path) is None


def test_unreadable_file_is_none(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')

    def failing_reader(read_path: Path, offset: int) -> ParsedLines:
        raise PermissionError(f"cannot read {read_path} from {offset}")

    assert JsonLinesCache(read_lines=failing_reader).read(path) is None


def test_transform_is_applied_once_and_kept(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\nnot json\n')
    reader = CountingReader()
    transform = CountingTransform()
    cache = JsonLinesCache(read_lines=reader)
    first = cache.read(path, transform)
    second = cache.read(path, transform)
    assert first == ParsedLines(
        records=[{"a": 1, "seen": True}], skipped=1, next_offset=18
    )
    assert second is first
    assert transform.count == 1
    assert reader.calls == [(path, 0)]


def test_different_transforms_are_kept_separately(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = CountingReader()
    transform = CountingTransform()
    cache = JsonLinesCache(read_lines=reader)
    with_transform = cache.read(path, transform)
    without_transform = cache.read(path)
    assert with_transform.records == [{"a": 1, "seen": True}]
    assert without_transform.records == [{"a": 1}]
    # どちらも持っているので、もう一度読んでも読み直さない
    assert cache.read(path, transform) is with_transform
    assert cache.read(path) is without_transform
    assert reader.calls == [(path, 0), (path, 0)]


class BlockingReader:
    """read_json_lines の代わり。blocked_path を読むときは、started を立ててから release が立つまで待つ。

    呼ばれた (パス, 読み始める位置) を calls に記録する。ほかのパスは待たずに読む。
    """

    def __init__(self, blocked_path: Path):
        self.blocked_path = blocked_path
        self.calls: list[tuple[Path, int]] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, path: Path, offset: int) -> ParsedLines:
        self.calls.append((path, offset))
        if path == self.blocked_path:
            self.started.set()
            assert self.release.wait(WAIT_SECONDS)
        return read_json_lines(path, offset)


def start_reading(cache: JsonLinesCache, path: Path, results: list) -> threading.Thread:
    """別のスレッドで cache.read(path) を始め、結果を results に足すスレッドを返す。"""
    thread = threading.Thread(target=lambda: results.append(cache.read(path)))
    thread.start()
    return thread


def test_same_file_read_at_the_same_time_is_read_once(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = BlockingReader(path)
    cache = JsonLinesCache(read_lines=reader)
    results: list = []
    first = start_reading(cache, path, results)
    assert reader.started.wait(WAIT_SECONDS)
    # 1 つ目が読んでいる間に始めた 2 つ目は、1 つ目が読み終えるまで待つ
    second = start_reading(cache, path, results)
    second.join(0.2)
    assert second.is_alive()
    reader.release.set()
    first.join(WAIT_SECONDS)
    second.join(WAIT_SECONDS)
    assert reader.calls == [(path, 0)]
    assert len(results) == 2
    assert results[0] is results[1]


def test_other_file_is_read_while_a_file_is_being_read(tmp_path):
    blocked = tmp_path / "a.jsonl"
    other = tmp_path / "b.jsonl"
    write_lines(blocked, '{"a": 1}\n')
    write_lines(other, '{"b": 2}\n')
    reader = BlockingReader(blocked)
    cache = JsonLinesCache(read_lines=reader)
    results: list = []
    thread = start_reading(cache, blocked, results)
    assert reader.started.wait(WAIT_SECONDS)
    # a.jsonl を読んでいる間も、b.jsonl は待たずに読める
    assert cache.read(other).records == [{"b": 2}]
    reader.release.set()
    thread.join(WAIT_SECONDS)
    assert results[0].records == [{"a": 1}]


def test_least_recently_used_file_is_dropped_over_max_files(tmp_path):
    paths = [tmp_path / f"{name}.jsonl" for name in ("a", "b", "c")]
    for path in paths:
        write_lines(path, '{"a": 1}\n')
    a, b, c = paths
    reader = CountingReader()
    cache = JsonLinesCache(max_files=2, read_lines=reader)
    cache.read(a)
    cache.read(b)
    # a を使い直すと、最も長く使っていないのは b になる
    cache.read(a)
    cache.read(c)
    assert reader.calls == [(a, 0), (b, 0), (c, 0)]
    # a と c は持っているが、b は捨てた
    cache.read(a)
    cache.read(c)
    assert reader.calls == [(a, 0), (b, 0), (c, 0)]
    cache.read(b)
    assert reader.calls == [(a, 0), (b, 0), (c, 0), (b, 0)]


def test_least_recently_used_file_is_dropped_over_max_bytes(tmp_path):
    # どのファイルも 9 バイト。上限 20 バイトでは 2 つまで持つ
    paths = [tmp_path / f"{name}.jsonl" for name in ("a", "b", "c")]
    for path in paths:
        write_lines(path, '{"a": 1}\n')
    a, b, c = paths
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader, max_bytes=20)
    cache.read(a)
    cache.read(b)
    cache.read(a)
    cache.read(c)
    assert reader.calls == [(a, 0), (b, 0), (c, 0)]
    cache.read(a)
    cache.read(c)
    assert reader.calls == [(a, 0), (b, 0), (c, 0)]
    cache.read(b)
    assert reader.calls == [(a, 0), (b, 0), (c, 0), (b, 0)]


def test_file_larger_than_max_bytes_is_kept_until_another_file_is_read(tmp_path):
    big = tmp_path / "big.jsonl"
    small = tmp_path / "small.jsonl"
    write_lines(big, '{"a": 1}\n' * 10)
    write_lines(small, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader, max_bytes=20)
    # 上限より大きくても、最後に読んだ 1 件は持つ
    cache.read(big)
    cache.read(big)
    assert reader.calls == [(big, 0)]
    # ほかのファイルを読むと、上限を超えるため古い big を捨てる
    cache.read(small)
    cache.read(big)
    assert reader.calls == [(big, 0), (small, 0), (big, 0)]


def replace_file(path: Path, text: str) -> None:
    """text を書いた別のファイルで path を置き換える (os.replace。path の inode が変わる)。"""
    replacement = path.with_name(path.name + ".new")
    write_lines(replacement, text)
    os.replace(replacement, path)


def test_file_read_again_after_change_is_counted_once_in_bytes(tmp_path):
    path = tmp_path / "a.jsonl"
    other = tmp_path / "b.jsonl"
    write_lines(path, '{"a": 1}\n')
    write_lines(other, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader, max_bytes=20)
    cache.read(path)
    # 別のファイルに置き換えて読み直したファイルは、前の大きさ (9) を合計から引き、新しい大きさ (10) だけを数える。
    # そのため、b (9) を足しても合計は 19 で、上限 20 を超えずに 2 つとも持つ
    replace_file(path, '{"a": 12}\n')
    cache.read(path)
    cache.read(other)
    cache.read(path)
    cache.read(other)
    assert reader.calls == [(path, 0), (path, 0), (other, 0)]


# --- 追記の分だけ読む ---


def test_appended_lines_are_read_from_previous_next_offset(tmp_path):
    # 書いている途中の最後の行は読まず、その行が書き終わったら、その行の頭 (前回の next_offset) から読む
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n{"b"')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    assert cache.read(path).records == [{"a": 1}]
    with path.open("a", encoding="utf-8") as file:
        file.write(': 2}\n{"c": 3}\n')
    assert cache.read(path) == read_json_lines(path)
    assert reader.calls == [(path, 0), (path, 9)]


def request_line(number: int, state_text: str) -> dict:
    return {
        "event": "request",
        "number": number,
        "state_text": state_text,
        "history_text": f"履歴 {number}",
        "raw_response": {"model": "jev"},
    }


def test_appended_request_lines_with_transform_share_text_with_previous_line(tmp_path):
    # 追記の最初の要求の行は、前回の最後の要求の行と同じ文字列のオブジェクトを使う
    path = tmp_path / "requests.jsonl"
    state_text = "## ゲームの操作方法\n" + "あ" * 50
    header = {"event": "header", "questions": []}
    write_lines(
        path,
        "".join(
            json.dumps(line, ensure_ascii=False) + "\n"
            for line in (header, request_line(1, state_text))
        ),
    )
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    first = cache.read(path, lighten_request_lines)
    with path.open("a", encoding="utf-8") as file:
        for number in (2, 3):
            file.write(
                json.dumps(request_line(number, state_text), ensure_ascii=False) + "\n"
            )
    second = cache.read(path, lighten_request_lines)
    assert second.records == lighten_request_lines(read_json_lines(path).records)
    assert second.records[2]["state_text"] is first.records[1]["state_text"]
    assert second.records[3]["state_text"] is first.records[1]["state_text"]
    assert reader.calls == [(path, 0), (path, first.next_offset)]


def test_appended_read_adds_skipped_lines_and_takes_new_next_offset(tmp_path):
    # skipped は前回の分 + 追記の分、next_offset は追記の分を読んだ値
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\nnot json\n')
    cache = JsonLinesCache(read_lines=CountingReader())
    assert cache.read(path).skipped == 1
    with path.open("a", encoding="utf-8") as file:
        file.write('[1, 2]\n{"b": 2}\n{"c"')
    second = cache.read(path)
    # 18 + len('[1, 2]\n') + len('{"b": 2}\n') = 34。改行で終わっていない {"c" は読まない
    assert second == ParsedLines(
        records=[{"a": 1}, {"b": 2}], skipped=2, next_offset=34
    )


def test_smaller_file_is_read_from_start(tmp_path):
    # 前回より小さくなったファイルは、丸ごと読み直す (同じ大きさで更新時刻だけが違うときは
    # test_changed_mtime_is_read_again)
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n{"b": 2}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    cache.read(path)
    write_lines(path, '{"c": 3}\n')
    assert cache.read(path).records == [{"c": 3}]
    assert reader.calls == [(path, 0), (path, 0)]


def test_replaced_larger_file_is_read_from_start(tmp_path):
    # 別のファイル (inode が違う) に置き換わったら、大きくなっていても丸ごと読み直す
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    cache.read(path)
    replace_file(path, '{"x": 0}\n{"y": 1}\n')
    assert cache.read(path).records == [{"x": 0}, {"y": 1}]
    assert reader.calls == [(path, 0), (path, 0)]


def test_appended_read_does_not_change_previous_result(tmp_path):
    # 前回返した ParsedLines と、その records のリストは、追記を読んだ後も変わらない
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\nnot json\n')
    cache = JsonLinesCache(read_lines=CountingReader())
    first = cache.read(path)
    first_records = first.records
    with path.open("a", encoding="utf-8") as file:
        file.write('{"b": 2}\nbroken\n')
    second = cache.read(path)
    assert second.records == [{"a": 1}, {"b": 2}]
    assert first == ParsedLines(records=[{"a": 1}], skipped=1, next_offset=18)
    assert first.records is first_records
    assert second.records is not first_records


def test_total_bytes_counts_grown_size(tmp_path):
    # 伸びたファイルは、伸びた後の大きさで合計に数える。a (9 → 27) と b (9) の合計 36 は上限 30 を超えるため、
    # 最も長く使っていない b を捨てる
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    write_lines(a, '{"a": 1}\n')
    write_lines(b, '{"b": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader, max_bytes=30)
    cache.read(a)
    cache.read(b)
    with a.open("a", encoding="utf-8") as file:
        file.write('{"a": 2}\n{"a": 3}\n')
    cache.read(a)
    cache.read(b)
    assert reader.calls == [(a, 0), (b, 0), (a, 9), (b, 0)]
