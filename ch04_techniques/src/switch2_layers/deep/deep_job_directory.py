"""deep のジョブのディレクトリー deep/<種類>-<連番3桁>/ と、その中の job.json の用意。

- 連番は種類ごとに 1 から振る (deep/ にある同じ種類のディレクトリーの番号の最大 + 1)
- job.json: {"kind", "scene_id", "trigger", "requested_t", "previous_latest_frame_t", "latest_frame_t",
  "documents": [notes/ からの / 区切りの相対パス, ...], "map_layout"}
- map_layout は、map のジョブで書くマップの型 ("grid" か "free")。map 以外の種類では null。ジョブを作る側が
  read_job_map_layout で 1 回だけ決め、DeepJob.map_layout に入れる
- documents は、そのジョブの対象の文書 (deep が Edit・Write で直接編集してよい文書) の相対パスの文字列のリスト
  (note_paths.deep_document_notes の結果をそのまま並べる)。controls と tips で場面がある場合は、場面の文書と
  共通の文書の両方 (tips の場面は box_targets.md も)。map は map/map.json と map/places.md、game は game.md。
  まだ無い文書のパスも含める
- deep は notes/ の文書の本体を直接編集するため、ジョブのディレクトリーには job.json だけを置く (文書のコピーや
  作業用のディレクトリーを作らない。ジョブの実行で、DeepRunner が記録のファイルを足す)
- ジョブのディレクトリーの用意 (create_job_directory) では文書の中身を読まない
- 次のジョブの previous_latest_frame_t は、同じ文書の前回の正常に終わったジョブ (deep_job_success) の
  job.json の latest_frame_t から取る (find_previous_latest_frame_t)。ただし map の文書で、notes/map/map.json の
  layout がそのジョブの map_layout と違う (deep がマップの型を書き換える) ときと、前回の正常に終わったジョブの
  map_layout がそのジョブの map_layout と違うときは None にし、deep に記録とフレームを最初から読ませる
"""

import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from switch2_layers.deep.deep_job_success import is_succeeded_job, read_succeeded_jobs
from switch2_layers.deep.deep_types import DeepJob, DocumentKey, DocumentKind
from switch2_layers.file_io import write_json_atomic
from switch2_layers.notes.game_map_layout import read_game_map_layout
from switch2_layers.notes.map_document import MapLayout, read_map_document
from switch2_layers.notes.note_paths import deep_document_notes
from switch2_layers.run_directory import DEEP_DIR, RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

JOB_FILE = "job.json"
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
        documents = list(list_documents(paths.notes_dir, job.key))
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
    if (
        parse_job_directory_name(job_dir.name) is None
        or job_dir.parent.name != DEEP_DIR
    ):
        raise ValueError(f"not a deep job directory (deep/<kind>-<number>): {job_dir}")
    return job_dir.parent.parent


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


def read_job_map_layout(notes_dir: Path, kind: DocumentKind) -> MapLayout | None:
    """種類 kind のジョブで書くマップの型 (DeepJob.map_layout) を返す。

    kind が map なら、notes.game_map_layout.read_game_map_layout(notes_dir) (ゲームの性質の文書の「マップの形式」の
    節の最初の空でない行が `- 型: マス目型` なら grid、それ以外は free)。map 以外の種類では、文書を読まずに None。
    ジョブを作る側 (deep.deep_scheduler の request・保留した要求の起動・deep-once) が、ジョブごとに 1 回だけ呼び、
    同じ結果を DeepJob.map_layout と find_previous_latest_frame_t の map_layout に渡す。
    """
    if kind is DocumentKind.MAP:
        return read_game_map_layout(notes_dir)
    return None


def find_previous_latest_frame_t(
    paths: RunPaths, key: DocumentKey, map_layout: MapLayout | None = None
) -> float | None:
    """同じ文書 key の、前回の正常に終わったジョブに渡した最新のフレームの時刻を返す。

    deep/ の同じ種類のジョブのディレクトリーのうち、job.json の scene_id が key.scene_id と同じで、正常に終わったもの
    (deep_job_success.is_succeeded_job。run.jsonl の deep_end の status が ok か、result.json が正常な終わりを示す) を
    連番の大きい順に見て、最初に見つかったジョブの job.json の latest_frame_t (数でなければ None) を返す。
    失敗・起動の拒否・stop・kill で終わったジョブと、まだ実行中のジョブは飛ばす (そのジョブに渡した期間は、次のジョブが
    読み直す)。正常に終わった同じ文書のジョブが無ければ None。
    map_layout は、これから作るジョブの DeepJob.map_layout (read_job_map_layout の結果)。key.kind が map で
    map_layout が None でないときは、先に型の違いを確かめる (map_layout_will_change): map.json を
    notes.map_document.read_map_document(paths.notes_dir) で読めて、その layout が map_layout と違えば、前回の
    ジョブを見ずに None を返す (deep がマップの型を書き換えるときに、記録とフレームを最初から読ませるため)。
    map.json が無い・読めない・形に合わない (read_map_document が None) ときと、型が同じときは、上の規則で決める。
    ただし、見つかった前回のジョブの job.json の map_layout が "grid" か "free" で、map_layout の値と違えば None を
    返す (型を書き換えるジョブが map.json を新しい型で書いた後に正常に終わらなかったときも、次のジョブに最初から
    読ませるため)。job.json に map_layout が無い・null・ほかの値のときは、この比べ方をしない。
    この関数はゲームの性質の文書を読まない。map 以外の種類では map.json も読まない。
    request・保留した要求の起動・deep-once (deep.deep_scheduler) が、この関数で previous_latest_frame_t を決める。
    """
    if (
        key.kind is DocumentKind.MAP
        and map_layout is not None
        and map_layout_will_change(paths.notes_dir, map_layout)
    ):
        return None
    same_kind = sorted(
        (
            (number, job_dir)
            for kind, number, job_dir in _job_directories(paths.deep_dir)
            if kind == key.kind
        ),
        reverse=True,
    )
    succeeded_in_run_log = read_succeeded_jobs(paths.run_log)
    for _, job_dir in same_kind:
        try:
            value = read_job_file(job_dir)
        except ValueError:
            continue
        if value.get("scene_id") != key.scene_id:
            continue
        if not is_succeeded_job(paths.relative(job_dir), job_dir, succeeded_in_run_log):
            continue
        if (
            key.kind is DocumentKind.MAP
            and map_layout is not None
            and _previous_map_layout_differs(value, map_layout)
        ):
            return None
        latest = value.get("latest_frame_t")
        if isinstance(latest, int | float) and not isinstance(latest, bool):
            return float(latest)
        return None
    return None


def map_layout_will_change(notes_dir: Path, map_layout: MapLayout) -> bool:
    """型 map_layout で書く map のジョブが、マップの型を書き換えるなら True を返す。

    notes_dir の map.json を notes.map_document.read_map_document で読めて、その layout が map_layout と違えば True。
    map.json が無い・読めない・形に合わない (read_map_document が None) ときと、型が同じときは False。
    """
    document = read_map_document(notes_dir)
    if document is None:
        return False
    return document.layout is not map_layout


def _previous_map_layout_differs(
    job_value: dict[str, object], map_layout: MapLayout
) -> bool:
    """前回のジョブの job.json の値 job_value の map_layout が "grid" か "free" で、map_layout の値と違えば True。

    map_layout が無い・null・ほかの値なら False (前回のジョブの型が分からないため、比べない)。
    """
    previous = job_value.get("map_layout")
    valid_values = {layout.value for layout in MapLayout}
    return previous in valid_values and previous != map_layout.value


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


def _job_file_value(job: DeepJob, documents: list[str]) -> dict[str, object]:
    """job.json に書く値を返す。"""
    return {
        "kind": job.kind.value,
        "scene_id": job.scene_id,
        "trigger": job.trigger.value,
        "requested_t": job.requested_t,
        "previous_latest_frame_t": job.previous_latest_frame_t,
        "latest_frame_t": job.latest_frame_t,
        "documents": documents,
        "map_layout": None if job.map_layout is None else job.map_layout.value,
    }


def _remove_partial_directory(job_dir: Path) -> None:
    """作りかけのジョブのディレクトリーを中身ごと消す。消すのに失敗したら警告だけ書く (元の例外を優先する)。"""
    try:
        shutil.rmtree(job_dir)
    except OSError:
        logger.warning("failed to remove %s", job_dir, exc_info=True)
