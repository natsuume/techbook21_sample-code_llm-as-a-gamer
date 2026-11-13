"""実行のディレクトリー (runs/<YYYYmmdd-HHMMSS>/) の一覧と、その中のパスの照合。

外から来た名前 (API のパスの実行の名前、files の API のパス) でファイルのパスを組み立てる前に、ここで一覧と照合し、
runs_dir と実行のディレクトリーの外を指せないようにする。ファイルの中身は読まない (読むのは run_records)。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from switch2_agent.advisor_roles import ADVISOR_ROLES

# 役割ごとの advisor の実行のディレクトリー名 (advisor-<役割>-<連番>。fullmatch で使う)。
# 1 つ目のグループは役割の name、2 つ目は連番 (3 桁以上の数字)
ADVISOR_RUN_DIR_PATTERN = re.compile(
    rf"advisor-({'|'.join(re.escape(role.name) for role in ADVISOR_ROLES)})-(\d{{3,}})"
)
# files の API で返してよいファイルを置くディレクトリー (実行のディレクトリーの直下)
FILE_TOP_DIRS = ("thumbs", "frames")
# files の API で返してよい advisor のディレクトリー (実行のディレクトリーの直下。fullmatch で使う)。
# 役割ごとの名前のほかに、advisor-001 のような役割を持たない形の名前も含む
ADVISOR_FILE_TOP_DIR_PATTERN = re.compile(r"advisor-[^/\\]+")


@dataclass(frozen=True)
class AdvisorRunDir:
    """役割ごとの advisor の実行のディレクトリー 1 つ。

    name    ディレクトリー名 (例: advisor-strategy-001)
    role    役割の name (switch2_agent.advisor_roles.AdvisorRole.name。例: strategy)
    index   役割ごとの連番 (1 始まり。例: 1)
    path    ディレクトリーのパス (実行のディレクトリー / name)
    """

    name: str
    role: str
    index: int
    path: Path


def list_run_names(runs_dir: Path) -> list[str]:
    """runs_dir の直下にある実行のディレクトリーの名前を、名前の降順 (新しい順) で返す。

    入れるのは、ディレクトリー (シンボリックリンクではないもの) で、名前が `.` で始まらないものだけ。
    ファイルと、ディレクトリーを指すシンボリックリンクは入れない。
    """
    raise NotImplementedError


def find_run_dir(runs_dir: Path, run: str) -> Path | None:
    """外から来た実行の名前 run を list_run_names(runs_dir) と照合し、一致すれば runs_dir / run を返す。

    一致しなければ (`..`、`a/b`、無い名前など) None を返す。run でパスを組み立てるのは、一覧と照合した後だけにする。
    """
    raise NotImplementedError


def list_advisor_run_dirs(run_dir: Path) -> list[AdvisorRunDir]:
    """run_dir の直下で、名前が ADVISOR_RUN_DIR_PATTERN に fullmatch するディレクトリーの一覧を返す。

    並びは advisor_roles.ADVISOR_ROLES の役割の順で、同じ役割の中は連番の昇順。
    役割を持たない形の名前 (advisor-001 など) と、ファイルは入れない。
    """
    raise NotImplementedError


def list_advisor_files(advisor_dir: AdvisorRunDir) -> list[dict]:
    """advisor_dir.path の下のファイル (サブディレクトリーの中も含む) の一覧を返す。

    各項目は {"path": "<advisor_dir.name>/<advisor_dir.path からの相対パス (/ 区切り)>", "bytes": ファイルの大きさ}。
    並びは path の昇順。入れるのは Path.is_file() が真のものだけ。ディレクトリーが無ければ空のリストを返す。
    """
    raise NotImplementedError


def resolve_run_file(run_dir: Path, relative: str) -> Path | None:
    """files の API のパス relative を検査し、返してよいファイルなら、その resolve した Path を返す。

    次のどれかに当たれば None を返す:
        - relative が空、`/` か `\\` で始まる、`\\` を含む、NUL (\\x00) を含む
        - `/` で区切った部分に `..`、`.`、空の部分 (`a//b` や末尾の `/`) がある
        - 最初の部分が FILE_TOP_DIRS のどれでもなく、ADVISOR_FILE_TOP_DIR_PATTERN にも fullmatch しない
          (例: run.jsonl、game_notes/x)
        - 部分が 1 つだけ (thumbs など、ディレクトリーそのもの)
        - (run_dir / relative).resolve(strict=True) が失敗する (OSError。無いファイルなど)
        - resolve したパスが run_dir.resolve() の中にない (シンボリックリンクで外に出る場合)
        - resolve したパスがファイルでない
    """
    raise NotImplementedError
