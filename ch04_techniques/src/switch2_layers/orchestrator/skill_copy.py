"""主ループの Claude Code に読ませるスキルを、実行ディレクトリーの .claude/skills/ に写す。

写すものは 2 種類:
    本体の操作のスキル   パッケージの orchestrator/skills/switch-<名前>/ (DEFAULT_SWITCH_SKILLS_DIR)。セッションを
                        始めるときに写す
    ゲームの内容のスキル  Claude が notes/skills/game-<名前>/SKILL.md に書いたもの。セッションを始めるときと、
                        各ターンの result を受け取るたびに写す
dontAsk のモードでは、Claude は .claude/ の下に書けない (保護されたパス)。そのため、ゲームの内容のスキルは notes/ に
書かせ、このモジュールが写す。写したスキルは、Claude Code が .claude/skills/ の変化を見つけて読み直した後
(写してから 2〜3 秒ほど後。2026-11-16、Claude Code 2.1.325 で確かめた) から、同じセッションで使える。写した直後に
送ったターンで使える保証は無い。
Claude Code は、セッションを始めたときに無かった .claude/skills/ の変化を見つけない。そのため、主ループは claude を
起動する前に prepare_claude_skills_dir で .claude/skills/ を作る (写すスキルが無くても作る)。

写す規則 (copy_skill_directories):
    - 写す元のディレクトリーの直下の、名前が接頭辞 (switch- か game-) で始まるディレクトリーのうち、SKILL_FILE が
      通常のファイルとしてあるものを写す
    - シンボリックリンク (ディレクトリーのリンク・SKILL.md のリンク・中のファイルのリンク) は写さない。ディレクトリーか
      SKILL.md がリンクなら、そのスキルを写さない。中のほかのファイルのリンクは、そのファイルだけを写さない
    - 写し先に同じ名前のスキルがあれば置き換える (写し先のディレクトリーを消してから写す)。中身 (ファイルの一覧と
      バイト列) が写し先と同じなら写さない (Claude Code にスキルを読み直させないため)
    - 写す元から消えたスキルは、写し先から消さない
"""

import logging
from pathlib import Path

from switch2_layers.run_directory import RunPaths

# logging を設定しなければ、WARNING 以上は標準エラー出力に書かれる
logger = logging.getLogger(__name__)

# スキルの本文のファイル
SKILL_FILE = "SKILL.md"
# 本体の操作のスキルの名前の接頭辞
SWITCH_SKILL_PREFIX = "switch-"
# ゲームの内容のスキルの名前の接頭辞
GAME_SKILL_PREFIX = "game-"
# Claude がゲームの内容のスキルを書く、notes/ の下のディレクトリー
GAME_SKILLS_NOTE_DIR = "skills"
# 本体の操作のスキルを置く、パッケージの中のディレクトリー
DEFAULT_SWITCH_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def game_skills_source_dir(paths: RunPaths) -> Path:
    """Claude がゲームの内容のスキルを書くディレクトリー paths.notes_dir / GAME_SKILLS_NOTE_DIR を返す。"""
    raise NotImplementedError


def prepare_claude_skills_dir(paths: RunPaths) -> Path:
    """paths.claude_skills_dir (.claude/skills/) を、親を含めて作り (既にあれば何もしない)、そのパスを返す。

    作れなければ OSError をそのまま出す。
    """
    raise NotImplementedError


def copy_skill_directories(
    source_dir: Path, target_dir: Path, prefix: str
) -> tuple[str, ...]:
    """source_dir の直下のスキルのうち、名前が prefix で始まるものを target_dir に写し、写した名前を名前の順に返す。

    写す規則はモジュールの docstring のとおり。中身が写し先と同じで写さなかったスキルは、戻り値に含めない。
    source_dir が無ければ何も写さずに空のタプルを返す。target_dir は作ってあるものとする。
    1 つのスキルを写すのに失敗したら (OSError)、logging で警告を書き、そのスキルを飛ばして残りを写す
    (例外は出さない。写しかけのディレクトリーは残さない)。
    """
    raise NotImplementedError


def copy_switch_skills(source_dir: Path, paths: RunPaths) -> tuple[str, ...]:
    """本体の操作のスキルを写す: copy_skill_directories(source_dir, paths.claude_skills_dir, SWITCH_SKILL_PREFIX)。

    source_dir は DEFAULT_SWITCH_SKILLS_DIR (テストでは一時ディレクトリー)。
    """
    raise NotImplementedError


def copy_game_skills(paths: RunPaths) -> tuple[str, ...]:
    """ゲームの内容のスキルを写す:
    copy_skill_directories(game_skills_source_dir(paths), paths.claude_skills_dir, GAME_SKILL_PREFIX)。"""
    raise NotImplementedError
