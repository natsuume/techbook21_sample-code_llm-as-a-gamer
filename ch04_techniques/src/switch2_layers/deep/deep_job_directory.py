"""deep のジョブのディレクトリー deep/<種類>-<連番3桁>/ と、その中の job.json の用意。

- 連番は種類ごとに 1 から振る (deep/ にある同じ種類のディレクトリーの番号の最大 + 1)
- job.json: {"kind", "scene_id", "trigger", "requested_t", "previous_latest_frame_t", "latest_frame_t",
  "documents": [notes/ からの / 区切りの相対パス, ...]}
- documents は、そのジョブの対象の文書 (deep が Edit・Write で直接編集してよい文書) の相対パスの文字列のリスト
  (note_paths.deep_document_notes の結果をそのまま並べる)。controls と tips で場面がある場合は、場面の文書と
  共通の文書の両方 (tips の場面は box_targets.md も)。まだ無い文書のパスも含める
- deep は notes/ の文書の本体を直接編集するため、ジョブのディレクトリーには job.json だけを置く (文書のコピーや
  作業用のディレクトリーを作らない。ジョブの実行で、DeepRunner が記録のファイルを足す)
- ジョブの用意では文書の中身を読まない。map の文書の列挙 (note_paths.deep_document_notes) は、シンボリックリンクを
  解決した先が notes/ の中の通常のファイルでないものを除く
"""

import json
import logging
import re
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from switch2_layers.deep.deep_types import DeepJob, DocumentKey, DocumentKind
from switch2_layers.file_io import sha256_bytes, write_bytes_atomic, write_json_atomic
from switch2_layers.notes.note_paths import MAP_DIR, deep_document_notes
from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

JOB_FILE = "job.json"
WORK_DIR = "work"
NO_DOCUMENT_SHA256 = "none"  # job.json で、まだ無い文書の sha256 に書く値 (publish-note の --base-sha256 none と同じ)
# ジョブのディレクトリー名の形: <種類>-<3 桁以上の数字>
JOB_DIRECTORY_NAME_PATTERN = re.compile(r"(?P<kind>[a-z]+)-(?P<number>[0-9]{3,})")


def job_directory_name(kind: DocumentKind, number: int) -> str:
    """ジョブのディレクトリー名 `<種類>-<連番>` を返す。連番は 3 桁に満たなければ 0 で埋める (例: controls-001)。"""
    return f"{kind.value}-{number:03d}"


def parse_job_directory_name(name: str) -> tuple[DocumentKind, int] | None:
    """ジョブのディレクトリー名を (種類, 連番) にする。`<DocumentKind の値>-<3 桁以上の数字>` の形でなければ None。"""
    match = JOB_DIRECTORY_NAME_PATTERN.fullmatch(name)
    if match is None:
        return None
    try:
        kind = DocumentKind(match.group("kind"))
    except ValueError:
        return None
    return kind, int(match.group("number"))


def next_job_number(deep_dir: Path, kind: DocumentKind) -> int:
    """deep_dir にある種類 kind のジョブのディレクトリーの連番の最大 + 1 を返す。無ければ (deep_dir が無い場合も) 1。"""
    numbers = [
        number
        for found_kind, number, _ in _job_directories(deep_dir)
        if found_kind == kind
    ]
    return max(numbers, default=0) + 1


def create_job_directory(
    paths: RunPaths,
    job: DeepJob,
    list_documents: Callable[[Path, DocumentKey], list[str]] = deep_document_notes,
) -> Path:
    """job のディレクトリーを deep/ に作り、job.json を用意して、そのパスを返す。

    list_documents は対象の文書の相対パスを列挙する関数 (既定 notes.note_paths.deep_document_notes)。テストでは、
    列挙の失敗や、決まった一覧を表す関数を渡せる。
    1. 連番 next_job_number(paths.deep_dir, job.kind) で deep/<種類>-<連番>/ を mkdir(exist_ok=False) で作る。
       既にあれば (同時に作られた場合) 連番を 1 増やして作り直す
    2. 対象の文書 list_documents(paths.notes_dir, job.key) を列挙する。文書の中身は読まず、コピーもハッシュも作らない
    3. job.json を置き換えの書き込みで書く (モジュールの docstring の形。documents は 2 の結果をそのままの順で並べた
       文字列のリスト)。ジョブのディレクトリーには job.json のほかに何も作らない
    1 でディレクトリーを作った後に失敗したら (list_documents の例外、job.json の書き込みの OSError など。Exception)、
    作りかけのジョブのディレクトリーを中身ごと消してから、元の例外をそのまま出す。消すのに失敗しても、
    消すときの例外ではなく元の例外を出す。消したディレクトリーの連番は、次のジョブがもう一度使う。
    例外は呼び出し側の DeepScheduler.request から外に出て、surface・middle・自動の呼び出しが deep_request_error として
    記録する。
    """
    job_dir = _make_numbered_directory(paths.deep_dir, job.kind)
    try:
        documents = _copy_documents(
            paths.notes_dir,
            job_dir / WORK_DIR,
            list_documents(paths.notes_dir, job.key),
        )
        (job_dir / WORK_DIR).mkdir(exist_ok=True)
        if job.kind is DocumentKind.MAP:
            (job_dir / WORK_DIR / MAP_DIR).mkdir(exist_ok=True)
        write_json_atomic(job_dir / JOB_FILE, _job_file_value(job, documents))
    except Exception:
        _remove_partial_directory(job_dir)
        raise
    return job_dir


def run_directory_of_job(job_dir: Path) -> Path:
    """ジョブのディレクトリー job_dir (<実行ディレクトリー>/deep/<種類>-<連番>/) の実行ディレクトリーを返す。

    job_dir の名前がジョブのディレクトリー名 (parse_job_directory_name で読める名前) で、親の名前が deep
    (run_directory.DEEP_DIR) なら、親の親 (job_dir.parent.parent) を返す。そうでなければ ValueError にする。
    パスの文字列だけで判断し、ファイルシステムには問い合わせない (シンボリックリンクも解決しない)。
    """
    raise NotImplementedError


def read_job_file(job_dir: Path) -> dict[str, object]:
    """job_dir/job.json を読んで返す。無い・読めない・object でなければ ValueError にする。"""
    path = job_dir / JOB_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    if isinstance(value, dict):
        return value
    raise ValueError(f"{path} is not a JSON object")


def find_previous_latest_frame_t(paths: RunPaths, key: DocumentKey) -> float | None:
    """同じ文書 key の前回のジョブに渡した最新のフレームの時刻を返す。

    deep/ の同じ種類のジョブのディレクトリーのうち、job.json の scene_id が key.scene_id と同じものを連番の大きい順に見て、
    最初に読めた job.json の latest_frame_t (数でなければ None) を返す。同じ文書のジョブが無ければ None。
    """
    same_kind = sorted(
        (
            (number, job_dir)
            for kind, number, job_dir in _job_directories(paths.deep_dir)
            if kind == key.kind
        ),
        reverse=True,
    )
    for _, job_dir in same_kind:
        try:
            value = read_job_file(job_dir)
        except ValueError:
            continue
        if value.get("scene_id") != key.scene_id:
            continue
        latest = value.get("latest_frame_t")
        if isinstance(latest, int | float) and not isinstance(latest, bool):
            return float(latest)
        return None
    return None


def _job_directories(deep_dir: Path) -> list[tuple[DocumentKind, int, Path]]:
    """deep_dir にあるジョブのディレクトリーの (種類, 連番, パス) のリストを返す。deep_dir が無ければ空のリスト。"""
    try:
        entries = list(deep_dir.iterdir())
    except FileNotFoundError:
        return []
    directories: list[tuple[DocumentKind, int, Path]] = []
    for entry in entries:
        parsed = parse_job_directory_name(entry.name)
        if parsed is not None and entry.is_dir():
            directories.append((parsed[0], parsed[1], entry))
    return directories


def _make_numbered_directory(deep_dir: Path, kind: DocumentKind) -> Path:
    """deep_dir/<種類>-<連番>/ を作ってパスを返す。同じ名前が既にあれば、連番を 1 増やして作り直す。"""
    number = next_job_number(deep_dir, kind)
    while True:
        job_dir = deep_dir / job_directory_name(kind, number)
        try:
            job_dir.mkdir(exist_ok=False)
        except FileExistsError:
            number += 1
            continue
        return job_dir


def _copy_documents(
    notes_dir: Path, work_dir: Path, notes: Sequence[str]
) -> list[dict[str, str]]:
    """notes の文書を work_dir にコピーし、job.json の documents の値 [{"note", "sha256"}] を返す。

    本体を 1 回だけ読んだバイト列でコピーとハッシュを作る。無い文書 (FileNotFoundError・通常のファイルでない) は
    コピーせず、sha256 を NO_DOCUMENT_SHA256 にする。
    """
    documents: list[dict[str, str]] = []
    for note in notes:
        data = _read_document(notes_dir / note)
        if data is None:
            documents.append({"note": note, "sha256": NO_DOCUMENT_SHA256})
            continue
        copy_path = work_dir / note
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(copy_path, data)
        documents.append({"note": note, "sha256": sha256_bytes(data)})
    return documents


def _read_document(path: Path) -> bytes | None:
    """文書 path のバイト列を返す。無いか通常のファイルでなければ None。"""
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except FileNotFoundError:
        # 確かめた後に消えた (publish-note の置き換えの途中など)
        return None


def _job_file_value(job: DeepJob, documents: list[dict[str, str]]) -> dict[str, object]:
    """job.json に書く値を返す。"""
    return {
        "kind": job.kind.value,
        "scene_id": job.scene_id,
        "trigger": job.trigger.value,
        "requested_t": job.requested_t,
        "previous_latest_frame_t": job.previous_latest_frame_t,
        "latest_frame_t": job.latest_frame_t,
        "documents": documents,
    }


def _remove_partial_directory(job_dir: Path) -> None:
    """作りかけのジョブのディレクトリーを中身ごと消す。消すのに失敗したら警告だけ書く (元の例外を優先する)。"""
    try:
        shutil.rmtree(job_dir)
    except OSError:
        logger.warning("failed to remove %s", job_dir, exc_info=True)
