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

# スレッドを使うテストで、相手を待つ時間の上限 (秒)。超えたらテストを失敗にする
WAIT_SECONDS = 5.0


class CountingReader:
    """read_json_lines の代わり。呼ばれたパスを calls に記録してから、実際に読む。"""

    def __init__(self):
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> ParsedLines:
        self.calls.append(path)
        return read_json_lines(path)


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
    assert reader.calls == [path]


def test_changed_size_is_read_again(tmp_path):
    path = tmp_path / "a.jsonl"
    write_lines(path, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader)
    assert cache.read(path).records == [{"a": 1}]
    # 記録が伸びたときも、丸ごと読み直す
    with path.open("a", encoding="utf-8") as file:
        file.write('{"b": 2}\n')
    assert cache.read(path).records == [{"a": 1}, {"b": 2}]
    assert reader.calls == [path, path]


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
    assert reader.calls == [path, path]


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

    def failing_reader(read_path: Path) -> ParsedLines:
        raise PermissionError(f"cannot read {read_path}")

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
    assert reader.calls == [path]


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
    assert reader.calls == [path, path]


class BlockingReader:
    """read_json_lines の代わり。blocked_path を読むときは、started を立ててから release が立つまで待つ。

    呼ばれたパスを calls に記録する。ほかのパスは待たずに読む。
    """

    def __init__(self, blocked_path: Path):
        self.blocked_path = blocked_path
        self.calls: list[Path] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, path: Path) -> ParsedLines:
        self.calls.append(path)
        if path == self.blocked_path:
            self.started.set()
            assert self.release.wait(WAIT_SECONDS)
        return read_json_lines(path)


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
    assert reader.calls == [path]
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
    assert reader.calls == [a, b, c]
    # a と c は持っているが、b は捨てた
    cache.read(a)
    cache.read(c)
    assert reader.calls == [a, b, c]
    cache.read(b)
    assert reader.calls == [a, b, c, b]


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
    assert reader.calls == [a, b, c]
    cache.read(a)
    cache.read(c)
    assert reader.calls == [a, b, c]
    cache.read(b)
    assert reader.calls == [a, b, c, b]


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
    assert reader.calls == [big]
    # ほかのファイルを読むと、上限を超えるため古い big を捨てる
    cache.read(small)
    cache.read(big)
    assert reader.calls == [big, small, big]


def test_file_read_again_after_change_is_counted_once_in_bytes(tmp_path):
    path = tmp_path / "a.jsonl"
    other = tmp_path / "b.jsonl"
    write_lines(path, '{"a": 1}\n')
    write_lines(other, '{"a": 1}\n')
    reader = CountingReader()
    cache = JsonLinesCache(read_lines=reader, max_bytes=20)
    cache.read(path)
    # 書き換えて読み直したファイルは、前の大きさ (9) を合計から引き、新しい大きさ (10) だけを数える。
    # そのため、b (9) を足しても合計は 19 で、上限 20 を超えずに 2 つとも持つ
    write_lines(path, '{"a": 12}\n')
    cache.read(path)
    cache.read(other)
    cache.read(path)
    cache.read(other)
    assert reader.calls == [path, path, other]
