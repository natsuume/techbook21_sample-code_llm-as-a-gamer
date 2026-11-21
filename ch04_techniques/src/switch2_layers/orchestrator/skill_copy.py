"""主ループの Claude Code に読ませるスキルを、実行ディレクトリーの .claude/skills/ に写す。

写し先は引数で変えられる (target_dir。既定は .claude/skills/)。主ループの Codex (--agent codex) では、
実行ディレクトリーの .agents/skills/ (orchestrator.codex_session_command.CODEX_SKILLS_RELATIVE_DIR) に写す。写す規則と
写さなかった理由の文は、写し先に依らず同じ。以下の説明は Claude Code のもの (Codex のスキルの読み方は
orchestrator.codex_session_command に書く)。

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
本体の操作のスキルを写すときは、写し先の隣 (.claude/) に一時のディレクトリーを作って中身を書いてから、写し先の
ディレクトリーを消して一時のディレクトリーの名前を変える。失敗したら一時のディレクトリーを消す (写しかけの
ディレクトリーを残さない)。ゲームの内容のスキルは、写し先の SKILL_FILE を置き換えの書き込みで書く。
"""

import contextlib
import logging
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.file_io import TEMP_PREFIX, TEMP_SUFFIX, write_bytes_atomic
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
# ゲームの内容のスキルの name の値の形 (引用符を外した中身)
GAME_SKILL_NAME_PATTERN = re.compile(r"game-[a-z0-9]+(?:-[a-z0-9]+)*")
# 引用符の無い description の値の先頭に置けない、YAML の指示子
YAML_INDICATOR_CHARACTERS = "-?:,[]{}#&*!|>'\"%@`"
# 引用符の無い description の値にできない、YAML が文字列でない値として読む語 (大文字と小文字を区別しない)
YAML_SPECIAL_VALUES: tuple[str, ...] = (
    "true",
    "false",
    "yes",
    "no",
    "on",
    "off",
    "null",
    "~",
)
# 引用符の無い値を YAML が数として読む形 (10 進・小数・指数・16 進・8 進・2 進・60 進・無限大・非数)
YAML_NUMBER_PATTERN = re.compile(
    r"[-+]?(?:"
    r"[0-9][0-9_]*(?::[0-5]?[0-9])*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?"
    r"|\.[0-9][0-9_]*(?:[eE][-+]?[0-9]+)?"
    r"|0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+"
    r"|\.(?:inf|Inf|INF)"
    r")"
    r"|\.(?:nan|NaN|NAN)"
)
# 引用符の無い値の中で、YAML が値の区切りかコメントの始まりとして読む並び
YAML_PLAIN_BREAK_PATTERN = re.compile(r":[ \t]|[ \t]#")
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
    "\N{LINE SEPARATOR}",
    "\N{PARAGRAPH SEPARATOR}",
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
INVALID_NAME_REASON = (
    "frontmatter の name ({name}) が、game- で始まり英小文字・数字・ハイフンだけの名前でない"
    " (ディレクトリーの名前もその形にし、name: game-<名前> と書く)"
)
INVALID_DESCRIPTION_REASON = (
    "frontmatter の description の値が、YAML で 1 つの文字列として読めない形のおそれがある"
    ' (description: "<どの場面で使うか>" のように " で囲んで書き、中に " と \\ を入れない)'
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
    return paths.notes_dir / GAME_SKILLS_NOTE_DIR


def prepare_skills_dir(target_dir: Path) -> Path:
    """スキルの写し先 target_dir を、親を含めて作り (既にあれば何もしない)、そのパスを返す。

    作れなければ OSError をそのまま出す。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def prepare_claude_skills_dir(paths: RunPaths) -> Path:
    """paths.claude_skills_dir (.claude/skills/) を、親を含めて作り (既にあれば何もしない)、そのパスを返す。

    prepare_skills_dir(paths.claude_skills_dir) と同じ。作れなければ OSError をそのまま出す。
    """
    return prepare_skills_dir(paths.claude_skills_dir)


def copy_skill_directories(
    source_dir: Path, target_dir: Path, prefix: str
) -> tuple[str, ...]:
    """source_dir の直下のスキルのうち、名前が prefix で始まるものを target_dir に写し、写した名前を名前の順に返す。

    写す規則はモジュールの docstring の「本体の操作のスキルを写す規則」のとおり。中身が写し先と同じで写さなかった
    スキルは、戻り値に含めない。source_dir が無ければ何も写さずに空のタプルを返す。target_dir は作ってあるものとする。
    1 つのスキルを写すのに失敗したら (OSError)、logging で警告を書き、そのスキルを飛ばして残りを写す
    (例外は出さない。写しかけのディレクトリーは残さない)。
    """
    copied: list[str] = []
    for skill_dir in _skill_directories(source_dir, prefix):
        try:
            files = _regular_files(skill_dir)
            if _regular_files_or_none(target_dir / skill_dir.name) == files:
                continue
            _replace_skill_directory(target_dir, skill_dir.name, files)
        except OSError:
            logger.warning("failed to copy the skill %s", skill_dir, exc_info=True)
            continue
        copied.append(skill_dir.name)
    return tuple(copied)


def copy_switch_skills(
    source_dir: Path, paths: RunPaths, target_dir: Path | None = None
) -> tuple[str, ...]:
    """本体の操作のスキルを写す: copy_skill_directories(source_dir, 写し先, SWITCH_SKILL_PREFIX)。

    source_dir は DEFAULT_SWITCH_SKILLS_DIR (テストでは一時ディレクトリー)。写し先は target_dir (None なら
    paths.claude_skills_dir)。
    """
    target = target_dir if target_dir is not None else paths.claude_skills_dir
    return copy_skill_directories(source_dir, target, SWITCH_SKILL_PREFIX)


def has_skill_shell_command(text: str) -> bool:
    """text に、スキルからシェルのコマンドを実行する書き方 (INLINE_SHELL_PATTERN か FENCED_SHELL_PATTERN) があれば True。"""
    return (
        INLINE_SHELL_PATTERN.search(text) is not None
        or FENCED_SHELL_PATTERN.search(text) is not None
    )


def is_plain_skill_name(value: str) -> bool:
    """name の値 value (前後の空白を除いたもの) が、ゲームの内容のスキルの名前として書ける形なら True を返す。

    引用符の無い形か、" か ' の 1 組で囲んだ形で、中身が GAME_SKILL_NAME_PATTERN に fullmatch すること。
    """
    return GAME_SKILL_NAME_PATTERN.fullmatch(_unquote(value)) is not None


def is_plain_skill_description(value: str) -> bool:
    """description の値 value (前後の空白を除いたもの) が、YAML で 1 つの文字列として読める形なら True を返す。

    次のどちらかのときだけ True:
        - " で囲み、中に " と \\ を含まない形
        - 引用符の無い形で、次のどれにも当たらない: 空、YAML_PLAIN_BREAK_PATTERN (`: ` と ` #`。空白の代わりの
          タブも含む) を含む、: で終わる、YAML_INDICATOR_CHARACTERS のどれかで始まる、YAML_SPECIAL_VALUES の
          どれか (大文字と小文字を区別しない)、YAML_NUMBER_PATTERN に fullmatch する (数)
    ' で囲んだ形は、先頭の ' が指示子に当たるため False。
    """
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        return '"' not in inner and "\\" not in inner
    if not value or value[0] in YAML_INDICATOR_CHARACTERS:
        return False
    if YAML_PLAIN_BREAK_PATTERN.search(value) is not None or value.endswith(":"):
        return False
    if value.lower() in YAML_SPECIAL_VALUES:
        return False
    return YAML_NUMBER_PATTERN.fullmatch(value) is None


def check_game_skill_text(directory_name: str, text: str) -> str | None:
    """ゲームの内容のスキルの SKILL.md の中身 text を確かめ、写してよければ None、だめなら理由の文を返す (I/O なし)。

    はじめに text の \\r\\n を \\n にそろえ、以後は \\n だけで行に分ける。次の順に確かめ、最初に当たった理由を返す:
        0. そろえた text に UNUSUAL_LINE_BREAK_CHARACTERS のどれかがある (\\r は \\r\\n の一部でないものだけが残る)
           UNUSUAL_LINE_BREAK_REASON (character は、そろえた text の中で最初に現れたその文字を `U+<16 進 4 桁>`
           (大文字) にしたもの。例: \\x0b → U+000B)
        1. 1 行目が FRONTMATTER_FENCE でない、または閉じの FRONTMATTER_FENCE の行が無い   NO_FRONTMATTER_REASON
        2. frontmatter の空でない行が FRONTMATTER_LINE_PATTERN に fullmatch しない (字下げした行・`- ` で始まる行・
           コメントの行を含む)、または `:` の直後に空白もタブも無いまま値が続く (YAML はその行を 1 つの文字列として
           読むため)                                                                 MALFORMED_FRONTMATTER_REASON
        3. 同じ欄が 2 回ある                                                         DUPLICATED_KEY_REASON
        4. GAME_SKILL_FRONTMATTER_KEYS のほかの欄がある (欄の名前の大文字と小文字は区別する。欄は名前の順に
           ", " でつなぐ)                                                           EXTRA_KEYS_REASON
        5. name か description が無い、または値 (前後の空白を除き、両端の同じ引用符 ' か " を 1 組除いたもの) が空
           (足りない欄を GAME_SKILL_FRONTMATTER_KEYS の順に ", " でつなぐ)              MISSING_KEYS_REASON
        6. name の値 (前後の空白を除き、両端の同じ引用符 ' か " を 1 組除く) が directory_name と違う
                                                                                    NAME_MISMATCH_REASON
        7. name の値 (前後の空白を除いたもの) が is_plain_skill_name に合わない          INVALID_NAME_REASON
        8. description の値 (前後の空白を除いたもの) が is_plain_skill_description に合わない
                                                                                    INVALID_DESCRIPTION_REASON
        9. そろえた text のどこか (frontmatter を含む) に has_skill_shell_command が当たる   SHELL_COMMAND_REASON
    directory_name は notes/skills/ の下のディレクトリーの名前 (GAME_SKILL_PREFIX で始まるもの)。行の終わりは \\n と
    \\r\\n のどちらも受け付ける。
    """
    text = text.replace("\r\n", "\n")
    for character in text:
        if character in UNUSUAL_LINE_BREAK_CHARACTERS:
            return UNUSUAL_LINE_BREAK_REASON.format(character=f"U+{ord(character):04X}")
    lines = text.split("\n")
    if lines[0] != FRONTMATTER_FENCE or FRONTMATTER_FENCE not in lines[1:]:
        return NO_FRONTMATTER_REASON
    end = lines.index(FRONTMATTER_FENCE, 1)
    fields = _frontmatter_fields(lines[1:end])
    if isinstance(fields, str):
        return fields
    extra = sorted(set(fields) - set(GAME_SKILL_FRONTMATTER_KEYS))
    if extra:
        return EXTRA_KEYS_REASON.format(keys=", ".join(extra))
    missing = [
        key for key in GAME_SKILL_FRONTMATTER_KEYS if not _unquote(fields.get(key, ""))
    ]
    if missing:
        return MISSING_KEYS_REASON.format(keys=", ".join(missing))
    name = _unquote(fields["name"])
    if name != directory_name:
        return NAME_MISMATCH_REASON.format(name=name, directory=directory_name)
    if not is_plain_skill_name(fields["name"]):
        return INVALID_NAME_REASON.format(name=name)
    if not is_plain_skill_description(fields["description"]):
        return INVALID_DESCRIPTION_REASON
    if has_skill_shell_command(text):
        return SHELL_COMMAND_REASON
    return None


def check_game_skill_bytes(directory_name: str, data: bytes) -> str | None:
    """SKILL.md のバイト列 data を確かめ、写してよければ None、だめなら理由の文を返す (I/O なし)。

    data を UTF-8 (strict) で文にできなければ UNREADABLE_REASON。できれば、その文を check_game_skill_text で確かめた
    結果を返す。copy_game_skills は、この関数で確かめた data をそのまま写し先に書く。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return UNREADABLE_REASON
    return check_game_skill_text(directory_name, text)


def read_skill_bytes(path: Path) -> bytes:
    """path を、シンボリックリンクをたどらずに開いて (os.O_NOFOLLOW)、通常のファイルなら中身のバイト列を返す。

    開いたファイルを os.fstat で調べ、通常のファイルでなければ OSError を出す。path がシンボリックリンクのとき
    (開けずに OSError) と、読めないときも OSError をそのまま出す。copy_game_skills の read_bytes の既定値。
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise OSError(f"skill file is not a regular file: {path}")
        return file.read()


def copy_game_skills(
    paths: RunPaths,
    read_bytes: Callable[[Path], bytes] = read_skill_bytes,
    target_dir: Path | None = None,
) -> GameSkillCopyResult:
    """ゲームの内容のスキルを、モジュールの docstring の「ゲームの内容のスキルを写す規則」で、
    game_skills_source_dir(paths) から写し先 (target_dir。None なら paths.claude_skills_dir) に写す。

    read_bytes は SKILL_FILE を読む関数 (既定 read_skill_bytes)。写す候補の SKILL_FILE ごとに 1 回だけ呼び、返った
    バイト列を check_game_skill_bytes で確かめ、通ったらそのバイト列を写し先の SKILL_FILE に置き換えの書き込み
    (file_io.write_bytes_atomic) で書く (元のファイルを読み直さない)。テストでは、読んだ後に元のファイルを変える関数を
    渡す。
    notes/skills/ が無ければ、何も写さずに GameSkillCopyResult((), ()) を返す。写し先は作ってあるものとする。
    1 つのスキルを読むか写すのに失敗したら (read_bytes の OSError を含む)、logging で警告を書き、そのスキルを
    飛ばして残りを写す (例外は出さず、skipped にも入れない。写しかけのファイルは残さない)。
    """
    if target_dir is None:
        target_dir = paths.claude_skills_dir
    copied: list[str] = []
    skipped: list[SkippedGameSkill] = []
    for skill_dir in _skill_directories(
        game_skills_source_dir(paths), GAME_SKILL_PREFIX
    ):
        name = skill_dir.name
        try:
            data = read_bytes(skill_dir / SKILL_FILE)
        except OSError:
            logger.warning("failed to read the game skill %s", skill_dir, exc_info=True)
            continue
        reason = check_game_skill_bytes(name, data)
        if reason is not None:
            skipped.append(SkippedGameSkill(name=name, reason=reason))
            continue
        try:
            if _regular_files_or_none(target_dir / name) == {SKILL_FILE: data}:
                continue
            _write_skill_file_only(target_dir / name, data)
        except OSError:
            logger.warning("failed to copy the game skill %s", skill_dir, exc_info=True)
            continue
        copied.append(name)
    return GameSkillCopyResult(copied=tuple(copied), skipped=tuple(skipped))


def _unquote(value: str) -> str:
    """value の前後の空白を除き、両端が同じ引用符 (' か ") なら 1 組だけ外して返す。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _frontmatter_fields(lines: list[str]) -> dict[str, str] | str:
    """frontmatter の行 (囲みの --- の行を除く) を {欄: 値 (前後の空白を除く)} にする。

    形が合わなければ、check_game_skill_text の 2 か 3 の理由の文を返す。
    """
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        match = FRONTMATTER_LINE_PATTERN.fullmatch(line)
        if match is None:
            return MALFORMED_FRONTMATTER_REASON.format(line=line)
        key, rest = match.group(1), match.group(2)
        if rest and rest[0] not in " \t":
            return MALFORMED_FRONTMATTER_REASON.format(line=line)
        if key in fields:
            return DUPLICATED_KEY_REASON.format(key=key)
        fields[key] = rest.strip()
    return fields


def _skill_directories(source_dir: Path, prefix: str) -> list[Path]:
    """source_dir の直下の、名前が prefix で始まり、SKILL_FILE が通常のファイルとしてあるディレクトリーを名前の順に返す。

    ディレクトリーか SKILL_FILE がシンボリックリンクのものは含めない。source_dir が無いか読めなければ空のリスト。
    """
    try:
        children = sorted(source_dir.iterdir()) if source_dir.is_dir() else []
    except OSError:
        logger.warning("failed to list the skills in %s", source_dir, exc_info=True)
        return []
    found: list[Path] = []
    for child in children:
        if (
            not child.name.startswith(prefix)
            or child.is_symlink()
            or not child.is_dir()
        ):
            continue
        skill_file = child / SKILL_FILE
        if skill_file.is_symlink() or not skill_file.is_file():
            continue
        found.append(child)
    return found


def _regular_files(directory: Path) -> dict[str, bytes]:
    """directory の下の通常のファイル (シンボリックリンクを除く) の、/ 区切りの相対パス → 中身を返す。

    シンボリックリンクのディレクトリーの中には入らない。読めなければ OSError をそのまま出す。
    """
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(directory, followlinks=False):
        directories.sort()
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def _regular_files_or_none(directory: Path) -> dict[str, bytes] | None:
    """directory がリンクでないディレクトリーなら _regular_files の結果を、そうでなければ None を返す。"""
    if directory.is_symlink() or not directory.is_dir():
        return None
    return _regular_files(directory)


def _write_skill_file_only(target: Path, data: bytes) -> None:
    """ゲームの内容のスキルの写し先のディレクトリー target の中を、中身が data の SKILL_FILE だけにする。

    target がディレクトリーでなければ (リンクを含む) 消してから作る。target の中の SKILL_FILE のほかの項目を消し、
    SKILL_FILE を file_io.write_bytes_atomic で置き換えて書く (書きかけの SKILL_FILE を残さない)。
    """
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        target.unlink()
    target.mkdir(exist_ok=True)
    for child in target.iterdir():
        if child.name == SKILL_FILE:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    write_bytes_atomic(target / SKILL_FILE, data)


def _replace_skill_directory(
    target_dir: Path, name: str, files: dict[str, bytes]
) -> None:
    """target_dir/name を、files (相対パス → 中身) だけを持つディレクトリーに置き換える。

    target_dir の親 (.claude/) に一時のディレクトリーを作って files を書き、target_dir/name を消してから、一時の
    ディレクトリーの名前を target_dir/name に変える。失敗したら一時のディレクトリーを消し、OSError をそのまま出す。
    """
    temporary = Path(
        tempfile.mkdtemp(
            dir=target_dir.parent, prefix=f"{TEMP_PREFIX}{name}.", suffix=TEMP_SUFFIX
        )
    )
    try:
        for relative, data in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        target = target_dir / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        temporary.rename(target)
    except BaseException:
        with contextlib.suppress(OSError):
            shutil.rmtree(temporary)
        raise
