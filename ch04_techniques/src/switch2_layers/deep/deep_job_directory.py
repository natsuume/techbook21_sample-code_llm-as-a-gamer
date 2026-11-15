"""deep のジョブのディレクトリー deep/<種類>-<連番3桁>/ と、その中の job.json・work/ の用意。

- 連番は種類ごとに 1 から振る (deep/ にある同じ種類のディレクトリーの番号の最大 + 1)
- job.json: {"kind", "scene_id", "trigger", "requested_t", "previous_latest_frame_t", "latest_frame_t",
  "documents": [{"note": notes/ からの相対パス, "sha256": SHA-256 か "none"}]}
- work/: 対象の文書のコピーを work/<notes/ からの相対パス> に置く。controls と tips で場面がある場合は、
  場面の文書と共通の文書の両方を入れる。まだ無い文書はコピーせず、sha256 を "none" にする。
  map では、work/map/ をいつも作る (文書が無くても)
"""

from pathlib import Path

from switch2_layers.deep.deep_types import DeepJob, DocumentKey, DocumentKind
from switch2_layers.run_directory import RunPaths

JOB_FILE = "job.json"
WORK_DIR = "work"
NO_DOCUMENT_SHA256 = "none"  # job.json で、まだ無い文書の sha256 に書く値 (publish-note の --base-sha256 none と同じ)


def job_directory_name(kind: DocumentKind, number: int) -> str:
    """ジョブのディレクトリー名 `<種類>-<連番>` を返す。連番は 3 桁に満たなければ 0 で埋める (例: controls-001)。"""
    raise NotImplementedError


def parse_job_directory_name(name: str) -> tuple[DocumentKind, int] | None:
    """ジョブのディレクトリー名を (種類, 連番) にする。`<DocumentKind の値>-<3 桁以上の数字>` の形でなければ None。"""
    raise NotImplementedError


def next_job_number(deep_dir: Path, kind: DocumentKind) -> int:
    """deep_dir にある種類 kind のジョブのディレクトリーの連番の最大 + 1 を返す。無ければ (deep_dir が無い場合も) 1。"""
    raise NotImplementedError


def create_job_directory(paths: RunPaths, job: DeepJob) -> Path:
    """job のディレクトリーを deep/ に作り、job.json と work/ を用意して、そのパスを返す。

    1. 連番 next_job_number(paths.deep_dir, job.kind) で deep/<種類>-<連番>/ を mkdir(exist_ok=False) で作る。
       既にあれば (同時に作られた場合) 連番を 1 増やして作り直す
    2. 対象の文書 notes.note_paths.deep_document_notes(paths.notes_dir, job.key) のそれぞれについて、
       本体のファイルがあればバイト列を 1 回読み、そのバイト列を work/<相対パス> に書き (親のディレクトリーを作る)、
       同じバイト列の SHA-256 を記録する (コピーとハッシュが同じ内容を表すようにする)。無ければ NO_DOCUMENT_SHA256
    3. work/ を作る (コピーが無くても)。map なら work/map/ も作る
    4. job.json を置き換えの書き込みで書く (モジュールの docstring の形。documents は 2 の順)
    """
    raise NotImplementedError


def read_job_file(job_dir: Path) -> dict[str, object]:
    """job_dir/job.json を読んで返す。無い・読めない・object でなければ ValueError にする。"""
    raise NotImplementedError


def find_previous_latest_frame_t(paths: RunPaths, key: DocumentKey) -> float | None:
    """同じ文書 key の前回のジョブに渡した最新のフレームの時刻を返す。

    deep/ の同じ種類のジョブのディレクトリーのうち、job.json の scene_id が key.scene_id と同じものを連番の大きい順に見て、
    最初に読めた job.json の latest_frame_t (数でなければ None) を返す。同じ文書のジョブが無ければ None。
    """
    raise NotImplementedError
