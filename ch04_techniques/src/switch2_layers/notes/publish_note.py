"""publish-note の本体: deep が編集した文書を、SHA-256 を比べてから本体と入れ替える。

    switch2-layers publish-note --run-dir <実行ディレクトリー> --note <notes/ からの相対パス>
                                --base-sha256 <ハッシュ | none> --from <編集したファイル>

- 本体の今の SHA-256 (無ければ none) が --base-sha256 と一致したら、--from の内容で本体を置き換える
  (一時ファイル + os.replace。親のディレクトリーが無ければ作る)。標準出力に {"status": "published", "sha256": 新しいハッシュ}、
  終了コード 0
- 一致しなければ置き換えない。標準出力に {"status": "conflict", "current_sha256": 今のハッシュ}、終了コード 3。
  deep は本体を読み直し、取り込むか上書きするかを決めて、今のハッシュを付けてもう一度実行する
- --note が notes/ の外を指す (絶対パス・.. を含む・notes/ の外へのシンボリックリンク) か、--from が無いときは、
  何もせず終了コード 2
- --from も置き場所を限る。シンボリックリンクを解決した先が、同じ実行のいずれかの deep のジョブの work/
  (<run-dir>/deep/<種類>-<連番3桁>/work/ の下) に無ければ、何もせず終了コード 2。
  理由: deep (Claude Code) に publish-note の実行だけを許す使い方で、プロンプトインジェクションを受けた deep が、
  自分では読めないホストのファイル (.env など) を --from に指定して notes/ に写すと、その内容が Decision Model や
  middle への要求に入ってしまう。--from をジョブの作業用のディレクトリーに限ることで、deep が自分で書いたもの
  (と job の用意で写した文書) だけを本体に入れられるようにする
- ハッシュの比較と置き換えは、notes/.publish.lock のファイルロック (fcntl.flock の排他ロック) の中で行う。
  同じ実行の publish-note どうしは、比較から置き換えまでを 1 つずつ行う
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

EXIT_PUBLISHED = 0
EXIT_INVALID = 2
EXIT_CONFLICT = 3
# 本体の文書が無いことを表す、SHA-256 の代わりの値
NO_NOTE_SHA256 = "none"


class PublishStatus(StrEnum):
    """publish-note の結果の種類。値は標準出力の JSON の status。"""

    PUBLISHED = "published"
    CONFLICT = "conflict"
    INVALID = "invalid"


class NotePathError(ValueError):
    """--note が notes/ の外を指すか、文書のファイルとして使えないパスだった。"""


class SourcePathError(ValueError):
    """--from が deep のジョブの work/ の外を指すか、通常のファイルでなかった。"""


@dataclass(frozen=True)
class PublishResult:
    """publish-note の結果。

    status           結果の種類
    sha256           PUBLISHED のときの、置き換えた後の本体の SHA-256 (--from の内容のハッシュ)。それ以外は None
    current_sha256   CONFLICT のときの、本体の今の SHA-256 (本体が無ければ NO_NOTE_SHA256)。それ以外は None
    message          INVALID のときの理由 (標準エラー出力に書く)。それ以外は None
    """

    status: PublishStatus
    sha256: str | None = None
    current_sha256: str | None = None
    message: str | None = None

    def exit_code(self) -> int:
        """終了コード (PUBLISHED → EXIT_PUBLISHED、CONFLICT → EXIT_CONFLICT、INVALID → EXIT_INVALID) を返す。"""
        raise NotImplementedError

    def stdout_json(self) -> dict[str, str] | None:
        """標準出力に書く JSON の値を返す。

        PUBLISHED → {"status": "published", "sha256": sha256}、CONFLICT → {"status": "conflict", "current_sha256":
        current_sha256}、INVALID → None (標準出力には何も書かない)。
        """
        raise NotImplementedError


def resolve_note_path(notes_dir: Path, note: str) -> Path:
    """--note の値 note を、notes_dir の中の文書のパスにして返す。

    次のときは NotePathError にする:
        - note が空、または絶対パス
        - note のパスの要素に .. を含む
        - notes_dir / note のシンボリックリンクを解決した先 (Path.resolve) が、notes_dir を解決した先の中に無い
          (途中のディレクトリーや文書自体が notes/ の外へのシンボリックリンクの場合)
        - 解決した先が notes_dir 自体、既にあるディレクトリー、または notes/.publish.lock
    戻り値はシンボリックリンクを解決した先のパス (置き換えるのはリンク自体ではなく、その先の文書)。
    文書がまだ無くてもよい。
    """
    raise NotImplementedError


def resolve_source_path(run_dir: Path, source: Path) -> Path:
    """--from の値 source を、シンボリックリンクを解決した先のパスにして返す。

    解決した先 (Path.resolve) を、run_dir/deep を解決した先からの相対パスにしたとき、次のすべてを満たさなければ
    SourcePathError にする:
        - run_dir/deep の下にある
        - 1 つ目の要素が deep のジョブのディレクトリー名 (deep.deep_job_directory.parse_job_directory_name で読める名前)
        - 2 つ目の要素が work で、その下にある (work/ 自体や、job.json などの work/ の外のファイルは不可)
        - 通常のファイルである
    途中のディレクトリーやファイル自体が、work/ の外へのシンボリックリンクなら、解決した先が外になるため不可。
    """
    raise NotImplementedError


def publish_note(
    run_dir: Path, note: str, base_sha256: str, source: Path
) -> PublishResult:
    """--from (source) の内容で、実行ディレクトリー run_dir の notes/<note> を置き換える (モジュールの docstring の規則)。

    1. run_dir/notes がディレクトリーでなければ INVALID
    2. resolve_note_path が NotePathError なら INVALID
    3. resolve_source_path が SourcePathError なら INVALID (無いファイル・通常のファイルでないものを含む)
    4. notes/.publish.lock を開いて排他ロックを取る (ロックのファイルが無ければ作る)
    5. ロックの中で本体の今の SHA-256 (無ければ NO_NOTE_SHA256) を計算し、base_sha256 (大文字小文字を区別しない) と比べる
       - 一致しなければ置き換えず CONFLICT (current_sha256 = 今のハッシュ)
       - 一致したら、3 で解決した先のファイルを読んだバイト列で本体を置き換え (file_io.write_bytes_atomic。
         親のディレクトリーが無ければ作る)、PUBLISHED (sha256 = そのバイト列の SHA-256)
    6. ロックを放す
    INVALID のときは notes/ の中を何も変えない。source の読み込みに失敗したら (OSError) INVALID にする。
    """
    raise NotImplementedError
