"""ゲームの情報の 4 ファイル (操作方法・攻略情報・目標と進行状況と戦略・問題の解決の助言) を用意し、読み取って要求の文にする。

ファイルは <root>/<ゲーム名>/<種別>.md に置く。種別ごとに受け持ちの advisor (advisor_roles.ADVISOR_ROLES の
note_kind) が Skill の手順で書き換え、Pipeline は要求のたびに最新の内容を読む (advisor の出力は戦略の受け渡しに
使わない)。NOTE_KINDS に無い種別のファイル (以前の実行で作られた progress.md など) は、作らず、読まず、変えない。
起動時に、無いファイルと、空か空白だけのファイルには、default_notes の既定の内容 (controls と strategy) を書く。
"""

import re
import sys
from collections.abc import Mapping
from pathlib import Path

from switch2_remote.error_message import describe_error

from switch2_agent.default_notes import DEFAULT_NOTES

# ゲームの情報の種別。並びは要求の文に並べる順でもある
NOTE_KINDS = ("controls", "tips", "strategy", "advice")
# 種別 → 要求の文で使う見出し
NOTE_TITLES = {
    "controls": "ゲームの操作方法",
    "tips": "攻略情報・Tips",
    "strategy": "目標・進行状況・戦略",
    "advice": "問題の解決の助言",
}
# まだ何も書かれていない種別の代わりに置く文
EMPTY_NOTE_TEXT = "(まだ書かれていない)"
# ゲーム名に使える文字。ディレクトリー名に使うため、パスの区切りや .. を含められないようにする
GAME_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def game_notes_dir(root: Path, game: str) -> Path:
    """ゲーム game の情報を置くディレクトリー root / game を返す (作らない)。

    game が GAME_NAME_PATTERN に fullmatch しなければ ValueError にする (root の外を指せないようにする)。
    """
    if GAME_NAME_PATTERN.fullmatch(game) is None:
        raise ValueError(f"game name must use only letters, digits, _ and -: {game!r}")
    return root / game


def ensure_game_notes(
    root: Path, game: str, defaults: Mapping[str, str] = DEFAULT_NOTES
) -> dict[str, Path]:
    """ゲームの情報のディレクトリーと、NOTE_KINDS の 4 つの <種別>.md を用意し、種別 → パスの辞書を返す。

    ディレクトリーは親を含めて作る。<種別>.md は種別ごとに次のようにする:
        - 無ければ作る。中身は defaults にその種別があればその内容、無ければ空にする
        - あって、中身が空か空白だけで、defaults にその種別があれば、その内容で書き直す
        - あって、中身が空白以外を含むなら変えない。読めない (OSError、UnicodeDecodeError) ときも変えない
        - 空のファイルを書き直せない (OSError) ときは、警告を標準エラー出力に 1 行書き、変えずに続ける
    defaults の既定値は default_notes.DEFAULT_NOTES (controls と strategy の既定の内容)。
    NOTE_KINDS に無い種別のファイル (以前の実行で作られた progress.md など) は作らず、あっても変えず、
    返す辞書にも入れない。game が不正なら game_notes_dir と同じく ValueError にする。
    """
    notes_dir = game_notes_dir(root, game)
    notes_dir.mkdir(parents=True, exist_ok=True)
    paths = {kind: notes_dir / f"{kind}.md" for kind in NOTE_KINDS}
    for kind, path in paths.items():
        default = defaults.get(kind, "")
        # "x" は既にあるファイルを開かない (中身も更新時刻も変えない)
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(default)
            continue
        except FileExistsError:
            pass
        if default and is_blank_file(path):
            try:
                path.write_text(default, encoding="utf-8")
            except OSError as e:
                # 書き直せない空のファイルは、読めないファイルと同じく変えずに起動を続ける
                print(
                    f"warning: could not write default notes to {path}: {describe_error(e)}",
                    file=sys.stderr,
                    flush=True,
                )
    return paths


def is_blank_file(path: Path) -> bool:
    """path のファイルの中身が空か空白だけなら True を返す。

    読めない (OSError、UnicodeDecodeError) ときは False を返す (中身のわからないファイルを書き直さないため)。
    """
    try:
        return not path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False


def read_game_notes(paths: Mapping[str, Path]) -> dict[str, str]:
    """paths (種別 → パス) のファイルを UTF-8 で読み、種別 → 中身の辞書を返す。

    無いファイルと読めないファイル (OSError、UnicodeDecodeError) は空文字にする。読めない理由は呼び出し側に
    返さず、無いファイル以外は警告を標準エラー出力に 1 行書く。
    """
    notes: dict[str, str] = {}
    for kind, path in paths.items():
        try:
            notes[kind] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            notes[kind] = ""
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"warning: could not read {path}: {describe_error(e)}",
                file=sys.stderr,
                flush=True,
            )
            notes[kind] = ""
    return notes


def format_game_notes(notes: Mapping[str, str]) -> str:
    """NOTE_KINDS の順に、`## <NOTE_TITLES の見出し>` の行と中身を並べた文を返す。

    各種別は `## <見出し>\\n<中身の前後の空白を除いたもの>` とし、種別の間は空行 1 つ (\\n\\n) でつなぐ。
    中身の前後の空白を除くと空になるか、notes にその種別が無ければ、中身の代わりに EMPTY_NOTE_TEXT を置く。
    """
    sections = []
    for kind in NOTE_KINDS:
        content = notes.get(kind, "").strip()
        sections.append(f"## {NOTE_TITLES[kind]}\n{content or EMPTY_NOTE_TEXT}")
    return "\n\n".join(sections)
