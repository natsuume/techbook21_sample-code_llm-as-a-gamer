"""主ループの Codex に読ませないスキルの、SKILL.md のパスを集める。

Codex は、作業ディレクトリーからリポジトリーのルートまでの各階層の .agents/skills/<名前>/SKILL.md と、ユーザーの
スキル ($HOME/.agents/skills と $CODEX_HOME/skills) と、管理者のスキル (/etc/codex/skills) を読む。主ループの
Codex には実行ディレクトリーの .agents/skills/ のスキルだけを読ませたいので、それ以外を集めて、-c の
skills.config で 1 つずつ止める (codex_session_command.skills_disable_override)。同梱のスキル
($CODEX_HOME/skills/.system/) は skills.bundled.enabled=false で止めるため、ここでは集めない。

たどる範囲は、Codex が読む範囲を含むよう、実行ディレクトリーの親からファイルシステムの根までとする
(親の階層のスキルを止めることは 2026-11-21、codex-cli 0.171.0 で確かめた。ユーザーと管理者のスキルは、このマシンに
無いため確かめていない)。
"""

from collections.abc import Mapping
from pathlib import Path

from switch2_layers.orchestrator.codex_session_command import (
    CODEX_SKILLS_RELATIVE_DIR,
)

# スキルのディレクトリーに置く、スキルの本文のファイル
SKILL_FILE = "SKILL.md"
# Codex の設定の場所を変える環境変数と、その既定のディレクトリー ($HOME からの相対パス)
CODEX_HOME_ENV = "CODEX_HOME"
DEFAULT_CODEX_HOME_RELATIVE = Path(".codex")
# $CODEX_HOME の下のユーザーのスキルのディレクトリー
CODEX_HOME_SKILLS_DIR = "skills"
# 管理者のスキルのディレクトリー
ADMIN_SKILLS_DIR = Path("/etc/codex/skills")


def skill_files_in(skills_dir: Path) -> list[Path]:
    """skills_dir の直下の <名前>/SKILL_FILE のうち、ファイルとしてあるもの (Path.is_file。シンボリックリンクは
    たどる) を名前の順に返す。

    名前が . で始まるディレクトリー (同梱のスキルの .system など) は含めない。skills_dir が無いか、一覧を読めなければ
    (OSError) 空のリスト。
    """
    try:
        if not skills_dir.is_dir():
            return []
        children = sorted(skills_dir.iterdir())
    except OSError:
        return []
    files: list[Path] = []
    for child in children:
        if child.name.startswith("."):
            continue
        skill_file = child / SKILL_FILE
        try:
            if skill_file.is_file():
                files.append(skill_file)
        except OSError:
            continue
    return files


def user_skill_dirs(environ: Mapping[str, str]) -> list[Path]:
    """ユーザーと管理者のスキルのディレクトリーを返す。

    environ の HOME があれば $HOME/.agents/skills、CODEX_HOME があれば $CODEX_HOME/skills (無ければ
    $HOME/.codex/skills)、最後に ADMIN_SKILLS_DIR。HOME も CODEX_HOME も無ければ ADMIN_SKILLS_DIR だけ。
    """
    dirs: list[Path] = []
    home = environ.get("HOME")
    if home:
        dirs.append(Path(home) / CODEX_SKILLS_RELATIVE_DIR)
    codex_home = environ.get(CODEX_HOME_ENV)
    if codex_home:
        dirs.append(Path(codex_home) / CODEX_HOME_SKILLS_DIR)
    elif home:
        dirs.append(Path(home) / DEFAULT_CODEX_HOME_RELATIVE / CODEX_HOME_SKILLS_DIR)
    dirs.append(ADMIN_SKILLS_DIR)
    return dirs


def find_codex_skill_files_to_disable(
    run_dir: Path, environ: Mapping[str, str]
) -> tuple[Path, ...]:
    """主ループの Codex に読ませないスキルの SKILL.md のパスを、重複を除いて見つけた順に返す。

    run_dir.parents (run_dir 自体は含めない。近い親から根まで) のそれぞれの <親>/.agents/skills/ と、
    user_skill_dirs(environ) のそれぞれのディレクトリーについて、skill_files_in の結果を並べる。
    run_dir は絶対パス (シンボリックリンクを解決したもの) を渡す。environ は子プロセスに渡す環境変数。
    """
    found: dict[Path, None] = {}
    dirs = [parent / CODEX_SKILLS_RELATIVE_DIR for parent in run_dir.parents]
    for skills_dir in [*dirs, *user_skill_dirs(environ)]:
        for path in skill_files_in(skills_dir):
            found[path] = None
    return tuple(found)
