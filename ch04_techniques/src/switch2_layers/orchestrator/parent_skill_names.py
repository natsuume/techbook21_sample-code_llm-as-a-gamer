"""実行ディレクトリーの親のディレクトリーにある、Claude Code のスキルの名前を集める。

Claude Code は --setting-sources project でも、作業ディレクトリーから親のディレクトリーをたどって、それぞれの
.claude/skills/ のスキルを読む (リポジトリーの root まで。git の linked worktree ではその root まで)。このリポジトリー
では ch04_techniques/.claude/skills/record-* がこれに当たる。主ループの Claude Code にはこれらを読ませないため、
集めた名前を --settings の skillOverrides で off にする (orchestrator.claude_session_command.build_session_settings)。

たどる範囲は、Claude Code が読む範囲を含むよう、実行ディレクトリーの親からファイルシステムの根までとする。
skillOverrides はスキルを名前で選ぶため、実行ディレクトリーの .claude/skills/ のスキル (switch-*・game-*) と同じ
名前のスキルが親のディレクトリーにあると、その名前のスキルは実行ディレクトリーの分も見えなくなる。
"""

from pathlib import Path

from switch2_layers.run_directory import CLAUDE_DIR, CLAUDE_SKILLS_DIR

# スキルのディレクトリーに置く、スキルの本文のファイル
SKILL_FILE = "SKILL.md"
# スキルを置くディレクトリーの、それぞれのディレクトリーからの相対パス (.claude/skills)
SKILLS_RELATIVE_DIR = Path(CLAUDE_DIR) / CLAUDE_SKILLS_DIR


def find_parent_skill_names(run_dir: Path) -> tuple[str, ...]:
    """run_dir の親のディレクトリーにあるスキルの名前を、重複を除いて名前の順に返す。

    run_dir.parents (run_dir 自体は含めない。近い親から根まで) のそれぞれについて、
    <親>/SKILLS_RELATIVE_DIR/<名前>/SKILL_FILE がファイルとしてある (Path.is_file。シンボリックリンクは
    たどる) <名前> を集める。run_dir は絶対パス (シンボリックリンクを解決したもの) を渡す。
    .claude/skills/ が無い親は飛ばす。ディレクトリーの一覧を読めない (OSError) 親も飛ばす (例外は出さない)。
    """
    raise NotImplementedError
