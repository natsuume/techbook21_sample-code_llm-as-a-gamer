"""主ループの Claude Code に読ませるスキルを、実行ディレクトリーの .claude/skills/ に写す。

写すものは 2 種類:
    本体の操作のスキル   パッケージの orchestrator/skills/switch-<名前>/ (DEFAULT_SWITCH_SKILLS_DIR)。セッションを
                        始めるときに写す (copy_switch_skills)
    ゲームの内容のスキル  Claude が notes/skills/game-<名前>/SKILL.md に書いたもの。セッションを始めるときと、
                        各ターンの result を受け取るたびに、中身を確かめてから写す (copy_game_skills)
dontAsk のモードでは、Claude は .claude/ の下に書けない (保護されたパス)。そのため、ゲームの内容のスキルは notes/ に
書かせ、このモジュールが写す。写したスキルは、Claude Code が .claude/skills/ の変化を見つけて読み直した後
(写してから 2〜3 秒ほど後。2026-11-16、Claude Code 2.1.325 で確かめた) から、同じセッションで使える。写した直後に
送ったターンで使える保証は無い。
Claude Code は、セッションを始めたときに無かった .claude/skills/ の変化を見つけない。そのため、主ループは claude を
起動する前に prepare_claude_skills_dir で .claude/skills/ を作る (写すスキルが無くても作る)。

本体の操作のスキルを写す規則 (copy_skill_directories):
    - 写す元のディレクトリーの直下の、名前が接頭辞で始まるディレクトリーのうち、SKILL_FILE が通常のファイルとしてある
      ものを、ディレクトリーごと写す
    - シンボリックリンク (ディレクトリーのリンク・SKILL.md のリンク・中のファイルのリンク) は写さない。ディレクトリーか
      SKILL.md がリンクなら、そのスキルを写さない。中のほかのファイルのリンクは、そのファイルだけを写さない
    - 写し先に同じ名前のスキルがあれば置き換える (写し先のディレクトリーを消してから写す)。中身 (ファイルの一覧と
      バイト列) が写し先と同じなら写さない (Claude Code にスキルを読み直させないため)
    - 写す元から消えたスキルは、写し先から消さない
ゲームの内容のスキルを写す規則 (copy_game_skills):
    ゲームの内容のスキルはモデルが書いたものなので、信頼される場所 (.claude/skills/) に上げる前に中身を確かめる。
    スキルの frontmatter には権限やツールを足す欄 (allowed-tools・hooks など) があり、本文には読み込むときにシェルの
    コマンドを実行する書き方がある (https://code.claude.com/docs/en/skills の Frontmatter reference と
    dynamic context injection)。そのため次の規則で写す:
    - notes/skills/ の直下の、名前が GAME_SKILL_PREFIX で始まるディレクトリーの SKILL_FILE だけを写す。ほかのファイルは
      写さない (notes/ は Claude が読めるため、参考のファイルは notes/ から読める)
    - ディレクトリーがシンボリックリンク、SKILL_FILE が無い、SKILL_FILE が通常のファイルでない (リンクを含む) ものは、
      写さず、写さなかったスキル (skipped) にも入れない
    - SKILL_FILE は 1 回だけバイト列で読み (read_skill_bytes)、そのバイト列を check_game_skill_bytes で確かめる。
      理由が返ったものは写さずに skipped に入れる
    - 確かめて通ったら、読んだバイト列をそのまま写し先の SKILL_FILE に書く。元のファイルを読み直したり、ファイルと
      して写し直したりしない (確かめた中身と写す中身を一致させるため)
    - 写さなかったスキルの写し先は変えない (前に写した版が .claude/skills/ にあれば残す)
    - 写すときは、.claude/skills/<名前>/ の中を SKILL_FILE だけにする (前の版のほかのファイルがあれば消す)。
      読んだバイト列が写し先の SKILL_FILE と同じで、ほかのファイルも無ければ写さない
    - 写す元から消えたスキルは、写し先から消さない
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
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
# ゲームの内容のスキルの frontmatter に書いてよい欄 (どちらも書く)
GAME_SKILL_FRONTMATTER_KEYS: tuple[str, ...] = ("name", "description")
# frontmatter の始まりと終わりの行
FRONTMATTER_FENCE = "---"
# frontmatter の 1 行の形 `<欄>: <値>` (欄は行の先頭から書き、字下げしない)
FRONTMATTER_LINE_PATTERN = re.compile(r"([A-Za-z0-9_-]+):(.*)")
# スキルからシェルのコマンドを実行する書き方 (https://code.claude.com/docs/en/skills の dynamic context injection)。
# インラインの形 !`<コマンド>` は、行の先頭か空白の直後の ! だけが実行されるが、ここでは位置に依らず !` を含めば
# 写さない。複数行の形は ```! で始まるコードブロックで、これも位置に依らず、3 つ以上の ` か ~ の後に (空白と
# タブを挟んで) ! が続けば写さない (~~~! も ```! と同じに扱う)
INLINE_SHELL_PATTERN = re.compile(r"!`")
FENCED_SHELL_PATTERN = re.compile(r"(?:`{3,}|~{3,})[ \t]*!")
# SKILL.md に含めてはいけない文字。\n のほかの行の区切りになりうる文字と NUL。YAML などの読み手によっては行の区切りに
# なり、frontmatter の確かめをすり抜けて欄を足せるため、含む SKILL.md は写さない。\r は \r\n の一部でないものだけ
# (\r\n は \n にそろえてから確かめる)
UNUSUAL_LINE_BREAK_CHARACTERS: tuple[str, ...] = (
    "\r",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    " ",
    " ",
    "\x00",
)

# 写さなかった理由 (Claude が直せるよう、次のターンの入力に載せる)
NO_FRONTMATTER_REASON = "SKILL.md の先頭に --- の行で囲んだ frontmatter が無い (1 行目を --- にし、name と description を書く)"
MALFORMED_FRONTMATTER_REASON = "frontmatter の行が `欄: 値` の 1 行の形でない (字下げ・複数行の値・コメントは書けない): {line}"
DUPLICATED_KEY_REASON = "frontmatter に同じ欄が 2 回ある: {key}"
EXTRA_KEYS_REASON = "frontmatter に name と description のほかの欄がある: {keys}"
MISSING_KEYS_REASON = "frontmatter に {keys} が無い"
NAME_MISMATCH_REASON = (
    "frontmatter の name ({name}) がディレクトリーの名前 ({directory}) と違う"
)
SHELL_COMMAND_REASON = (
    "スキルからシェルのコマンドを実行する書き方 (!` か ```! のブロック) がある"
)
UNREADABLE_REASON = "SKILL.md を UTF-8 の文として読めない"
UNUSUAL_LINE_BREAK_REASON = "SKILL.md に、\\n のほかの行の区切りか NUL の文字 ({character}) がある (行の区切りは \\n か \\r\\n にする)"


@dataclass(frozen=True)
class SkippedGameSkill:
    """中身を確かめて写さなかったゲームの内容のスキル。

    name     スキルの名前 (notes/skills/ の下のディレクトリーの名前)
    reason   写さなかった理由 (〜_REASON の文。Claude に渡す)
    """

    name: str
    reason: str


@dataclass(frozen=True)
class GameSkillCopyResult:
    """copy_game_skills の結果。

    copied    写したスキルの名前 (名前の順。中身が写し先と同じで写さなかったものは含めない)
    skipped   中身を確かめて写さなかったスキル (名前の順)
    """

    copied: tuple[str, ...]
    skipped: tuple[SkippedGameSkill, ...]


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

    写す規則はモジュールの docstring の「本体の操作のスキルを写す規則」のとおり。中身が写し先と同じで写さなかった
    スキルは、戻り値に含めない。source_dir が無ければ何も写さずに空のタプルを返す。target_dir は作ってあるものとする。
    1 つのスキルを写すのに失敗したら (OSError)、logging で警告を書き、そのスキルを飛ばして残りを写す
    (例外は出さない。写しかけのディレクトリーは残さない)。
    """
    raise NotImplementedError


def copy_switch_skills(source_dir: Path, paths: RunPaths) -> tuple[str, ...]:
    """本体の操作のスキルを写す: copy_skill_directories(source_dir, paths.claude_skills_dir, SWITCH_SKILL_PREFIX)。

    source_dir は DEFAULT_SWITCH_SKILLS_DIR (テストでは一時ディレクトリー)。
    """
    raise NotImplementedError


def has_skill_shell_command(text: str) -> bool:
    """text に、スキルからシェルのコマンドを実行する書き方 (INLINE_SHELL_PATTERN か FENCED_SHELL_PATTERN) があれば True。"""
    raise NotImplementedError


def check_game_skill_text(directory_name: str, text: str) -> str | None:
    """ゲームの内容のスキルの SKILL.md の中身 text を確かめ、写してよければ None、だめなら理由の文を返す (I/O なし)。

    はじめに text の \\r\\n を \\n にそろえ、以後は \\n だけで行に分ける。次の順に確かめ、最初に当たった理由を返す:
        0. そろえた text に UNUSUAL_LINE_BREAK_CHARACTERS のどれかがある (\\r は \\r\\n の一部でないものだけが残る)
           UNUSUAL_LINE_BREAK_REASON (character は、そろえた text の中で最初に現れたその文字を `U+<16 進 4 桁>`
           (大文字) にしたもの。例: \\x0b → U+000B)
        1. 1 行目が FRONTMATTER_FENCE でない、または閉じの FRONTMATTER_FENCE の行が無い   NO_FRONTMATTER_REASON
        2. frontmatter の空でない行が FRONTMATTER_LINE_PATTERN に fullmatch しない (字下げした行・`- ` で始まる行・
           コメントの行を含む)                                                       MALFORMED_FRONTMATTER_REASON
        3. 同じ欄が 2 回ある                                                         DUPLICATED_KEY_REASON
        4. GAME_SKILL_FRONTMATTER_KEYS のほかの欄がある (欄の名前の大文字と小文字は区別する。欄は名前の順に
           ", " でつなぐ)                                                           EXTRA_KEYS_REASON
        5. name か description が無い、または値 (前後の空白を除く) が空 (足りない欄を
           GAME_SKILL_FRONTMATTER_KEYS の順に ", " でつなぐ)                           MISSING_KEYS_REASON
        6. name の値 (前後の空白を除き、両端の同じ引用符 ' か " を 1 組除く) が directory_name と違う
                                                                                    NAME_MISMATCH_REASON
        7. そろえた text のどこか (frontmatter を含む) に has_skill_shell_command が当たる   SHELL_COMMAND_REASON
    directory_name は notes/skills/ の下のディレクトリーの名前 (GAME_SKILL_PREFIX で始まるもの)。行の終わりは \\n と
    \\r\\n のどちらも受け付ける。
    """
    raise NotImplementedError


def check_game_skill_bytes(directory_name: str, data: bytes) -> str | None:
    """SKILL.md のバイト列 data を確かめ、写してよければ None、だめなら理由の文を返す (I/O なし)。

    data を UTF-8 (strict) で文にできなければ UNREADABLE_REASON。できれば、その文を check_game_skill_text で確かめた
    結果を返す。copy_game_skills は、この関数で確かめた data をそのまま写し先に書く。
    """
    raise NotImplementedError


def read_skill_bytes(path: Path) -> bytes:
    """path を、シンボリックリンクをたどらずに開いて (os.O_NOFOLLOW)、通常のファイルなら中身のバイト列を返す。

    開いたファイルを os.fstat で調べ、通常のファイルでなければ OSError を出す。path がシンボリックリンクのとき
    (開けずに OSError) と、読めないときも OSError をそのまま出す。copy_game_skills の read_bytes の既定値。
    """
    raise NotImplementedError


def copy_game_skills(
    paths: RunPaths, read_bytes: Callable[[Path], bytes] = read_skill_bytes
) -> GameSkillCopyResult:
    """ゲームの内容のスキルを、モジュールの docstring の「ゲームの内容のスキルを写す規則」で、
    game_skills_source_dir(paths) から paths.claude_skills_dir に写す。

    read_bytes は SKILL_FILE を読む関数 (既定 read_skill_bytes)。写す候補の SKILL_FILE ごとに 1 回だけ呼び、返った
    バイト列を check_game_skill_bytes で確かめ、通ったらそのバイト列を写し先の SKILL_FILE に置き換えの書き込み
    (file_io.write_bytes_atomic) で書く (元のファイルを読み直さない)。テストでは、読んだ後に元のファイルを変える関数を
    渡す。
    notes/skills/ が無ければ、何も写さずに GameSkillCopyResult((), ()) を返す。paths.claude_skills_dir は作ってある
    ものとする。1 つのスキルを読むか写すのに失敗したら (read_bytes の OSError を含む)、logging で警告を書き、そのスキルを
    飛ばして残りを写す (例外は出さず、skipped にも入れない。写しかけのファイルは残さない)。
    """
    raise NotImplementedError
