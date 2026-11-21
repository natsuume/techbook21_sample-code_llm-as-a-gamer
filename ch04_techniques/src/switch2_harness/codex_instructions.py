"""Codex のプレイヤーへの指示ファイル (AGENTS.md) の文を、Claude Code 版のテンプレートから作る。

テンプレート (instructions/*.md) は Claude Code 版の 1 つだけを持ち、Codex 版は読んだテンプレートの
Claude Code のツールの名前の箇所 (CODEX_REPLACEMENTS) と、サブコマンドの書き方 (CLAUDE_HARNESS_COMMAND) だけを
置き換えて作る。ほかの文は Claude Code 版と同じ。
    - 画像を開く: Read ツール → view_image ツール
    - notes.md を読む: Read ツール・Read → シェルのコマンド (cat など)
    - notes.md に書く: Write か Edit ツール・Edit → apply_patch ツール・apply_patch
    - サブコマンド: `uv run switch2-harness` → ハーネスの実行ファイルの絶対パス (codex_command.harness_executable)。
      Codex の rules がサンドボックスの外で動かすのは、その絶対パスの形だけのため
ツールの名前の元の文は、そのテンプレートにちょうど 1 回現れなければならない。0 回か 2 回以上なら ValueError にする
(Claude Code 版のテンプレートが変わったときに、置き換えの漏れに気づくため)。サブコマンドの書き方は、現れるものをすべて置き換える。
"""

from collections.abc import Sequence
from pathlib import Path

from switch2_harness.instructions import ReadTemplate

# Claude Code 版のテンプレートの、サブコマンドの書き出し
CLAUDE_HARNESS_COMMAND = "uv run switch2-harness"

# テンプレートのファイル名 → (Claude Code 版の文, Codex 版の文) の組。この順に置き換える
CODEX_REPLACEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "base.md": (
        (
            "- `look` が表示した PNG は、Read ツールで開いて読みます",
            "- `look` が表示した PNG は、view_image ツールで開いて読みます",
        ),
    ),
    "notes.md": (
        (
            "操作の前に `notes.md` を Read ツールで読んでください",
            "操作の前に `notes.md` をシェルのコマンド (`cat` など) で読んでください",
        ),
        (
            "その場で Write か Edit ツールで `notes.md` に書いてください",
            "その場で apply_patch ツールで `notes.md` に書いてください",
        ),
    ),
    "reflection.md": (
        (
            "`notes.md` を Read ツールで読み直してから操作を続けてください",
            "`notes.md` をシェルのコマンド (`cat` など) で読み直してから操作を続けてください",
        ),
        (
            "Edit が失敗することがあります。そのときは `notes.md` を Read で読み直してから書いてください",
            "apply_patch が失敗することがあります。そのときは `notes.md` をシェルのコマンドで読み直してから書いてください",
        ),
    ),
    "multi-frame.md": (
        (
            "- 並べた画像を Read ツールで開いて読みます。",
            "- 並べた画像を view_image ツールで開いて読みます。",
        ),
    ),
}


def replace_exactly_once(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    """text の中の、replacements の (元の文, 新しい文) の元の文を、順に新しい文に置き換えて返す。

    それぞれの元の文は、その時点の text にちょうど 1 回現れなければならない。0 回か 2 回以上なら ValueError にする。
    """
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"expected exactly one {old!r} in the text, found {count}")
        text = text.replace(old, new)
    return text


def codex_template(file_name: str, text: str, executable: Path) -> str:
    """テンプレート file_name の本文 text を Codex 版にして返す。

    CODEX_REPLACEMENTS に file_name があれば replace_exactly_once で置き換え、続けて CLAUDE_HARNESS_COMMAND を
    すべて str(executable) (ハーネスの実行ファイルの絶対パス) に置き換える。
    """
    replaced = replace_exactly_once(text, CODEX_REPLACEMENTS.get(file_name, ()))
    return replaced.replace(CLAUDE_HARNESS_COMMAND, str(executable))


def codex_read_template(read_template: ReadTemplate, executable: Path) -> ReadTemplate:
    """read_template で読んだテンプレートを codex_template で Codex 版にして返す、テンプレートの読み込みの関数を返す。

    instructions.render_instructions に渡して、AGENTS.md の本文を作る。executable はハーネスの実行ファイルの絶対パス。
    """

    def read_codex_template(file_name: str) -> str:
        return codex_template(file_name, read_template(file_name), executable)

    return read_codex_template
