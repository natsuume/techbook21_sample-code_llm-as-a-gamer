"""ゲームの情報の 4 ファイル (操作方法・攻略情報・進行状況・戦略) を用意し、読み取って要求の文にする。

ファイルは <root>/<ゲーム名>/<種別>.md に置く。advisor が Skill の手順で書き換え、
Pipeline は要求のたびに最新の内容を読む (advisor の出力は戦略の受け渡しに使わない)。
"""

import re
from collections.abc import Mapping
from pathlib import Path

# ゲームの情報の種別。並びは要求の文に並べる順でもある
NOTE_KINDS = ("controls", "tips", "progress", "strategy")
# 種別 → 要求の文で使う見出し
NOTE_TITLES = {
    "controls": "ゲームの操作方法",
    "tips": "攻略情報・Tips",
    "progress": "進行状況",
    "strategy": "戦略",
}
# まだ何も書かれていない種別の代わりに置く文
EMPTY_NOTE_TEXT = "(まだ書かれていない)"
# ゲーム名に使える文字。ディレクトリー名に使うため、パスの区切りや .. を含められないようにする
GAME_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def game_notes_dir(root: Path, game: str) -> Path:
    """ゲーム game の情報を置くディレクトリー root / game を返す (作らない)。

    game が GAME_NAME_PATTERN に fullmatch しなければ ValueError にする (root の外を指せないようにする)。
    """
    raise NotImplementedError


def ensure_game_notes(root: Path, game: str) -> dict[str, Path]:
    """ゲームの情報のディレクトリーと、NOTE_KINDS の 4 つの <種別>.md を作り、種別 → パスの辞書を返す。

    ディレクトリーは親を含めて作る。作るファイルの中身は空にする。既にあるファイルは変えない。
    game が不正なら game_notes_dir と同じく ValueError にする。
    """
    raise NotImplementedError


def read_game_notes(paths: Mapping[str, Path]) -> dict[str, str]:
    """paths (種別 → パス) のファイルを UTF-8 で読み、種別 → 中身の辞書を返す。

    無いファイルと読めないファイル (OSError、UnicodeDecodeError) は空文字にする。読めない理由は呼び出し側に
    返さず、無いファイル以外は警告を標準エラー出力に 1 行書く。
    """
    raise NotImplementedError


def format_game_notes(notes: Mapping[str, str]) -> str:
    """NOTE_KINDS の順に、`## <NOTE_TITLES の見出し>` の行と中身を並べた文を返す。

    各種別は `## <見出し>\\n<中身の前後の空白を除いたもの>` とし、種別の間は空行 1 つ (\\n\\n) でつなぐ。
    中身の前後の空白を除くと空になるか、notes にその種別が無ければ、中身の代わりに EMPTY_NOTE_TEXT を置く。
    """
    raise NotImplementedError
