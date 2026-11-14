"""JSON Lines のファイルを読んだ結果を、ファイルの署名 (大きさ・更新時刻・inode) とともに持つキャッシュ。

API は要求のたびに実行の記録を読むため、1 時間の実行 (requests.jsonl が 100 MB を超える) では、丸ごと読むと
要求ごとに数秒かかる。ファイルの署名が前に読んだときと同じなら、前に読んだ結果を返して読み直さない。
inode が同じで大きくなったファイル (実行中に追記されて伸びた記録) は、前に読んだ位置から後だけを読み足す。
それ以外で署名が違えば (別のファイルに置き換わった・小さくなった・同じ大きさで書き換わった)、丸ごと読み直す。
記録のファイルは追記だけで伸びる前提のため、同じ inode のまま書き換えて大きくしたファイルは見分けられない。
キャッシュに入れた結果 (ParsedLines とその records の辞書) は、呼び出し側で書き換えない。
"""

import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from run_dashboard.jsonl_lines import ParsedLines, read_json_lines

# 持つファイルの数の既定の上限
DEFAULT_MAX_FILES = 1024
# 持つファイルの元の大きさ (署名の size) の合計の既定の上限 (バイト)。1 時間の実行の requests.jsonl (約 150 MB)
# を持ったまま、ほかの実行のファイルも持てる大きさにする。読んだ結果のメモリーは元の大きさの数倍になる
DEFAULT_MAX_BYTES = 256 * 1024 * 1024

# 読んだ行の辞書のリストを受け取り、持つ形の行の辞書のリストを返す関数 (run_records.lighten_request_lines など)。
# 行の数と順を変えず、各行の結果はその行と直前の行で決まるものにする
RecordsTransform = Callable[[list[dict]], list[dict]]


@dataclass(frozen=True)
class FileSignature:
    """ファイルの署名。どれかが変われば、ファイルが書き換わったとみなす。

    size       大きさ (os.stat の st_size)
    mtime_ns   更新時刻 (os.stat の st_mtime_ns)
    inode      inode の番号 (os.stat の st_ino。同じ名前で置き換えられたファイルを見分ける)
    """

    size: int
    mtime_ns: int
    inode: int


@dataclass(frozen=True)
class _CacheEntry:
    """キャッシュの 1 件。lines を読む前に取った署名 signature とともに持つ。"""

    signature: FileSignature
    lines: ParsedLines


class JsonLinesCache:
    """JSON Lines のファイルを読んだ結果 (jsonl_lines.ParsedLines) のキャッシュ。

    キーは (ファイルのパス, transform)。同じパスでも transform が違えば、別の結果として持つ。
    持つ数が max_files を超えるか、持つファイルの元の大きさ (署名の size) の合計が max_bytes を超えたら、
    最も長く使っていない (read で返していない) ものから捨てる。ただし、最後に読んだ 1 件は、大きさによらず持つ。
    数だけで限ると、stream.jsonl の多い実行で、大きな requests.jsonl が先に捨てられて読み直しが繰り返されるため、
    大きさの合計でも限る。
    FastAPI の同期のハンドラーはスレッドプールで並行に動くため、次の 2 種類のロックを使う:
        - キーごとのロック: 同じキーの read を 1 つずつ行う。画面が同じファイルを使う API を同時に呼んでも、
          ファイルを読むのは最初の 1 つだけで、ほかはその結果を待って使う (大きなファイルを並行に何度も読まない)
        - 全体のロック: 持っている結果とキーごとのロックの辞書の読み書きを守る。ファイルを読む間は持たないため、
          違うファイルの read は並行に進む
    キーごとのロックは、一度 read したキーの分を持ち続ける (1 つ数十バイトで、数は読んだファイルの数まで)。

    属性:
        max_files    持つ結果の数の上限
        max_bytes    持つファイルの元の大きさの合計の上限 (バイト)
        read_lines   ファイルを読む関数 read_lines(path, offset) (offset は読み始めるバイトの位置。既定
                     jsonl_lines.read_json_lines)。テストでは呼ばれた引数を記録する関数を渡す
    """

    def __init__(
        self,
        max_files: int = DEFAULT_MAX_FILES,
        read_lines: Callable[[Path, int], ParsedLines] = read_json_lines,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        """空のキャッシュを作る。"""
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.read_lines = read_lines
        self._lock = threading.Lock()
        # 持っている結果のファイルの元の大きさの合計
        self._total_bytes = 0
        # (パス, transform) → 結果。並びは使った順 (末尾が最近使ったもの)
        self._entries: OrderedDict[
            tuple[Path, RecordsTransform | None], _CacheEntry
        ] = OrderedDict()
        # (パス, transform) → そのキーの read を 1 つずつ行うためのロック
        self._key_locks: dict[tuple[Path, RecordsTransform | None], threading.Lock] = {}

    def read(
        self, path: Path, transform: RecordsTransform | None = None
    ) -> ParsedLines | None:
        """path の JSON Lines を読んだ結果を返す。前に読んだときと署名が同じなら、前の結果を返す。

        署名 (file_signature) が前に読んだときと違うときは、次のように読む:
            - inode が同じで、大きさが前より大きい (追記で伸びた): 前の結果の next_offset から後だけを
              read_lines(path, 前の next_offset) で読み、前の結果に足した新しい結果を持つ (前の結果の ParsedLines と
              records のリストは変えない。skipped は前と追記の分の合計、next_offset は追記の分を読んだ値)
            - それ以外 (inode が違う・大きさが前以下・まだ読んでいない): read_lines(path, 0) で丸ごと読み直す
        記録のファイルは追記だけで伸びる (第4章は書き換えない) 前提で、同じ inode のまま書き換えて大きくした
        ファイルは追記と見分けられない (前の結果に、書き換えた後の位置から読んだ行を足してしまう)。
        transform があれば、読んだ records に当てた結果を持って返す。追記の分は、前の結果に行があれば
        transform([前の結果の最後の行] + 追記の行) の 2 つ目以降を足す (RecordsTransform の約束により、丸ごと読んで
        当てたときと同じ値になる)。
        ファイルが無いか、読めない (OSError) なら None を返す (前に持っていた結果も捨てる)。
        署名はファイルを読む前に取る。読む間にファイルが伸びても、次の read で署名が違うため、その続きを読む。
        同じキーの read がほかのスレッドで進んでいれば、それが終わるのを待ってから署名を比べる。
        """
        key = (path, transform)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            return self._read_locked(key, path, transform)

    def _read_locked(
        self,
        key: tuple[Path, RecordsTransform | None],
        path: Path,
        transform: RecordsTransform | None,
    ) -> ParsedLines | None:
        """key のロックを持った状態で、read の本体 (署名の比較・読み直し・保存) を行う。"""
        try:
            signature = file_signature(path)
        except OSError:
            self._discard(key)
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.signature == signature:
                self._entries.move_to_end(key)
                return entry.lines
        try:
            if entry is not None and _is_appended(entry.signature, signature):
                lines = self._read_appended(path, entry.lines, transform)
            else:
                lines = self._read_whole(path, transform)
        except OSError:
            self._discard(key)
            return None
        with self._lock:
            self._remove_locked(key)
            self._entries[key] = _CacheEntry(signature=signature, lines=lines)
            self._total_bytes += signature.size
            while len(self._entries) > 1 and (
                len(self._entries) > self.max_files
                or self._total_bytes > self.max_bytes
            ):
                oldest = next(iter(self._entries))
                self._remove_locked(oldest)
        return lines

    def _read_whole(
        self, path: Path, transform: RecordsTransform | None
    ) -> ParsedLines:
        """path を先頭から read_lines(path, 0) で読み、transform があれば records に当てた結果を返す。"""
        lines = self.read_lines(path, 0)
        if transform is None:
            return lines
        return ParsedLines(
            records=transform(lines.records),
            skipped=lines.skipped,
            next_offset=lines.next_offset,
        )

    def _read_appended(
        self, path: Path, previous: ParsedLines, transform: RecordsTransform | None
    ) -> ParsedLines:
        """path の、前の結果 previous の next_offset から後 (追記の分) だけを読み、previous に足した結果を返す。

        records は新しいリスト (previous と、そのリストは変えない)。skipped は previous と追記の分の合計、
        next_offset は追記の分を読んだ値。transform があれば、追記の行に当てた結果を足す。前の結果に行があれば
        transform([previous の最後の行] + 追記の行) の 2 つ目以降を足す (直前の行で決まる結果を、丸ごと読んで
        当てたときと同じにするため)。
        """
        appended = self.read_lines(path, previous.next_offset)
        records = appended.records
        if transform is not None:
            if previous.records:
                records = transform([previous.records[-1], *records])[1:]
            else:
                records = transform(records)
        return ParsedLines(
            records=previous.records + records,
            skipped=previous.skipped + appended.skipped,
            next_offset=appended.next_offset,
        )

    def _discard(self, key: tuple[Path, RecordsTransform | None]) -> None:
        """key の結果を持っていれば捨てる。"""
        with self._lock:
            self._remove_locked(key)

    def _remove_locked(self, key: tuple[Path, RecordsTransform | None]) -> None:
        """全体のロックを持った状態で、key の結果を持っていれば捨て、大きさの合計から引く。"""
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry.signature.size


def _is_appended(previous: FileSignature, current: FileSignature) -> bool:
    """署名 previous のファイルに追記して current になったとみなせるなら True (inode が同じで大きくなった)。"""
    return current.inode == previous.inode and current.size > previous.size


def file_signature(path: Path) -> FileSignature:
    """path のファイルの署名を返す。ファイルが無いか、読めなければ OSError をそのまま出す。"""
    stat = os.stat(path)
    return FileSignature(
        size=stat.st_size, mtime_ns=stat.st_mtime_ns, inode=stat.st_ino
    )
